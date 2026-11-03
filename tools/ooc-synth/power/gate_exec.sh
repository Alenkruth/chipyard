#!/usr/bin/env bash
# gate_exec.sh -- run one simulator through the shared host-wide sim gate (RULES.md B: at most 2 simulators).
# Same protocol as /data/akrish/ift-overhaul/phase4/fpga-r5/simref/run.sh runone(): take flock on the shared
# gate.lock, wait while >= 2 simulators run (same pgrep pattern), start the command, hold the lock 8 s so the
# new process is visible to the next waiter, release, then wait for the command and return its exit code.
# Usage (as pilot.sh GATE_CMD prefix): GATE_CMD=/home/jht9sy/work/chipyard/tools/ooc-synth/power/gate_exec.sh
#   gate_exec.sh <simulator> <args...>
# Writes "WAIT start/end" lines with real clock time to stderr (the step log) so the runner can report waits.
LOCK=${GATE_LOCK:-/data/akrish/ift-overhaul/phase4/fpga-r5/simref/gate.lock}
nsims() { pgrep -f '^\S*(simulator-chipyard|VFireSim)' | wc -l; }
exec 8>"$LOCK"; flock 8
w=0
while [ "$(nsims)" -ge 2 ]; do
  [ $w = 0 ] && echo "$(date '+%F %T') GATE WAIT start ($(nsims) sims running)" >&2; w=1; sleep 15
done
[ $w = 1 ] && echo "$(date '+%F %T') GATE WAIT end" >&2
echo "$(date '+%F %T') GATE launch: $1" >&2
"$@" 8>&- &
pid=$!; sleep 8; flock -u 8; exec 8>&-
wait $pid
