# S06a verification: SQLite tuning and concurrency

2026-09-16. Implemented on `codex/s06a`; local final verification and remote CI
status are recorded below. Main is not updated by this slice.

## Changes and behavioral evidence

- Both adapters apply and verify explicit WAL/DELETE policy on create, existing
  catalogs and restored copies. FULL synchronization and FK enforcement remain.
- Read snapshots allow same-process and cross-process writers to commit; Store
  stat/find/get observe committed metadata during another connection's write.
- Read transactions reject writes, writable CTEs and configuration changes,
  including EXPLAIN/PRAGMA preparation-time effects. A caught error still poisons
  its transaction. Existing shared application write transactions are unchanged.
- Access gates retain DELETE mode and block maintenance while participants are
  open. Backup artifacts stay standalone; restored live copies adopt WAL and
  preserve the explicitly installed cleanup index.
- The index upgrade is transactional/idempotent, preserves application SQL
  objects, and rejects conflicting names rather than replacing objects.
- Statistics maintenance requires explicit exclusive access, refreshes planner
  statistics, and reports checkpoint results. A pinned raw-driver snapshot
  prevents truncation; releasing it permits truncation. The WAL grows while
  pinned, so default automatic checkpoints are not a hard size cap.
- WAL opens reject runtimes without the upstream WAL-reset fix before creating
  files. Version-guard tests cover vulnerable versions and documented backports.
  Concurrency tests do not reproduce or certify absence of upstream timing bugs.

The local initial runtime was Python 3.12.10 / SQLite 3.49.1. After checking the
[SQLite advisory](https://www.sqlite.org/wal.html#walreset), final qualification
uses installed Python 3.12.13 / SQLite 3.53.1 in an isolated environment, with the
same locked dependencies and all format extras. Python version alone does not
establish a patched SQLite library. MeldDB remains pinned at `8970099`.

## Reproducible metadata experiment

Run `uv run --frozen python tools/sqlite_benchmark.py` with a patched runtime.
Each query sample is one SQL call plus its explicit read transaction; 25 samples
per query. Synthetic SQL fixtures use 50,000 rows, not physical payloads.
Queue history contains 50 pending jobs (0.1%); query limit is 25. Metadata has
100 equally populated source groups; filtered and keyset limits are 25. Migration
measurement resumes after ID 40,000 with a 100-row limit. No ANALYZE is run in
the benchmark. Timings are illustrative warm local medians, not release targets.

| Measurement (ms) | sqlite3 | MeldDB |
| --- | ---: | ---: |
| Pending queue before index | 1.9752 | 1.6108 |
| Pending queue after index | 0.0603 | 0.0588 |
| Index upgrade/build | 7.823 | 12.142 |
| 1,000 inserts + retirement + rollback before index | 2.4656 | 2.3656 |
| Same write probe after index | 3.7808 | 4.0244 |
| Resumed migration nullable-OR query | 1.9485 | 1.7324 |
| Resumed migration direct ID boundary | 0.2002 | 0.1443 |
| Declared source/time-style composite filter | 0.0652 | 0.0719 |
| ID keyset page | 0.0466 | 0.0501 |
| Application reference join | 0.0402 | 0.0155 |
| Application child-FK lookup | 0.0130 | 0.0147 |

The pending index consumes one additional used database page on this workload.
The write probe repeats ten rolled-back batches: it measures statement/index
maintenance and rollback cost, **not durable commit throughput**. Real costs
depend on pending-job proportion, disk, concurrency and checkpoint timing.

Plans change as follows:

- Queue: primary-key search on storage_id that filters completed jobs becomes
  search on `ms_gc_pending`, which contains only pending jobs.
- Migration: full primary-key index scan becomes an `id>?` index range search.
  No new migration index is necessary for this fixture.
- Metadata filters use the declared composite index and indexed blob lookup.
  The ID tie-break still uses a temporary partial sort; it is not claimed fixed.
- Keyset pagination uses the blob ID range index and metadata primary key.
- Application joins use their declared primary/child indexes; FK lookup uses the
  application-owned covering index. MeldStore does not install domain indexes.

The experiment excludes Store.find's per-record descriptor SQL and payload I/O.
It is not a 10–30 GB consumer benchmark. Sparse schema-version populations and
different ordering/selectivity can need different indexes; defer those choices
to measured S08 workloads. Cache/mmap and automatic checkpoint thresholds were
not changed. No NoSQL, graph, caching policy or dependency upgrade was introduced.

## Verification commands and qualification status

Commands use the existing lockfile (`--frozen`) and the isolated patched-runtime
environment, with all extras installed:

```console
uv run --frozen --no-sync --extra test pytest -q
uv run --frozen --no-sync --extra test ruff check .
uv run --frozen --no-sync python tools/sqlite_benchmark.py
uv build
uv run --frozen --no-sync python tools/package_smoke.py
uv run --frozen --no-sync python tools/package_smoke.py --formats
```

Final local suite: **392 passed, 4 skipped** on Windows Python 3.12.13 / SQLite
3.53.1. Ruff passed. Both wheel/sdist builds and clean core/all-format installs
passed. CI qualification is pending. Four local Windows symlink
permission skips must be covered in CI. Wheel/sdist clean
installs exercise both adapters, index installation, SQLite maintenance, existing
lifecycle/migrations, backup/restore and all optional formats.

S07 real S3, S08 integrated consumer performance, macOS/PostgreSQL, licensing and
package publication remain unqualified. Process-interruption tests are not
power-loss certification. No benchmark here expands those qualification claims.
