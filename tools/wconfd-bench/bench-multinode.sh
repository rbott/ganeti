#!/usr/bin/env bash
#
# bench-multinode.sh: wconfd benchmark on a multi-node vcluster, with a
# concurrent migration-job load phase.
#
# Sets up an 8-node vcluster (1 master + 7 added nodes, 6 master candidates),
# grows config.data offline, then measures wconfd RPC latency both idle and
# while a batch of gnt-instance migrate jobs runs against the cluster.
#
# Usage: bench-multinode.sh [tier-kb ...]   (default: 1000)
# Env:   NODECOUNT   (default 8)
#        JOB_BATCH   concurrent migrate jobs in the load phase (default 8)
#        OUTDIR      results dir (default /out or ./results)

set -e -u -o pipefail

VCROOT=${VCROOT:-/opt/ganeti-vcluster}
MASTER=node1
NODECOUNT=${NODECOUNT:-8}
JOB_BATCH=${JOB_BATCH:-8}
OUTDIR=${OUTDIR:-/out}
BENCH_DIR=${BENCH_DIR:-/src/tools/wconfd-bench}
PY=${PY:-python3}
NETPREFIX=${NETPREFIX:-192.0.2}

[ -d "$OUTDIR" ] || OUTDIR=./results
mkdir -p "$OUTDIR"
ts=$(date +%Y%m%d-%H%M%S)
CSV="$OUTDIR/multinode-$ts.csv"
LOADCSV="$OUTDIR/multinode-load-$ts.csv"
CONFIG="$VCROOT/$MASTER/usr/local/var/lib/ganeti/config.data"

log() { echo "[wconfd-bench-mn] $*"; }

tiers=("$@")
[ ${#tiers[@]} -eq 0 ] && tiers=(1000)

# --- stage + networking -----------------------------------------------------
bash "$BENCH_DIR/stage.sh"

# hosts entries for cluster + all nodes
if ! grep -q "$NETPREFIX.1 cluster" /etc/hosts; then
  {
    echo "$NETPREFIX.1 cluster"
    for ((i=0; i < NODECOUNT; i++)); do
      echo "$NETPREFIX.$((10 + i)) node$((i + 1))"
    done
  } >> /etc/hosts
fi

# per-node IPs on the dummy interface
ip link add gnt type dummy 2>/dev/null || true
ip link set dev gnt up 2>/dev/null || true
for ((i=0; i < NODECOUNT; i++)); do
  ip addr add "$NETPREFIX.$((10 + i))/32" dev gnt 2>/dev/null || true
done
ip route add "$NETPREFIX.0/24" dev gnt 2>/dev/null || true

# --- vcluster setup ---------------------------------------------------------
if [ ! -d "$VCROOT/$MASTER" ]; then
  log "creating $NODECOUNT-node vcluster at $VCROOT"
  for ((i=1; i <= NODECOUNT; i++)); do
    mkdir -p "$VCROOT/node$i/usr/local"
    ln -sfn "$VCROOT/node$i/usr/local/etc" "$VCROOT/node$i/etc"
    ln -sfn "$VCROOT/node$i/usr/local/var" "$VCROOT/node$i/var"
    mkdir -p "$VCROOT/node$i/usr/local/etc/default" "$VCROOT/node$i/usr/local/etc/ganeti"
  done
  mkdir -p "$VCROOT"
  /src/tools/vcluster-setup -E -N -c "$NODECOUNT" "$VCROOT"
fi

# noop OS provider
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

# --- cluster init -----------------------------------------------------------
if [ ! -f "$CONFIG" ]; then
  log "initializing cluster on $MASTER"
  ssh-keygen -A >/dev/null 2>&1 || true
  [ -f /root/.ssh/id_rsa ] || ssh-keygen -t rsa -N "" -f /root/.ssh/id_rsa >/dev/null
  cat /root/.ssh/id_rsa.pub > /root/.ssh/authorized_keys
  export GANETI_ROOTDIR="$VCROOT/$MASTER" GANETI_HOSTNAME="$MASTER"
  gnt-cluster init \
    --no-etc-hosts --no-ssh-init --master-netdev=lo \
    --enabled-disk-templates=diskless --enabled-hypervisors=fake \
    cluster
  "$VCROOT/$MASTER/cmd" gnt-cluster modify --ipolicy-bounds-specs \
    "min:disk-size=0,cpu-count=1,disk-count=0,memory-size=1,nic-count=0,spindle-use=0/max:disk-size=1048576,cpu-count=8,disk-count=16,memory-size=32768,nic-count=8,spindle-use=12"
else
  log "cluster already initialized"
fi
# --- sshd + node-add staging (multi-node only) ------------------------------
# gnt-node add SSHes to the target node's IP (all loopback here) and runs
# node-daemon-setup remotely. Needs sshd up, root key auth, and the versioned
# lib tree it expects.
if ! command -v sshd >/dev/null && [ ! -x /usr/sbin/sshd ]; then
  log "installing openssh-server"
  apt-get update >/dev/null 2>&1
  apt-get install -y --no-install-recommends openssh-server >/dev/null 2>&1
fi
mkdir -p /run/sshd
[ -f /root/.ssh/id_rsa ] || ssh-keygen -t rsa -N "" -f /root/.ssh/id_rsa >/dev/null
grep -q "$(cut -d' ' -f2 /root/.ssh/id_rsa.pub)" /root/.ssh/authorized_keys 2>/dev/null || \
  cat /root/.ssh/id_rsa.pub >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
# versioned lib dir + node-daemon-setup wrapper the remote add expects
mkdir -p /usr/local/lib/ganeti
ln -sfn /src/tools /usr/local/lib/ganeti/3.2
if [ ! -x /usr/local/lib/ganeti/node-daemon-setup ]; then
  printf '#!/bin/sh\nexport PYTHONPATH=/src\nexec /usr/bin/python3 /src/tools/node-daemon-setup "$@"\n' \
    > /usr/local/lib/ganeti/node-daemon-setup
  chmod +x /usr/local/lib/ganeti/node-daemon-setup
fi
pgrep -x sshd >/dev/null || /usr/sbin/sshd

# --- start daemons ----------------------------------------------------------
log "starting daemons on all nodes"
(cd "$VCROOT" && ./start-all) || true
sleep 3

log "waiting for wconfd + luxid"
"$VCROOT/$MASTER/cmd" "$PY $BENCH_DIR/bench_driver.py wait"
for _ in $(seq 1 30); do
  "$VCROOT/$MASTER/cmd" gnt-node list >/dev/null 2>&1 && break
  sleep 1
done

# --- add remaining nodes ----------------------------------------------------
current_nodes=$("$VCROOT/$MASTER/cmd" gnt-node list --no-headers -o name 2>/dev/null | wc -l)
if [ "$current_nodes" -lt "$NODECOUNT" ]; then
  log "adding nodes 2..$NODECOUNT"
  for ((i=2; i <= NODECOUNT; i++)); do
    if ! "$VCROOT/$MASTER/cmd" gnt-node list --no-headers -o name 2>/dev/null | grep -qx "node$i"; then
      "$VCROOT/$MASTER/cmd" gnt-node add --no-ssh-key-check "node$i" >/dev/null
      log "  node$i added"
    fi
  done
fi
log "cluster nodes: $("$VCROOT/$MASTER/cmd" gnt-node list --no-headers 2>/dev/null | wc -l)"

# --- migration-capable instances --------------------------------------------
# Ensure a pool of real diskless instances spread across nodes exists for
# migration jobs (offline clones are config-only; jobs need real instances).
ensure_instances() {
  local count=$1
  local have
  have=$("$VCROOT/$MASTER/cmd" "$PY $BENCH_DIR/bench_driver.py instances --prefix real-" 2>/dev/null | wc -l)
  local n=$have
  while [ "$n" -lt "$count" ]; do
    n=$((n + 1))
    local target="node$(( (n % NODECOUNT) + 1 ))"
    "$VCROOT/$MASTER/cmd" gnt-instance add \
      -t diskless --no-install -o noop --no-nics -n "$target" "real-$n" >/dev/null 2>&1 || true
  done
  log "real instances for jobs: $count"
}

# --- benchmark helper -------------------------------------------------------
run_bench() {
  local label=$1
  "$VCROOT/$MASTER/cmd" "$PY $BENCH_DIR/bench_driver.py run --label $label --csv $CSV"
}

# --- baseline + grow + load sweep -------------------------------------------
ensure_instances "$JOB_BATCH"
# one template named bench-* for the offline cloner. The existence check reads
# wconfd, which may still be loading right after a restart, so tolerate an
# already_exists failure from the add as well.
if ! "$VCROOT/$MASTER/cmd" "$PY $BENCH_DIR/bench_driver.py instances --prefix bench-tpl" 2>/dev/null | grep -qx 'bench-tpl'; then
  "$VCROOT/$MASTER/cmd" gnt-instance add \
    -t diskless --no-install -o noop --no-nics -n "$MASTER" bench-tpl >/dev/null 2>&1 || \
    log "bench-tpl add skipped (already present)"
fi

log "baseline: config size $(stat -c%s "$CONFIG") bytes"
run_bench baseline

for tier_kb in "${tiers[@]}"; do
  "$BENCH_DIR/grow_restart.sh" "$tier_kb"
  "$VCROOT/$MASTER/cmd" "$PY $BENCH_DIR/bench_driver.py wait" >/dev/null
  size=$(stat -c%s "$CONFIG")
  log "tier ${tier_kb}KB: size $size bytes, idle benchmark"
  run_bench "${tier_kb}kb-idle"

  log "tier ${tier_kb}KB: submitting $JOB_BATCH concurrent modify jobs"
  "$VCROOT/$MASTER/cmd" "$PY $BENCH_DIR/job_load.py --count $JOB_BATCH --csv $LOADCSV --label ${tier_kb}kb-load"
done

log "done. idle results: $CSV ; load results: $LOADCSV"
echo
echo "== idle latency =="
"$PY" "$BENCH_DIR/format_results.py" "$CSV"
echo
echo "== under job load ($JOB_BATCH concurrent modify jobs) =="
[ -f "$LOADCSV" ] && "$PY" "$BENCH_DIR/format_results.py" --load "$LOADCSV" || echo "no load data"
