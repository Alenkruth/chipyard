// See LICENSE for license details

#include "bridges/clock.h"
#include "bridges/dmibridge.h"
#include "bridges/fased_memory_timing_model.h"
#include "bridges/heartbeat.h"
#include "bridges/ift_bridge.h"
#include "bridges/peek_poke.h"
#include "bridges/tsibridge.h"
#include "core/bridge_driver.h"
#include "core/simif.h"
#include "core/simulation.h"
#include "core/systematic_scheduler.h"
#include "core/timing.h"

#include <algorithm>
#include <cinttypes>
#include <cstdlib>
#include <set>
#include <unistd.h>

class firesim_top_t : public systematic_scheduler_t, public simulation_t {
public:
  firesim_top_t(simif_t &simif,
                widget_registry_t &registry,
                const std::vector<std::string> &args);

  int simulation_run() override;

  // [CHUNKDRAIN] patch 54: runs the end-of-run drain BEFORE the bridges' finish().
  // execute_simulation_flow() calls record_end_times() before simulation_finish(), so
  // the reported "Target Cycles Emulated" / PASSED / FAILED cycle counts do NOT include
  // the drained cycles (simulation.cc:146-150,166-178).
  void simulation_finish() override;

  bool simulation_timed_out() override { return !terminated; }

private:
  simif_t &simif;
  /// Reference to the peek-poke bridge.
  peek_poke_t &peek_poke;
  /// Reference to the clock widget (cumulative target cycle since the last flash).
  clockmodule_t &clockmod;
  /// Flag to indicate that the simulation was terminated.
  bool terminated = false;

  // [CHUNKDRAIN] patch 54 state.
  /// Largest single STEP written to PeekPoke (+step-chunk=N, default 1M).
  uint64_t step_chunk = 1000000;
  /// Wall-clock deadline of the end-of-run drain (+step-drain-timeout=S, default 10 s).
  double drain_timeout_s = 10.0;
  /// True when the patch-53 start guard refused to run (nothing may touch the target).
  bool refused_stale = false;
  /// +dump-mstack is set: skip the drain so the post-run M-stack dump sees the
  /// target exactly where it stopped (simulation.cc:152-164 runs after finish()).
  bool skip_drain = false;

  /// Pull every IFT stream until it stays empty (2 empty pulls >= 1 ms apart).
  /// Ticks ONLY the IFT bridges. Returns false if max_s passed first.
  bool settle_ift_streams(const std::vector<ift_bridge_t *> &ifts, double max_s);
  void end_of_run_drain();
};

firesim_top_t::firesim_top_t(simif_t &simif,
                             widget_registry_t &registry,
                             const std::vector<std::string> &args)
    : systematic_scheduler_t(args), simulation_t(registry, args), simif(simif),
      peek_poke(registry.get_widget<peek_poke_t>()),
      clockmod(registry.get_widget<clockmodule_t>()) {

  // Cycles to advance before profiling instrumentation registers in models.
  std::optional<uint64_t> profile_interval;
  for (auto &arg : args) {
    if (arg.find("+profile-interval=") == 0) {
      profile_interval = atoi(arg.c_str() + 18);

      if (*profile_interval == 0) {
        fprintf(stderr, "Must provide a profile interval > 0\n");
        exit(1);
      }
    }
    // [CHUNKDRAIN] patch 54 plusargs.
    if (arg.find("+step-chunk=") == 0) {
      const uint64_t n = strtoull(arg.c_str() + 12, nullptr, 0);
      if (n == 0) {
        fprintf(stderr, "+step-chunk must be > 0\n");
        exit(1);
      }
      step_chunk = std::min<uint64_t>(n, MAX_MIDAS_STEP);
    }
    if (arg.find("+step-drain-timeout=") == 0) {
      drain_timeout_s = strtod(arg.c_str() + 20, nullptr);
    }
    if (arg.find("+dump-mstack=") == 0) {
      skip_drain = true;
    }
  }

  registry.add_widget(
      new heartbeat_t(simif, registry.get_widget<clockmodule_t>(), args));

  // Add functions you'd like to periodically invoke on a paused simulator here.
  if (profile_interval) {
    register_task(
        0, [&, profile_interval] { // capture profile_interval by value,
                                   //  since its lifetime is bound to
                                   //  firesim_top_t's constructor
          for (auto &mod : registry.get_bridges<FASEDMemoryTimingModel>()) {
            mod->profile();
          }
          return *profile_interval;
        });
  }
}

int firesim_top_t::simulation_run() {
  int exit_code = 0;
  // [STALESTEP] patch 53 (owner 2026-10-17): the PeekPoke STEP path holds one active
  // horizon plus a 2-deep queue and is reset only by reprogramming the FPGA.  A launch
  // that ends early (program exit / abort / kill) leaves its step unconsumed; the 4th such
  // launch after a flash writes STEP into a full queue, the AXI-Lite write is never
  // acknowledged, the next BAR0 read gets no completion and the host takes a fatal MCE
  // (phase4/crash-20261017/fix-scope.md).  Reading DONE is safe; writing STEP is not.
  // So refuse to step when a stale horizon is still pending: exit 75, reflash required.
  // patch 54: `terminated = true` makes simulation_timed_out() false, so
  // execute_simulation_flow() returns 75 instead of mapping it to EXIT_FAILURE
  // (simulation.cc:166,183); `refused_stale` keeps simulation_finish() from draining.
  if (!peek_poke.is_done()) {
    fprintf(stderr,
            "[STALESTEP] PeekPoke DONE=0 at start: a stale step horizon from an earlier "
            "launch is pending; refusing to write STEP. Reflash the FPGA (firesim "
            "infrasetup) before the next run.\n");
    fflush(stderr);
    refused_stale = true;
    terminated = true;
    return 75;
  }
  // [CHUNKDRAIN] patch 54: step in chunks of at most step_chunk cycles.  Invariant: a
  // STEP is written only right after a DONE read returned 1, so the PeekPoke STEP queue
  // never holds more than the one active horizon, and at most one chunk is left
  // unconsumed when a bridge terminates; simulation_finish() drains it.  The scheduler
  // still hands out the same steps (get_largest_stepsize), and run_scheduled_tasks()
  // still runs only once the whole scheduler step has been consumed.
  fprintf(stderr,
          "[CHUNKDRAIN] start: DONE=1 tcycle=%" PRIu64 " step-chunk=%" PRIu64 "\n",
          clockmod.tcycle(),
          step_chunk);
  while (!terminated && !finished_scheduled_tasks()) {
    run_scheduled_tasks();
    uint64_t remaining = get_largest_stepsize();
    while (remaining > 0 && !terminated) {
      if (!peek_poke.is_done()) {
        // Unreachable by construction (the inner loop below exits only on DONE=1).
        fprintf(stderr,
                "[CHUNKDRAIN] internal error: DONE=0 before a STEP write; not writing "
                "STEP\n");
        exit_code = 75;
        terminated = true;
        break;
      }
      const uint64_t n = std::min<uint64_t>(remaining, step_chunk);
      peek_poke.step(n, false);
      remaining -= n;
      while (!peek_poke.is_done() && !terminated) {
        for (auto *bridge : registry.get_all_bridges()) {
          bridge->tick();
          if (bridge->terminate()) {
            exit_code = bridge->exit_code();
            terminated = true;
            break;
          }
        }
      }
    }
  }
  return exit_code;
}

bool firesim_top_t::settle_ift_streams(const std::vector<ift_bridge_t *> &ifts,
                                       double max_s) {
  if (ifts.empty())
    return true;
  const midas_time_t start = timestamp();
  int quiet = 0;
  while (quiet < 2) {
    uint64_t before = 0, after = 0;
    for (auto *b : ifts)
      before += b->beats_seen();
    for (auto *b : ifts)
      b->tick();
    for (auto *b : ifts)
      after += b->beats_seen();
    if (after == before) {
      if (++quiet < 2)
        usleep(1000);
    } else {
      quiet = 0;
    }
    if (diff_secs(timestamp(), start) > max_s)
      return false;
  }
  return true;
}

// [CHUNKDRAIN] patch 54 (owner 2026-10-17, "A+B"): end-of-run drain.
//   1. Settle: the target has stopped (a terminated TSI/DMI bridge grants no more
//      tokens; on a max-cycles end the horizon is 0).  Pull the IFT stream tail into the
//      bin (records up to that stop point, previously left in the FPGA queue).
//   2. Drain: if DONE=0, keep the target running in its exit loop until the leftover
//      chunk is consumed.  TSI/DMI only get their token gate re-armed (no fesvr calls
//      after their fesvr finished); IFT records are pulled and DISCARDED (post-run);
//      every other bridge is ticked as usual.  Deadline drain_timeout_s.
//   3. Post-drain settle: empty the IFT FPGA queue (discarded), so the next launch's
//      first pull does not return this launch's tail.
// Nothing here writes STEP.  If the deadline passes, DONE stays 0 and the next launch's
// patch-53 guard refuses with exit 75; the exit code of this launch is unchanged.
void firesim_top_t::end_of_run_drain() {
  auto ifts = registry.get_bridges<ift_bridge_t>();
  auto tsis = registry.get_bridges<tsibridge_t>();
  auto dmis = registry.get_bridges<dmibridge_t>();
  std::set<bridge_driver_t *> gated(tsis.begin(), tsis.end());
  gated.insert(dmis.begin(), dmis.end());

  uint64_t tail0 = 0, tail1 = 0;
  for (auto *b : ifts)
    tail0 += b->beats_seen();
  if (!settle_ift_streams(ifts, 2.0))
    fprintf(stderr, "[CHUNKDRAIN] warning: IFT stream did not go quiet within 2 s\n");
  for (auto *b : ifts)
    tail1 += b->beats_seen();

  if (peek_poke.is_done()) {
    fprintf(stderr,
            "[CHUNKDRAIN] end: horizon already consumed (DONE=1), tcycle=%" PRIu64
            ", IFT tail beats kept=%" PRIu64 "\n",
            clockmod.tcycle(),
            tail1 - tail0);
    return;
  }
  if (skip_drain) {
    fprintf(stderr,
            "[STALESTEP-DRAIN] drain skipped (+dump-mstack): DONE=0, reflash required "
            "before the next launch\n");
    fflush(stderr);
    return;
  }

  for (auto *b : ifts)
    b->set_discard(true);
  const uint64_t t0 = clockmod.tcycle();
  const midas_time_t start = timestamp();
  bool drained = true;
  while (!peek_poke.is_done()) {
    if (diff_secs(timestamp(), start) > drain_timeout_s) {
      drained = false;
      break;
    }
    for (auto *bridge : registry.get_all_bridges()) {
      if (gated.count(bridge)) {
        continue; // handled below
      }
      bridge->tick();
    }
    for (auto *b : tsis)
      b->drain_keepalive();
    for (auto *b : dmis)
      b->drain_keepalive();
  }
  const double secs = diff_secs(timestamp(), start);
  const uint64_t t1 = clockmod.tcycle();

  if (!drained) {
    fprintf(stderr,
            "[STALESTEP-DRAIN] reflash required: leftover step not drained within "
            "%.1f s (tcycle %" PRIu64 " -> %" PRIu64
            ", DONE=0). The next launch will refuse with exit 75 until the FPGA is "
            "reflashed (firesim infrasetup).\n",
            drain_timeout_s,
            t0,
            t1);
    fflush(stderr);
    return;
  }
  if (!settle_ift_streams(ifts, 1.0))
    fprintf(stderr,
            "[CHUNKDRAIN] warning: IFT stream not empty 1 s after the drain; the next "
            "launch's bin may start with stale records\n");
  uint64_t discarded = 0;
  for (auto *b : ifts)
    discarded += b->discarded();
  fprintf(stderr,
          "[CHUNKDRAIN] end: drained %" PRIu64 " target cycles in %.3f s, DONE=1, "
          "tcycle=%" PRIu64 ", IFT tail beats kept=%" PRIu64
          ", post-run beats discarded=%" PRIu64 "\n",
          t1 - t0,
          secs,
          t1,
          tail1 - tail0,
          discarded);
  fflush(stderr);
}

void firesim_top_t::simulation_finish() {
  if (!refused_stale)
    end_of_run_drain();
  simulation_t::simulation_finish();
}

std::unique_ptr<simulation_t>
create_simulation(simif_t &simif,
                  widget_registry_t &registry,
                  const std::vector<std::string> &args) {
  return std::make_unique<firesim_top_t>(simif, registry, args);
}
