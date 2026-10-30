// ift_bridge.cc — BOOM IFT FireSim bridge driver.
// See ift_bridge.h for the stream format and record layout.

#include "ift_bridge.h"

#include <cassert>
#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>

char ift_bridge_t::KIND;

// IFT record layout (256 bits = 32 bytes, little-endian):
//   bits[1:0]     event_type  (01=commit, 10=squash, 00=NOP/padding)
//   bits[4:2]     priv
//   bits[44:5]    pc[39:0]
//   bits[76:45]   insn[31:0]
//   bits[83:77]   flags (domain, spec, atk, s_acc, s_prop, s_tx, src_tainted)
//   bits[85:84]   infl_dropped              (2b saturating COUNT of discarded influencers;
//                                            was a 1b sticky infl_ovf flag)
//   bits[101:86]  op_count[15:0]            (16b = uopIDCounterWidthCF)
//   bits[103:102] spec_branch_is_atk, spec_branch_is_secret
//   bits[119:104] spec_branch_op_id[15:0]   (16b = uopIDCounterWidthCF)
//   bits[120]     single_step
//   bits[144:121] fu_bitmap[23:0]
//   bits[147:145] cntd_deny_count           (3b Log2Bucket: issue-port denial cycles)
//   bits[150:148] stall_cycles_rob          (3b Log2Bucket: ROB-full dispatch stall)
//   bits[153:151] stall_cycles_stq          (3b Log2Bucket: STQ head-of-line residency)
//   bits[217:154] influencer[0..3]          (4 × 16b each)
//     Per slot: valid(1)+op_count(8)+infl_type(5)+is_atk(1)+is_secret(1)
//     op_count holds only the LOW 8 bits of the producer's id; software reconstructs
//     it against this record's own op_count.
//   bits[255:218] reserved/zero
//
// NOTE: this driver does not decode any of the above -- it extracts only event_type
// (bits[1:0]) and copies the remaining 254 bits opaquely, so a layout change needs no
// code change here.  The decoders that DO care are IFTBridgeIO.scala (packer) and
// base-attacks/fuzzer/decode_ift_bridge.py (unpacker); keep all three in lockstep.
struct ift_record_t {
  uint64_t words[4];  // 4 × 64b = 256b
};
static_assert(sizeof(ift_record_t) == 32, "IFT record must be 32 bytes");

// Extract event_type from a packed record.
static inline int event_type(const ift_record_t &r) {
  return static_cast<int>(r.words[0] & 0x3);
}

ift_bridge_t::ift_bridge_t(simif_t &sim,
                            StreamEngine &stream,
                            int /*wid*/,
                            const std::vector<std::string> &args,
                            int stream_idx,
                            int stream_depth,
                            unsigned int retire_width,
                            unsigned int record_bits,
                            const ClockInfo &clock_info)
    : streaming_bridge_driver_t(sim, stream, &KIND),
      stream_idx(stream_idx),
      stream_depth(stream_depth),
      retire_width(retire_width),
      record_bits(record_bits),
      clock_info(clock_info) {

  outfilename = "";
  const std::string ift_out_arg = "+ift-out=";
  for (const auto &arg : args) {
    if (arg.find(ift_out_arg) == 0) {
      outfilename = arg.substr(ift_out_arg.size());
    }
  }
  // [DRAINHIST] optional interval print period in seconds (0 = off, the default).
  const std::string period_arg = "+ift-drain-stats-period=";
  for (const auto &arg : args) {
    if (arg.find(period_arg) == 0) {
      dh_period_ns = strtoull(arg.substr(period_arg.size()).c_str(), nullptr, 10) *
                     1000000000ULL;
    }
  }
  // If no +ift-out= is provided, drain the stream without writing.
  drain_only = outfilename.empty();
  if (drain_only) {
    fprintf(stdout, "[IFTBridge] no +ift-out= provided — draining stream without recording\n");
  }

  // [DRVXDMA] One page-aligned buffer for the whole run (see ift_bridge.h).  The old
  // per-tick std::vector was only malloc-aligned (16 B) and was also zero-filled
  // (32 KB) on every tick.
  const size_t want = static_cast<size_t>(stream_depth) * STREAM_WIDTH_BYTES;
  dma_buf_bytes = ((want + page_size - 1) / page_size) * page_size;
  dma_buf = static_cast<uint8_t *>(std::aligned_alloc(page_size, dma_buf_bytes));
  if (!dma_buf) {
    fprintf(stderr, "[IFTBridge] ERROR: cannot allocate %zu-byte DMA buffer\n",
            dma_buf_bytes);
    abort();
  }
}

ift_bridge_t::~ift_bridge_t() {
  if (outfile) fclose(outfile);
  std::free(dma_buf);
}

void ift_bridge_t::init() {
  // [DRAINHIST] wall-time origin; the interval snapshot starts here too.
  dh_start_ns = dh_iv_ns = dh_now_ns();
  dh_next_print_ns = dh_start_ns + dh_period_ns;
  if (drain_only) return;
  outfile = fopen(outfilename.c_str(), "wb");
  if (!outfile) {
    fprintf(stderr, "[IFTBridge] ERROR: cannot open output file '%s'\n",
            outfilename.c_str());
  }
}

// [DRAINHIST] monotonic clock in ns (vDSO, no syscall).
uint64_t ift_bridge_t::dh_now_ns() {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return static_cast<uint64_t>(ts.tv_sec) * 1000000000ULL + ts.tv_nsec;
}

// [DRAINHIST] one line per period: pulls, empty/full pulls, beats and the share of the
// interval spent in pull() and in the copy/fwrite loop.
void ift_bridge_t::dh_print_interval(uint64_t now) {
  uint64_t pulls = 0;
  for (int k = 0; k < DH_BUCKETS; ++k) pulls += dh_hist[k];
  const double dt = (now - dh_iv_ns) * 1e-9;
  const uint64_t beats = total_beats - dh_iv_beats;
  fprintf(stderr,
          "[IFTDRAIN] interval t=%.1fs dt=%.2fs pulls=%" PRIu64 " empty=%" PRIu64
          " full=%" PRIu64 " beats=%" PRIu64 " beats_per_s=%.0f pull=%.1f%% write=%.1f%%\n",
          (now - dh_start_ns) * 1e-9, dt, pulls - dh_iv_pulls, dh_hist[0] - dh_iv_empty,
          dh_full - dh_iv_full, beats, dt > 0 ? beats / dt : 0.0,
          dt > 0 ? 100.0 * (dh_pull_ns - dh_iv_pull_ns) * 1e-9 / dt : 0.0,
          dt > 0 ? 100.0 * (dh_write_ns - dh_iv_write_ns) * 1e-9 / dt : 0.0);
  dh_iv_ns = now;
  dh_iv_pulls = pulls;
  dh_iv_beats = total_beats;
  dh_iv_full = dh_full;
  dh_iv_empty = dh_hist[0];
  dh_iv_pull_ns = dh_pull_ns;
  dh_iv_write_ns = dh_write_ns;
  dh_next_print_ns = now + dh_period_ns;
}

void ift_bridge_t::tick() {
  // [DRAINHIST] time outside this bridge's tick (other bridges, DONE polling, scheduler).
  const uint64_t t0 = dh_now_ns();
  if (dh_last_tick_end_ns != 0) dh_outside_ns += t0 - dh_last_tick_end_ns;
  ++dh_ticks;
  tick_stream(t0);
  const uint64_t t3 = dh_now_ns();
  dh_last_tick_end_ns = t3;
  if (dh_period_ns != 0 && !discard && t3 >= dh_next_print_ns) dh_print_interval(t3);
}

void ift_bridge_t::tick_stream(uint64_t t0) {
  // Each DMA beat is 512 bits = 64 bytes = 2 IFT records of 256 bits each.
  constexpr int RECORDS_PER_BEAT = 2;
  constexpr int BYTES_PER_RECORD = 32;  // 256 bits
  constexpr int BYTES_PER_BEAT   = RECORDS_PER_BEAT * BYTES_PER_RECORD;  // 64

  static_assert(BYTES_PER_BEAT == STREAM_WIDTH_BYTES, "IFT beat must equal the stream width");

  // pull_stream returns the number of bytes consumed; beats are BYTES_PER_BEAT each.
  // We read in chunks matching the DMA buffer depth, into the page-aligned dma_buf.
  const size_t max_bytes = static_cast<size_t>(stream_depth) * BYTES_PER_BEAT;
  const uint8_t *buf = dma_buf;

  size_t bytes_received = pull(stream_idx, dma_buf, max_bytes, BYTES_PER_BEAT);
  const uint64_t t1 = dh_now_ns();  // [DRAINHIST]
  if (bytes_received == 0) {
    if (!discard) {
      ++dh_hist[0];
      dh_pull_ns += t1 - t0;
      dh_empty_pull_ns += t1 - t0;
    }
    return;
  }
  // [DRVXDMA] Never trust a count larger than the request.  A failed pread() used to come
  // back as (size_t)-1 here; the loop below then walked off the end of the buffer into
  // unmapped heap (SIGSEGV, exit 139).  cpu_managed_stream.cc now stops on that error;
  // this check keeps any other bad count from turning into an out-of-bounds read.
  if (bytes_received > max_bytes) {
    fprintf(stderr, "[IFTBridge] ERROR: pull returned %zu bytes for a %zu-byte request; "
                    "stream is corrupt, stopping\n", bytes_received, max_bytes);
    flush();
    exit(74);
  }

  // Truncate to complete beats; a partial beat can arrive when the FPGA resets
  // mid-DMA at simulation teardown — discard the incomplete tail.
  bytes_received -= (bytes_received % BYTES_PER_BEAT);
  const size_t num_beats = bytes_received / BYTES_PER_BEAT;
  // [CHUNKDRAIN] patch 54: post-run beats (end-of-run drain) are pulled and dropped.
  if (discard) {
    ++dh_discard_pulls;  // [DRAINHIST]
    discarded_beats += num_beats;
    return;
  }
  // [DRAINHIST] run-phase pull: size bucket, queue-full count, time in pull().
  {
    const int k = num_beats == 0 ? 0 : 64 - __builtin_clzll(static_cast<uint64_t>(num_beats));
    ++dh_hist[k < DH_BUCKETS ? k : DH_BUCKETS - 1];
    if (num_beats >= static_cast<size_t>(stream_depth)) ++dh_full;
    if (num_beats > dh_max_beats) dh_max_beats = num_beats;
    dh_pull_ns += t1 - t0;
  }
  total_beats += num_beats;

  if (drain_only || !outfile) return;

  for (size_t i = 0; i < num_beats; ++i) {
    const uint8_t *beat = buf + i * BYTES_PER_BEAT;
    for (int r = 0; r < RECORDS_PER_BEAT; ++r) {
      ift_record_t rec;
      memcpy(&rec, beat + r * BYTES_PER_RECORD, BYTES_PER_RECORD);
      if (event_type(rec) != 0) {
        fwrite(&rec, BYTES_PER_RECORD, 1, outfile);
        ++total_records;
      }
    }
  }
  dh_write_ns += dh_now_ns() - t1;  // [DRAINHIST] copy + fwrite loop
}

void ift_bridge_t::flush() {
  if (outfile) fflush(outfile);
}

void ift_bridge_t::finish() {
  // [DRAINHIST] the final fflush counts as write time.
  const uint64_t tf = dh_now_ns();
  flush();
  dh_write_ns += dh_now_ns() - tf;
  if (drain_only)
    fprintf(stderr, "[IFTBridge] drain-only mode — no records written\n");
  else
    fprintf(stderr, "[IFTBridge] Wrote %" PRIu64 " records (%" PRIu64 " beats) to %s\n",
            total_records, total_beats, outfilename.c_str());
  if (discarded_beats != 0)
    fprintf(stderr, "[IFTBridge] discarded %" PRIu64 " post-run beats (end-of-run drain, "
                    "not written)\n", discarded_beats);

  // [DRAINHIST] summary.  Run phase = init() .. start of the end-of-run discard (or now).
  const uint64_t end = dh_run_end_ns != 0 ? dh_run_end_ns : dh_now_ns();
  const double wall = (end - dh_start_ns) * 1e-9;
  uint64_t pulls = 0;
  for (int k = 0; k < DH_BUCKETS; ++k) pulls += dh_hist[k];
  const double pull_s = dh_pull_ns * 1e-9, empty_s = dh_empty_pull_ns * 1e-9;
  const double write_s = dh_write_ns * 1e-9, out_s = dh_outside_ns * 1e-9;
  const uint64_t nonempty = pulls - dh_hist[0];
  fprintf(stderr,
          "[IFTDRAIN] run wall_s=%.3f ticks=%" PRIu64 " pulls=%" PRIu64 " nonempty=%" PRIu64
          " full=%" PRIu64 " (depth %d) max_pull_beats=%" PRIu64 "\n",
          wall, dh_ticks, pulls, nonempty, dh_full, stream_depth, dh_max_beats);
  fprintf(stderr,
          "[IFTDRAIN] volume beats=%" PRIu64 " bytes_pulled=%" PRIu64 " records=%" PRIu64
          " bytes_written=%" PRIu64 " beats_per_s=%.0f MB_written_per_s=%.2f"
          " beats_per_nonempty_pull=%.1f\n",
          total_beats, total_beats * 64, total_records, total_records * 32,
          wall > 0 ? total_beats / wall : 0.0, wall > 0 ? total_records * 32 / wall / 1e6 : 0.0,
          nonempty ? double(total_beats) / nonempty : 0.0);
  fprintf(stderr,
          "[IFTDRAIN] time pull_s=%.3f (empty %.3f, nonempty %.3f) write_s=%.3f outside_s=%.3f"
          " | pct of wall: pull %.1f write %.1f outside %.1f | us per empty pull %.2f,"
          " per nonempty pull %.2f, nonempty pull MB/s %.1f\n",
          pull_s, empty_s, pull_s - empty_s, write_s, out_s,
          wall > 0 ? 100 * pull_s / wall : 0.0, wall > 0 ? 100 * write_s / wall : 0.0,
          wall > 0 ? 100 * out_s / wall : 0.0,
          dh_hist[0] ? 1e6 * empty_s / dh_hist[0] : 0.0,
          nonempty ? 1e6 * (pull_s - empty_s) / nonempty : 0.0,
          pull_s - empty_s > 0 ? total_beats * 64 / (pull_s - empty_s) / 1e6 : 0.0);
  fprintf(stderr, "[IFTDRAIN] hist beats_per_pull:");
  for (int k = 0; k < DH_BUCKETS; ++k) {
    if (dh_hist[k] == 0) continue;
    if (k == 0) fprintf(stderr, " 0:%" PRIu64, dh_hist[k]);
    else if (k == 1) fprintf(stderr, " 1:%" PRIu64, dh_hist[k]);
    else {
      const unsigned long long lo = 1ULL << (k - 1);
      unsigned long long hi = (1ULL << k) - 1;
      if (hi > static_cast<unsigned long long>(stream_depth)) hi = stream_depth;
      if (lo == hi) fprintf(stderr, " %llu:%" PRIu64, lo, dh_hist[k]);
      else fprintf(stderr, " %llu-%llu:%" PRIu64, lo, hi, dh_hist[k]);
    }
  }
  fprintf(stderr, " | full(=%d):%" PRIu64 "\n", stream_depth, dh_full);
  fprintf(stderr, "[IFTDRAIN] post-run discard_pulls=%" PRIu64 " discard_beats=%" PRIu64 "\n",
          dh_discard_pulls, discarded_beats);
}
