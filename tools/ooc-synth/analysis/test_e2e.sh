#!/bin/sh
# End-to-end test on fixtures; usage: test_e2e.sh [TMPDIR]. Checks byte-identical reruns.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
T=${1:-$(mktemp -d)}
rm -rf "$T/work" "$T/out1" "$T/out2"; mkdir -p "$T/work"
python3 -I "$HERE/make_fixtures.py" "$T/work"
python3 -I "$HERE/manifest.py" gen --out "$T/manifest.csv" > "$T/manifest.txt"
for n in 1 2; do
  python3 -I "$HERE/aggregate.py" --work "$T/work" --out "$T/out$n" --manifest "$T/manifest.csv"
  python3 -I "$HERE/report.py" --dir "$T/out$n" --manifest "$T/manifest.csv" --out "$T/out$n/report.html"
done
( cd "$T/out1" && sha1sum * ) > "$T/s1"; ( cd "$T/out2" && sha1sum * ) > "$T/s2"
diff "$T/s1" "$T/s2" && echo "BYTE-IDENTICAL on rerun"; cat "$T/s1"; echo "tmp=$T"
# content checks for the ift_nobridge / width_mode paths
O="$T/out1/ift_overhead.csv"
grep -q '^tap_bridge,ICache,"sets=64,ways=8",ift-ift_nobridge' "$O" || { echo "FAIL: no tap_bridge row"; exit 1; }
grep -q '^delta,ICache,"sets=64,ways=8",ift_nobridge-base' "$O" || { echo "FAIL: no pinned-module max delta row"; exit 1; }
grep -q '^scaling_delta,ICache,"sets=32,ways=4",ift_nobridge-base' "$O" || { echo "FAIL: no spot-check scaling row"; exit 1; }
grep -q '^delta,Rob,"entries=256,width=4",ift-base' "$O" || { echo "FAIL: no ift-base delta row at max"; exit 1; }
grep -q '^delta,Rob,"entries=256,width=2",ift_nobridge-base' "$O" || { echo "FAIL: no width-2 nobridge delta row"; exit 1; }
grep -Eq 'Rob,"entries=(128|32),' "$O" && { echo "FAIL: needs_clamp_fix point leaked into derived tables"; exit 1; }
grep -q 'residual,base,2,dispatch_only' "$T/out1/core_vs_sum.csv" || { echo "FAIL: no dispatch_only residual"; exit 1; }
grep -q 'residual,base,2,full' "$T/out1/core_vs_sum.csv" || { echo "FAIL: no full residual"; exit 1; }
grep -q 'residual,ift_nobridge,2,full' "$T/out1/core_vs_sum.csv" || { echo "FAIL: no ift_nobridge full residual"; exit 1; }
grep -q ',infeasible,' "$T/manifest.csv" || { echo "FAIL: no infeasible status"; exit 1; }
grep -q ',needs_clamp_fix,' "$T/manifest.csv" || { echo "FAIL: no needs_clamp_fix status"; exit 1; }
grep -q ',needs_fix,' "$T/manifest.csv" || { echo "FAIL: no needs_fix status"; exit 1; }
grep -q 'TOTAL points' "$T/manifest.txt" || { echo "FAIL: no count table"; exit 1; }
echo "CONTENT CHECKS OK"
