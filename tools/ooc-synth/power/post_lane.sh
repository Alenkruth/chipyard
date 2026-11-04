#!/usr/bin/env bash
# post_lane.sh -- power calibration, per config after its S2: C1 (SAIF with array elements), fill table, V (Vivado
#                 activity power + coverage), and the cal_fit.py input layout.  Plan power-calibration-plan.md section 3.
#
# Usage : post_lane.sh ID [ID ...]   (cal_cfgs.env ids); launch detached:  setsid nohup bash post_lane.sh ... > LOG 2>&1 < /dev/null &
# Root: BASE=${CAL_W_BASE:-/data/akrish/scratch/power-cal} (env CAL_W_BASE overrides); every path below derives from it.
# Per id, W=$BASE/<id>:
#   wait   until $W/logs/S2.rc exists (sim_lane.sh S2 finished) and $W/window.env has A, N
#   C1     $W/bin/fst2saif (built by pilot.sh C0 from the tree source) --arrays over the window [A, A+N) -> $W/s2a.saif;
#          profile plateau line printed (same check as pilot.sh C1).  With --trace-max-array 256 the register files are in
#          the dump, so --mem-from-ports is not used (it was the pilot's workaround).
#   FILL   saif_fill_regs.py -> $W/s2a_fill.tcl (renamed/replicated registers get their RTL register's activity)
#   V      Vivado saif_match_diag.tcl on the P2 checkpoint (DIAG_FILL, DIAG_POWER=1 -> power_saif.rpt depth 3,
#          DIAG_ACTIVITY=1 -> source tags), 8 threads, cwd $W/v (never the repo).  Launched only when at most one simulator runs
#          (one sim <= 22 threads + V 9 <= 40; controller 2026-11-03 12:1x).
#   COVER  saif_match_offline.py --act -> $W/v/offline_match.json; FF coverage gate >= 90% (plan 1.5) printed PASS/FAIL
#   LAYOUT $W/vectorless.rpt (= P2 point power_hier.rpt, same netlist), $W/power_dhry.rpt (= v/power_saif.rpt),
#          $W/util_hier.rpt (= P2 point), $W/cg_inputs.json (already built by build_cg_inputs.py)
# Output: "POSTLANE <time> ..." milestone lines; exit 0 when all ids are done, 1 at the first failure.  Nothing is deleted.
set -u
HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
BASE=${CAL_W_BASE:-/data/akrish/scratch/power-cal}
STRIP=TOP/TestDriver/testHarness/chiptop0/system/tile_prci_domain/element_reset_domain_boom_tile
VIVADO=/tools/Xilinx/Vivado/2023.1/bin/vivado
say() { echo "POSTLANE $(date '+%F %T') $*"; }
n_sims() { pgrep -f '^\S*(simulator-chipyard|VFireSim)' | wc -l; }   # RULES B: <= 2 sims, <= 40 threads; a sim is <= 22 threads, V is 9
for id in "$@"; do
  W=$BASE/$id; PT=$(ls -d "$W"/ooc/points/Tile/base/*/ | head -1)
  while [ ! -f "$W/logs/S2.rc" ]; do sleep 60; done
  # shellcheck disable=SC1091
  . "$W/window.env"; say "$id S2 done; window A=$A N=$N"
  [ -x "$W/bin/fst2saif" ] || { say "$id FAIL no $W/bin/fst2saif"; exit 1; }
  /usr/bin/time -v -o "$W/logs/C1a.time" "$W/bin/fst2saif" --fst "$W/s2.fst" --scope "$STRIP" --clock clock --from-cycle "$A" --cycles "$N" \
     --out-period-ns 10 --arrays --out "$W/s2a.saif" --profile "$W/s2a_profile.csv" > "$W/s2a.conv.out" 2> "$W/logs/C1a.err" \
     || { say "$id C1 FAIL $(tail -2 "$W/logs/C1a.err" | tr '\n' ' ')"; exit 1; }
  say "$id C1 ok $(grep -E '^(bits|arrays_emitted|total_tc)=' "$W/s2a.conv.out" | tr '\n' ' ') $(python3 -I - "$W/s2a_profile.csv" "$A" "$N" <<'PY'
import csv,sys
rows=list(csv.DictReader(open(sys.argv[1]))); a=int(sys.argv[2]); n=int(sys.argv[3])
w=[int(r['toggles']) for r in rows if a<=int(r['first_cycle'])<a+n]; m=sum(w)/len(w)
print('profile buckets=%d mean=%.0f max_dev=%.1f%%'%(len(w),m,100*max(abs(x-m) for x in w)/m))
PY
)"
  python3 -I "$HERE/saif_fill_regs.py" --saif "$W/s2a.saif" --strip "$STRIP" --out "$W/s2a_fill.tcl" > "$W/logs/fill.out" 2>&1 \
     || { say "$id FILL FAIL $(tail -1 "$W/logs/fill.out")"; exit 1; }
  waited=0; while [ "$(n_sims)" -gt 1 ]; do [ $waited = 0 ] && say "$id V waiting: 2 simulators run (V 9 + 2 sims would exceed 40 threads)"; waited=1; sleep 30; done
  mkdir -p "$W/v"; say "$id V started"
  ( cd "$W/v" && DIAG_DCP="$PT/post_synth.dcp" DIAG_SAIF="$W/s2a.saif" DIAG_STRIP="$STRIP" DIAG_OUT="$W/v" DIAG_FILL="$W/s2a_fill.tcl" \
      DIAG_POWER=1 DIAG_ACTIVITY=1 OOC_MAX_THREADS=8 "$VIVADO" -mode batch -nojournal -nolog -source "$HERE/saif_match_diag.tcl" > "$W/v/vivado.log" 2>&1 )
  vrc=$?; say "$id V end rc=$vrc $(grep -E 'SAIF_FILL' "$W/v/vivado.log" | head -2 | tr '\n' ' ')"
  [ $vrc = 0 ] && [ -s "$W/v/power_saif.rpt" ] || { say "$id V FAIL (see $W/v/vivado.log)"; exit 1; }
  python3 -I "$HERE/saif_match_offline.py" --saif "$W/s2a.saif" --strip "$STRIP" --diag "$W/v" --act "$W/v" > "$W/v/offline.out" 2>&1 \
     || { say "$id COVER FAIL $(tail -1 "$W/v/offline.out")"; exit 1; }
  cp "$PT/power_hier.rpt" "$W/vectorless.rpt"; cp "$W/v/power_saif.rpt" "$W/power_dhry.rpt"; cp "$PT/util_hier.rpt" "$W/util_hier.rpt"
  say "$id done; dynamic vectorless $(grep -m1 'Dynamic (W)' "$W/vectorless.rpt" | awk -F'|' '{print $3}') activity $(grep -m1 'Dynamic (W)' "$W/power_dhry.rpt" | awk -F'|' '{print $3}'); coverage: $(grep -iE 'FD|FF' "$W/v/offline.out" | tail -3 | tr '\n' ' ')"
done
say "ALL DONE: $*"
