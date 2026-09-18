#!/usr/bin/env python3
"""Render a wconfd-bench CSV as an ASCII table for console output.

Pivots the long CSV (one row per method per tier) into one row per tier,
with p50/p95 columns per method, so the scaling trend reads left-to-right.

Usage: format_results.py <csv>
"""

import csv
import sys


def load(path):
  # tiers in insertion order; methods sorted for stable column order
  tiers = []
  methods = []
  # data[tier][method] = (p50, p95, config_bytes)
  data = {}
  with open(path, newline="") as f:
    for row in csv.DictReader(f):
      tier = row["label"]
      method = row["method"]
      if tier not in data:
        data[tier] = {}
        tiers.append(tier)
      if method not in methods:
        methods.append(method)
      data[tier][method] = (float(row["p50_ms"]), float(row["p95_ms"]),
                            int(row["config_bytes"]))
  return tiers, methods, data


def human_size(n):
  if n < 1024:
    return f"{n}B"
  if n < 1024 * 1024:
    return f"{n / 1024:.0f}KB"
  return f"{n / 1024 / 1024:.2f}MB"

def load_phase(path):
  with open(path, newline="") as f:
    rows = list(csv.DictReader(f))
  headers = ["label", "config_bytes", "jobs", "jobs_ok",
             "readconfig_p50_ms", "readconfig_max_ms",
             "lock_p50_ms", "lock_max_ms", "lock_timeouts"]
  # drop columns that are entirely absent (older CSVs)
  headers = [h for h in headers if any(h in r for r in rows)]
  table = [[r.get(h, "-") for h in headers] for r in rows]
  widths = [max(len(headers[i]), *(len(r[i]) for r in table))
            for i in range(len(headers))]

  def fmt(cells):
    return "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)) \
      + " |"

  sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
  print(sep)
  print(fmt(headers))
  print(sep)
  for r in table:
    print(fmt(r))
  print(sep)


def main():
  if len(sys.argv) > 2 and sys.argv[1] == "--load":
    load_phase(sys.argv[2])
    return
  tiers, methods, data = load(sys.argv[1])

  # Build columns: tier, size, then p50/p95 per method.
  headers = ["tier", "size"]
  for m in methods:
    headers += [f"{m} p50", f"{m} p95"]

  rows = []
  for tier in tiers:
    # config_bytes is per-row but identical across methods of a tier
    size = next(iter(data[tier].values()))[2]
    row = [tier, human_size(size)]
    for m in methods:
      if m in data[tier]:
        p50, p95, _ = data[tier][m]
        row += [f"{p50:.1f}", f"{p95:.1f}"]
      else:
        row += ["-", "-"]
    rows.append(row)

  widths = [max(len(headers[i]), *(len(r[i]) for r in rows))
            for i in range(len(headers))]

  def fmt(cells):
    return "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)) \
      + " |"

  sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
  print(sep)
  print(fmt(headers))
  print(sep)
  for r in rows:
    print(fmt(r))
  print(sep)


if __name__ == "__main__":
  main()
