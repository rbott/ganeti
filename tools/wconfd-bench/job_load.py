#!/usr/bin/env python3
"""Drive a concurrent config-modifying job load and measure wconfd latency.

Submits N concurrent `gnt-instance modify` jobs (each toggles an instance
tag, forcing an exclusive wconfd config lock + full config serialize/write —
the same path INSTANCE_MIGRATE / REPLACE_DISKS use). While the queue drains,
measures wconfd ReadConfig / TryUpdateLocks latency from the master and
counts lock acquisitions exceeding the 30s retry budget from PR #1972.

Usage (on the master node, via node1/cmd):
  job_load.py --count N --csv FILE --label L
"""

import argparse
import csv
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, "/src/lib")

from ganeti import pathutils  # noqa: E402
from ganeti import wconfd  # noqa: E402
from ganeti.utils import livelock  # noqa: E402

LOCK_TIMEOUT_S = 30.0
DRAIN_TIMEOUT_S = 300


def sh(args):
  return subprocess.run(args, capture_output=True, text=True)


def real_instances(count):
  # Read names straight from wconfd: `gnt-instance list` goes through luxid's
  # query engine, which is itself slow at large config sizes and would mask
  # the wconfd numbers (and can hang on config-only clones).
  client = wconfd.Client()
  cfg = client.ReadConfig()
  names = [i["name"] for i in cfg["instances"].values()
           if i["name"].startswith("real-")]
  return sorted(names)[:count]


def submit_modify(name, i):
  t0 = time.perf_counter()
  # -B memory=<n>MiB forces an exclusive wconfd config lock + full config
  # serialize/write, the same path INSTANCE_MIGRATE / REPLACE_DISKS take.
  mem = 128 + (i % 8)
  try:
    r = subprocess.run(["gnt-instance", "modify", "--submit",
                        "-B", f"memory={mem}", name],
                       capture_output=True, text=True, timeout=60)
    rc, out = r.returncode, (r.stdout + r.stderr).strip()
  except subprocess.TimeoutExpired:
    rc, out = 1, "submit timed out (luxid slow)"
  dt = time.perf_counter() - t0
  return (name, rc == 0, dt, out)


def measure_during_load(stop_evt, samples):
  client = wconfd.Client()
  while not stop_evt.is_set():
    t0 = time.perf_counter()
    try:
      client.ReadConfig()
      rc_ms = (time.perf_counter() - t0) * 1000.0
    except Exception:
      rc_ms = -1.0
    t0 = time.perf_counter()
    lock_ms = -1.0
    lock_failed = False
    try:
      ll = livelock.LiveLock("wconfd-bench-load")
      ctx = (0, ll.GetPath(), os.getpid())
      client.TryUpdateLocks(ctx, [["cluster/BGL", "shared"]])
      client.TryUpdateLocks(ctx, [["cluster/BGL", "release"]])
      lock_ms = (time.perf_counter() - t0) * 1000.0
    except Exception:
      lock_failed = True
    samples.append((rc_ms, lock_ms, lock_failed))
    time.sleep(0.05)


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--count", type=int, default=8)
  ap.add_argument("--csv", required=True)
  ap.add_argument("--label", required=True)
  args = ap.parse_args()

  insts = real_instances(args.count)
  if not insts:
    print("no real-* instances; aborting load phase", file=sys.stderr)
    return 1

  stop_evt = threading.Event()
  samples = []
  sampler = threading.Thread(target=measure_during_load,
                             args=(stop_evt, samples), daemon=True)
  sampler.start()

  results = []
  threads = []

  def worker(name, i):
    results.append(submit_modify(name, i))

  for i, name in enumerate(insts):
    t = threading.Thread(target=worker, args=(name, i))
    threads.append(t)
    t.start()
  for t in threads:
    t.join()

  # Drain the job queue while still sampling. `gnt-job list` goes through
  # luxid's query engine, which is itself slow at large config sizes; give it
  # a per-call timeout and bound the whole drain so a wedged luxid can't hang
  # the phase (the sampler already captured the under-load numbers).
  deadline = time.time() + DRAIN_TIMEOUT_S
  while time.time() < deadline:
    try:
      out = subprocess.run(["gnt-job", "list", "--no-headers", "-o", "status"],
                           capture_output=True, text=True, timeout=20)
      busy = [s for s in out.stdout.split() if s in ("running", "waiting")]
      if not busy:
        break
    except subprocess.TimeoutExpired:
      pass  # luxid query wedged; keep sampling, don't block the phase
    time.sleep(5)
  stop_evt.set()
  sampler.join(timeout=5)

  ok_jobs = sum(1 for r in results if r[1])
  try:
    config_bytes = os.path.getsize(pathutils.CLUSTER_CONF_FILE)
  except OSError:
    config_bytes = 0

  rc = [s[0] for s in samples if s[0] >= 0]
  lk = [s[1] for s in samples if s[1] >= 0]
  timeouts = sum(1 for s in samples
                 if s[2] or s[1] / 1000.0 > LOCK_TIMEOUT_S)

  def med(v):
    return sorted(v)[len(v) // 2] if v else -1.0

  def mx(v):
    return max(v) if v else -1.0

  row = {
    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "label": args.label,
    "config_bytes": config_bytes,
    "jobs": len(results),
    "jobs_ok": ok_jobs,
    "readconfig_p50_ms": round(med(rc), 1),
    "readconfig_max_ms": round(mx(rc), 1),
    "lock_p50_ms": round(med(lk), 1),
    "lock_max_ms": round(mx(lk), 1),
    "lock_timeouts": timeouts,
  }

  fields = list(row.keys())
  exists = os.path.exists(args.csv)
  os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
  with open(args.csv, "a", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    if not exists:
      w.writeheader()
    w.writerow(row)

  print(f"jobs: {ok_jobs}/{len(results)} ok; "
        f"ReadConfig p50={row['readconfig_p50_ms']}ms "
        f"max={row['readconfig_max_ms']}ms; "
        f"lock p50={row['lock_p50_ms']}ms max={row['lock_max_ms']}ms; "
        f"lock_timeouts={timeouts}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
