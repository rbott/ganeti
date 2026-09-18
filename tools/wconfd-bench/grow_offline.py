#!/usr/bin/env python3
"""Grow config.data offline by cloning a template instance N times.

Usage: grow_offline.py <config.data> <target-kb>

Run with wconfd STOPPED. Clones the first bench-* instance (created via
`gnt-instance add` as a template) into new entries with fresh UUID/name,
bumping serial_no and mtime. After growth, wconfd is restarted and re-reads
the larger config.

Cloned instances are spread round-robin over the cluster's node UUIDs as
their primary_node, so on a multi-node vcluster the clones are distributed
and migrations/failovers have valid target nodes.
"""

import copy
import json
import sys
import time
import uuid


def main():
  path, target_kb = sys.argv[1], int(sys.argv[2])
  target = target_kb * 1024

  with open(path) as f:
    data = json.load(f)

  insts = data["instances"]
  nodes = list(data["nodes"].keys())
  template = None
  for key, inst in insts.items():
    if inst["name"].startswith("bench-"):
      template = inst
      break
  if template is None:
    template = next(iter(insts.values()))

  size = len(json.dumps(data))
  added = 0
  while size < target:
    new = copy.deepcopy(template)
    new["uuid"] = str(uuid.uuid4())
    new["name"] = f"bench-clone-{added}"
    if nodes:
      new["primary_node"] = nodes[added % len(nodes)]
    new["serial_no"] = data["serial_no"] + 1
    new["ctime"] = new["mtime"] = time.time()
    insts[new["uuid"]] = new
    added += 1
    if added % 100 == 0:
      size = len(json.dumps(data))
  size = len(json.dumps(data))

  data["serial_no"] += added
  data["mtime"] = time.time()

  with open(path, "w") as f:
    json.dump(data, f)
  print(f"added {added} clones over {len(nodes)} node(s) -> {size} bytes")


if __name__ == "__main__":
  main()
