#!/usr/bin/env bash
# Usage:
#   elaborate.sh <module> <variant> <params>      e.g.  elaborate.sh Rob ift entries=128,width=4
#   elaborate.sh --dump-options [out.json]        CoreFuzzing.scala option lists as JSON (default $OOC_WORK/options.json)
#   elaborate.sh --modules                        module registry (instance paths, params, option lists) as JSON
#   elaborate.sh --width-map [out.json]           static width -> issue widths (BOOM presets, see OocPoint.scala WidthMap)
# Optional OOC_PATCH_CLASSES=<dir of compiled classes>: prepended to the classpath for the elaboration only,
# to test a STAGED generator diff without rebuilding the shared jar; part of the cache key and recorded in
# elab.json (patch_classes_sha1).  Unset for every production point.
# OOC_STATIC_FIX=auto|all|off|control (default auto): auto elaborates every point that is not the hardware-
# built configuration (points.py static_fix) with the private static-clamp jar (scala/patched_jar.sh; the
# staged diff OOC_STATIC_FIX_DIFF applied to a copy of the tree) and max points with the stock jar; all =
# patched jar for every point, off = stock jar for every point, control = unpatched private rebuild (proofs).
# Params: module size keys (see --modules), width=W, width_mode=full|dispatch_only (default full).
# Writes $OOC_WORK/points/<module>/<variant>/<params>/rtl/{<top>.sv,<top>_deps.sv,filelist.f,top.txt,elab.json}.
# Route: one full TestHarness elaboration per distinct config (ooc.OocPointConfig, scala/src/OocPoint.scala),
# cached in $OOC_WORK/elab/<key>/ and shared by every point with the same config; the module and its
# submodule closure are cut out by instance path (scala/extract_rtl.py).  Exit 0 only if rtl/ is complete.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$HERE/ooc.env" ] && . "$HERE/ooc.env"
CHIPYARD_ROOT="${CHIPYARD_ROOT:-$(git -C "$HERE" rev-parse --show-toplevel)}"
: "${OOC_WORK:?set OOC_WORK (tools/ooc-synth/ooc.env or environment)}"
FIRTOOL_BIN="${FIRTOOL_BIN:-firtool}"
OOC_JAVA_HEAP="${OOC_JAVA_HEAP:-16G}"
export CHIPYARD_ROOT OOC_WORK
log() { echo "[ooc-elab $(date '+%F %T')] $*" >&2; }
die() { log "ERROR: $*"; exit 1; }

CP="$("$HERE/scala/build.sh")" || die "scala build failed"
CLS="${CP##*:}"
OPTS_CACHE="$CLS/options.json"
if [ ! -s "$OPTS_CACHE" ]; then
  java -cp "$CP" ooc.DumpOptions "$OPTS_CACHE.tmp.$$" && mv "$OPTS_CACHE.tmp.$$" "$OPTS_CACHE" || die "DumpOptions failed"
fi
cmp -s "$OPTS_CACHE" "$OOC_WORK/options.json" 2>/dev/null || cp "$OPTS_CACHE" "$OOC_WORK/options.json"
WMAP_CACHE="$CLS/widthmap.json"
if [ ! -s "$WMAP_CACHE" ]; then
  java -cp "$CP" ooc.DumpWidthMap "$WMAP_CACHE.tmp.$$" && mv "$WMAP_CACHE.tmp.$$" "$WMAP_CACHE" || die "DumpWidthMap failed"
fi
cmp -s "$WMAP_CACHE" "$OOC_WORK/widthmap.json" 2>/dev/null || cp "$WMAP_CACHE" "$OOC_WORK/widthmap.json"

case "${1:-}" in
  --dump-options) out="${2:-$OOC_WORK/options.json}"; cp "$OPTS_CACHE" "$out"; echo "$out"; exit 0 ;;
  --width-map) out="${2:-$OOC_WORK/widthmap.json}"; cp "$WMAP_CACHE" "$out"; echo "$out"; exit 0 ;;
  --modules) python3 "$HERE/scala/points.py" modules --options "$OPTS_CACHE"; exit 0 ;;
  ""|-h|--help) sed -n '2,14p' "$0"; exit 2 ;;
esac
[ $# -eq 3 ] || { sed -n '2,14p' "$0"; exit 2; }

RES="$(python3 "$HERE/scala/points.py" resolve "$1" "$2" "$3" --options "$OPTS_CACHE" --widthmap "$WMAP_CACHE")" || die "bad point"
jget() { python3 -c 'import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]])' "$RES" "$1"; }
POINT="$(jget point)"; SPEC="$(jget spec)"; PATTERN="$(jget pattern)"; CPATTERN="$(jget count_pattern)"
PT="$OOC_WORK/points/$POINT"; RTL="$PT/rtl"

# ---- generator jar: stock, or the private static-clamp jar ----
SFMODE="${OOC_STATIC_FIX:-auto}"
case "$SFMODE" in auto|all|off|control) ;; *) die "OOC_STATIC_FIX must be auto|all|off|control" ;; esac
SFNEED="$(jget static_fix)"
SFAPPLIED=False
if [ "$SFMODE" = all ] || [ "$SFMODE" = control ] || { [ "$SFMODE" = auto ] && [ "$SFNEED" = True ]; }; then
  SFJAR="$("$HERE/scala/patched_jar.sh" $([ "$SFMODE" = control ] && echo --control))" || die "patched_jar.sh failed"
  CP="$(OOC_GENERATOR_JAR="$SFJAR" "$HERE/scala/build.sh")" || die "scala build against $SFJAR failed"
  CLS="${CP##*:}"
  SFAPPLIED=True
  log "static fix ($SFMODE): elaborating with $(basename "$SFJAR")"
fi

# ---- elaboration, cached per (config spec, compiled classes) ----
PATCH_SHA=""; ELAB_CP="$CP"
if [ -n "${OOC_PATCH_CLASSES:-}" ]; then
  [ -d "$OOC_PATCH_CLASSES" ] || die "OOC_PATCH_CLASSES is not a directory: $OOC_PATCH_CLASSES"
  PATCH_SHA="$(cd "$OOC_PATCH_CLASSES" && find . -type f -name '*.class' | sort | xargs sha1sum | sha1sum | cut -c1-40)"
  ELAB_CP="$OOC_PATCH_CLASSES:$CP"
  log "PATCHED elaboration: $OOC_PATCH_CLASSES (sha1 $PATCH_SHA) shadows the jar"
fi
KEY="$(printf '%s|%s%s' "$SPEC" "$(basename "$CLS")" "${PATCH_SHA:+|$PATCH_SHA}" | sha1sum | cut -c1-16)"
ED="$OOC_WORK/elab/$KEY"; mkdir -p "$ED"
exec 8>"$ED/.lock"
log "point $POINT  spec '$SPEC'  elab $ED"
flock 8
if [ ! -f "$ED/.ok" ]; then
  rm -f "$ED/ooc.fir" "$ED/ooc.sv" "$ED/meta.json"
  t0=$(date +%s.%N)
  ( cd "$CHIPYARD_ROOT" && java -Xmx"$OOC_JAVA_HEAP" -Xss8M -Djava.io.tmpdir="$ED" -Dooc.spec="$SPEC" -cp "$ELAB_CP" \
      chipyard.Generator --target-dir "$ED" --name ooc --top-module chipyard.harness.TestHarness \
      --legacy-configs ooc:OocPointConfig ) > "$ED/chisel.log" 2>&1 \
    || { grep -m3 -E 'requirement failed|Exception|Error' "$ED/chisel.log" >&2 || true; die "chisel elaboration failed: $ED/chisel.log"; }
  t1=$(date +%s.%N)
  # chipyard's firtool options (common.mk) minus --split-verilog / --repl-seq-mem: memories stay
  # behavioural (*_ext modules) so Vivado infers BRAM/LUTRAM as in the FireSim flow.
  "$FIRTOOL_BIN" --format=fir --warn-on-unprocessed-annotations --disable-annotation-classless \
      --disable-annotation-unknown \
      --lowering-options=emittedLineLength=2048,noAlwaysComb,disallowLocalVariables,verifLabels,disallowPortDeclSharing,locationInfoStyle=wrapInAtSquareBracket \
      --annotation-file="$ED/ooc.anno.json" -o "$ED/ooc.sv" "$ED/ooc.fir" > "$ED/firtool.log" 2>&1 \
    || die "firtool failed: $ED/firtool.log"
  t2=$(date +%s.%N)
  FIRSHA="$(sha1sum "$ED/ooc.fir" | cut -c1-40)"
  [ -n "${OOC_KEEP_FIR:-}" ] || rm -f "$ED/ooc.fir"
  # provenance of the tree (the jar snapshot is what was elaborated; see build.sh)
  gitinfo() { local d="$1"; printf '{"head":"%s","dirty":%s,"diff_sha1":"%s"}' \
      "$(git -C "$d" rev-parse HEAD)" \
      "$( [ -z "$(git -C "$d" status --porcelain --untracked-files=no)" ] && echo false || echo true)" \
      "$(git -C "$d" diff HEAD | sha1sum | cut -c1-40)"; }
  python3 - "$ED/meta.json" <<EOF
import json, sys
m = {"spec": "$SPEC", "key": "$KEY", "classpath_classes": "$(basename "$CLS")",
     "jar": "$(basename "${CP%%:*}")", "jar_meta": open("${CP%%:*}.meta").read().strip() if __import__('os').path.exists("${CP%%:*}.meta") else "",
     "patch_classes_sha1": "$PATCH_SHA", "chisel_s": round($t1 - $t0, 1), "firtool_s": round($t2 - $t1, 1), "fir_sha1": "$FIRSHA",
     "sv_sha1": "$(sha1sum "$ED/ooc.sv" | cut -c1-40)",
     "firtool_version": "$("$FIRTOOL_BIN" --version | grep -o 'firtool-[0-9.]*' | head -1)",
     "git": {"chipyard": json.loads('$(gitinfo "$CHIPYARD_ROOT")'),
             "boom": json.loads('$(gitinfo "$CHIPYARD_ROOT/generators/boom")'),
             "rocket-chip": json.loads('$(gitinfo "$CHIPYARD_ROOT/generators/rocket-chip")')}}
json.dump(m, open(sys.argv[1], "w"), indent=1, sort_keys=True)
EOF
  touch "$ED/.ok"
  log "elaborated in $(python3 -c "print(round($t2-$t0,1))") s (chisel $(python3 -c "print(round($t1-$t0,1))") s, firtool $(python3 -c "print(round($t2-$t1,1))") s)"
else
  log "reusing cached elaboration $ED"
fi
flock -u 8

# ---- extraction into the point dir ----
mkdir -p "$RTL"
for f in "$RTL"/*; do [ -e "$f" ] && rm -f "$f"; done
python3 "$HERE/scala/extract_rtl.py" emit "$ED/ooc.sv" "$PATTERN" "$RTL" "$CPATTERN" > /dev/null || die "extraction failed for '$PATTERN'"
OOC_SF_APPLIED="$SFAPPLIED" python3 - "$RTL" "$ED/meta.json" "$RES" "${CP%%:*}.check" <<'EOF'
import json, sys, os
rtl, meta, res = sys.argv[1], json.load(open(sys.argv[2])), json.loads(sys.argv[3])
ex = json.load(open(os.path.join(rtl, "extract.json")))
os.remove(os.path.join(rtl, "extract.json"))
measured = len(ex["copies"])
if measured != res["instances_per_tile"]:
    for f in os.listdir(rtl):
        os.remove(os.path.join(rtl, f))
    raise SystemExit("instances_per_tile: registry says %d, the design has %d copies (%s); rtl/ cleared"
                     % (res["instances_per_tile"], measured, ex["copies"]))
jar_check = open(sys.argv[4]).read().strip() if os.path.exists(sys.argv[4]) else ""
e = {"point": res["point"], "module": res["module"], "variant": res["variant"], "params": res["params"],
     "width": res["width"], "top": ex["top"], "instance_path": ex["instance_path"],
     "instances_per_tile": measured, "instance_copies": ex["copies"], "width_mode": res["width_mode"],
     "issue_width": res["issue_width"], "extraction_note": res["extraction_note"], "jar_check": jar_check,
     "n_modules": ex["n_modules"], "rtl_lines": ex["rtl_lines"],
     "rtl_sha1": ex["rtl_sha1"], "files": ex["files"], "spec": res["spec"], "elab_key": meta["key"],
     "elaboration_seconds": round(meta["chisel_s"] + meta["firtool_s"], 1), "chisel_s": meta["chisel_s"],
     "firtool_s": meta["firtool_s"], "firtool_version": meta["firtool_version"], "git": meta["git"],
     "jar": meta["jar"], "jar_meta": meta["jar_meta"], "patch_classes_sha1": meta.get("patch_classes_sha1", ""), "fir_sha1": meta["fir_sha1"],
     "elab_kind": "fullconfig-extract",
     "static_fix": {"mode": os.environ.get("OOC_STATIC_FIX", "auto"), "needed": res["static_fix"],
                    "applied": os.environ.get("OOC_SF_APPLIED") == "True",
                    "jar_meta": meta["jar_meta"] if os.environ.get("OOC_SF_APPLIED") == "True" else ""}}
json.dump(e, open(os.path.join(rtl, "elab.json"), "w"), indent=1, sort_keys=True)
open(os.path.join(rtl, "elab.json"), "a").write("\n")
print("%s  top=%s  modules=%d  lines=%d  rtl_sha1=%s" % (e["point"], e["top"], e["n_modules"], e["rtl_lines"], e["rtl_sha1"][:12]))
EOF
