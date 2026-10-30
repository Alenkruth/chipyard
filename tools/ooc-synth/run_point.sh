#!/usr/bin/env bash
# Usage: run_point.sh <point-dir>
# Synthesizes one OOC point (rtl/ prepared by elaborate.sh) and writes result.json.
# Exit 0 only if synthesis finished and result.json is complete. Safe to launch detached:
#   setsid nohup tools/ooc-synth/run_point.sh <dir> < /dev/null > /dev/null 2>&1 &
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$HERE/ooc.env" ] && . "$HERE/ooc.env"
VIVADO_BIN="${VIVADO_BIN:-vivado}"
XILINX_PART="${XILINX_PART:-xcu250-figd2104-2L-e}"
OOC_CLOCK_NS="${OOC_CLOCK_NS:-10.0}"
export XILINX_PART OOC_CLOCK_NS

[ $# -eq 1 ] && [ -d "$1" ] || { echo "usage: $0 <point-dir>" >&2; exit 2; }
PT="$(cd "$1" && pwd)"
TCL="$HERE/synth/ooc_synth.tcl"
PARSER="$HERE/synth/parse_util.py"
RTL="$PT/rtl"

# Per-point lock: a second copy exits 3 without touching anything.
exec 9>"$PT/.run.lock"
flock -n 9 || { echo "point locked by another run: $PT" >&2; exit 3; }

case "$PT" in */points/*) POINT="${PT#*/points/}";; *) POINT="$(basename "$(dirname "$(dirname "$PT")")")/$(basename "$(dirname "$PT")")/$(basename "$PT")";; esac

rm -f "$PT/result.json" "$PT/util_hier.rpt" "$PT/.time.txt"
fail() {  # write a failed result.json and exit nonzero
  python3 -I - "$PT/result.json" "$POINT" "$1" <<'PY'
import json,sys
p,pt,why=sys.argv[1:4]; parts=pt.split("/")+["",""]
json.dump({"point":pt,"module":parts[0],"variant":parts[1],"params":parts[2],"status":"failed","reason":why},open(p,"w"),indent=1)
PY
  echo "FAILED: $1" >&2; exit 1
}

[ -f "$RTL/filelist.f" ] && [ -f "$RTL/top.txt" ] || fail "rtl/filelist.f or rtl/top.txt missing"
TOP="$(tr -d ' \t\r\n' < "$RTL/top.txt")"
[ -n "$TOP" ] || fail "empty top.txt"
command -v "$VIVADO_BIN" >/dev/null 2>&1 || [ -x "$VIVADO_BIN" ] || fail "VIVADO_BIN not found: $VIVADO_BIN"

export OOC_POINT_DIR="$PT" OOC_TOP="$TOP" OOC_FILELIST="$RTL/filelist.f"
T0=$(date +%s)
( cd "$PT" && /usr/bin/time -v -o "$PT/.time.txt" "$VIVADO_BIN" -mode batch -nojournal -nolog \
    -source "$TCL" > "$PT/synth.log" 2>&1 )
RC=$?
WALL=$(( $(date +%s) - T0 ))
PEAK_KB=$(sed -n 's/.*Maximum resident set size (kbytes): *\([0-9]*\).*/\1/p' "$PT/.time.txt" 2>/dev/null | head -1)
PEAK_MB=$(( ${PEAK_KB:-0} / 1024 ))
[ $RC -eq 0 ] || fail "vivado exit $RC (see synth.log)"
grep -q '^OOC_SYNTH_DONE' "$PT/synth.log" || fail "synthesis did not complete"
[ -s "$PT/util_hier.rpt" ] || fail "util_hier.rpt missing"

VER="$(sed -n 's/.*Vivado v\([0-9][0-9.]*\).*/\1/p' "$PT/synth.log" | head -1)"
SHA="$(sha1sum "$TCL" | cut -d' ' -f1)"
python3 -I "$PARSER" --rpt "$PT/util_hier.rpt" --out "$PT/result.json" --point "$POINT" \
  --meta "top=$TOP" --meta "wall_s=$WALL" --meta "peak_mb=$PEAK_MB" --meta "vivado_version=${VER:-unknown}" \
  --meta "part=$XILINX_PART" --meta "clock_ns=$OOC_CLOCK_NS" --meta "tcl_sha1=$SHA" \
  || fail "result parse failed"
echo "OK $POINT wall_s=$WALL peak_mb=$PEAK_MB"
