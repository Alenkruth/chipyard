#!/usr/bin/env bash
# Production sweep driver for the OOC synthesis harness.
#
#   sweep.sh [--manifest F] [--clamp-verified] [--dry-run] [--workers N] [--limit N]
#
# For every manifest point with status pending (and needs_clamp_fix only with --clamp-verified):
#   elaborate.sh (private patched jar for needs_clamp_fix points via scala/patched_jar.sh; otherwise OOC_STATIC_FIX=auto,
#   i.e. the plain jar for max points) -> run_point.sh -> manifest.py set-status done|failed.
# Rules: (1) at most OOC_MAX_BUILDS=2 builds host-wide (Generator + firtool + Vivado together), checked under a host-wide
# lock before EVERY elaboration and EVERY Vivado start, wait start/end logged; (2) resumable: a point with a complete
# result.json whose .synth_rtl_sha1 matches rtl/elab.json is skipped (and marked done); (3) the stale-jar gate stays on:
# OOC_ALLOW_STALE_JAR must be unset or the sweep refuses to start; (4) detach with
#   setsid nohup tools/ooc-synth/sweep.sh ... < /dev/null > $OOC_WORK/logs/sweep.out 2>&1 &
# and wake a waiter with tools/ooc-synth/notify_on_exit.sh <pid> sweep $OOC_WORK/logs/sweep/rc; the sweep writes rc and
# wall_s files next to its logs and exits when done; (5) paths come from ooc.env only; (6) on TERM/INT only its own
# PIDs (workers, their children and descendants, by exact PID) are stopped.  Points stay pending when interrupted.
# Exit: 0 all runnable points done; 1 some failed; 2 usage/refusal.  Nothing is polled by an agent.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$HERE/ooc.env" ] && . "$HERE/ooc.env"
: "${OOC_WORK:?OOC_WORK not set (ooc.env)}"
CHIPYARD_ROOT="${CHIPYARD_ROOT:-$(git -C "$HERE" rev-parse --show-toplevel)}"; export CHIPYARD_ROOT OOC_WORK
. "$HERE/sweep_gate.sh"
MAN="$OOC_WORK/manifest.csv"; VERIFIED=""; DRY=0; WORKERS="${OOC_SWEEP_WORKERS:-2}"; LIMIT=0
while [ $# -gt 0 ]; do case "$1" in
  --manifest) MAN="$2"; shift 2;; --clamp-verified) VERIFIED="--clamp-verified"; shift;;
  --dry-run) DRY=1; shift;; --workers) WORKERS="$2"; shift 2;; --limit) LIMIT="$2"; shift 2;;
  -h|--help) sed -n '2,19p' "$0"; exit 2;; *) echo "unknown arg $1" >&2; exit 2;; esac; done
[ -f "$MAN" ] || { echo "no manifest: $MAN" >&2; exit 2; }
[ "$WORKERS" -le "$OOC_MAX_BUILDS" ] || WORKERS="$OOC_MAX_BUILDS"
[ -z "${OOC_ALLOW_STALE_JAR:-}" ] || [ "$OOC_ALLOW_STALE_JAR" = 0 ] || { echo "refusing: OOC_ALLOW_STALE_JAR is set; the stale-jar gate must stay on" >&2; exit 2; }
unset OOC_ALLOW_STALE_JAR

PLAN="$(python3 -I "$HERE/sweep_plan.py" "$MAN" "$OOC_WORK" $VERIFIED)" || { echo "plan failed" >&2; exit 2; }
cnt() { printf '%s\n' "$PLAN" | grep -c "^$1"$'\t' || true; }
if [ "$DRY" = 1 ]; then
  echo "manifest $MAN  clamp_verified=$([ -n "$VERIFIED" ] && echo yes || echo no)  workers=$WORKERS  max_builds=$OOC_MAX_BUILDS"
  echo "counts: RUN=$(cnt RUN) (patched jar: $(printf '%s\n' "$PLAN" | awk -F'\t' '$1=="RUN"&&$6=="all"' | wc -l), plain/auto: $(printf '%s\n' "$PLAN" | awk -F'\t' '$1=="RUN"&&$6=="auto"' | wc -l)) SKIP_DONE=$(cnt SKIP_DONE) HOLD_CLAMP=$(cnt HOLD_CLAMP) SKIP_STATUS=$(cnt SKIP_STATUS)"
  printf '%s\n' "$PLAN" | awk -F'\t' '$1=="SKIP_STATUS"{c[$7]++} END{for(k in c) print "  skipped by status " k ": " c[k]}' | sort
  b="$(builds)"; echo "gate now: $b build(s) running; a start would $([ "$b" -ge "$OOC_MAX_BUILDS" ] && echo "WAIT (>= $OOC_MAX_BUILDS)" || echo "proceed")"
  echo "stale-jar gate: ON (OOC_ALLOW_STALE_JAR unset)"
  echo "ordered plan (action point mode):"
  printf '%s\n' "$PLAN" | awk -F'\t' '$1!="SKIP_STATUS"{printf "  %-11s %-60s %s %s\n",$1,$2,$6,($1=="RUN"?"elaborate+synth":$7)}'
  echo "DRY RUN: nothing launched"; exit 0
fi

RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"; LD="$OOC_WORK/logs/sweep"; mkdir -p "$LD"
GATE_LOG="$LD/$RUN_ID.gate"; LOCK="$LD/gate.lock"; STATE="$LD/$RUN_ID.pids"; MLOCK="$MAN.lock"
: > "$STATE"; rm -f "$LD/rc" "$LD/wall_s"; T0=$(date +%s)
glog() { echo "[$(date '+%F %T')] $*" >> "$GATE_LOG"; }
mark() { flock "$MLOCK" python3 -I "$HERE/analysis/manifest.py" set-status "$MAN" "$1" "$2"; }
descend() { local p c; for c in $(ps -o pid= --ppid "$1" 2>/dev/null); do descend "$c"; done; echo "$1"; }
stop_own() {  # exact PIDs only: the recorded workers and their descendants
  local p q; for p in $(cat "$STATE" 2>/dev/null); do for q in $(descend "$p"); do kill "$q" 2>/dev/null; done; done; }
trap 'glog "signal: stopping own pids"; stop_own; echo 130 > "$LD/rc"; exit 130' TERM INT

# Start "$@" as a gated build: wait while >= OOC_MAX_BUILDS builds run (host-wide lock around check+launch+settle so two
# workers cannot both pass), then run it; returns its exit code.  $LABEL names it in the gate log.
gated() {
  local label="$1"; shift; local waited=0 b ws
  exec 5>"$LOCK"; flock 5
  b="$(builds)"
  if [ "$b" -ge "$OOC_MAX_BUILDS" ]; then
    ws=$(date +%s); glog "wait start: $b builds running (next: $label)"
    while [ "$(builds)" -ge "$OOC_MAX_BUILDS" ]; do sleep 20; done
    glog "wait end after $(( $(date +%s) - ws )) s: $(builds) running (next: $label)"
  fi
  glog "start: $label (with $(builds) running)"
  "$@" & local pid=$!; echo "$pid" >> "$STATE"
  local n0 t=0; n0="$(builds)"   # hold the lock until the child shows up as a build (or ends / 90 s), so the next check sees it
  while kill -0 "$pid" 2>/dev/null && [ "$(builds)" -le "$n0" ] && [ $t -lt 90 ]; do sleep 2; t=$((t+2)); done
  flock -u 5; exec 5>&-
  wait "$pid"; local rc=$?; glog "end: $label rc=$rc"; return $rc
}

do_point() {  # point module variant params mode
  local pt="$1" m="$2" v="$3" p="$4" mode="$5" d="$OOC_WORK/points/$1" n
  n="$(echo "${m}_${v}_${p}" | tr '=,' '__')"; mkdir -p "$LD/pts"
  local lg="$LD/pts/$n"
  OOC_STATIC_FIX="$mode" gated "elab $pt [$mode]" "$HERE/elaborate.sh" "$m" "$v" "$p" < /dev/null > "$lg.elab.log" 2>&1 \
    || { mark "$pt" failed; return 1; }
  rm -f "$d/.synth_rtl_sha1"
  gated "vivado $pt" "$HERE/run_point.sh" "$d" < /dev/null > "$lg.synth.log" 2>&1 || { mark "$pt" failed; return 1; }
  python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["rtl_sha1"])' "$d/rtl/elab.json" > "$d/.synth_rtl_sha1" || { mark "$pt" failed; return 1; }
  mark "$pt" done
}

# SKIP_DONE points are marked done (resume). Then a simple pool of WORKERS; each does one point at a time.
printf '%s\n' "$PLAN" | awk -F'\t' '$1=="SKIP_DONE"{print $2}' | while IFS= read -r pt; do mark "$pt" done; done
glog "sweep $RUN_ID start: $(cnt RUN) to run, $(cnt SKIP_DONE) already done, $(cnt HOLD_CLAMP) held (clamp), workers=$WORKERS"
NFAIL=0; N=0; JOBS=()
while IFS=$'\t' read -r act pt m v p mode _; do
  [ "$act" = RUN ] || continue
  [ "$LIMIT" -gt 0 ] && [ "$N" -ge "$LIMIT" ] && break
  while [ "$(jobs -rp | wc -l)" -ge "$WORKERS" ]; do wait -n || NFAIL=$((NFAIL+1)); done
  N=$((N+1)); do_point "$pt" "$m" "$v" "$p" "$mode" & echo $! >> "$STATE"
done <<< "$PLAN"
while [ "$(jobs -rp | wc -l)" -gt 0 ]; do wait -n || NFAIL=$((NFAIL+1)); done
glog "sweep $RUN_ID end: launched=$N failed=$NFAIL"
echo $(( $(date +%s) - T0 )) > "$LD/wall_s"; RC=$([ $NFAIL -eq 0 ] && echo 0 || echo 1); echo $RC > "$LD/rc"
echo "SWEEP_DONE launched=$N failed=$NFAIL rc=$RC logs=$LD gate=$GATE_LOG"
exit $RC
