#!/usr/bin/env bash
# Wait for a job to end and print one line, so the caller (run with run_in_background) is re-invoked by the harness.
# No polling by an agent: this script is the waiter. Uses kill -0 on the PID (never pgrep -f).
# usage: notify_on_exit.sh <pid> <label> [rc-file] [wall-file]
set -u
pid=$1; label=$2; rcf=${3:-}; wallf=${4:-}
t0=$(date +%s)
while kill -0 "$pid" 2>/dev/null; do sleep 20; done
sleep 5   # let the launcher write its rc/wall files
rc=unknown; [ -n "$rcf" ] && [ -f "$rcf" ] && rc=$(cat "$rcf")
wall=""; [ -n "$wallf" ] && [ -f "$wallf" ] && wall=" wall_s=$(cat "$wallf")"
echo "OOC_JOB_DONE label=$label pid=$pid rc=$rc${wall} at=$(date '+%F %T') waited_s=$(( $(date +%s)-t0 ))"
