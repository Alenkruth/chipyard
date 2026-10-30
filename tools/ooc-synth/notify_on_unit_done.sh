#!/usr/bin/env bash
# Wait for a systemd --user unit to stop being active, then print one line. Run with run_in_background so the harness wakes the caller once.
# usage: notify_on_unit_done.sh <unit> [unit...]   (shell waiter; no agent polls)
set -u
t0=$(date +%s)
for u in "$@"; do
  while [ "$(systemctl --user is-active "$u" 2>/dev/null)" = "active" ] || [ "$(systemctl --user is-active "$u" 2>/dev/null)" = "activating" ]; do sleep 60; done
done
echo "OOC_UNITS_DONE units=\"$*\" at=$(date '+%F %T') waited_s=$(( $(date +%s)-t0 ))"
