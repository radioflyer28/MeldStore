# Metadata queries, edits, and evolution (S03)

These are implemented APIs, extending the [S01 contracts](contracts.md) and
[local-file workflow](local-files.md). Both MeldDB and sqlite3 adapters use the
same ordinary SQLite tables. No document/graph API or payload I/O is involved.

## Typed queries and pagination

```python
from datetime import datetime, timezone
from meldstore import Order, Predicate

order = (Order("captured_at"),)
page = store.find(
    schema=dataset,
    where={"source": "instrument-a"},
    predicates=(
        Predicate("captured_at", "gte", datetime(2026, 1, 1, tzinfo=timezone.utc)),
        Predicate("captured_at", "lt", datetime(2026, 2, 1, tzinfo=timezone.utc)),
    ),
    order_by=order,
    limit=100,
)
if page:
    after = store.cursor(page[-1], schema=dataset, order_by=order)
    next_page = store.find(
        schema=dataset, where={"source": "instrument-a"},
        predicates=(
            Predicate("captured_at", "gte", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            Predicate("captured_at", "lt", datetime(2026, 2, 1, tzinfo=timezone.utc)),
        ),
        order_by=order, limit=100, after=after,
    )
```

`where` equality and `predicates` are ANDed. Supported operators: `eq`, `ne`,
`lt`, `lte`, `gt`, `gte`, `in`, `not_in`, `is_null`, `not_null`. The limit is
1–1000 records; at most 64 predicates, 100 values per membership predicate,
and eight explicit order fields. Unknown fields, SQL expressions, implicit type
coercion, nonfinite floats, out-of-int64 integers, and naive timestamps fail.
The public `id` is also queryable/orderable; other reserved fields are not.
Each query selects ready records of exactly the supplied schema version.

`None` with `eq`/`ne` means IS NULL/IS NOT NULL. Other comparisons and membership
follow SQL null semantics: `ne` and `not_in` do not include null rows. Membership
lists cannot contain null. `Order(field, descending=True)` reverses that field;
ascending nulls sort first and descending nulls last. An ascending `id` tie-breaker
is appended unless explicitly supplied last. Default order is ascending `id`.
Legacy `after="blob-id"` works only with that default order.

`FindCursor` is a plain frozen value, scoped to schema version and ordering, not
a signed token. Keep filters unchanged between pages. Pagination is not a
snapshot across calls: concurrent edits/imports/migrations may move records
across the cursor and cause omissions/repeats. Use application SQL in one
transaction when snapshot semantics are required. Do not modify cursor values.

`Index("source", "captured_at")` creates a composite SQL index. The tests check
an actual range-query plan using it. Richer OR expressions, joins, aggregations,
and cross-version projections belong in explicit application SQL.

Timestamp inputs are aware Python datetimes, normalized to fixed-width UTC
`YYYY-MM-DDTHH:MM:SS.ffffffZ`. Records retain SQL-friendly strings and boolean
0/1 values. SQL comparisons preserve microsecond boundaries without conversion
functions. Direct SQL writers must honor calendar validity; SQL CHECKs enforce
timestamp shape, not every valid calendar date.

## Guarded metadata edits

```python
record = store.stat(blob_id)
updated = store.update_metadata(
    blob_id, schema=dataset, changes={"title": "Corrected title"},
    expected_version=record["version"],
)
```

Only supplied fields change. A nonempty declared-field mapping is required;
immutable fields and reserved fields are rejected. `None` clears an optional
field. Missing ready ID raises `NotFoundError`; a mismatched record schema or
concurrency version raises `ConflictError`. A retired schema declaration raises
`SchemaConflictError`. Successful edits increment concurrency version exactly
once, without modifying object bytes, hashes, keys, or encoding descriptors.

Pass `tx=tx` inside `catalog.transaction()` to compose application SQL. Returned
records are provisional until the outer commit. Validation/SQL failure poisons
the shared transaction even if caught. Direct SQL writers must still include
`WHERE id=? AND schema_version=? AND version=?` and increment `version`; triggers
enforce immutable fields and the increment, not the presence of a stale-check
predicate. This remains a cooperative SQL contract, not protection from arbitrary
DDL or writes to library bookkeeping.

## Explicit metadata migration

```python
from meldstore import BlobSchema, Integer, MetadataMigration

dataset_v2 = BlobSchema(
    dataset.name, {**dataset.fields, "revision": Integer(required=True)},
    version=dataset.version + 1, indexes=dataset.indexes,
    handlers=dataset.handlers,
    validator_id=dataset.validator_id, payload_validator=dataset.payload_validator,
)
migration = MetadataMigration(
    name="dataset-add-revision", source=dataset, target=dataset_v2,
    transform_id="application.add-revision.v1",
    transform=lambda values: dict(values, revision=1),
)
preview = store.migrate(migration, dry_run=True, batch_size=100)
progress = store.migrate(migration, batch_size=100, max_batches=1)
while progress["state"] != "complete":
    progress = store.migrate(migration, batch_size=100, max_batches=1)
print(store.migration_status(migration.name))
```

Migration advances one version of the same logical schema. There is no implicit
diff on registration, read-time transformation, down-migration, or payload
rewrite. Existing named fields keep their scalar type across all versions;
use a new field name and explicit conversion for type changes. Removal is logical:
old columns remain in the shared table and become null in migrated rows. Required
and immutable flags may change per version. The explicit transformation may
change an old immutable field; ordinary edits cannot. Handler allowlists and
payload-validator IDs must stay unchanged in metadata-only migrations.

The callable receives a fresh full metadata dictionary with strict Python inputs
(aware datetime, bool, etc.), not a blob object or its ID. It returns the target
schema's complete metadata mapping. Missing required/unknown/invalid values fail.
Callbacks must be deterministic, free of external effects, and fast: they run
inside the SQL transaction and may run again after rollback, dry-run, or a crash.
MeldStore persists only the application's stable transform ID, never executable
code, SHA256 fingerprints, or a proof that callbacks are equivalent. The caller
must supply the same semantics on every resume; do not change a callback under
an existing ID. Application-side validation failures can be corrected while
preserving those semantics and then retried.

Initialization commits the target definition, shared-table upgrade, and named
job together. `max_batches=0` performs only this step. New imports/edits then use
the latest schema; old rows remain readable but frozen for backfill. Old schema
inserts, ordinary edits, and metadata deletion are rejected by SQL guards.
Finish publishing existing blobs before starting/resuming. Unbound prepared
tokens for the old schema cannot be finalized after initialization; resolve them
first or prepare against the target declaration. A preparation racing migration
may leave an unreferenced object, subject to the existing S04 cleanup deferral.

Each subsequent batch (1–1000 rows, default 100) commits metadata, schema version,
concurrency increment, and ID checkpoint together. `max_batches=None` runs to
completion; a positive limit returns after that many batches. Progress reports
`name`, `schema_name`, source/target versions, `transform_id`, `state`, `total_rows`,
`migrated_rows`, and `last_id`. Totals describe source rows at initialization,
not concurrent target-version imports. A failed batch rolls back entirely;
previous batches remain committed. Reopen and call with the same plan to resume.
A repeated completed migration returns its durable result. One active migration
per logical schema is allowed. No automatic retries or background execution.

Dry-run executes initialization and all remaining transformations in a single
rollback-only transaction, validates SQL constraints including deferred FKs, and
returns simulated progress with `dry_run=True`. It persists no job/schema/data
changes. It cannot be combined with `max_batches`. Reads are batch-bounded, but
the dry-run holds the writer reservation throughout and can consume substantial
journal space. It is a validation run, not a cheap estimate or guarantee that
later live input has not changed. Callbacks/hooks must not emit external effects.

## SQL layout and application coordination

The stable `ms_blobs(id)` table is never rebuilt. Object descriptors keep their
original preparation schema version; current metadata schema version advances
independently. Retrieval still verifies the original exact bytes with XXH3-128.

The explicit extension adds `ms_metadata_layouts` (layout version 1),
`ms_migrations`, and transient `ms_migration_steps`; base `ms_format` remains 1.
Untouched S01/S02 metadata tables retain their original layout. Upgraded tables
contain the union of registered fields with version-conditional required checks:
v2 requirements never invalidate a v1 row. Metadata's identity triple FK becomes
`NO ACTION DEFERRABLE INITIALLY DEFERRED` on update, so its schema version and the
parent blob's version can change atomically; deletion remains restrictive.
Library indexes become version-scoped partial indexes, named
`<table>_v<version>_i<number>`. In particular, declaration-level uniqueness is
per schema version during coexistence. Application-owned global unique indexes
remain global and may reject transformations.

The table upgrade copies all metadata in one transaction before bounded backfill;
it is not an online/batched DDL operation. Budget temporary database/journal space
and a writer pause. No foreign-key disabling, private MeldDB connection access,
or parent-table renaming is used. SQLite's
[rebuild guidance](https://www.sqlite.org/lang_altertable.html#otheralter) and
[deferred-FK semantics](https://www.sqlite.org/foreignkeys.html#fk_deferred)
explain why reconstruction and commit-time checking are explicit here.

Application indexes and persistent triggers attached to metadata are recreated
from their exact definitions. Views and references to stable blob IDs retain
their names and contents. Altered library DDL is rejected, not silently repaired.
Connection-local TEMP triggers must be explicitly removed before rebuilding.

Incoming FKs directly into the metadata table cause a safe refusal unless the
application supplies both `before_ddl(tx)` and `after_ddl(tx)` on the migration.
These hooks must temporarily remove the incoming references and restore their
exact table/FK definitions in the same transaction. For a simple child table:

```python
def before_ddl(tx):
    tx.sql("CREATE TABLE app_child_backup AS SELECT * FROM app_child")
    tx.sql("DROP TABLE app_child")

def after_ddl(tx):
    tx.sql(original_child_ddl)  # exact original CREATE TABLE statement
    tx.sql("INSERT INTO app_child SELECT * FROM app_child_backup")
    tx.sql("DROP TABLE app_child_backup")
```

This example is insufficient for a child with its own indexes, triggers, or
dependent tables: the application must preserve those and order its full
dependency chain explicitly. MeldStore verifies restored child table definitions,
FK signatures, library objects, and foreign-key consistency; application data
preservation and additional objects remain the hook author's responsibility.
Hooks run only on initial upgrade, not normal resume. Hook errors roll back the
upgrade. Prefer FKs to `ms_blobs(id)` plus joins for ordinary domain relationships;
these need no hooks. Custom constraint redesign is a separate application
migration, not something inferred by MeldStore.

## Qualification limits

Tests exercise microsecond boundaries, composite query plans, typed cursors,
stale updates, mixed versions, required/immutable rules, dry-run, uniqueness/FK
failures, DDL preservation/refusal, and abrupt process termination followed by
cross-adapter resume. This establishes metadata transaction behavior, not
power-loss durability, production workload performance, payload maintenance,
backup, codecs, or S3. Those remain later slices. PostgreSQL is not qualified.
