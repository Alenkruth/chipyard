#!/usr/bin/env bash
# prewin.sh -- A1.3 step 5 pre-pass (2026-11-04). Usage: prewin.sh ID [ID ...]   (ids of the CAL_FILE; root = CAL_W_BASE, required)
# Root: CAL_W_BASE (env; or the CAL_W_BASE= line of CAL_FILE when only CAL_FILE is exported); no default, so a lane never runs on a wrong root.
# DRYRUN=1: run every check per id (inputs of earlier steps print PENDING), print the commands, launch nothing.
# Per id: one fst2saif pass over its S2 FST to get the whole-trace toggle profile (the converter profiles cycles 0..end of
# the trace whatever the window), then cal_window.py corrects A in window.env (the pilot.sh window kept as window.env.pilot).
# Same converter command as post_lane.sh C1, but outputs go under $W/prof/, so the post lane later writes s2a.* fresh on
# the corrected window (one V per id instead of two, as on the old tree). 1 thread. Stops at the first failure (exit 1).
set -u
# CAL_W_BASE not exported but CAL_FILE given: take it from the CAL_FILE line (same line pilot.sh reads), so a lane never falls back to the calibration root
[ -z "${CAL_W_BASE:-}" ] && [ -n "${CAL_FILE:-}" ] && [ -f "$CAL_FILE" ] && CAL_W_BASE=$(sed -n 's/^CAL_W_BASE=//p' "$CAL_FILE" | head -1)
ROOT=${CAL_W_BASE:?CAL_W_BASE not set (export it or give CAL_FILE with a CAL_W_BASE= line)}; PW=/home/jht9sy/work/chipyard/tools/ooc-synth/power
STRIP=TOP/TestDriver/testHarness/chiptop0/system/tile_prci_domain/element_reset_domain_boom_tile
if [ "${DRYRUN:-0}" = 1 ]; then
  nf=0; ok() { echo "  ok      $*"; }; pe() { echo "  PENDING $*"; }; bd() { nf=$((nf+1)); echo "  FAIL    $*"; }
  chkf() { if [ -e "$1" ]; then ok "$2: $1"; else bd "$2 missing: $1"; fi; }
  chkl() { if [ -e "$1" ]; then ok "$2: $1"; else pe "$2 (made by an earlier step): $1"; fi; }
  echo "PREWIN $(date '+%F %T') DRYRUN ROOT=$ROOT ids: $*"
  chkf "$PW/cal_window.py" "cal_window.py"
  if python3 -I "$PW/cal_window.py" --help > /dev/null 2>&1; then ok "cal_window.py --help runs"; else bd "cal_window.py does not start"; fi
  [ "$#" -gt 0 ] || bd "no id given"
  for id in "$@"; do
    W=$ROOT/$id
    echo "  -- id $id: W=$W"
    chkl "$W/window.env" "window.env (pilot.sh window)"; chkl "$W/logs/S1.log" "S1 log (mcycle)"; chkl "$W/bin/fst2saif" "converter"; chkl "$W/s2.fst" "S2 dump"
    echo "  | $W/bin/fst2saif --fst $W/s2.fst --scope $STRIP --clock clock --from-cycle <A_pilot> --cycles <N> --out-period-ns 10 --arrays --out $W/prof/s2a_pre.saif --profile $W/prof/s2a_profile_pre.csv"
    echo "  | python3 -I $PW/cal_window.py --profile $W/prof/s2a_profile_pre.csv --mcycle <mcycle> --n 100000 --write $W/window.env --r <R> --maxc <MAXC>"
  done
  echo "PREWIN $(date '+%F %T') DRYRUN SUMMARY FAIL=$nf (nothing launched)"; [ "$nf" -eq 0 ]; exit $?
fi
for id in "$@"; do
  W=$ROOT/$id; mkdir -p "$W/prof"
  unset R A N MAXC; . "$W/window.env"
  M=$(grep -h 'mcycle = ' "$W/logs/S1.log" | head -1 | awk '{print $3}')
  [ -n "$M" ] || { echo "PREWIN $id FAIL no mcycle in S1.log"; exit 1; }
  echo "PREWIN $(date '+%F %T') $id start R=$R A_pilot=$A N=$N MAXC=$MAXC mcycle=$M"
  "$W/bin/fst2saif" --fst "$W/s2.fst" --scope "$STRIP" --clock clock --from-cycle "$A" --cycles "$N" --out-period-ns 10 --arrays \
     --out "$W/prof/s2a_pre.saif" --profile "$W/prof/s2a_profile_pre.csv" > "$W/prof/conv.out" 2> "$W/prof/conv.err" \
     || { echo "PREWIN $id CONV FAIL: $(tail -2 "$W/prof/conv.err" | tr '\n' ' ')"; exit 1; }
  cp -n "$W/window.env" "$W/window.env.pilot"
  out=$(python3 -I "$PW/cal_window.py" --profile "$W/prof/s2a_profile_pre.csv" --mcycle "$M" --n 100000 \
        --write "$W/window.env" --r "$R" --maxc "$MAXC" 2>&1); rc=$?
  echo "PREWIN $(date '+%F %T') $id cal_window rc=$rc $out"
  [ $rc = 0 ] || { echo "PREWIN $id WINDOW FAIL (window.env.pilot kept)"; exit 1; }
  echo "PREWIN $(date '+%F %T') $id window.env: $(tr '\n' ' ' < "$W/window.env")"
done
echo "PREWIN $(date '+%F %T') ALL DONE"
