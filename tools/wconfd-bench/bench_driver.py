#!/usr/bin/env python3
"""wconfd-bench driver: measure wconfd RPC latency.

Runs inside the vcluster node context (GANETI_ROOTDIR/GANETI_HOSTNAME set),
so ganeti.pathutils already points at the node's sockets and config.data.

Subcommands:
  wait                       block until wconfd answers Echo (<=120s)
  run --label L --csv F      warmup + measured rounds, append CSV rows

Methods measured:
  Echo            trivial RPC round-trip baseline
  ReadConfig      full config serialization out of wconfd
  TryUpdateLocks  shared-lock request/release round-trip (config-lock proxy)

The client connects to the wconfd unix socket, which requires being on the
master node and running as the masterd user/group; hence this script is
invoked via the node's `cmd` wrapper as root.
"""

import argparse
import csv
import os
import statistics
import sys
import time

sys.path.insert(0, "/src/lib")

from ganeti import pathutils  # noqa: E402
from ganeti import wconfd  # noqa: E402
from ganeti.utils import livelock  # noqa: E402

ROUNDS = 10
METHODS = ("Echo", "ReadConfig", "TryUpdateLocks")

CSV_FIELDS = [
  "ts", "label", "config_bytes", "method",
  "p50_ms", "p95_ms", "min_ms", "max_ms", "rounds",
]


def config_size():
  try:
    return os.path.getsize(pathutils.CLUSTER_CONF_FILE)
  except OSError:
    return -1


def call(client, method):
  t0 = time.perf_counter()
  if method == "Echo":
    client.Echo("bench")
  elif method == "ReadConfig":
    client.ReadConfig()
  elif method == "TryUpdateLocks":
    ll = livelock.LiveLock("wconfd-bench")
    ctx = (0, ll.GetPath(), os.getpid())
    client.TryUpdateLocks(ctx, [["cluster/BGL", "shared"]])
    client.TryUpdateLocks(ctx, [["cluster/BGL", "release"]])
  else:
    raise ValueError(method)
  return (time.perf_counter() - t0) * 1000.0


def pct(samples, p):
  s = sorted(samples)
  k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
  return s[k]


def cmd_wait():
  deadline = time.time() + 120
  while time.time() < deadline:
    try:
      c = wconfd.Client()
      if c.Echo("probe") == "probe":
        print("wconfd ready")
        return 0
    except Exception:
      pass
    time.sleep(2)
  print("wconfd did not become ready", file=sys.stderr)
  return 1


def cmd_run(label, csv_path):
  client = wconfd.Client()
  size = config_size()
  rows = []
  for method in METHODS:
    call(client, method)  # warmup
    samples = [call(client, method) for _ in range(ROUNDS)]
    rows.append({
      "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
      "label": label,
      "config_bytes": size,
      "method": method,
      "p50_ms": round(statistics.median(samples), 1),
      "p95_ms": round(pct(samples, 95), 1),
      "min_ms": round(min(samples), 1),
      "max_ms": round(max(samples), 1),
      "rounds": len(samples),
    })
    r = rows[-1]
    print(f"{method:16s} p50={r['p50_ms']:>8}ms p95={r['p95_ms']:>8}ms "
          f"min={r['min_ms']:>8}ms max={r['max_ms']:>8}ms")

  os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
  exists = os.path.exists(csv_path)
  with open(csv_path, "a", newline="") as f:
    w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
    if not exists:
      w.writeheader()
    w.writerows(rows)
  return 0


def cmd_instances(prefix):
  """Print instance names (optionally filtered by prefix) from wconfd.

  Bypasses luxid's query engine, which is slow at large config sizes and
  would mask/hang the benchmark orchestration.
  """
  client = wconfd.Client()
  cfg = client.ReadConfig()
  for inst in cfg["instances"].values():
    name = inst["name"]
    if not prefix or name.startswith(prefix):
      print(name)
  return 0

def main():
  ap = argparse.ArgumentParser()
  sub = ap.add_subparsers(dest="cmd", required=True)
  sub.add_parser("wait")
  runp = sub.add_parser("run")
  runp.add_argument("--label", required=True)
  runp.add_argument("--csv", required=True)
  instp = sub.add_parser("instances")
  instp.add_argument("--prefix", default="")
  args = ap.parse_args()

  if args.cmd == "wait":
    return cmd_wait()
  if args.cmd == "instances":
    return cmd_instances(args.prefix)
  return cmd_run(args.label, args.csv)


if __name__ == "__main__":
  sys.exit(main())
