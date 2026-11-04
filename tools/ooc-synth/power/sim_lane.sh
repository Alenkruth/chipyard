#!/usr/bin/env bash
# sim_lane.sh -- run the power-calibration simulation lane for one or more configs, one simulator at a time.
#
# Usage : sim_lane.sh ID [ID ...]        (ids from cal_cfgs.env: W4 W2 W1 MIN4 HELD2)
#         launch it detached:  setsid nohup bash sim_lane.sh W4 W2 ... > LOG 2>&1 < /dev/null &
# Per id: S0 (smoke dump, 60k cycles) -> C0 (converter vs reference, 1 thread, runs beside the next sim)
#         -> S1 (untraced reference run) -> wait C0 -> window -> S2 (traced run to MAXC).  C1/V are not run here.
# Host rule (controller 2026-11-03 08:5x, owner order "ooc takes the gaps"):
#   - a simulator is launched only when 0 simulators run host-wide (same process pattern as pilot.sh n_sims / the gate);
#   - right after the launch the count is re-checked; if another simulator started in the same instant (count > 1),
#     OUR just-started step is stopped (its process group) before it does work, and the launch is retried at the next gap.
# Root   : BASE=${CAL_W_BASE:-/data/akrish/scratch/power-cal}; work dir of id = $BASE/<id>, passed to every pilot.sh call as W=$BASE/<id>.
# C0_SERIAL=1: wait for C0 to end before S1 (peak = one simulator only, e.g. beside builds); default 0 = C0 runs beside S1.
# Output: one milestone line per event on stdout ("SIMLANE <time> ..."); exit 0 when every id is done,
#         exit 1 on the first failed step (nothing after it is started).  Nothing is deleted.
set -u
HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
PILOT="$HERE/pilot.sh"; BASE=${CAL_W_BASE:-/data/akrish/scratch/power-cal}
C0_SERIAL=${C0_SERIAL:-0}
n_sims() { pgrep -f '^\S*(simulator-chipyard|VFireSim)' | wc -l; }
say() { echo "SIMLANE $(date '+%F %T') $*"; }
pid_of() { cat "$BASE/$1/logs/$2.pid" 2>/dev/null; }
wait_step() { # id step -> rc
  local p; p=$(pid_of "$1" "$2"); while [ -n "$p" ] && kill -0 "$p" 2>/dev/null; do sleep 10; done
  cat "$BASE/$1/logs/$2.rc" 2>/dev/null || echo 99; }
run_sim() { # id step: gap wait, launch, recheck, wait, rc
  local id=$1 st=$2 tries=0 p
  while :; do
    local waited=0; while [ "$(n_sims)" -gt 0 ]; do [ $waited = 0 ] && say "$id $st waiting for a gap ($(n_sims) sim running)"; waited=1; sleep 20; done
    [ $waited = 1 ] && say "$id $st gap found"
    CAL_ID=$id W="$BASE/$id" SLOT_GRANTED=1 bash "$PILOT" "$st" > "$BASE/$id/logs/$st.launch.out" 2>&1 || { say "$id $st LAUNCH REFUSED: $(tail -2 "$BASE/$id/logs/$st.launch.out" | tr '\n' ' ')"; return 1; }
    sleep 3; p=$(pid_of "$id" "$st")
    if [ "$(n_sims)" -gt 1 ]; then
      kill -TERM -- "-$p" 2>/dev/null; tries=$((tries+1)); say "$id $st collided with another sim at start: stopped ours (pgid $p), retry $tries"
      while kill -0 "$p" 2>/dev/null; do sleep 2; done; sleep 30; continue
    fi
    say "$id $st STARTED pid $p"; break
  done
  local rc; rc=$(wait_step "$id" "$st"); say "$id $st END rc=$rc"
  [ "$st" = S2 ] && return 0   # S2 ends at +max-cycles by design (nonzero sim rc); judged by the FST, checked by C1
  [ "$rc" = 0 ]; }
for id in "$@"; do
  run_sim "$id" S0 || { say "$id S0 FAILED"; exit 1; }
  CAL_ID=$id W="$BASE/$id" bash "$PILOT" C0 > "$BASE/$id/logs/C0.launch.out" 2>&1 || { say "$id C0 LAUNCH REFUSED"; exit 1; }
  say "$id C0 STARTED pid $(pid_of "$id" C0)"
  if [ "$C0_SERIAL" = 1 ]; then
    rc=$(wait_step "$id" C0); say "$id C0 END rc=$rc $(grep -h C0_RESULT "$BASE/$id/logs/C0.log" 2>/dev/null)"; [ "$rc" = 0 ] || { say "$id C0 FAILED"; exit 1; }
    run_sim "$id" S1 || { say "$id S1 FAILED"; exit 1; }
  else
    run_sim "$id" S1 || { say "$id S1 FAILED"; exit 1; }
    rc=$(wait_step "$id" C0); say "$id C0 END rc=$rc $(grep -h C0_RESULT "$BASE/$id/logs/C0.log" 2>/dev/null)"; [ "$rc" = 0 ] || { say "$id C0 FAILED"; exit 1; }
  fi
  CAL_ID=$id W="$BASE/$id" bash "$PILOT" window > "$BASE/$id/logs/window.out" 2>&1 || { say "$id window FAILED: $(tail -1 "$BASE/$id/logs/window.out")"; exit 1; }
  say "$id window $(grep -h 'R=' "$BASE/$id/logs/window.out" | tail -1)"
  run_sim "$id" S2 || { say "$id S2 FAILED"; exit 1; }
  ls -l "$BASE/$id/s2.fst" 2>/dev/null | awk -v i="$id" '{print "SIMLANE " i " S2 fst bytes " $5}'
done
say "ALL DONE: $*"
