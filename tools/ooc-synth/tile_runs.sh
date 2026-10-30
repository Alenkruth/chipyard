#!/usr/bin/env bash
# Whole-tile OOC runs, one after the other: elaborate (BoomTile cut from one full-config elaboration) then synthesize.
# usage: tile_runs.sh <list-file>      list lines: "<variant> <params>"   e.g.  base width=4
# Needs OOC_WORK, CHIPYARD_ROOT, FIRTOOL_BIN, VIVADO_BIN in the environment (or tools/ooc-synth/ooc.env). Stale-jar gate stays ON.
# Default OOC_STATIC_FIX=off (whole-tile points at built size need no clamp); width 1/2 ift_nobridge needs the rob.scala memWidth fix, so run it with OOC_STATIC_FIX=all (builds the private patched jar on demand).
# Runs as a systemd --user unit so a session restart does not kill it; the caller waits with notify_on_unit_done.sh.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$HERE/ooc.env" ] && . "$HERE/ooc.env"
: "${OOC_WORK:?}"; LIST="$1"
export OOC_STATIC_FIX="${OOC_STATIC_FIX:-off}" OOC_UTIL_DEPTH="${OOC_UTIL_DEPTH:-8}"
unset OOC_ALLOW_STALE_JAR
mkdir -p "$OOC_WORK/logs"
rc_all=0
while read -r variant params; do
  [ -z "${variant:-}" ] && continue
  PT="$OOC_WORK/points/Tile/$variant/$params"
  if [ -f "$PT/result.json" ] && grep -q '"status": "ok"' "$PT/result.json"; then echo "skip done: Tile/$variant/$params"; continue; fi
  echo "[$(date '+%F %T')] elaborate Tile/$variant/$params"
  "$HERE/elaborate.sh" Tile "$variant" "$params" > "$OOC_WORK/logs/tile_${variant}_${params//[=,]/_}.el.log" 2>&1 \
    || { echo "[$(date '+%F %T')] ELAB FAILED Tile/$variant/$params"; rc_all=1; continue; }
  echo "[$(date '+%F %T')] synth Tile/$variant/$params"
  "$HERE/run_point.sh" "$PT" > "$OOC_WORK/logs/tile_${variant}_${params//[=,]/_}.syn.log" 2>&1 \
    && echo "[$(date '+%F %T')] DONE Tile/$variant/$params" || { echo "[$(date '+%F %T')] SYNTH FAILED Tile/$variant/$params"; rc_all=1; }
done < "$LIST"
echo "[$(date '+%F %T')] ALL DONE rc=$rc_all"; exit $rc_all
