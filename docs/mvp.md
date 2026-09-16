# Blob Store MVP brief

2026-09-15. Finalized scope baseline from the design discussion. This specifies
a new library; the APIs below are proposed, not existing MeldDB capabilities.
This is the authoritative MeldStore MVP specification. MeldStore is a separate
project and Python package; implementation does not belong in MeldDB core.

Acceptance amendment, September 16, 2026: the user approved a real Docker-local
RustFS or Garage S3-compatible service for S07 qualification in place of the
original AWS-only requirement. AWS-specific evidence is deferred; compatible
service results do not qualify AWS or constitute AWS certification. S07 is
implemented and qualified on pinned RustFS; S08 is next. See the
[implemented S3 API](s3.md) and [verification record](s07-verification.md).

## Project Description

A synchronous Python blob store with user-defined relational metadata schemas,
explicit serialization handlers, XXH3-128 integrity verification, and local/S3
storage through obstore. Its initial metadata adapter uses MeldDB's explicit SQL
connection and transaction operations. The persisted schema uses ordinary SQL
tables, foreign keys, constraints, and junction tables, and remains usable without
MeldDB. It provides durable storage and retrieval without caching features.
The library has no built-in recording, aircraft, sensor, track, or truth concepts.
Applications define their own metadata and domain relationships.

## Why This Milestone

Applications need reusable storage for files and structured values with searchable
metadata. One motivating consumer needs approximately 100 Parquet recordings, each
100–300 MB, with sensor/configuration references and recording intervals. It must
find recordings through SQL, correlate tracks with aircraft truth, and retrieve
verified files without rebuilding storage mechanics. The earlier Cacheness
combines this need with caching policies; the new library isolates blob storage.

## User-Visible Outcome

Users can register a metadata schema, import existing Parquet files unchanged,
or serialize DataFrames/arrays with a chosen handler; query metadata without
loading files; retrieve and verify payloads; edit permitted metadata; explicitly
delete unreferenced records; migrate metadata; and restore a verified backup.

Separately, an application-owned cUAS integration example can import a radar recording containing many track UUIDs,
register small occurrence rows for those tracks, import a single-track aircraft
truth recording, and store/query correlations with ordinary SQL joins. No track
is split into its own Parquet file.

## Completion Class

Integration and operational. Unit tests alone cannot establish completion.
Acceptance includes local filesystem behavior, an actual S3-compatible service
with an explicitly designated disposable bucket/prefix,
ordinary SQL interoperability, interrupted operations, and backup restoration.
S3 credentials and a disposable destination are needed for that qualification.
Docker-local RustFS or Garage is permitted under the September 16 amendment;
AWS remains separately unqualified.

## Final Integrated Acceptance

First demonstrate a domain-neutral dataset catalog: register scalar metadata,
store files/arrays/DataFrames, query and edit metadata without payload I/O,
reference public blob IDs from an application table, and commit both atomically.
Run it without any cUAS schema or domain-validation package installed.

Then use cUAS as a separate integration example with representative 100–300 MB
files and a workload totaling roughly 10–30 GB.
Catalog one radar file with several tracks and one aircraft truth file with
exactly one track. Commit recordings and domain metadata atomically. Query a run
interval, find matching track occurrences, correlate observations to truth, and
resolve the aircraft and configuration with SQL. Group results by recording and
retrieve/verify each file once. Change an annotation without rewriting bytes.
Prove stale-write rejection, restrictive deletion, crash recovery, and restoration
into a fresh database/object root. Repeat storage acceptance locally and on S3.
An ordinary SQL driver must open the same catalog and run its reference queries.

## Architectural Decisions

### Generic blob metadata with application-owned domain semantics

The library owns storage locations, object descriptors, digests, lifecycle, and
schema-defined blob metadata tables. Users supply metadata fields; the library
does not interpret their domain meaning. A recording is one application-defined
use of a blob record, not a built-in entity or schema package type.

The cUAS application registers ordinary recording metadata through the generic
schema API. It owns the truth subtype/association and its constraints, sensors,
aircraft, configurations, tracks, runs, identifications, and correlations. There
is no duplicate recording catalog. The truth association references the public
blob/recording ID; its single-track rule belongs entirely to application SQL.

The library exposes public blob IDs and a documented relational schema for
joins and foreign keys. Internal object bookkeeping is not an application write
API. A shared SQL database provides cross-boundary referential integrity. A
separate metadata database would lose that simple transaction/FK contract and is
outside the MVP.

### Ordinary SQL throughout

Application relationships are explicit SQL foreign keys or junction rows.
Optional JSON within SQL rows stores structured payloads
and descriptive details, never authoritative relationship references. Query-critical
fields are ordinary columns. No managed document collections or graph API are
required. This replaces the earlier proposed MeldDB-managed catalog.

Library migrations define the public blob schema; separate application migrations
define domain tables, foreign keys, and cross-table rules using that contract.
Both define stable SQL names and constraints;
MeldDB's private table layout is not a persistence contract. Initial support is
SQLite through MeldDB, plus a direct sqlite3 adapter to prove independence.
PostgreSQL is a later qualification target, not an MVP support claim.

### Schema definitions are versioned relational contracts

Each registered schema declares stable scalar fields, nullability, indexes,
metadata validation, allowed handlers, and optional application-supplied payload
validation. A Python Reference abstraction is not part of the MVP; application
SQL migrations define foreign keys to documented public blob identities and
between domain tables. The initial field set is text, int64,
finite float, boolean, and UTC timestamp. Rich optional payloads may use validated
JSON text. Reject unknown fields and implicit coercion by default.

Definitions are immutable by schema name/version. Re-registering identical
definitions is harmless; incompatible reuse fails. Record schema version,
metadata concurrency version, and handler encoding version are separate concepts.

Example generic declaration (illustrative syntax):

```python
dataset_schema = BlobSchema(
    name="dataset",
    version=1,
    fields={
        "name": Text(required=True),
        "source": Text(),
        "captured_at": Timestamp(),
    },
    indexes=[Index("source", "captured_at")],
    handlers=["file", "pandas.parquet", "numpy.npz"],
)
```

The library supplies ID, schema/concurrency versions, and internal object
association. User fields cannot overwrite storage descriptors or lifecycle.
Each logical schema has one library-owned metadata table shared by its versions;
independent schemas never compete to own the same table. Registration exposes
stable validated SQL identifiers and the public key contract for joins/FKs.
Applications do not guess table names or use MeldDB physical mappings.

Do not build an automatic schema-diff engine. Simple declarations generate basic
DDL. Library-owned metadata evolution uses explicit migrations; application-owned
migrations supply domain foreign keys and richer cross-row rules. Coordinate
their ordering against the public schema contract. Domain constraints remain
effective for SQL writers; identifier quoting/validation is mandatory.

New versions do not reinterpret old records. During migration, shared table
constraints must allow declared old versions; tighten constraints only after
validation/backfill. Provide an explicit metadata migration runner accepting a
named application transformation, dry-run mode, bounded batches, progress, and
resumption. Commit transformed metadata, schema version, concurrency version,
and progress together per batch. A resumed run skips already migrated records;
stale writes report conflicts. No implicit read-time conversion or down-migration.

### obstore supplies local and S3 object operations

Accept configured storage backends, keeping credentials outside the catalog.
Store relative object keys and storage identity. Start with one object per blob,
immutable bytes, and no deduplication. Use fresh keys and reject unintended
overwrites. Qualify conditional writes and multipart behavior on the selected
backend; do not base publication on an assumed atomic S3 rename.

S07 keeps the catalog and persistent coordination directory on a single host.
All participants share those local resources; remote payloads do not create a
distributed catalog or lease service. Identity is bound to the coordination
path and explicit endpoint/bucket/prefix. Existing remote markers cannot be
adopted from a new local directory. Credentials remain runtime configuration.

Existing files are privately staged and hashed on local disk. Nonempty uploads
use 5 MiB multipart chunks with concurrency two per blob, followed by server-side
create-only multipart copy; empty payloads use conditional PUT. Incomplete
multipart uploads require external cleanup. See [S3 contracts](s3.md) for
configuration, temporary-space requirements, recovery, and backend limitations.

### Explicit, optional serialization handlers

Include bytes/existing-file passthrough, Pandas-to-Parquet, NumPy NPZ, and NumPy
Blosc2 array files. Support explicit custom handler registration. Format packages
are optional extras; importing the core must not import Pandas/NumPy/Blosc2.
Record handler ID, encoding version, options/descriptor required for decoding.

Handlers validate, write to a staged local path, and read from a verified local
path. They do not issue SQL or own permanent object locations. Hash the closed,
finished file because writers may seek and rewrite headers. Avoid automatic
pickle fallback and object-dtype arrays. Define and test index, dtype, shape,
nullable-column, categorical, and time-zone preservation for supported formats.
Unsupported structures fail explicitly. Direct streaming and remote slicing are
later optimizations; temporary disk usage must be documented.

### XXH3-128 integrity

Hash exact stored bytes after serialization/compression. Store `xxh3_128`, a
32-character lowercase hex digest, and byte size. Verify before deserialization
and before exposing a successful downloaded-file result. XXH3-128 targets
accidental corruption, not adversarial authenticity. Record identities do not
depend on the digest. Existing-file import preserves its bytes exactly.
SHA-256 metadata fingerprints, entry signatures, and key management are deferred.

### Readable UTC time

SQLite stores canonical TEXT `YYYY-MM-DDTHH:MM:SS.ffffffZ`; writes and query
parameters use the same fixed format. The API requires aware datetime values
and normalizes to UTC. Direct text comparisons support range queries. Applications
define interval semantics; the cUAS example uses half-open intervals.
Export the same ISO-8601 representation. Preserve native sample precision/time
bases in Parquet. A future PostgreSQL adapter maps catalog instants to
TIMESTAMPTZ(6). Calendar validity, precision conversion, and clock interpretation
are explicit ingestion responsibilities.

### Prepare bytes, then finalize in a shared transaction

Proposed API sketch (names are illustrative):

```python
store.install_schema(dataset_schema)  # Explicit registration/migration

prepared = store.prepare_file(path, handler="file")
with catalog.transaction() as tx:
    blob = store.finalize(
        prepared,
        tx=tx,
        schema="dataset",
        metadata=metadata,
        id=blob_id,
    )
    insert_application_reference(tx, blob.id)  # Application-owned SQL
    store.publish(blob.id, tx=tx)
# Success is reported only after the outer commit.

with store.materialize(blob.id) as verified_path:
    consume_file(verified_path)
```

Preparation serializes/hashes/uploads outside the catalog transaction. Finalize
creates library-owned publishing rows; the application adds dependent rows using
the same transaction; publish checks generic readiness and transitions to ready.
Application-installed SQL triggers may enforce additional domain invariants on
that documented transition. No built-in track-count or aircraft validation exists.
Generic `put`/`import_file` wrap this sequence when no domain transaction is needed.
Transaction adapters enforce connection ownership; no nested independent commit.
Preparing an object does not make a blob record visible. Tokens bind object key,
size, digest, and storage identity; they are validated against staged state.

The ordinary operations are `put`, `import_file`, `stat`, `find`, `get`,
`materialize`, `update_metadata`, and `delete`, plus schema and maintenance APIs.
`find` provides bounded scalar filters and pagination; richer domain queries use
documented SQL. `get` decodes data; `materialize` exposes a context-managed verified
file for existing-Parquet workflows. `stat`/`find` do not touch payload storage.

Creation accepts a caller ID for retry resolution; an existing different record
is a conflict. Resolve an uncertain commit by that ID and matching object/metadata
before retrying. Metadata updates require expected concurrency version. Immutable
schema fields cannot be edited. Direct SQL updates must honor/increment that
version through documented guarded SQL behavior.

Payload replacement is deferred: the generic MVP uses immutable objects and stable
blob identities to simplify integrity and reference semantics. A changed payload
gets a new blob ID; applications choose whether/how to link revisions. Metadata
remains explicitly editable. This policy applies equally to every domain.

## Error Handling Strategy

Distinguish not found, validation, unsupported handler/version, stale version,
integrity failure, reference restriction, storage failure, and uncertain outcome.
Do not treat corruption as absence. Do not silently fall back to another format.
Transport retries must not trigger a second logical catalog creation.

Uploaded objects without committed records are orphans. Failed/uncertain commits
must be resolved before cleanup; never assume rollback from a lost response.
Readers see ready records only. Library guards check object and metadata readiness.
Application constraints run in the shared transaction; the cUAS example uses its
own SQL guards for truth-track cardinality and aircraft/logger ownership.

Deletion first resolves domain RESTRICT dependencies explicitly. In one shared
transaction, the library retires the blob and removes its schema rows together
with explicitly authorized domain changes. Pending deletion retains the object
location until maintenance completes deletion. A missing object on retry is
success; permission/network failures remain pending. Destructive maintenance
requires exclusive reader/writer access in the MVP. Reconciliation reports
orphans, missing objects, corrupt objects, and unfinished operations before cleanup.

## Risks and Unknowns

The shared transaction API is new work. MeldDB raw SQL access is available, but
does not automatically supply this library's schema registry, migration runner,
concurrency versions, or publication rules. Local filesystem durability and S3
conditional/multipart behavior require evidence. Whole-object verification and
temporary copies may dominate latency; measure representative files. Track count
and file layout, not just file count, determine catalog/retrieval costs.

## Existing Codebase / Prior Art

Existing Cacheness provides serialization and storage experience; it is not a
compatibility contract. MeldDB provides explicit SQL transactions and SQLite
physical backup, with no core runtime dependencies. Its managed logical export
does not include these externally defined tables. The cUAS ERD v3 defines the
reference relational integration, including truth recordings and correlations.

## Relevant Requirements

- BS-01: Explicit durable creation and retrieval, stable public IDs.
- BS-02: User-defined relational schemas and SQL relationships.
- BS-03: Existing-file and structured-format handlers, including custom handlers.
- BS-04: XXH3-128 verification of exact stored bytes.
- BS-05: obstore local and S3-compatible operation; AWS qualification deferred
  under the September 16, 2026 acceptance amendment.
- BS-06: Shared transactions, conflict detection, and interruption recovery.
- BS-07: Explicit metadata schema evolution and resumable migration.
- BS-08: SQL interoperability without MeldDB.
- BS-09: Coordinated catalog/payload backup and restoration.
- BS-10: Domain-neutral operation plus a separate cUAS SQL integration example.

## Scope

### In Scope

The requirements above, synchronous operation, whole-object reads, explicit
maintenance, indexed metadata discovery, immutable payloads, and application-owned
SQL joins. Local multiple-process metadata writes must respect constraints and
optimistic concurrency; one connection handle is not shared across threads.

### Out of Scope

Cache keys, decorators, TTL/eviction, access counters, automatic deduplication,
automatic schema diffs, reverse migrations, payload replacement, partial remote
reads, in-place array editing, online garbage collection, multi-machine shared
catalog service, PostgreSQL qualification, graph/NoSQL APIs, automatic correlation
algorithms, legacy-store migration, SHA-256 metadata fingerprints, entry signing,
and built-in domain entities/rules or a Python foreign-key DSL.

### Non-Goals

No compatibility promise with the old Cacheness API. No new blob APIs or optional
blob dependencies in MeldDB. No universal ORM, cloud service, or distributed
transaction claim.

## Technical Constraints

Python 3.12+ initially, matching MeldDB. Qualify Windows and Linux; record macOS
as unqualified until executed. Core library dependencies include MeldDB, obstore,
and xxhash; this does not change MeldDB's dependency-free core. Pin and test a
compatible dependency set. Preserve ID/JSON/timestamp encodings across adapters.
Performance acceptance records staging space, peak memory, hash time, SQL calls,
and end-to-end import/retrieval time; no invented throughput target is a release
gate. No per-track duplicate file retrieval and no whole-file byte buffers in
the existing-file import/materialize paths.

## Integration Points

MeldDB SQL transactions; direct sqlite3 conformance adapter; obstore LocalStore
and S3Store; Pandas/Parquet, NumPy/NPZ, Blosc2 handlers; generic application schema
registration and transaction composition. The cUAS example supplies its own SQL
constraints. Backup captures a consistent catalog plus all
referenced ready objects while mutations/cleanup are paused, and restores only
into a fresh destination. Offline transfer to S3 requires both a fresh local
catalog/coordination directory and an empty remote prefix. It verifies payloads
before publishing the destination catalog last, preserving public IDs, logical
storage identity, domain references, and the source. The caller explicitly
switches applications after successful verification; there is no automatic
cutover. S3-backed stores can also produce verified local backups.

## Testing Requirements

Test observable behavior: schema validation, handler round trips, indexed query
results, UTC/microsecond boundaries, SQL FK/cardinality violations, missing handler
versions, stale updates, and direct-driver interoperability. Inject interruption
before/after upload, publication, commit, deletion, and metadata migration batches.
Process-crash tests are not power-loss certification. Exercise a real
S3-compatible service separately from mocks; record its pinned image and client
versions. AWS service behavior needs separate, deferred evidence. Interrupted
multipart remnants need backend cleanup coverage. Verify
backup restoration and local-to-S3 transfer with hashes and domain queries.

## Acceptance Criteria

1. Bytes/files: exact bytes survive import, reopen, materialize, and verification;
   corruption fails explicitly and no duplicate record appears after retry.
2. Metadata: schemas, indexes, restrictive references, optimistic edits, and a
   version-1-to-version-2 migration work through both SQLite adapters.
3. Formats: supported Parquet, NPZ, and Blosc2 values round-trip; custom handler
   registration works; missing extras fail with actionable errors.
4. cUAS: radar/truth files remain intact, SQL correlations resolve aircraft and
   configurations, and recording/occurrence publication is atomic. All domain
   tables, rules, and correlation queries live in the example application.
5. Operations: interruptions reconcile safely; explicit deletion respects readers
   and references; backup restoration and S3 transfer preserve IDs and bytes.
6. Portability: reference queries and SQL constraints work without MeldDB;
   removing MeldDB does not require converting the relational data model.
7. Genericity: a dataset/document application registers different metadata and
   imports, queries, verifies, and deletes blobs without any cUAS code installed.
   Core API names and built-in tables contain no aircraft/sensor/truth concepts.

## Open Questions

These do not reopen the chosen architecture:

- Repository: radioflyer28/MeldStore; Python package: meldstore. Repository setup
  is authorized; implementing the MVP follows the implementation plan.
- Representative data for S08. AWS-specific qualification remains deferred and
  requires a separately designated disposable AWS destination and credentials.
- cUAS source UUID reuse, timestamp precision conversion, and authority for track
  validation/correlation: the schema package defines these domain policies.
- Measured performance targets after the first representative workload baseline.
- Whether legacy data import or additional platforms become subsequent milestones.
