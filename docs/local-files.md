# S02: local file storage

S04 extends this baseline with [retirement, recovery, and exclusive maintenance](lifecycle.md).
Its access protocol and commit-failure rules supersede the historical limits below.

S02 adds unchanged-file import, prepared tokens, SQL publication, metadata-only
queries, and verified temporary materialization. S3 and structured codecs are not
implemented here. Any file, including an existing Parquet/NPZ/Blosc2 file, can be
stored unchanged with the `file` handler; it is not decoded or re-encoded.

## Import and retrieve

```python
from meldstore import BlobSchema, Catalog, LocalStorage, Store, Text, Timestamp

dataset = BlobSchema(
    "dataset",
    {"title": Text(required=True), "captured_at": Timestamp()},
    handlers=("file",),
)

with Catalog("catalog.sqlite", adapter="melddb") as catalog:
    store = Store(catalog, LocalStorage("objects"))
    store.install_schema(dataset)
    record = store.import_file(
        "existing.parquet",
        schema=dataset,
        metadata={"title": "example"},
        id="application-chosen-id",
    )
    assert store.stat(record["id"])["state"] == "ready"
    for result in store.find(schema=dataset, where={"title": "example"}, limit=100):
        with store.materialize(result["id"]) as verified_path:
            # Read the file here. Close all opened handles before leaving this block.
            print(verified_path)
```

`Catalog` owns its connection; `Store` borrows it. Use `adapter="sqlite"` to open
the same database with the standard-library adapter. Reopen with the same object
root. `LocalStorage` creates/reads a small `meldstore-storage.json` identity marker
through obstore. Catalog rows contain the random storage ID and relative object
key, not an absolute root path or credentials. A different root identity is
rejected before retrieval. The marker is identification, not authentication.
Object roots and application SQL writers are trusted, private to the application;
do not externally mutate objects or remove/copy identity markers to bypass checks.

`stat` and `find` return detached plain dictionaries containing `id`, `state`,
`schema_name`, `schema_version`, metadata concurrency `version`, `metadata`,
`token`, `storage_id`, `object_key`, `byte_size`, `digest`, `hash_algorithm`,
`handler_id`, and `handler_version`. Changing a returned dictionary persists
nothing. Metadata values use the S01 SQL representation: boolean 0/1 and canonical
UTC timestamp text. Write/query inputs still use strict Python bool/aware datetime.

`find(schema=..., where={...}, limit=100, after=None)` supports equality, including
null, combined with AND. It orders by public ID using SQLite's binary text order;
the previous page's final ID can be supplied as `after`. `limit` is 1–1000.
Pagination is not a cross-call snapshot. Unknown fields are rejected and values
are bound. These queries perform no object operations; normal queries expose only
ready records. Range predicates and richer ordering arrive in S03. Application
SQL remains available for joins and advanced predicates now.

The caller ID is a nonempty UTF-8 string up to 128 bytes; omitted IDs are random
UUID hex strings. IDs never derive from content. A sequential retry of
`import_file` with the same ID, schema, metadata, storage identity, file handler,
size and XXH3-128 digest returns the existing ready record without another upload.
The source is snapshotted/hashed on retry, so a missing source cannot resolve it;
use `stat` or explicit token replay instead. Different or unfinished records
conflict. An equal-content race can leave an unreferenced prepared object, but
only one blob ID. Matching uses size/hash, not adversarial byte authentication.
Retries do not re-verify existing stored bytes; `materialize` always does.

## Shared application transaction

```python
prepared = store.prepare_file("existing.parquet", schema=dataset)
with catalog.transaction() as tx:
    record = store.finalize(
        prepared, tx=tx, schema=dataset,
        metadata={"title": "example"}, id="shared-example",
    )
    tx.sql(
        "INSERT INTO application_notes (blob_id, note) VALUES (?, ?)",
        (record["id"], "Application-owned relation"),
    )
    store.publish(record["id"], tx=tx)
# The creation succeeded only after this outer commit.
```

The application creates `application_notes` beforehand, with its ordinary SQL FK
to `ms_blobs(id) ON DELETE RESTRICT`. No storage work occurs in finalize/publish.
Python validation failures there poison the shared transaction even if caught,
just as SQL failures do. Foreign/expired transaction handles are rejected.
Preparation, convenience import, and materialization reject an active transaction
on their catalog. They cannot police independent external connections or callbacks.

`prepare_file` requires the installed `BlobSchema`, rather than just a handler,
so the optional application payload validator runs against a closed private
snapshot before upload. The callback receives a `Path`, must be read-only, and
must not perform catalog writes. Modification is detected by a second hash.
For `import_file`, metadata validation occurs before snapshot/upload too.
Do not edit the source concurrently: size/mtime/ctime changes during copying are
rejected, but this is not a filesystem snapshot against adversarial writers.

`PreparedFile` is a frozen value descriptor, not a tracked model. After successful
upload its exact fields are recorded in the SQL preparation journal. Finalize
compares the supplied descriptor against that journal, schema, and configured
storage ID. The token can be replayed after reopening (including another adapter).
The same token and ID/metadata are idempotent; binding a token to another ID fails.
Do not delete prepared objects while an outcome is uncertain. A token is not an
authorization credential, and journals are not signed.

Finalize inserts publishing identity, metadata, and object association rows.
Publish checks their consistency and changes the state to ready; per-schema SQL
triggers require metadata plus a compatible prepared descriptor, and application
triggers can impose additional rules. Return values inside a transaction are
provisional. Rollback removes the new identity, metadata, object association, and
application rows together, leaving a prepared object available for explicit retry.
Committed publishing rows remain hidden until an explicit publication succeeds.

## File operations and integrity

1. Copy the original into an owned temporary directory in 1 MiB chunks; close and
   flush that snapshot. Hash the finished bytes using XXH3-128, recording size and
   a 32-character lowercase digest.
2. Upload via obstore `LocalStore` multipart to a random `staging/` key, with 5 MiB
   chunks and concurrency 2. Promote to a random `objects/` key using
   `rename(overwrite=False)`. Existing destinations are never replaced.
3. Record the completed descriptor in a short SQL transaction, then return a token.
4. Retrieval streams through obstore into a separate private temporary file and
   checks size and hash before yielding its path. The path is removed on context
   exit, including consumer exceptions. There is no cache or reusable download.

Why a separate upload key? In obstore 0.11.1, non-overwrite `put` forces a
non-multipart operation that materializes the entire input. Multipart staging
followed by local no-overwrite promotion avoids that whole-file buffer. This
implementation and its no-overwrite behavior are tested on local storage only;
it is **not the future S3 publication algorithm**. See the upstream
[put contract](https://developmentseed.org/obstore/latest/api/put/) and
[rename contract](https://developmentseed.org/obstore/latest/api/rename/).

The API never yields the permanent object path, and never removes or rewrites the
source. A consumer may modify its temporary copy without changing the stored
object. Temporary disk space is required: one source snapshot during upload,
plus the stored/staged object; one full temporary copy during retrieval. Backend
promotion may transiently use additional storage. `staging_directory=` selects
an existing scratch directory; default is the OS temporary directory. Callers
must close their file handles before materialization context exit, particularly
on Windows. Process termination may leave temporary/staging files behind.

XXH3-128 detects accidental corruption, not malicious substitution. Missing
referenced objects and size/hash failures raise `IntegrityError`; transport or
permission failures raise `StorageError`, not `NotFoundError`. The latter means
no ready catalog row. Catalog damage such as a missing object association also
raises `IntegrityError`. Source-open failures preserve ordinary filesystem errors.

## Additive SQL extension and boundaries

S01's `ms_format=1`, public identity table, and metadata tables are unchanged.
`Store.install_schema` explicitly installs a version-1 payload extension:

- `ms_payload_format`: payload extension version marker.
- `ms_payload_schemas`: explicit schema enrollment, distinguishing first install
  from missing/altered publication guards on reinstall.
- `ms_prepared`: immutable completed-upload descriptors and schema FK.
- `ms_objects`: unique blob-to-token association with restrictive SQL FKs.
- Core insert and per-schema publication triggers.

This requires no metadata-table rebuild and preserves application FK targets.
Partial/altered payload-extension DDL and non-publishing S01 fixture rows are
rejected rather than adopted. Reinstallation is idempotent. Registry and payload
bookkeeping are library-owned, not an application write API. Registration is not
a full audit of every existing record. As in S01, arbitrary DDL or bypassing SQL
constraints can violate guarantees; no defensive DB sandbox is promised.

The preparation journal is not a blob listing: an entry can be unassociated after
rollback, interruption, or a racing retry. A process exit after upload but before
journaling can leave an object without any SQL row. No such object is ready.
No automatic cleanup runs. S04 will supply reconciliation, uncertain-outcome
resolution, restrictive retirement/deletion, and exclusive maintenance. Do not
manually garbage-collect objects based solely on current ready-row references.

The tests exercise abrupt process exits at upload completion, preparation,
finalization, publication-before-commit, and after commit. They are not power-loss
durability certification. Atomic visibility does not prove all filesystem data
and directory entries were durably flushed. There is no distributed transaction.
S3, codecs, schema migration runner, deletion, backup and relocation remain later
slices. See the implementation plan for those acceptance gates.
