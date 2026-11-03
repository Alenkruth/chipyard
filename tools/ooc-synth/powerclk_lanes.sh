#!/usr/bin/env bash
# Clocked power reruns (owner 2026-10-31 ~22:4x: "Fix + rerun 13 variants"). Every earlier OOC run had no clock
# (XDC 'if' rejected); ooc_synth.tcl now writes a plain create_clock. RTL is copied from the elaborated points of
# /data/akrish/scratch/ooc-gaps/power (no elaboration, no reads of the shared tree).
# usage: powerclk_lanes.sh sanity     one small clocked point (RegfileFp 40, width 4); exit 0 only if the clock is applied
#        powerclk_lanes.sh lane A|B   the 13 variant tiles, split in two lanes, synthesis + report_power
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
S=/data/akrish/scratch/ooc-gaps/power/points/Tile; G=/data/akrish/scratch/ooc-gaps/power-clk
export VIVADO_BIN=/tools/Xilinx/Vivado/2023.1/bin/vivado XILINX_PART=xcu250-figd2104-2L-e OOC_CLOCK_NS=10 OOC_UTIL_DEPTH=8 OOC_POWER=1
unset OOC_LEGACY_NOCLOCK OOC_PRIMS OOC_FLATTEN
K=rob=256,fb=64,ftq=32,ras=32,iprf=192,fprf=96,iq_int=32,iq_mem=32,iq_fp=32,ldq=40,stq=40,btb_sets=128,btb_ways=2,tage=7,ic_sets=64,ic_ways=8,dc_sets=128,dc_ways=8
clock_ok() { grep -q 'ooc_clk' "$1/power_hier.rpt" 2>/dev/null && ! grep -q 'Designutils 20-1307' "$1/synth.log"; }
lane_list() {
  case "$1" in
    A) printf '%s\n' "ift $K,width=4" "base $K,width=4" "ift $K,width=2,width_mode=dispatch_only" "base $K,width=1,width_mode=dispatch_only" "ift_nobridge $K,width=1" "base $K,width=1" ;;
    B) printf '%s\n' "reconf_ift $K,width=4" "ift_nobridge $K,width=4" "reconf_base $K,width=4" "base $K,width=2" "ift_nobridge $K,width=2" "base $K,width=2,width_mode=dispatch_only" "ift $K,width=1,width_mode=dispatch_only" ;;
  esac
}
case "${1:-}" in
  sanity)
    PT="$G/sanity/RegfileFp/base/pregs=40,width=4"; mkdir -p "$PT"; cp -a /data/akrish/scratch/ooc-gaps/prims/points/RegfileFp/base/pregs=40,width=4/rtl "$PT/"
    OOC_UTIL_DEPTH=2 "$HERE/run_point.sh" "$PT" > "$PT.syn.log" 2>&1 || { echo "[$(date '+%F %T')] SANITY SYNTH FAILED"; exit 1; }
    if clock_ok "$PT"; then echo "[$(date '+%F %T')] SANITY OK: clock applied; $(grep -m1 'Total On-Chip Power' "$PT/power_hier.rpt")"; else echo "[$(date '+%F %T')] SANITY FAILED: no clock in power report"; exit 1; fi ;;
  lane)
    rc=0
    while read -r v p; do
      PT="$G/points/Tile/$v/$p"; mkdir -p "$PT"; [ -d "$PT/rtl" ] || cp -a "$S/$v/$p/rtl" "$PT/"
      echo "[$(date '+%F %T')] START $v $p"
      if "$HERE/run_point.sh" "$PT" > "$PT.syn.log" 2>&1 && clock_ok "$PT"; then echo "[$(date '+%F %T')] DONE $v $p $(grep -m1 'Total On-Chip Power' "$PT/power_hier.rpt" | tr -s ' ')"
      else echo "[$(date '+%F %T')] FAILED $v $p"; rc=1; fi
    done < <(lane_list "$2")
    echo "[$(date '+%F %T')] LANE $2 END rc=$rc"; exit $rc ;;
  *) sed -n 2,6p "$0"; exit 2 ;;
esac
