#!/usr/bin/env bash
#
# wconfd-bench: measure wconfd RPC latency vs. config.data size using a vcluster.
#
# Usage: bench.sh smoke          # baseline + 100KB tier (fast validation)
#        bench.sh 500kb          # baseline + grow until config >= 500KB
#        bench.sh all            # baseline + 100KB then 500KB steps to 5MB
#        bench.sh 100kb 1mb ...  # explicit tiers
#
# Config growth is OFFLINE by default (fast): wconfd is stopped, a template
# instance is cloned into config.data, wconfd/luxid are restarted. Set
# GROW_MODE=gnt to grow via real `gnt-instance add` instead (slow, but
# exercises the real write path).
#
# Results are written as CSV to /out/results-<timestamp>.csv (or ./results).

set -e -u -o pipefail

VCROOT=${VCROOT:-/opt/ganeti-vcluster}
NODE=${NODE:-node1}
OUTDIR=${OUTDIR:-/out}
BENCH_DIR=${BENCH_DIR:-/src/tools/wconfd-bench}
PY=${PY:-python3}
GROW_MODE=${GROW_MODE:-offline}
NETPREFIX=${NETPREFIX:-192.0.2}

[ -d "$OUTDIR" ] || OUTDIR=./results
mkdir -p "$OUTDIR"

ts=$(date +%Y%m%d-%H%M%S)
CSV="$OUTDIR/results-$ts.csv"

CONFIG="$VCROOT/$NODE/usr/local/var/lib/ganeti/config.data"

log() { echo "[wconfd-bench] $*"; }

# --- tier selection ---------------------------------------------------------
case "${1:-smoke}" in
  smoke) tiers=(100) ;;
  all)   tiers=(100 500 1000 1500 2000 2500 3000 3500 4000 4500 5000) ;;
  *)     tiers=()
         for t in "$@"; do tiers+=("$(( ${t%kb} ))"); done ;;
esac

# --- 0. stage install tree + networking -------------------------------------
bash "$BENCH_DIR/stage.sh"

# hosts entries for cluster + node (vcluster-setup -E skips /etc/hosts)
grep -q "$NETPREFIX.1 cluster" /etc/hosts || \
  printf "%s.1 cluster\n%s.10 node1\n" "$NETPREFIX" "$NETPREFIX" >> /etc/hosts

 # node IP on dummy interface (vcluster daemons bind to it)
ip link add gnt type dummy 2>/dev/null || true
ip link set dev gnt up 2>/dev/null || true
ip addr add "$NETPREFIX.10/32" dev gnt 2>/dev/null || true
ip route add "$NETPREFIX.0/24" dev gnt 2>/dev/null || true

# --- 1. vcluster setup ------------------------------------------------------
if [ ! -d "$VCROOT/$NODE" ]; then
  log "creating vcluster at $VCROOT"
  mkdir -p "$VCROOT/$NODE/usr/local"
  # vcluster-setup hardcodes $nodedir/etc and $nodedir/var, but the
  # configure-time paths expand to $nodedir/usr/local/{etc,var}. Bridge them
  # BEFORE setup so its internal writes resolve.
  ln -sfn "$VCROOT/$NODE/usr/local/etc" "$VCROOT/$NODE/etc"
  ln -sfn "$VCROOT/$NODE/usr/local/var" "$VCROOT/$NODE/var"
  mkdir -p "$VCROOT/$NODE/usr/local/etc/default" "$VCROOT/$NODE/usr/local/etc/ganeti"
  /src/tools/vcluster-setup -E -N -c 1 "$VCROOT"
fi

# noop OS provider (OS search path is the compiled-in /srv/ganeti/os)
if [ ! -d /srv/ganeti/os/noop ]; then
  log "installing noop OS provider"
  mkdir -p /srv/ganeti/os/noop
  echo 20 > /srv/ganeti/os/noop/ganeti_api_version
  touch /srv/ganeti/os/noop/parameters.list
  for s in create import export rename verify; do
    printf '#!/bin/sh\nexit 0\n' > "/srv/ganeti/os/noop/$s"
    chmod +x "/srv/ganeti/os/noop/$s"
  done
fi

# --- 2. cluster init (idempotent) -------------------------------------------
if [ ! -f "$CONFIG" ]; then
  log "initializing cluster"
  ssh-keygen -A >/dev/null 2>&1 || true
  export GANETI_ROOTDIR="$VCROOT/$NODE" GANETI_HOSTNAME="$NODE"
  gnt-cluster init \
    --no-etc-hosts --no-ssh-init --master-netdev=lo \
    --enabled-disk-templates=diskless --enabled-hypervisors=fake \
    cluster
  # permissive ipolicy so nic-less/disk-less instances validate
  "$VCROOT/$NODE/cmd" gnt-cluster modify --ipolicy-bounds-specs \
    "min:disk-size=0,cpu-count=1,disk-count=0,memory-size=1,nic-count=0,spindle-use=0/max:disk-size=1048576,cpu-count=8,disk-count=16,memory-size=32768,nic-count=8,spindle-use=12"
else
  log "cluster already initialized"
fi

# --- 3. start daemons -------------------------------------------------------
log "starting daemons"
(cd "$VCROOT" && ./start-all) || true
sleep 3

# --- 4. wait for wconfd + luxid ---------------------------------------------
# wconfd answers Echo; luxid must be ready before any gnt job is submitted
# (job submission uses the wconfd livelock handshake, which races on startup).
log "waiting for wconfd"
"$VCROOT/$NODE/cmd" "$PY $BENCH_DIR/bench_driver.py wait"
log "waiting for luxid"
for _ in $(seq 1 30); do
  "$VCROOT/$NODE/cmd" gnt-instance list >/dev/null 2>&1 && break
  sleep 1
done
# --- 5. ensure a template instance exists for offline cloning ---------------
if ! "$VCROOT/$NODE/cmd" gnt-instance list --no-headers -o name 2>/dev/null | grep -q '^bench-'; then
  log "adding template instance bench-tpl"
  "$VCROOT/$NODE/cmd" gnt-instance add \
    -t diskless --no-install -o noop --no-nics -n "$NODE" bench-tpl >/dev/null
fi

# --- 6. baseline benchmark --------------------------------------------------
log "baseline: config size $(stat -c%s "$CONFIG") bytes"
"$VCROOT/$NODE/cmd" "$PY $BENCH_DIR/bench_driver.py run --label baseline --csv $CSV"

# --- 7. grow config tier by tier --------------------------------------------
for tier_kb in "${tiers[@]}"; do
  if [ "$GROW_MODE" = offline ]; then
    "$BENCH_DIR/grow_restart.sh" "$tier_kb"
  else
    "$BENCH_DIR/grow.sh" "$VCROOT" "$NODE" "$tier_kb"
  fi
  "$VCROOT/$NODE/cmd" "$PY $BENCH_DIR/bench_driver.py wait" >/dev/null
  size=$(stat -c%s "$CONFIG")
  log "tier ${tier_kb}KB: actual size $size bytes, benchmarking"
  "$VCROOT/$NODE/cmd" "$PY $BENCH_DIR/bench_driver.py run --label ${tier_kb}kb --csv $CSV"
done

log "done. results: $CSV"
echo
"$PY" "$BENCH_DIR/format_results.py" "$CSV"
