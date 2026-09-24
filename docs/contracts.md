# S01 public contracts

This is the S01 relational baseline. S02's current additive payload contract and
implemented APIs are documented in [local file storage](local-files.md).
S03 adds [metadata queries, guarded edits, and explicit evolution](metadata.md).
S04 adds [lifecycle recovery and cooperative exclusive maintenance](lifecycle.md),
including the requirement to reopen after a commit failure.
Historical S01-only limitations below do not replace those later contracts.

## Schema declarations

```python
from meldstore import BlobSchema, Index, Text, Timestamp

dataset = BlobSchema(
    name="dataset",
    version=1,
    fields={
        "title": Text(required=True),
        "source": Text(immutable=True),
        "captured_at": Timestamp(),
    },
    indexes=(Index("source", "captured_at"),),
    handlers=("file", "pandas.parquet"),
)
```

Fields are `Text`, `Integer` (signed int64), `Float` (finite binary64), `Boolean`,
and `Timestamp`. Each accepts `required=False` and `immutable=False`. Required
means both present and non-null; absent optional fields normalize to null. There
are no implicit defaults, coercions, nested document fields, references, or ORM
objects. Indexes may be composite and optionally `unique=True`; SQLite permits
multiple nulls in unique indexes. Index fields must be declared exactly once.

Declarations defensively copy inputs and are frozen. Field names are Unicode
identifiers up to 128 UTF-8 bytes. Empty strings, NUL, invalid Unicode, SQLite's
ASCII-case-insensitive duplicates, `id`, `schema_name`, `schema_version`,
`version`, and the `ms_` prefix are rejected. SQL keywords, spaces, apostrophes,
and double quotes are supported through mandatory identifier quoting. Schema
names are case-sensitive and limited to 48 UTF-8 bytes. Always obtain the table
name from `install_schema()` or `schema.table_name` and quote it with
`quote_identifier()`. Values use SQL bindings, never string interpolation.

Handler IDs are nonempty distinct strings, stored as an order-independent allowlist.
They do not imply installed codecs. Handler registration/lookup arrives in S05.
Optional `validator_id="application.rule.v1"` and `payload_validator=callable`
must be supplied together. The callable takes one payload, returns normally on
success, and raises on failure. `validate_payload(value)` invokes it explicitly;
registration/SQL does not. Only its stable ID is persisted, never Python code.
The application must change that ID when semantics change and supply the code
when reopening. S02/S05 must enforce validators before preparing bytes.

`normalize_metadata(mapping)` rejects unknown fields and wrong Python types,
rejects boolean-as-integer, and returns a fresh mapping ready for SQL bindings.
Float accepts Python float only. Boolean becomes 0/1. Timestamp requires an aware
datetime and normalizes to UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ`; strings and naive
datetimes are rejected. Range predicates must bind this same canonical format.
SQLite CHECKs enforce stored scalar types, nullability, booleans, finite floats,
and timestamp shape. SQLite affinity may coerce raw SQL input; SQL CHECKs cannot
recover the original Python type. Calendar validity for direct SQL timestamps
remains the writer's responsibility. Application SQL may add stronger constraints.

## Public relational layout and ownership

| Object | Contract |
| --- | --- |
| `ms_format` | Singleton format version; currently 1 |
| `ms_schemas` | Primary key `(name, version)`; full canonical declaration and table name |
| `ms_blobs` | Stable public `id TEXT NOT NULL PRIMARY KEY`; schema name/version and lifecycle state |
| `schema.table_name` | One metadata table per logical schema, with primary key `id`, schema name/version, concurrency `version`, and declared fields |

Names starting `ms_` belong to MeldStore. Metadata table names are `ms_data_`
plus lowercase UTF-8 hex of the schema name, independent of schema version.
Schema declarations are compared as full canonical JSON; no SHA256 fingerprint
or signature is generated. Field, index declaration, and handler ordering do not
change a definition (column ordering within a composite index does).

The metadata table's `(id, schema_name, schema_version)` references the same
unique triple in `ms_blobs`, which references `ms_schemas(name, version)`.
All foreign keys are restrictive, not cascading. Blob IDs and schema names are
immutable. Metadata rows must initially have concurrency version 1. Update
triggers enforce immutable fields and an increment of exactly one. SQL type
constraints prevent version overflow. Application writers must use guarded SQL:

```sql
UPDATE "ms_data_64617461736574"
SET title = ?, version = version + 1
WHERE id = ? AND version = ?
RETURNING version;
```

No returned row means missing ID or stale version; the later metadata API will
distinguish these. SQL cannot prove the client supplied an expected version;
the trigger enforces the increment, while the writer must include the predicate.
`REPLACE`, delete-and-reinsert, disabled triggers/FKs, or unauthorized DDL are not
supported edit paths. This is a cooperative database contract, not a sandbox
against applications with arbitrary file/DDL access.

Applications own ordinary SQL tables, migrations, junction rows, and domain
constraints. Their public FK target is `ms_blobs(id)` with `ON DELETE RESTRICT`.
They may join metadata using its `id`; do not write registry/identity/lifecycle
bookkeeping directly. S01 tests insert unpublished fixture rows solely to prove
the SQL contract; they are not examples of safe blob creation. S02 owns creation.

In MeldDB's public inspection terminology, both MeldStore's `ms_*` tables and
application tables are **external tables**: they are ordinary SQL objects outside
MeldDB's managed document/table model, even though they live in the same SQLite
database. MeldStore owns its schema and migration history; each application owns
its domain schema and migration history. `db.inspect()` may report those tables
under `external_tables`, but neither opening nor inspection registers, adopts, or
migrates them. No MeldDB managed physical table is a supported foreign-key target.

`install_schema()` installs core tables and a version-1 metadata table atomically.
Identical registration is idempotent. Conflicting definitions, unsupported
versions, namespace collisions, altered core/schema DDL, or missing guards fail;
there is no implicit repair. Additional application tables/triggers are allowed.
DDL comparison is deliberately conservative: even semantically equivalent edits
to library DDL require an explicit migration. Registration verifies the requested
schema, not an integrity audit of every registered schema or all existing rows.

## Explicit SQL and adapters

```python
from meldstore import Catalog

with Catalog("catalog.sqlite", adapter="melddb") as catalog:
    with catalog.transaction() as tx:
        table = catalog.install_schema(dataset, tx=tx)
        tx.sql(
            "CREATE TABLE IF NOT EXISTS application_notes ("
            "blob_id TEXT NOT NULL REFERENCES ms_blobs(id) ON DELETE RESTRICT, "
            "note TEXT NOT NULL)"
        )
    # Committed here, not at the end of install_schema().
```

Use `adapter="sqlite"` for the standard-library adapter. Both own their connection
and expose `tx.sql(statement, params=()) -> list[dict]`. Bind tuple/list positional
`?` parameters or dict named `:name` parameters. Execute one statement per call;
DDL/non-returning writes return `[]`; queries, CTEs and RETURNING return materialized
plain dictionaries. Alias duplicate result-column names yourself. `catalog.sql`
owns one transaction. Use `tx.sql`, not `catalog.sql`, inside an existing transaction.
`install_schema(schema, tx=tx)` participates in that same commit/rollback.

Connections and transaction handles are confined to their creating thread. A
handle expires on context exit. Nested transactions, foreign handles, closing an
active connection, and reuse of failed handles are rejected. A failed SQL or
installation operation poisons its transaction even if its exception is caught;
it cannot subsequently commit. An exception escaping the context rolls back DDL
and data together. A wrong-thread call is rejected without operating the owner
connection. A foreign handle is rejected without operating its source connection.

Both adapters use FK enforcement and synchronous FULL. Writable transactions
use `BEGIN IMMEDIATE`; `catalog.sql()` remains writable by default, even for a
SELECT. S06a adds explicit `write=False` transactions and makes Store metadata
reads use them. Both adapters now apply an explicit live journal policy (WAL by
default, requiring a patched SQLite runtime). See [SQLite contracts](sqlite.md)
for read-statement restrictions, upgrades, maintenance and backup behavior.
Results are materialized and should be bounded by SQL. File/object I/O remains
outside SQL write transactions. There is no connection-pooling API.

SQL transaction/configuration commands (including PRAGMA, ATTACH, DETACH) are
rejected; callers cannot turn off FK enforcement through the wrapper. Standalone
drivers **must enable `PRAGMA foreign_keys=ON` on each connection**. MeldDB is used
only through public open/runtime/transaction/SQL/maintenance/backup APIs. The
direct adapter independently uses standard-library SQLite connections. Neither
opening nor installing invokes document/graph APIs or creates managed tables.
The same catalog can therefore reopen through `adapter="sqlite"` or plain
`sqlite3` without schema adoption or data conversion.

SQL failures map to ValidationError, ConstraintError or BusyError with driver
causes retained. Transaction misuse maps to TransactionError; schema reuse/drift
to SchemaConflictError. `TransactionOutcomeError` carries `phase`, `outcome`,
`initiating_error` and `backend_error`; `CommitError` and `RollbackError` are its
public subclasses. Confirmed rollback re-raises the identical initiating error.
Uncertain begin/commit/rollback or required read-state cleanup quarantines the
catalog: close it, reopen it, and inspect durable state before deciding whether
to retry. Close attempts every owned database/lease release, with secondary
failures available as `cleanup_errors`. There is no automatic reconnect or
transaction retry. Separate connections/processes serialize writes through
SQLite; version predicates prevent stale overwrites. No process-local lock or
hidden unit of work substitutes for database constraints.

## Lifecycle contract for S02/S04

1. **Prepared token:** serialize, close, hash exact bytes using XXH3-128, upload
   under a fresh immutable key outside SQL. No blob is query-visible. Token binds
   storage identity/key/size/digest and must be checked against prepared state.
2. **Publishing:** finalize into the catalog using a caller-provided stable ID;
   add application references in the same explicit transaction.
3. **Ready:** publish verifies metadata/object descriptors and triggers application
   guards before setting ready. Report creation success only after outer commit.
4. **Pending deletion:** resolve restrictive application references explicitly;
   retire the catalog record and metadata together, retain object coordinates in
   pending deletion bookkeeping until exclusive maintenance confirms deletion.

High-level readers return ready records only. Raw SQL can observe committed
publishing/pending rows and must explicitly filter `state='ready'`. Publishing
may persist after an interrupted workflow; no reader may interpret it as ready.
Lost commit responses are resolved by caller ID and exact object/metadata match,
not by creating another ID. Different content under an existing ID is a conflict.
Uploaded but unreferenced objects are only candidate orphans: cleanup must wait
for uncertain outcomes and exclusive maintenance. Retrying an absent deletion
target succeeds; permission/transport failures remain pending.

S01 reserves state values but **does not enforce publication readiness, store
object descriptors, prepare tokens, or implement deletion/recovery**. Those need
S02/S04 implementation and failure-injection evidence, not just this state diagram.
No payload integrity/durability or crash-safety claim follows from S01 tests.

## Four independent versions

- **Database format:** library SQL layout/migration contract in `ms_format`.
- **Metadata schema:** immutable name/version declaration; rows retain the version
  they were written against. S01 only installs version 1; S03 supplies explicit,
  coordinated migrations with application constraints and stable FK targets.
- **Concurrency:** each metadata row starts at 1 and increments on edits; unrelated
  to format/schema versions. Callers provide expected version on guarded writes.
- **Handler encoding:** codec ID plus encoding version and decoding descriptor;
  added with payload descriptors in S02/S05, not inferred from schema version.

Format-1 development catalogs are pre-release. Later format evolution must fail
explicitly or use documented migrations; do not silently reinterpret these rows.
PostgreSQL is a later qualification target. Relational design is portable, but
SQLite DDL, triggers, and affinity are not claimed to be byte-for-byte portable SQL.

## Dependencies and qualification

MeldDB is pinned to public source commit
`adfc87fb9933412e67a2d4b7de316cd6db552d9c` (0.1.0rc1). `uv.lock` records the
resolved core/test/format dependency set. obstore and xxhash are installed core
dependencies; Parquet (`pandas`, `pyarrow`), NumPy, and Blosc2 have separate extras.
Importing MeldStore imports no optional codec or MeldDB; the sqlite adapter runs
with MeldDB imports blocked. The ordinary package install still declares MeldDB.

The Git dependency requires Git/network for a fresh source installation. GitHub
release artifacts retain this exact tested dependency; PyPI publication remains
out of scope until a qualified index dependency is available. No MeldDB core
files or dependencies are changed by MeldStore.

MeldDB's SQLite capability checks include quoted-key JSON access even for raw SQL
use. CI therefore requests uv-managed Python with a recent bundled SQLite, and
prints versions. An arbitrary OS Python linked against older SQLite may fail
MeldDB's startup probe; the direct adapter does not require that JSON capability.
CI covers Windows/Linux Python 3.12–3.14, both adapters, lint, builds and fresh
wheel/sdist installation. Until runs finish, the workflow is configuration, not
platform qualification. macOS, PostgreSQL, S3, codecs, and crash recovery remain
unqualified in this slice.
