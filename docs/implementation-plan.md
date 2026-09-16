# MeldStore MVP implementation plan

Status: S01-S03 complete, with local and Windows/Linux CI verification.
S04 passes local verification; cross-platform CI is pending. S05-S08 remain planned.
The MVP specification is authoritative.
This plan starts with a local file workflow and closes with operational evidence.

## Design gate: public contracts

S01 must write and test these contracts before later slices depend on them:

1. Stable public blob identity table and per-schema metadata tables. Specify
   table/column naming, reserved names, SQL types, FKs, and migration ownership.
2. Schema declaration grammar for scalar fields, nullability, indexes, immutable
   fields, handlers, and optional payload validation. No domain-specific DSL.
3. Transaction adapter: execute/bind/read semantics, connection ownership,
   failed transactions, commit/rollback, and direct application SQL composition.
4. State model: prepared token -> publishing catalog -> ready -> pending deletion;
   define uncertain outcomes, retry identity, and which states SQL readers may see.
5. Version contracts: database format, metadata schema, concurrency version,
   and handler encoding version. Define direct-SQL concurrency enforcement.

Record decisions in docs/contracts.md during S01. Treat unresolved differences
between MeldDB and sqlite3 as findings to resolve before extending the API.

## Module boundaries

- store: public orchestration and shared transaction composition.
- schema/migrations: declarations, SQL layout, versioning, data transformations.
- catalog adapters: MeldDB SQL and sqlite3; no object storage or domain semantics.
- storage: narrow obstore integration, temporary files, immutable publication.
- integrity: bounded-memory XXH3-128 hashing and verification.
- handlers: explicit optional format codecs; no SQL or permanent path ownership.
- maintenance: reconciliation, deletion queue, backup/restore, offline relocation.

These are responsibilities, not a requirement for one file/class per item.

## Delivery sequence

| Slice | Depends on | User-visible exit evidence |
| --- | --- | --- |
| S01 Contracts and relational foundation | None | Install a simple metadata schema; reference a public blob identity through application SQL; both adapters enforce transactions/FKs |
| S02 First local file workflow | S01 | Import, reopen, query, materialize and verify exact bytes; one interruption leaves no visible incomplete blob |
| S03 Metadata and evolution | S02 | Indexed typed queries, stale-edit conflicts, and resumable v1-to-v2 metadata migration |
| S04 Lifecycle and recovery | S03 | Shared publication, restricted deletion, uncertain-commit resolution, and exclusive cleanup survive process interruptions |
| S05 Format handlers | S02, S03 | Parquet/NPZ/Blosc2 and custom values round-trip under explicit contracts |
| S06 Backup and offline transfer | S04 | Fresh restore preserves public IDs, metadata, references, and verified payloads |
| S07 S3 qualification | S04, S05, S06 | Same lifecycle on real S3; multipart/conditional-write and local-to-S3 transfer evidence |
| S08 Consumer and release qualification | S01-S07 | Domain-neutral and cUAS examples, representative workload, clean installs, Windows/Linux evidence |

### S01 — contracts and relational foundation

Implemented: see [public contracts](contracts.md). Local Windows Python 3.12.10
verification passes 93 tests across MeldDB/sqlite3, Ruff, wheel/sdist builds, and
fresh installs of both artifacts. CI covers Windows/Linux Python 3.12-3.14.
All six jobs passed in [S01 CI](https://github.com/radioflyer28/MeldStore/actions/runs/35025377982)
for implementation commit `c5d7867`. See [verification record](s01-verification.md).
This establishes a relational foundation, not payload storage or crash recovery.

Implement generic declarations and basic schema installation against temporary
SQLite databases. Add MeldDB/direct sqlite3 adapters using application-owned SQL.
Establish dependency versions, optional format extras, uv.lock, and Windows/Linux
CI. Verify dependency installation source for MeldDB: do not assume PyPI availability;
use a reviewed pinned commit if needed and document release packaging implications.

Test repeat installation, conflicting definitions, quoting/unusual identifiers,
real SQL FKs from an application table, atomic rollback, nested/expired/foreign
transaction rejection, and SQL access with no MeldDB managed-table dependency.
Resolve whether future concurrent writes need stronger guarantees before accepting
the adapter contract. A contract decision is not a production-ready blob operation.

### S02 — local file vertical slice

Implemented: see [local-file contracts](local-files.md). Local Windows verification
passes 147 tests, including abrupt process exits and a 128 MiB bounded-memory
file transfer through each adapter. Payload codecs, recovery maintenance, and S3
are not included in this slice.
All six Windows/Linux Python 3.12-3.14 jobs passed in
[S02 CI](https://github.com/radioflyer28/MeldStore/actions/runs/35028995579).
See the [S02 verification record](s02-verification.md).

Implement LocalStore integration, file passthrough, bounded-memory hash/size,
staging, immutable keys, prepared tokens, catalog finalize/publish, stat/find and
materialize. Implement convenience import as the same protocol in one owned
transaction. Validate metadata before uploading in that convenience path.

Verify source files stay unchanged, bytes/digests survive reopen, metadata-only
queries make no storage calls, corrupted materialization fails before exposure,
caller-ID retries do not duplicate records, and publication interruption creates
at most an orphan. Include an application-owned row in the same SQL transaction.

### S03 — metadata and migrations

Implemented: [metadata API and evolution contracts](metadata.md). Includes typed
keyset queries, guarded edits, explicit v1/v2 coexistence, SQL-preserving upgrades,
dry-run and atomic resumable batches. Local Windows verification passes 188 tests,
Ruff, builds, and fresh wheel/sdist installs through both adapters. All six
Windows/Linux Python 3.12-3.14 jobs passed in
[S03 CI](https://github.com/radioflyer28/MeldStore/actions/runs/35119163433).
See [S03 verification](s03-verification.md).

Complete scalar predicates, bounded ordering/pagination, composite indexes,
UTC timestamp normalization, reserved-field rejection, and versioned edits.
Implement explicit metadata transformations with dry-run, batches, progress,
atomic checkpointing, and resumption. Schema-version coexistence must not impose
new required fields on old rows before backfill. Coordinate application constraints
with metadata table rebuilds; never silently discard foreign keys or triggers.

Test microsecond interval boundaries, naive/invalid times, int64 bounds, unknown
fields, stale writes, migration interruption, rollback, and direct SQL version
rules. Run the same behavior suite through both adapters.

### S04 — lifecycle hardening

Implemented: [lifecycle and recovery contracts](lifecycle.md). Retirement enforces
application SQL references, queues cleanup durably, and retains retry tombstones.
Prepared uploads require explicit discard. Exclusive leases cover catalog/root
participants and materialization readers; standalone SQL uses the public access
protocol. Local verification: 233 passed, 2 Windows symlink-permission skips;
Ruff, builds and fresh artifact installs pass. Cross-platform CI is pending.
See [S04 verification](s04-verification.md).

Complete lifecycle guards, application transaction rollback, uncertain-commit
resolution, restrictive references, pending deletion, reporting reconciliation,
and explicit exclusive maintenance. Define how exclusivity includes materialize
readers and direct SQL consumers; a process-local mutex is insufficient.

Inject process termination around upload/finalize/publish/commit/delete. Confirm
no ready row refers to partially published bytes; no uncertain reference is
collected; failed deletion stays resumable. Distinguish crash evidence from
power-loss guarantees. Make local durability assumptions explicit.

### S05 — handlers

Implement bytes, Pandas Parquet, NumPy NPZ, Blosc2 arrays, and custom registration.
Keep optional imports lazy. Record format descriptors/encoding versions. Test
DataFrame indexes, nullable columns, categories/time zones, array dtype/shape,
empty values, unsupported object arrays, missing extras, and unavailable handler
versions. No implicit pickle, re-encoding on passthrough import, or SQL in handlers.

### S06 — operational portability

Snapshot the shared SQL catalog and referenced payloads under explicit quiescence.
Specify a portable application/library metadata export, since MeldDB logical
export excludes external tables. Define the application's contribution for its
own tables and migrations; generic code cannot infer domain serialization.

Test interrupted export/restore to fresh destinations, invalid manifests, retained
IDs, reference integrity, destination no-overwrite, and verified relocation.
Backup restore must cover application-owned SQL, not only library tables.

### S07 — S3

Use an explicitly designated disposable AWS prefix. Exercise create-only writes,
multipart completion/abort remnants, stream verification, transport errors,
uncertain publication, pending deletion, and offline LocalStore-to-S3 relocation.
Avoid relying on rename atomicity or ETags as content digests. Record backend and
dependency versions. Emulator results do not close real S3 acceptance.

### S08 — consumers and release

First ship a dataset/document example that works without cUAS imports. Separately
implement the cUAS SQL example with radar multi-track and aircraft single-track
truth fixtures, domain-owned constraints, and correlation queries. Keep original
files intact and retrieve each selected file once regardless of matched tracks.

Use representative 100–300 MB payloads and roughly 10–30 GB total; record actual
track counts, memory, disk staging, hash and import/retrieval timings, and SQL calls.
Run wheel/sdist clean-install tests, both adapters, all optional handler sets, and
Windows/Linux checks. Mark macOS/PostgreSQL unqualified unless separately proven.
Select a license and resolve dependency distribution before package release.

## Completion rules

Each slice records behavior changes, test commands/results, and remaining limits.
No green unit suite substitutes for real S3 or process recovery evidence. Do not
mark the MVP complete with missing integrated acceptance. Implementation is not
authorization to publish a package or use arbitrary cloud storage resources.

Next action after S04 verification: S05 format handlers.
