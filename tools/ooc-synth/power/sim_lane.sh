#!/usr/bin/env bash
# sim_lane.sh -- run the power-calibration simulation lane for one or more configs, one simulator at a time.
#
# Usage : sim_lane.sh ID [ID ...]        (ids from cal_cfgs.env: W4 W2 W1 MIN4 HELD2)
#         launch it detached:  setsid nohup bash sim_lane.sh W4 W2 ... > LOG 2>&1 < /dev/null &
# Per id: S0 (smoke dump, 60k cycles) -> C0 (converter vs reference, 1 thread, runs beside the next sim)
#         -> S1 (untraced reference run) -> wait C0 -> window -> S2 (traced run to MAXC).  C1/V are not run here.
# Host rule (controller 2026-11-03 08:5x, owner order "ooc takes the gaps"; LANE_OTHER_SIMS_MAX since defense power):
#   - a simulator is launched only when at most LANE_OTHER_SIMS_MAX other simulators run host-wide (default 0 = launch only at 0 sims;
#     RULES B allows 2 sims, so the controller sets 1 when it grants slot 2; same process pattern as pilot.sh n_sims / the gate);
#   - right after the launch the count is re-checked; if it exceeds LANE_OTHER_SIMS_MAX + 1 (another simulator started in the same
#     instant), OUR just-started step is stopped (its process group) before it does work, and the launch is retried at the next gap.
# DRYRUN=1: run pilot.sh DRYRUN for S0 C0 S1 window S2 of every id (all checks, nothing launched), print every FAIL and PENDING line.
# Root   : BASE=${CAL_W_BASE:-/data/akrish/scratch/power-cal}; work dir of id = $BASE/<id>, passed to every pilot.sh call as W=$BASE/<id>.
# C0_SERIAL=1: wait for C0 to end before S1 (peak = one simulator only, e.g. beside builds); default 0 = C0 runs beside S1.
# Output: one milestone line per event on stdout ("SIMLANE <time> ..."); exit 0 when every id is done,
#         exit 1 on the first failed step (nothing after it is started).  Nothing is deleted.
set -u
HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
# CAL_W_BASE not exported but CAL_FILE given: take it from the CAL_FILE line (same line pilot.sh reads), so a lane never falls back to the calibration root
[ -z "${CAL_W_BASE:-}" ] && [ -n "${CAL_FILE:-}" ] && [ -f "$CAL_FILE" ] && CAL_W_BASE=$(sed -n 's/^CAL_W_BASE=//p' "$CAL_FILE" | head -1)
PILOT="$HERE/pilot.sh"; BASE=${CAL_W_BASE:-/data/akrish/scratch/power-cal}
C0_SERIAL=${C0_SERIAL:-0}
LANE_OTHER_SIMS_MAX=${LANE_OTHER_SIMS_MAX:-0}
n_sims() { pgrep -f '^\S*(simulator-chipyard|VFireSim)' | wc -l; }
say() { echo "SIMLANE $(date '+%F %T') $*"; }
pid_of() { cat "$BASE/$1/logs/$2.pid" 2>/dev/null; }
wait_step() { # id step -> rc
  local p; p=$(pid_of "$1" "$2"); while [ -n "$p" ] && kill -0 "$p" 2>/dev/null; do sleep 10; done
  cat "$BASE/$1/logs/$2.rc" 2>/dev/null || echo 99; }
run_sim() { # id step: gap wait, launch, recheck, wait, rc
  local id=$1 st=$2 tries=0 p
  while :; do
    local waited=0; while [ "$(n_sims)" -gt "$LANE_OTHER_SIMS_MAX" ]; do [ $waited = 0 ] && say "$id $st waiting for a gap ($(n_sims) sim running)"; waited=1; sleep 20; done
    [ $waited = 1 ] && say "$id $st gap found"
    CAL_ID=$id W="$BASE/$id" SLOT_GRANTED=1 bash "$PILOT" "$st" > "$BASE/$id/logs/$st.launch.out" 2>&1 || { say "$id $st LAUNCH REFUSED: $(tail -2 "$BASE/$id/logs/$st.launch.out" | tr '\n' ' ')"; return 1; }
    sleep 3; p=$(pid_of "$id" "$st")
    if [ "$(n_sims)" -gt $((LANE_OTHER_SIMS_MAX+1)) ]; then
      kill -TERM -- "-$p" 2>/dev/null; tries=$((tries+1)); say "$id $st collided with another sim at start: stopped ours (pgid $p), retry $tries"
      while kill -0 "$p" 2>/dev/null; do sleep 2; done; sleep 30; continue
    fi
    say "$id $st STARTED pid $p"; break
  done
  local rc; rc=$(wait_step "$id" "$st"); say "$id $st END rc=$rc"
  [ "$st" = S2 ] && return 0   # S2 ends at +max-cycles by design (nonzero sim rc); judged by the FST, checked by C1
  [ "$rc" = 0 ]; }
if [ "${DRYRUN:-0}" = 1 ]; then
  nf=0
  say "DRYRUN BASE=$BASE ids: $* ; CAL_FILE=${CAL_FILE:-<pilot default cal_cfgs.env>} ; LANE_OTHER_SIMS_MAX=$LANE_OTHER_SIMS_MAX ; simulators running now: $(n_sims)"
  [ -f "$PILOT" ] && echo "  ok      pilot.sh: $PILOT" || { echo "  FAIL    pilot.sh missing: $PILOT"; nf=$((nf+1)); }
  [ "$#" -gt 0 ] || { echo "  FAIL    no id given"; nf=$((nf+1)); }
  for id in "$@"; do
    for st in S0 C0 S1 window S2; do
      echo "  -- $id $st"
      out=$(CAL_ID=$id W="$BASE/$id" DRYRUN=1 bash "$PILOT" "$st" 2>&1); prc=$?
      echo "$out" | grep -E '^  (FAIL|PENDING|note) |DRYRUN SUMMARY|pilot.sh:'
      [ $prc = 0 ] || nf=$((nf+1))
    done
  done
  say "DRYRUN SUMMARY steps_with_FAIL=$nf (nothing launched)"; [ "$nf" -eq 0 ]; exit $?
fi
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
