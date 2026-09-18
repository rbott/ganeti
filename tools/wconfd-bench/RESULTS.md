# WConfd Performance vs. Config Size

Context: PR #1972 reports wconfd config-lock timeouts on clusters with 4–5MB
`config.data`. This measures how wconfd RPC latency scales with config size.

## Setup

- Ganeti built from source in the `ganeti/ci:trixie-py3` container
  (`--init`, `--privileged`, `nofile=1024`).
- vcluster (`tools/vcluster-setup`): single node for the idle sweep, 8 nodes
  (1 master + 6 master candidates) for the job-load sweep; fake hypervisor,
  diskless instances.
- `config.data` grown **offline** (stop wconfd, clone a template instance,
  restart wconfd/luxid) — reaches 5MB in seconds and distributes instances
  across node UUIDs.
- Latency measured directly at the wconfd unix socket: `Echo`, `ReadConfig`,
  `TryUpdateLocks` (shared BGL acquire + release).
- Load phase: 8 concurrent `gnt-instance modify -B memory=N` jobs (same
  exclusive-config-lock + full-serialize path as `INSTANCE_MIGRATE` /
  `INSTANCE_REPLACE_DISKS`), sampling wconfd latency while the queue drains.

Numbers are relative (container CPU scheduling); the signal is the trend.

## Idle latency vs. config size

| Config | Instances | Echo p50 | ReadConfig p50 | ReadConfig p95 | TryUpdateLocks p50 |
|---|---|---|---|---|---|
| 7.8 KB | 0 | 0.1 ms | 0.2 ms | 2.0 ms | 1.9 ms |
| 102 KB | 223 | 0.2 ms | 2.8 ms | 3.6 ms | 2.2 ms |
| 335 KB | 833 | 0.1 ms | 10.2 ms | 11.2 ms | 1.8 ms |
| 532 KB | 1133 | 0.1 ms | 17.5 ms | 18.9 ms | 1.7 ms |
| 1.0 MB | 2233 | 0.1 ms | 46.1 ms | 55.8 ms | 1.7 ms |
| 2.0 MB | 4433 | 0.1 ms | 154.7 ms | 155.5 ms | 1.7 ms |
| 3.0 MB | 6533 | 0.0 ms | 317.9 ms | 328.0 ms | 1.7 ms |
| 5.0 MB | 10933 | 0.2 ms | 882.0 ms | 899.2 ms | 1.9 ms |

`ReadConfig` is **superlinear** (per-100KB cost grows ~50x across the sweep);
`Echo` and `TryUpdateLocks` stay flat — the config-lock structure is O(1), the
cost is reading/serializing the config via `Text.JSON`.

## Under job load (8 concurrent modify jobs, 8-node vcluster)

| Config | ReadConfig p50 idle→load | ReadConfig max load | lock max load | lock timeouts |
|---|---|---|---|---|
| 2 MB | 148.6 → 177.5 ms | 516.0 ms | 753.6 ms | 0 |
| 3 MB | 318.0 → 722.2 ms | 2070.4 ms | 1526.3 ms | 0 |
| 5 MB | 886.6 → 2114.7 ms | 3779.6 ms | 1364.3 ms | 0 |

Under load, `ReadConfig` reaches **3.8s at 5MB** (4x idle) and lock latency
crosses **1.5s at 3MB**. No lock timeouts at 8 concurrent jobs, but the trend
is steep — higher concurrency or larger configs would plausibly breach the 30s
retry budget from PR #1972.

## aeson build (config (de)serialisation via `Data.Aeson`)

wconfd's config load (`loadConfig`/`parseConfig`) and save
(`encodeConfig`/`saveConfig`) were switched from `Text.JSON` to aeson
(`src/Ganeti/JSON/Aeson.hs`, instances generated in `src/Ganeti/THH.hs`).
Aeson output is drop-in: object key order differs, content is identical
(verified: canonical decode/encode equality holds on a 4.9MB config).

Config decode/encode in isolation (4.9MB `config.data`, both forced with the
same canonical traversal):

| Operation | Text.JSON | aeson | speedup |
|---|---|---|---|
| decode (startup + writeConfig parse) | ~680 ms | ~510 ms | ~1.3–1.4x |

Idle `ReadConfig` is **unchanged** (5MB: 882 → 878 ms): the idle round-trip is
dominated by the unix-socket transport (Python 4 KiB reads + Haskell `String`
UTF-8 I/O), not the JSON encoder.

Under job load the picture improves — repeated `writeConfig` →
serialize+flush during the modify jobs is where aeson pays off:

| Config | ReadConfig p50 idle→load | ReadConfig max load | lock max load | lock timeouts |
|---|---|---|---|---|
| 2 MB | 149.5 → 177.8 ms | 884.0 ms | 554.6 ms | 0 |
| 3 MB | 317.2 → 397.0 ms | 1074.3 ms | 903.9 ms | 0 |
| 5 MB | 878.3 → 1342.5 ms | 2536.3 ms | 1271.5 ms | 0 |

Load-phase `ReadConfig` p50 dropped **722 → 397 ms at 3MB (~1.8x)** and
**2115 → 1343 ms at 5MB (~1.6x)**; worst-case lock latency fell correspondingly
(2070 → 1074 ms at 3MB, 3780 → 2536 ms at 5MB). The superlinear idle trend is
unchanged — it is transport-bound — but contention under concurrent writers is
roughly halved.

## Notes

- Container must run with `--init` (tini): without a reaping PID 1, daemons
  become zombies under load and all wconfd/luxid calls hang.
- luxid's query path is a separate scaling wall: `gnt-instance list` /
  `gnt-job list` exceed the 30s client timeout at ~1MB+, while wconfd answers
  `ReadConfig` in ~46ms at 1MB. Instance names are read directly from wconfd
  to avoid masking.
- Harness: `tools/wconfd-bench/` (`bench.sh` single-node, `bench-multinode.sh`
  8-node + job load).
