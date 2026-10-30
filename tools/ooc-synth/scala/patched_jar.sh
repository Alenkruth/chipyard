#!/usr/bin/env bash
# Builds a PRIVATE generator jar with a staged BOOM v3 diff applied (the static-size clamp fix), for
# the OOC elaborations that need it.  The real tree and the shared .classpath_cache jar are only READ.
#
#   patched_jar.sh [--control]      prints one line: the path of the patched (or control) jar
#
# Inputs (ooc.env or environment):
#   OOC_WORK              all outputs (required)
#   OOC_STATIC_FIX_DIFF   the staged diff, paths a/src/main/scala/v3/... (relative to generators/boom)
#                         default: $CHIPYARD_ROOT/claude-artifacts/ooc-synthesis/staged/boom-ooc-static-fixes-v2.diff
#   OOC_GENERATOR_JAR     the live generator jar (default $CHIPYARD_ROOT/.classpath_cache/chipyard.jar)
# Route (all under $OOC_WORK/patched/<key>/, key = sha1 of base jar + v3 sources + diff + compiler):
#   1. scala/build.sh snapshots the live jar and runs its stale-jar gate (fails if the tree's Scala
#      sources are newer than the live jar: the jar must hold exactly the tree's RTL).
#   2. src/  = a copy of the tree's generators/boom/src/main/scala/v3 (working tree, as the jar was built);
#      the diff is applied to it with patch -F0 (no fuzz; any reject fails).  --control skips the diff.
#   3. scalac (scala/chisel versions read from build.sbt, chisel plugin, build.sbt's scalacOptions)
#      compiles all of v3 against the snapshot jar.
#   4. jar = snapshot with every boom/v3/ entry replaced by the new classes.  The class-name sets of the
#      old and new boom/v3 must be identical (checked; else fail): nothing stale can survive.
#   The jar lands in $OOC_WORK/cache/jars/chipyard-<sha1>.jar (where build.sh keeps snapshots), with a
#   .meta provenance line, so `OOC_GENERATOR_JAR=<it> scala/build.sh` uses it without another copy.
# Package boom.v3 is compiled only from generators/boom (build.sbt: lazy val boom); v4 and every
# downstream project are untouched; the diff adds no public signature used outside boom.v3.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OOC_DIR="$(dirname "$HERE")"
[ -f "$OOC_DIR/ooc.env" ] && . "$OOC_DIR/ooc.env"
CHIPYARD_ROOT="${CHIPYARD_ROOT:-$(git -C "$OOC_DIR" rev-parse --show-toplevel)}"
: "${OOC_WORK:?set OOC_WORK (ooc.env or environment)}"
COURSIER_CACHE="${COURSIER_CACHE:-$HOME/.cache/coursier}"
DIFF="${OOC_STATIC_FIX_DIFF:-$CHIPYARD_ROOT/claude-artifacts/ooc-synthesis/staged/boom-ooc-static-fixes-v2.diff}"
V3="$CHIPYARD_ROOT/generators/boom/src/main/scala/v3"
log() { echo "[ooc-patch] $*" >&2; }
die() { log "ERROR: $*"; exit 1; }
CONTROL=0; [ "${1:-}" = "--control" ] && CONTROL=1
[ -f "$DIFF" ] || die "staged diff not found: $DIFF"
[ -d "$V3" ] || die "BOOM v3 sources not found: $V3"

# 1. live jar snapshot + stale gate (build.sh; OOC_ALLOW_STALE_JAR=1 bypasses it exactly as for the
#    stock jar, and the bypass is recorded in the patched jar's .meta and in every elab.json jar_check)
CP="$("$HERE/build.sh")" || die "build.sh failed (stale or missing generator jar)"
SNAP="${CP%%:*}"
JSHA="$(basename "$SNAP" .jar)"; JSHA="${JSHA#chipyard-}"

SCALA_V="$(sed -n 's/^val scalaVersionFromChisel *= *"\([^"]*\)".*/\1/p' "$CHIPYARD_ROOT/build.sbt" | head -1)"
CHISEL_V="$(sed -n 's/^val chisel6Version *= *"\([^"]*\)".*/\1/p' "$CHIPYARD_ROOT/build.sbt" | head -1)"
[ -n "$SCALA_V" ] && [ -n "$CHISEL_V" ] || die "cannot read scala/chisel versions from build.sbt"
findjar() { find "$COURSIER_CACHE" -name "$1" -not -name '*-sources.jar' 2>/dev/null | head -1; }
SC="$(findjar "scala-compiler-$SCALA_V.jar")"; SR="$(findjar "scala-reflect-$SCALA_V.jar")"
PLUGIN="$(findjar "chisel-plugin_$SCALA_V-$CHISEL_V.jar")"
for j in SC SR PLUGIN; do [ -n "${!j}" ] || die "missing $j jar in $COURSIER_CACHE"; done
SCALAC_OPTS="-deprecation -unchecked -Ytasty-reader -Ymacro-annotations"   # build.sbt commonSettings

SRCSHA="$(cd "$V3" && find . -type f -name '*.scala' | LC_ALL=C sort | xargs sha1sum | sha1sum | cut -c1-40)"
DSHA="$(sha1sum "$DIFF" | cut -c1-40)"
[ $CONTROL = 1 ] && DTAG="control" || DTAG="$DSHA"
KEY="$(printf '%s|%s|%s|%s|%s|%s' "$JSHA" "$SRCSHA" "$DTAG" "$SCALA_V" "$CHISEL_V" "$SCALAC_OPTS" | sha1sum | cut -c1-16)"
PD="$OOC_WORK/patched/$KEY"; mkdir -p "$PD"
exec 7>"$PD/.lock"; flock 7
if [ -f "$PD/.ok" ]; then cat "$PD/.ok"; exit 0; fi
log "building $([ $CONTROL = 1 ] && echo CONTROL || echo PATCHED) jar in $PD (base jar $JSHA, v3 src $SRCSHA, diff $DTAG)"
rm -rf "$PD/src" "$PD/classes" "$PD"/*.tmp
mkdir -p "$PD/src/src/main/scala" "$PD/classes"

# 2. private source copy (+ diff)
cp -a "$V3" "$PD/src/src/main/scala/"
[ "$(cd "$PD/src/src/main/scala/v3" && find . -type f -name '*.scala' | LC_ALL=C sort | xargs sha1sum | sha1sum | cut -c1-40)" = "$SRCSHA" ] \
  || die "tree changed while copying; retry"
if [ $CONTROL = 0 ]; then
  ( cd "$PD/src" && patch -p1 -F0 --forward --batch --no-backup-if-mismatch < "$DIFF" ) > "$PD/patch.log" 2>&1 \
    || { cat "$PD/patch.log" >&2; die "the diff does not apply cleanly to the tree (see $PD/patch.log)"; }
  ! find "$PD/src" -name '*.rej' | grep -q . || die "patch left rejects in $PD/src"
fi

# 3. compile all of v3 against the snapshot
t0=$(date +%s)
find "$PD/src/src/main/scala/v3" -name '*.scala' | LC_ALL=C sort > "$PD/sources.txt"
java -Xss8M -Xmx8G -cp "$SC:$SR:$SNAP" scala.tools.nsc.Main -usejavacp -Xplugin:"$PLUGIN" $SCALAC_OPTS \
  -classpath "$SNAP" -d "$PD/classes" @"$PD/sources.txt" > "$PD/compile.log" 2>&1 \
  || { tail -30 "$PD/compile.log" >&2; die "scalac failed (see $PD/compile.log)"; }
t1=$(date +%s)
log "compiled $(wc -l < "$PD/sources.txt") v3 sources in $((t1 - t0)) s"

# 4. replace boom/v3 in a copy of the snapshot; the class-name sets must match exactly
( cd "$PD/classes" && find . -type f | sed 's|^\./||' | LC_ALL=C sort ) > "$PD/new_entries.txt"
grep -qv '^boom/v3/' "$PD/new_entries.txt" && die "compiled classes outside boom/v3: $(grep -v '^boom/v3/' "$PD/new_entries.txt" | head -3)"
unzip -Z1 "$SNAP" 'boom/v3/*' 2>/dev/null | grep -v '/$' | LC_ALL=C sort > "$PD/old_entries.txt"
# New classes must all exist in the jar (the diff adds no class).  Jar-only classes are allowed only if
# they are ORPHANS: stale products of deleted sources that the live jar still carries (seen 2026-10-28:
# 6 boom/v3/lsu/*CFExt classes); no Scala source in the tree names them, so nothing can load them, and
# they are dropped exactly as a clean sbt build would drop them.  Anything else fails.
LC_ALL=C comm -13 "$PD/old_entries.txt" "$PD/new_entries.txt" > "$PD/only_new.txt"
LC_ALL=C comm -23 "$PD/old_entries.txt" "$PD/new_entries.txt" > "$PD/only_jar.txt"
[ -s "$PD/only_new.txt" ] && { head "$PD/only_new.txt" >&2; die "compiled classes that the jar does not have (see $PD/only_new.txt)"; }
: > "$PD/orphans.txt"
while read -r e; do
  cn="$(basename "$e" .class)"; cn="${cn%%\$*}"
  if grep -rqw --include='*.scala' -e "$cn" "$CHIPYARD_ROOT/generators" "$CHIPYARD_ROOT/tools" "$CHIPYARD_ROOT/sims/firesim/sim" 2>/dev/null; then
    die "jar class $e is not produced by the v3 sources but '$cn' is named in the tree"
  fi
  echo "$e" >> "$PD/orphans.txt"
done < "$PD/only_jar.txt"
[ -s "$PD/orphans.txt" ] && log "dropping $(wc -l < "$PD/orphans.txt") orphan class(es) of deleted sources: $(tr '\n' ' ' < "$PD/orphans.txt")"
cp "$SNAP" "$PD/jar.tmp"
zip -q -d "$PD/jar.tmp" 'boom/v3/*' > /dev/null
( cd "$PD/classes" && zip -q -r -X "$PD/jar.tmp" boom )
[ "$(unzip -Z1 "$PD/jar.tmp" 'boom/v3/*' | grep -v '/$' | LC_ALL=C sort | sha1sum)" = "$(sha1sum < "$PD/new_entries.txt")" ] \
  || die "jar boom/v3 content check failed"
PSHA="$(sha1sum "$PD/jar.tmp" | cut -c1-40)"
OUT="$OOC_WORK/cache/jars/chipyard-$PSHA.jar"
mv "$PD/jar.tmp" "$OUT"
echo "static_fix base_jar_check=[$(cat "$SNAP.check")] orphans_dropped=$(wc -l < "$PD/orphans.txt") kind=$([ $CONTROL = 1 ] && echo control || echo patched) base_jar_sha1=$JSHA v3_src_sha1=$SRCSHA diff=$(basename "$DIFF") diff_sha1=$([ $CONTROL = 1 ] && echo none || echo "$DSHA") scala=$SCALA_V chisel=$CHISEL_V key=$KEY" > "$OUT.meta"
rm -rf "$PD/classes"   # the jar holds them; src/ and logs stay for inspection
echo "$OUT" > "$PD/.ok"
log "jar $OUT"
echo "$OUT"
