#!/usr/bin/env bash
# Compiles tools/ooc-synth/scala/src against a SNAPSHOT of the chipyard fat jar.
# Prints one line: "<classpath>" (snapshot jar + compiled classes) on stdout; logs go to stderr.
# Outputs are cached under $OOC_WORK/cache keyed by sha1 (jar) and sha1 (sources + jar), so a
# rebuild of the live jar during a sweep cannot change the classes a running sweep uses.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OOC_DIR="$(dirname "$HERE")"
[ -f "$OOC_DIR/ooc.env" ] && . "$OOC_DIR/ooc.env"
CHIPYARD_ROOT="${CHIPYARD_ROOT:-$(git -C "$OOC_DIR" rev-parse --show-toplevel)}"
: "${OOC_WORK:?set OOC_WORK (ooc.env or environment)}"
OOC_GENERATOR_JAR="${OOC_GENERATOR_JAR:-$CHIPYARD_ROOT/.classpath_cache/chipyard.jar}"
COURSIER_CACHE="${COURSIER_CACHE:-$HOME/.cache/coursier}"
log() { echo "[ooc-build] $*" >&2; }

[ -f "$OOC_GENERATOR_JAR" ] || { log "generator jar not found: $OOC_GENERATOR_JAR (build it with make in sims/verilator)"; exit 1; }
CACHE="$OOC_WORK/cache"; mkdir -p "$CACHE/jars"
exec 9>"$CACHE/.build.lock"; flock 9

# 1. jar snapshot (keyed by content)
JSHA="$(sha1sum "$OOC_GENERATOR_JAR" | cut -c1-40)"
SNAP="$CACHE/jars/chipyard-$JSHA.jar"
if [ ! -f "$SNAP" ]; then
  log "snapshotting $OOC_GENERATOR_JAR -> $SNAP"
  cp "$OOC_GENERATOR_JAR" "$SNAP.tmp.$$"
  [ "$(sha1sum "$SNAP.tmp.$$" | cut -c1-40)" = "$JSHA" ] || { rm -f "$SNAP.tmp.$$"; log "jar changed while copying; retry"; exit 1; }
  mv "$SNAP.tmp.$$" "$SNAP"
  # staleness record: Scala sources newer than the live jar at snapshot time (0 = jar matches the tree)
  NEWER="$(find "$CHIPYARD_ROOT/generators" "$CHIPYARD_ROOT/tools/stage" -path '*/src/main/scala/*' -name '*.scala' \
           -newer "$OOC_GENERATOR_JAR" 2>/dev/null | wc -l)"
  echo "live_jar_mtime=$(date -r "$OOC_GENERATOR_JAR" '+%F %T %z') scala_sources_newer_than_jar=$NEWER" > "$SNAP.meta"
  [ "$NEWER" = 0 ] || log "WARNING: $NEWER Scala source(s) are newer than $OOC_GENERATOR_JAR; rebuild the jar first"
fi

# 1b. staleness gate, checked on EVERY call (not only at snapshot time): Scala sources that are newer
#     than the generator jar mean the jar does not hold the tree's RTL.  Fails unless OOC_ALLOW_STALE_JAR=1.
STALE_SRC="$(find "$CHIPYARD_ROOT/generators" "$CHIPYARD_ROOT/tools" "$CHIPYARD_ROOT/sims/firesim/sim" \
             -path '*/src/main/scala/*' -name '*.scala' -newer "$OOC_GENERATOR_JAR" 2>/dev/null | sort)"
NSTALE="$(printf '%s' "$STALE_SRC" | grep -c . || true)"
echo "checked=$(date '+%F %T %z') jar_sha1=$JSHA live_jar_mtime=$(date -r "$OOC_GENERATOR_JAR" '+%F %T %z') scala_sources_newer_than_jar=$NSTALE allow_stale=${OOC_ALLOW_STALE_JAR:-0}" > "$SNAP.check"
if [ "$NSTALE" != 0 ]; then
  log "=================================================================================="
  log "STALE GENERATOR JAR: $NSTALE Scala source(s) are newer than $OOC_GENERATOR_JAR:"
  head -20 <<< "$STALE_SRC" | sed 's/^/[ooc-build]   /' >&2
  log "Rebuild the jar (any make in sims/verilator) before elaborating."
  log "=================================================================================="
  [ "${OOC_ALLOW_STALE_JAR:-0}" = 1 ] || exit 1
  log "OOC_ALLOW_STALE_JAR=1: continuing with the stale jar (recorded in elab.json)"
fi

# 2. compiler jars: versions read from build.sbt (never typed here)
SCALA_V="$(sed -n 's/^val scalaVersionFromChisel *= *"\([^"]*\)".*/\1/p' "$CHIPYARD_ROOT/build.sbt" | head -1)"
CHISEL_V="$(sed -n 's/^val chisel6Version *= *"\([^"]*\)".*/\1/p' "$CHIPYARD_ROOT/build.sbt" | head -1)"
[ -n "$SCALA_V" ] && [ -n "$CHISEL_V" ] || { log "cannot read scala/chisel versions from build.sbt"; exit 1; }
findjar() { find "$COURSIER_CACHE" -name "$1" -not -name '*-sources.jar' 2>/dev/null | head -1; }
SC="$(findjar "scala-compiler-$SCALA_V.jar")"; SR="$(findjar "scala-reflect-$SCALA_V.jar")"
PLUGIN="$(findjar "chisel-plugin_$SCALA_V-$CHISEL_V.jar")"
for j in SC SR PLUGIN; do [ -n "${!j}" ] || { log "missing $j jar in $COURSIER_CACHE (scala $SCALA_V, chisel $CHISEL_V)"; exit 1; }; done

# 3. compile (keyed by sources + jar)
SSHA="$( (cat "$HERE"/src/*.scala; echo "$JSHA $SCALA_V $CHISEL_V") | sha1sum | cut -c1-12)"
CLS="$CACHE/classes-$SSHA"
if [ ! -f "$CLS/.ok" ]; then
  log "compiling $(ls "$HERE"/src/*.scala | wc -l) source(s) -> $CLS"
  rm -rf "$CLS"; mkdir -p "$CLS"
  java -Xss8M -cp "$SC:$SR:$SNAP" scala.tools.nsc.Main -usejavacp -Xplugin:"$PLUGIN" \
    -classpath "$SNAP" -d "$CLS" "$HERE"/src/*.scala >&2
  echo "jar=$SNAP plugin=$PLUGIN scala=$SCALA_V chisel=$CHISEL_V" > "$CLS/.ok"
fi
echo "$SNAP:$CLS"
