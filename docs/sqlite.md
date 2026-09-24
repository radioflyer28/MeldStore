# SQLite settings, concurrency, and tuning

Both catalog adapters expose the same live-catalog policy through independent
public seams. The MeldDB adapter passes file journal selection to `melddb.open`
and validates `sqlite_runtime()`; the direct adapter configures and verifies the
standard-library connection itself. MeldStore does not reach into MeldDB private
connection objects.

## Opening and upgrading catalogs

`Catalog(path, journal_mode="wal", timeout=5.0)` selects WAL for new and existing
file-backed catalogs, including restored copies. Opening can therefore change
the file's journal mode, but does not install tables or indexes. Close other
participants before changing modes. A mode conflict raises an error, commonly
BusyError; there is no silent fallback. The effective live mode is checked.
Use `journal_mode="delete"` explicitly for rollback journaling. In-memory
catalogs retain SQLite's memory journal mode.

WAL requires a runtime containing SQLite's WAL-reset fix: 3.51.3 or later, or
the 3.50.7/3.44.6 backports on their respective release branches. Unpatched
file-backed WAL opens fail before creating files. Check
`sqlite3.sqlite_version`, not just the Python version. Other MeldDB runtime
requirements still apply; accepting a WAL backport does not qualify every old
SQLite release for MeldDB. See the [upstream WAL advisory](https://www.sqlite.org/wal.html#walreset).

Use a local filesystem with working SQLite locks. WAL is not a network-filesystem
deployment option; rollback mode is not a blanket qualification of network
filesystems either. S3 payloads do not make the SQLite catalog itself remote.

Both adapters keep foreign keys ON and synchronous FULL. The lock timeout is
configurable; writes are never automatically retried. Cache size, mmap and
automatic checkpoint thresholds remain SQLite defaults. The separate catalog
and storage-root access-gate databases retain DELETE journaling because their
maintenance-exclusion protocol depends on rollback locks.

Detached backups remain standalone DELETE-mode files. Open the restored catalog,
not the backup artifact, to apply the live policy. Use the backup API rather than
copying an open database without its WAL state.

## Explicit read transactions

```python
with catalog.transaction(write=False) as tx:
    rows = tx.sql("SELECT id FROM ms_blobs WHERE state=? LIMIT ?", ("ready", 100))

rows = catalog.sql("SELECT id FROM ms_blobs LIMIT 100", write=False)
```

Read transactions use deferred BEGIN and engine-enforced query-only state. A
top-level read-only CTE is accepted; INSERT, DDL, WITH-prefixed writes and
trigger-mediated persistent writes fail in the database engine and poison the
transaction even if the caller catches the error. Transaction/configuration SQL
is still rejected before execution. EXPLAIN remains restricted to SELECT because
some PRAGMAs take effect during preparation even when prefixed by EXPLAIN. This
is a cooperative SQL interface, not a sandbox for arbitrary Python UDF side
effects or activity on unrelated connections.

The snapshot begins with the first database read, not merely entering the context.
In WAL mode, another connection can commit while that snapshot stays consistent.
There is still only one writer at a time. Read handles cannot be promoted to
writers. Keep snapshots short: a long-lived reader can prevent checkpoint progress
and let the WAL grow.

`stat`, `find`, migration/deletion status and uncertain-outcome inspection now use
read transactions. Payload retrieval uses those metadata reads without retaining
a SQL transaction during file I/O. General `catalog.sql()` and `transaction()`
remain writable by default, preserving application transaction composition.
There is no SQL classification that silently changes that default.

## Explicit index upgrade

After installing the schema/lifecycle extension on a new or existing store:

```python
added = store.install_query_indexes()
```

This installs `ms_gc_pending`, a partial `(storage_id, object_key)` index for
pending cleanup jobs. It avoids walking retained completed jobs to fill a batch.
The operation is transactional and idempotent, returns the names added, and
rejects an existing same-name object with a different definition. It does not
rebuild tables, alter application objects, or run ANALYZE. Normal operations
remain compatible with catalogs that have not opted into this performance-only
upgrade. Backup/restore preserves the index.

User-declared composite/unique metadata indexes remain the source of domain
query indexes. Applications must index their own child foreign-key columns and
join/filter paths where useful; SQLite does not automatically create every child
FK index. Resumed migration batches now seek by ID instead of using a nullable
OR predicate. This uses the existing primary-key index with no schema upgrade.

## Planner statistics and checkpoints

Close participants, then open a separate `Catalog(..., maintenance=True)`:

```python
result = catalog.maintain_sqlite()  # PRAGMA optimize, then PASSIVE checkpoint
result = catalog.maintain_sqlite(analyze=True, checkpoint="truncate")
```

Maintenance requires an idle exclusive file-backed catalog, with no active
payload readers. `analyze=True` explicitly opts into full ANALYZE; the default
uses SQLite optimization. The MeldDB adapter delegates this work to
`maintain_sqlite`; the direct adapter uses its own control connection and reloads
planner statistics on the application connection. Neither runs on ordinary
reads, writes or close. Full ANALYZE can be expensive and changes query plans.

The checkpoint result contains `busy`, `log_frames` and `checkpointed_frames`.
PASSIVE can make partial progress despite busy=0; compare frame counts.
TRUNCATE may return busy=1 if a noncooperative external connection still holds
a snapshot. Do not treat that as successful truncation. In non-WAL mode SQLite
reports -1 frame counts. Cooperative gates cannot fence arbitrary raw drivers.
See [SQLite checkpoint guidance](https://www.sqlite.org/wal.html#ckpt) and
[PRAGMA optimize](https://www.sqlite.org/pragma.html#pragma_optimize).

## Evidence and limits

The [S06a verification record](s06a-verification.md) reports adapter tests and a
deterministic 50,000-row metadata experiment. Run `uv run --frozen python
tools/sqlite_benchmark.py` to reproduce query plans, timings and index cost on
your runtime. These are metadata-only measurements, not blob throughput targets.
The experiment does not time Store.find's per-record descriptor lookups; those
remain a potential optimization for S08. Filter ordering may still require a
temporary tie-break sort. S08 must measure actual consumer workloads before
adding more indexes or claiming end-to-end performance.
