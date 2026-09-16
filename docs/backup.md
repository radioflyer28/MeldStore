# Offline backup, restore, and local transfer (S06)

S06 copies the **whole shared SQLite catalog** and all referenced ready payloads.
Application tables, foreign keys, indexes, views, triggers, migration records,
MeldStore schema/concurrency versions, encoding descriptors, and retirement
history are included. This is not MeldDB's managed-data export, which does not
represent application-owned SQL tables.

## Backup

Close all application connections, materialized-file readers and direct SQL
participants first. Open a new exclusive maintenance session:

```python
from meldstore import Catalog, LocalStorage, Store, restore_backup

with Catalog("catalog.sqlite", maintenance=True) as catalog:
    store = Store(catalog, LocalStorage("objects"))
    manifest = store.backup(
        "backups/run-001",  # parent exists; this directory must not exist
        application={
            "id": "my-dataset-app",
            "schema_revision": "app-migration-12",
            "migration_package": "my-app==1.4",
        },
    )
```

Both `adapter="melddb"` and `adapter="sqlite"` produce the same portable
artifact. Exclusive access uses the S04 catalog/root leases for the entire
operation. All consumers must honor that cooperative access protocol; arbitrary
raw SQLite or filesystem writers are not fenced by these leases.

Backup requires a stable lifecycle: no unfinished publishing rows, unassociated
prepared tokens, pending cleanup jobs, or running metadata migrations. Resolve
or explicitly discard unfinished tokens, complete queued cleanup, and finish
migrations first. Backup never performs those actions on the caller's behalf.
Completed deletion tombstones and encoding descriptors are retained. S06 supports
one logical storage root per catalog, including retained historical descriptors;
multi-root catalogs are rejected rather than partially backed up.

The operation snapshots SQLite through its physical backup API, checks catalog
integrity/FKs/library schema, creates a SQL export, then copies objects through
obstore with whole-object XXH3-128 verification on **both** source and destination.
No payload storage I/O runs inside a long catalog SQL transaction. The physical
copy uses [SQLite's backup API](https://www.sqlite.org/backup.html), not a plain
copy of a live `.db` file that could omit committed WAL data.

## Artifact format 1

```text
run-001/
  manifest.json                 completion record, written last
  catalog.sqlite                standalone physical snapshot, no WAL dependency
  catalog.sql                   logical SQLite SQL export
  storage/
    meldstore-storage.json      original logical root identity
    objects/<immutable-key>     exact stored payload bytes
```

The manifest records format/version, UTC creation time, `xxh3_128`, storage UUID,
application declaration, byte sizes/digests of the catalog, SQL export and root
identity, plus each ready blob's ID, prepared token, relative object key, byte size
and digest. Descriptor paths are fixed or validated MeldStore keys. No arbitrary
manifest paths are followed. The manifest is bounded by `max_entries` (default
10,000, explicitly adjustable up to 1,000,000); duplicate keys and unsupported
formats are rejected. Catalog and manifest object lists must agree exactly.

Only catalog-referenced ready payloads are copied. Staging objects, unreferenced
orphans, already-deleted payload bytes, external application files, credentials,
and live access-gate files are not copied. Unknown files in an artifact are not
restored. Preserve the entire completed artifact; do not edit it or open its
catalog/root as a working store while backing up or restoring.

## Restore

```python
result = restore_backup("backups/run-001", "restored/run-001")
# The parent restored/ must exist; restored/run-001 must not exist.
assert result["application"]["schema_revision"] == "app-migration-12"

with Catalog(result["catalog"], adapter="sqlite") as catalog:
    store = Store(catalog, LocalStorage(result["storage"]))
    # Use normal stat/find/get/materialize and application SQL.
```

Restore validates manifest structure, catalog/SQL/identity hashes, SQLite integrity
and foreign keys, library schema, and the exact object inventory. Every payload
is verified before and after copying. It copies the catalog to a private partial
file, verifies its hash, then atomically creates `catalog.sqlite` using a
create-only hard link **after all objects are complete**. Access gates prevent
cooperating readers from opening the destination during restoration. Gate owners
are newly bound to the destination catalog path; source gate files are not reused.
The temporary link is removed after publication.

Blob IDs, metadata versions, prepared tokens, relative keys, hashes, encoding
versions, and the logical storage UUID remain unchanged. This is a new physical
copy of the same logical store, not an object rewrite or a new identity assignment.
Re-register any custom codecs/validators in the application process as usual;
backup never serializes Python executable code. `stat`/`materialize` do not require
optional format dependencies, and `get` still requires its exact registered codec.

Only trusted backups should be opened: XXH3 detects accidental corruption, not
malicious replacement of both data and manifest. SQLite and format decoding are
not a sandbox. Backups may contain all application data; secure them accordingly.

## Application-owned schema and SQL portability

The required `application` object has nonempty `id` and `schema_revision` strings
and may include ordinary finite JSON details (at most 64 KiB). The application
supplies its schema/migration package version and requirements for external data,
custom codecs or runtime functions. MeldStore cannot infer these semantics.
The declaration is informational: application startup must check compatibility
and install its own code/dependencies; restore does not run application migrations.

`catalog.sql` includes ordinary library **and application** table DDL/data, then
indexes, views and triggers. It can be imported into a fresh SQLite database using
the SQLite CLI or Python's sqlite3 without MeldDB/MeldStore. Inserts run before
triggers are created to avoid repeating application side effects. Foreign keys
are temporarily disabled in the dump to permit cyclic references and enabled at
the end; consumers should run `PRAGMA foreign_key_check` after manual import.
Normal `restore_backup` uses the verified physical snapshot, not script execution.

SQL export preserves NULL/integer/real/text/blob values, including embedded NUL
text, binary data, generated-column definitions, accessible rowids, WITHOUT ROWID
tables, and AUTOINCREMENT sequence state. It omits temporary objects, SQLite
optimizer statistics and connection settings. Physical restore also preserves
database header settings such as `user_version`; SQL consumers must restore any
application-required PRAGMAs explicitly. Virtual tables/extensions and attached
databases are outside this export contract. Unknown required SQL functions or
collations can cause validation failure; they are not silently replaced.

This is **SQLite portability outside MeldDB**, not a claim that SQLite-specific
DDL runs unchanged in PostgreSQL. A future cross-engine migration must translate
application and library DDL together. No document/graph model is involved.

## Offline local transfer

```python
with Catalog("catalog.sqlite", maintenance=True) as catalog:
    store = Store(catalog, LocalStorage("objects"))
    destination = store.transfer(
        "relocated/store-001",
        application={"id": "my-dataset-app", "schema_revision": "app-migration-12"},
    )
# Close the source, then explicitly change application configuration to the
# returned destination catalog and storage paths. Keep the source for rollback.
```

Transfer is backup plus restore through a private temporary artifact. It is
non-destructive: it neither deletes the source nor switches application settings.
Catalog locations contain relative keys plus a logical storage UUID, so no live
row mutation is needed for a verified local relocation. Do not continue writing
independently to both copies and expect synchronization. Local-to-S3 transfer and
S3 identity/conditional-write qualification remain S07 work.

## Failure, space and durability limits

Destinations must be fresh, with existing parents. Existing directories—even
empty ones—are refused; overlapping paths, symlinks and junctions are rejected.
There is no overwrite, automatic in-place restore, automatic source deletion, or
resumption into a partially created directory. A failed operation leaves its
partial destination for inspection; retry with a new destination. Remove an old
partial directory only after independently confirming it is the failed output.

Interrupted backup has no complete `manifest.json`. Interrupted restore before
publication has no final `catalog.sqlite`. An interruption after final publication
can leave a complete restore despite a lost response; inspect and validate it
instead of overwriting it. Process-exit tests establish these boundaries, not
power-loss certification. Local filesystem/obstore durability, directory-entry
persistence and storage hardware still matter. Atomic publication requires local
filesystem hard-link support. Network shares and hostile concurrent path changes
are unqualified.

Budget room for the catalog, SQL export, copied payloads and one object's temporary
materialization at a time. Transfer additionally holds a complete temporary backup.
Payload copies/hashes use bounded buffers; the manifest/object list is bounded by
`max_entries`, and logical SQL serialization may hold one large application row.
No online-backup throughput, representative-scale performance, deduplication or
incremental-backup claim is made in S06.
