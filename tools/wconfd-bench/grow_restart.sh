#!/usr/bin/env bash
# grow_restart.sh <target-kb>: grow config offline, restart wconfd only.
# Avoids start-all/stop-all (luxid hangs on stop when wconfd is down, and
# zombie daemons accumulate under the container's non-reaping PID 1). Only
# wconfd + luxid are needed for the benchmark; the rest stay running.
set -e -u
VCROOT=${VCROOT:-/opt/ganeti-vcluster}
NODE=${NODE:-node1}
TARGET_KB=$1
CONFIG="$VCROOT/$NODE/usr/local/var/lib/ganeti/config.data"

echo "stopping wconfd/luxid"
pkill -x ganeti-wconfd 2>/dev/null || true
pkill -x ganeti-luxid 2>/dev/null || true
sleep 2
# force if still alive
pkill -9 -x ganeti-wconfd 2>/dev/null || true
pkill -9 -x ganeti-luxid 2>/dev/null || true

echo "growing config to ${TARGET_KB}KB"
python3 /src/tools/wconfd-bench/grow_offline.py "$CONFIG" "$TARGET_KB"

echo "starting wconfd + luxid"
export GANETI_ROOTDIR="$VCROOT/$NODE"
export GANETI_HOSTNAME="$NODE"
/usr/local/sbin/ganeti-wconfd
sleep 2
# luxid is needed for gnt-* queries/jobs; wconfd alone only serves the driver.
/usr/local/sbin/ganeti-luxid
sleep 2
stat -c%s "$CONFIG"
