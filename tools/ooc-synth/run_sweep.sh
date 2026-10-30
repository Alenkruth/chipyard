#!/usr/bin/env bash
# Usage: run_sweep.sh [manifest.csv]   (default $OOC_WORK/manifest.csv)
# manifest columns (agent C): point,status (point quoted if it has commas); extra columns ignored. Runs points whose
# $OOC_WORK/points/<point>/ has no complete result.json, at most OOC_MAX_JOBS at once.
# Resumable: rerun at any time. Launch detached: setsid nohup run_sweep.sh < /dev/null > log 2>&1 &
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$HERE/ooc.env" ] && . "$HERE/ooc.env"
: "${OOC_WORK:?OOC_WORK not set (ooc.env)}"
MAX="${OOC_MAX_JOBS:-4}"; [ "$MAX" -gt 4 ] && { echo "OOC_MAX_JOBS capped to 4 (owner rule)" >&2; MAX=4; }
MAN="${1:-$OOC_WORK/manifest.csv}"
[ -f "$MAN" ] || { echo "no manifest: $MAN" >&2; exit 2; }

complete() { python3 -I -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("status")=="ok" and "total" in json.load(open(sys.argv[1])) else 1)' "$1" 2>/dev/null; }

# Parsed with csv (point ids contain commas, so C must quote them). Loop runs in the main shell so wait works.
while IFS= read -r pt; do
  [ -n "$pt" ] || continue
  d="$OOC_WORK/points/$pt"
  [ -f "$d/result.json" ] && complete "$d/result.json" && { echo "skip (done) $pt"; continue; }
  [ -f "$d/rtl/top.txt" ] || { echo "skip (not elaborated) $pt"; continue; }
  while [ "$(jobs -rp | wc -l)" -ge "$MAX" ]; do sleep 5; done
  echo "$(date +%T) start $pt"
  ( "$HERE/run_point.sh" "$d" > "$d/run.out" 2>&1; echo "$(date +%T) end $pt rc=$?" ) &
done < <(python3 -I -c 'import csv,sys
for r in csv.DictReader(open(sys.argv[1], newline="")): print(r["point"])' "$MAN")
wait
