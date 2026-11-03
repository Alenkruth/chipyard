#!/usr/bin/env bash
# OOC gap-closing runs (owner 2026-10-31: close gaps 1/2/3 + power at widths 4/2/1). Plan:
#   claude-artifacts/ooc-synthesis/plans/gaps-20261031.md
# usage: gaps_lanes.sh elab        elaborate every Tile point (4 in parallel) + copy RegfileFp sweep RTL; needs the tree unchanged
#        gaps_lanes.sh lane <1-4>  synthesis only (run_point.sh) from cached RTL, sequential, per-segment env
# Each lane is launched as its own systemd --user unit (see the plan).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
G=/data/akrish/scratch/ooc-gaps
export CHIPYARD_ROOT=/home/jht9sy/work/chipyard FIRTOOL_BIN=/home/jht9sy/work/chipyard/.conda-env/riscv-tools/bin/firtool
export VIVADO_BIN=/tools/Xilinx/Vivado/2023.1/bin/vivado XILINX_PART=xcu250-figd2104-2L-e OOC_CLOCK_NS=10
export OOC_STATIC_FIX=off OOC_UTIL_DEPTH=8
unset OOC_ALLOW_STALE_JAR OOC_POWER OOC_PRIMS OOC_FLATTEN
K=rob=256,fb=64,ftq=32,ras=32,iprf=192,fprf=96,iq_int=32,iq_mem=32,iq_fp=32,ldq=40,stq=40,btb_sets=128,btb_ways=2,tage=7,ic_sets=64,ic_ways=8,dc_sets=128,dc_ways=8   # full size (points.py writes it out)
F4=$K,width=4
MIN4=rob=32,fb=16,ftq=8,ras=4,iprf=48,fprf=40,iq_int=8,iq_mem=8,iq_fp=8,ldq=8,stq=8,btb_sets=32,btb_ways=1,tage=1,ic_sets=8,ic_ways=1,dc_sets=16,dc_ways=1,width=4
# point table: "<work> <variant> <params>"
points() {
  while read -r v p; do [ -n "${v:-}" ] && echo "design $v $p"; done < "$G/design-list.txt"
  echo "flat base $MIN4"; echo "flat base $F4"
  for x in base reconf_base ift_nobridge ift reconf_ift; do echo "power $x $K,width=4"; done
  for w in 2 1; do echo "power base $K,width=$w"; echo "power ift_nobridge $K,width=$w"
                   echo "power base $K,width=$w,width_mode=dispatch_only"; echo "power ift $K,width=$w,width_mode=dispatch_only"; done
}
# lane membership: design index (1-based line of design-list.txt) or named points
lane_list() {
  case "$1" in
    1) for i in 7 8 9 10 11 12; do echo "design #$i"; done; echo "power ift $K,width=4"; echo "power base $K,width=1,width_mode=dispatch_only" ;;
    2) for i in 1 2 3 4 5 6 13; do echo "design #$i"; done; echo "power reconf_ift $K,width=4"; echo "power base $K,width=2"; echo "power ift_nobridge $K,width=1" ;;
    3) for i in 14 15 16; do echo "design #$i"; done; echo "prims RegfileFp pregs=40,width=4"; echo "prims RegfileFp pregs=48,width=4"
       echo "flat base $MIN4"; echo "power ift_nobridge $K,width=4"; echo "power base $K,width=4"; echo "power ift $K,width=2,width_mode=dispatch_only"; echo "power base $K,width=2,width_mode=dispatch_only" ;;
    4) echo "flat base $F4"; echo "power reconf_base $K,width=4"; echo "power ift $K,width=1,width_mode=dispatch_only"; echo "power ift_nobridge $K,width=2"; echo "power base $K,width=1" ;;
  esac
}
case "${1:-}" in
  elab)
    mkdir -p "$G"/{design,flat,power,prims}/logs
    # gate check once, in the foreground: fails if any Scala source is newer than the generator jar
    OOC_WORK="$G/design" "$HERE/scala/build.sh" >/dev/null || { echo "ELAB GATE FAILED"; exit 1; }
    for m in 40 48; do s=/data/akrish/scratch/ooc-sweep/points/RegfileFp/base/pregs=$m,width=4; d="$G/prims/points/RegfileFp/base/pregs=$m,width=4"
      mkdir -p "$d"; cp -a "$s/rtl" "$d/"; done
    points | xargs -P4 -L1 bash -c 'OOC_WORK='"$G"'/$0 '"$HERE"'/elaborate.sh Tile "$1" "$2" > '"$G"'/$0/logs/el_$1_${2//[=,]/_}.log 2>&1 && echo "ELAB OK $0 $1 $2" || echo "ELAB FAILED $0 $1 $2"'
    n=$(points | wc -l); ok=0
    while read -r wk v p; do [ -s "$G/$wk/points/Tile/$v/$p/rtl/filelist.f" ] && ok=$((ok+1)); done < <(points)
    echo "[$(date '+%F %T')] ELAB SUMMARY ok=$ok of $n"; [ "$ok" = "$n" ] ;;
  lane)
    rc=0
    while read -r wk a b; do
      if [ "$wk" = design ]; then p=$(sed -n "${a#\#}p" "$G/design-list.txt" | cut -d' ' -f2); PT="$G/design/points/Tile/base/$p"
      elif [ "$wk" = prims ]; then PT="$G/prims/points/$a/base/$b"
      else PT="$G/$wk/points/Tile/$a/$b"; fi
      env=(); case "$wk" in power) env=(OOC_POWER=1);; flat) env=(OOC_FLATTEN=none);; prims) env=(OOC_PRIMS=1 OOC_POWER=1 OOC_UTIL_DEPTH=2);; esac
      echo "[$(date '+%F %T')] START $wk ${PT#$G/}"
      if env "${env[@]}" "$HERE/run_point.sh" "$PT" > "$PT.syn.log" 2>&1; then echo "[$(date '+%F %T')] DONE $wk ${PT#$G/}"
      else echo "[$(date '+%F %T')] FAILED $wk ${PT#$G/}"; rc=1; fi
    done < <(lane_list "$2")
    echo "[$(date '+%F %T')] LANE $2 END rc=$rc"; exit $rc ;;
  *) sed -n 2,6p "$0"; exit 2 ;;
esac
