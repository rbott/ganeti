#!/usr/bin/env bash
# grow.sh <vcluster-root> <node> <target-kb>
# Adds diskless fake instances until config.data >= target-kb.
set -e -u
VCROOT=$1; NODE=$2; TARGET_KB=$3
CONFIG="$VCROOT/$NODE/usr/local/var/lib/ganeti/config.data"
target=$((TARGET_KB * 1024))
# resume numbering after any existing bench-N instances
n=$("$VCROOT/$NODE/cmd" gnt-instance list --no-headers -o name 2>/dev/null | grep -c '^bench-' || true)
while [ "$(stat -c%s "$CONFIG")" -lt "$target" ]; do
  n=$((n + 1))
  "$VCROOT/$NODE/cmd" gnt-instance add \
    -t diskless --no-install -o noop --no-nics -n "$NODE" "bench-$n" >/dev/null
done
size=$(stat -c%s "$CONFIG")
echo "grown to $size bytes with $n instances (target $target)"
