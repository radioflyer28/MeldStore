# Lifecycle and recovery (S04)

S04 adds restricted retirement, durable cleanup jobs, explicit reconciliation,
uncertain-outcome inspection, and cross-process maintenance exclusion. All
metadata relationships remain ordinary SQL. No caching, graph/document APIs,
new payload codecs, or S3 behavior is introduced.

## Upgrade and access boundary

`Store.install_schema(schema)` now installs the additive lifecycle extension for
that schema. Existing S02/S03 deployments can explicitly call
`store.install_lifecycle()` to enroll all registered schemas without rebuilding
their tables. Library DDL drift and missing enrolled guards cause refusal, not
implicit repair. Resolve legacy `pending_delete` rows before upgrading.

Stop all old-version processes and nonparticipating consumers before first S04
adoption. Each storage root must belong to exactly one catalog; verify that
assumption for preexisting roots. S04 binds the root to the catalog's canonical
absolute path. It cannot discover another legacy catalog that previously used
the same root. A catalog may use multiple separate roots.

Every file-backed `Catalog` holds a shared access lease for its lifetime. Every
`Store` also acquires a shared lease for its root, retained until its catalog
closes. Maintenance uses exclusive leases instead. Gates are tiny, separate
SQLite files in rollback journal mode:

- `<catalog-path>.meldstore-access`
- `<storage-root>/meldstore-access.sqlite`

They contain only the owner path. A held read transaction supplies shared access;
`BEGIN EXCLUSIVE` supplies exclusive access. These are coordination databases,
not long-running transactions in the blob metadata catalog. Locks are released
when the process exits. No PID expiry, time-based lease stealing, process-local
mutex, or stale-lock-file deletion is used. The design relies on SQLite's
[shared/exclusive file locking](https://www.sqlite.org/lockingv3.html#locking).

Open conflicts fail immediately with `BusyError`, including an initialization
race. Close conflicting participants and retry explicitly. Sidecar files persist
after close and are **not stale locks to delete**. Do not rename/delete/change
their journal mode while participants exist. Use local filesystems with correct
SQLite locking. Network shares, hard-link aliases, concurrent path relocation,
fork-inherited connections, and live copies of catalogs/roots are unsupported.
Offline relocation and backup coordination belong to S06.

In-memory catalogs remain usable for schema/SQL tests, but a `Store` requires a
file-backed catalog. Connections remain thread-confined. Closing a catalog during
preparation or an active materialized-file context is rejected, keeping its lease
alive. Idle open connections also prevent maintenance: close them first.

## Restricted retirement

```python
record = store.stat(blob_id)
pending = store.delete(blob_id, expected_version=record["version"])
```

This is a **SQL-only operation**, not immediate physical deletion. It checks the
current metadata concurrency version, saves a permanent retirement tombstone and
cleanup job, transitions through `pending_delete`, and removes metadata, object
association, and the live `ms_blobs` identity in one transaction. Actual deletion
of the identity lets application foreign keys enforce their declared restrictions.
Application-owned FKs should use `ON DELETE RESTRICT`; deferred FKs are checked
at commit. Cascades declared by an application retain their ordinary SQL meaning.

The durable pending state lives in `ms_retired`/`ms_gc`, **not** a referenceable
live `ms_blobs` row. This prevents new application references to a retired blob.
Original prepared descriptors remain for audit/resolution. Retired blob IDs and
cleanup keys are never reused; tombstones are retained after physical deletion.
Do not delete these records manually.

`stat`/`find` no longer return the retired record. Existing private materializations
remain usable until their context ends; physical cleanup cannot run while that
catalog's access lease is held. Retirement can also explicitly abandon a complete
`publishing` record, subject to the same FK restrictions. Finish an active metadata
migration before retiring any blob of that logical schema.

Compose application changes in the same transaction:

```python
with catalog.transaction() as tx:
    tx.sql("DELETE FROM app_links WHERE blob_id=?", (blob_id,))
    pending = store.delete(blob_id, expected_version=record["version"], tx=tx)
# Both application changes and retirement committed here.
```

Stale versions raise `ConflictError`, unknown IDs raise `NotFoundError`, and FK
violations raise `ConstraintError` (or `CommitError` for deferred commit failures).
Any failure rolls back the entire transaction. Repeating deletion with the same
ID and retired metadata version returns the existing retirement status. A different
expected version conflicts. Inspect it with `store.deletion_status(blob_id)`;
`state` is `pending` or `done`, and failures retain `last_error`.

## Uncertain commits and prepared uploads

Any `TransactionOutcomeError` quarantines the catalog. `CommitError` identifies
an uncertain commit or cleanup after a confirmed commit; `RollbackError`
identifies rollback failure or cleanup after confirmed rollback. Inspect their
`phase`, `outcome`, `initiating_error`, and `backend_error`, then close, reopen,
and inspect durable state. An uncertain outcome is never evidence that nothing
committed, and MeldStore never reconnects or retries automatically. Returned
values from shared transactions remain provisional until the outer commit
succeeds. If close also encounters resource failures, it still attempts every
lease release and exposes those secondary exceptions in `cleanup_errors`.

For explicit preparation/publication, retain the caller ID and `PreparedFile`:

```python
outcome = store.resolve(
    blob_id, prepared=prepared, schema=dataset,
    metadata={"title": "Expected title"},
)
```

Resolution performs SQL only. It compares the exact token descriptor, schema,
and current metadata when a live blob exists; mismatches raise `ConflictError`.
It never publishes, reuploads, rewrites metadata, or deletes anything.

| Outcome | Meaning and next action |
| --- | --- |
| `ready` | Matching publication committed; returned `record` identifies it |
| `publishing` | Matching finalize committed; compose remaining application SQL and explicit publish |
| `prepared` | Journaled upload is unbound; retry explicit finalize with the same token/ID |
| `retired` | That token/ID was retired; inspect returned retirement status; do not recreate it |
| `discarded` | Token was explicitly revoked for cleanup; it cannot be finalized |
| `unknown` | Token is not journaled; no success/absence-of-upload conclusion follows |

`retired` establishes token/ID identity, not a comparison against deleted metadata.
If metadata has since changed or migrated, exact live resolution conflicts;
inspect current state explicitly. Convenience `import_file` still supports caller-ID
retry by exact schema/metadata/size/digest match. Retired IDs are rejected before
another upload. For uncertain metadata edits, inspect `stat` and compare the
expected version and fields; do not blindly repeat the mutation.

Unbound prepared objects are protected indefinitely, with no age/TTL cleanup.
After deciding an upload will not be finalized, call
`store.discard_prepared(prepared, tx=optional_tx)`. This SQL-only operation records
revocation and cleanup intent; repeated discard is idempotent. Bound or retired
tokens cannot be discarded this way. Resolve uncertain publication first.
Discarded tokens cannot be finalized even if their physical bytes still exist.

## Exclusive reconciliation and cleanup

Close **all** normal catalog connections first, including idle pool connections:

```python
with Catalog("catalog.sqlite", adapter="melddb", maintenance=True) as catalog:
    store = Store(catalog, LocalStorage("objects"))
    report = store.reconcile(verify=True)
    # Review report; select only exact unjournaled object/staging keys to discard.
    if approved_orphan_keys:
        store.queue_orphans(approved_orphan_keys)
    results = store.cleanup(limit=100)
```

All three maintenance operations require exclusive catalog **and** root access.
`reconcile` does not queue or delete objects. It reports ready/publishing records,
protected prepared tokens, pending cleanup, missing/corrupt objects, unjournaled
orphans/staging, unrecognized paths, and catalog errors. `verify=False` uses
inventory and byte sizes; `verify=True` additionally streams XXH3-128 verification
of live/protected payloads. Permission/transport errors fail reporting rather than
being classified as missing. Inspection never exposes corrupt data for use.

Reports have an explicit `max_entries` bound (default 10,000, maximum 1,000,000)
per inventory/catalog result set. Exceeding it raises instead of silently
truncating. Verification is bounded-memory per file but may need substantial
temporary disk/time. Reports are snapshots only while exclusive access remains
held. Original-source temporary directories left by process termination are not
in the object namespace and are not automatically deleted.

`queue_orphans(keys)` accepts 1–1000 distinct exact `objects/<32-lowercase-hex>` or
`staging/<32-lowercase-hex>` keys. It refuses any key in the prepared journal,
whether bound or unbound. Unknown names, identity/lease files, directories,
symlinks, junctions, and traversal paths are not deletion targets. Multipart
remnants with unrecognized names are reported for separate operator inspection.
Listing an object is not authorization to delete it: choose keys explicitly.

`cleanup(limit=1..1000)` processes only durable pending jobs for the configured
root. It rejects inconsistent foreign keys and jobs with live object references.
Object deletion through obstore happens **outside** catalog transactions; each
result is recorded in a short transaction afterward. Missing objects count as
success. Permission/transport/path-safety failures remain pending with an error;
subsequent runs retry them. A process exit after deleting bytes but before recording
success leaves a pending job that safely completes on retry. An uncertain result
commit requires reopening and inspecting the job, just like other commits.

## Standalone SQL consumers

SQLite transactions alone cannot exclude all materialization readers, especially
in WAL mode. A standalone driver must use the same cooperative access gate:

```python
import sqlite3
from meldstore import catalog_access

with catalog_access("catalog.sqlite"):
    with sqlite3.connect("catalog.sqlite") as raw:
        raw.execute("PRAGMA foreign_keys=ON")
        rows = raw.execute("SELECT id FROM ms_blobs WHERE state='ready'").fetchall()
    # Keep the lease until any payload use derived from these rows has finished.
```

Arbitrary SQL drivers or direct filesystem users that ignore this protocol are
not blocked by the sidecar gate. Stop them externally before maintenance. The
contract is cooperative, not an authorization sandbox. Do not bypass guards,
mutate bookkeeping tables, or use low-level storage methods for concurrent
application operations. Ordinary application relationships and joins remain SQL.

## Guarantees and remaining limits

S04 tests abrupt process exits around publication, retirement and cleanup;
lost commit responses before/after commit; FK rollback; cross-process shared and
exclusive access; reader lifetime; protected prepared objects; retryable deletion;
and unchanged payload retrieval. Both SQLite adapters exercise the same behavior.

These are **process-interruption results, not power-loss certification**. Local
obstore promotion, OS buffers, directory-entry persistence, disk/controller
behavior, and backups still determine durability under power failure. Existing
local-file size/XXH3 checks detect corruption but are not adversarial signatures.
No automatic GC, live online maintenance, S3 qualification, backup/restore,
payload codecs, PostgreSQL, or multi-machine service is claimed by this slice.
