# MeldStore MVP implementation plan

Status: S01-S07, including S06a, complete with local and Windows/Linux CI evidence.
S07 qualifies pinned RustFS, not AWS. S08 consumer and operational qualification
passed; dependency licensing/distribution remains a package-release gate.
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
| S06a SQLite tuning and concurrency | S06 | Consistent live-catalog settings after creation/reopen/restore, genuine read transactions, and measured index/query-plan evidence through both adapters |
| S07 S3-compatible qualification | S04, S05, S06 | Same lifecycle on a real S3-compatible service; multipart/conditional-write and offline transfer evidence; AWS evidence deferred |
| S08 Consumer and release qualification | S01-S07, including S06a | Domain-neutral and cUAS examples, representative workload, clean installs, Windows/Linux evidence |
| S09 MVP release preparation | S08 | Merged MVP, versioned candidate, release notes, verified artifacts and explicit distribution/publication gates |

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
Ruff, builds and fresh artifact installs pass. All six Windows/Linux Python
3.12-3.14 jobs passed all 235 tests, including symlinks, in
[S04 CI](https://github.com/radioflyer28/MeldStore/actions/runs/35130497148).
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

Implemented: [handler contracts](handlers.md) cover explicit `prepare`/`put`/`get`,
sealed logical encoding descriptors, bytes, native pandas/Polars/PyArrow Parquet,
single-array NPZ, dense Blosc2 arrays and custom registration. Narwhals was
[evaluated](narwhals-evaluation.md) and deferred in favor of backend-specific
preservation controls. Optional dependencies remain lazy. Existing file bytes,
catalog tables and application SQL foreign keys are preserved.

Local verification: 296 passed, 2 Windows symlink-permission skips. All six
Windows/Linux Python 3.12-3.14 jobs passed all 298 tests, lint/build, and fresh
core/format artifact installations in
[S05 CI](https://github.com/radioflyer28/MeldStore/actions/runs/35134699759).
See the [S05 verification record](s05-verification.md).

Implement bytes, Pandas Parquet, NumPy NPZ, Blosc2 arrays, and custom registration.
Keep optional imports lazy. Record format descriptors/encoding versions. Test
DataFrame indexes, nullable columns, categories/time zones, array dtype/shape,
empty values, unsupported object arrays, missing extras, and unavailable handler
versions. No implicit pickle, re-encoding on passthrough import, or SQL in handlers.

### S06 — operational portability

Implemented: [offline backup/restore and local transfer](backup.md) preserve the
whole shared catalog and verified ready payloads, including application SQL
tables/constraints and retirement/encoding history. A logical SQLite SQL export
and explicit application schema declaration accompany physical snapshots.
Restoration and transfer require fresh destinations; all copying precedes final
catalog publication. S06 requires one logical root and resolved lifecycle state.

Local verification: 349 passed, 4 Windows symlink-permission skips. All six
Windows/Linux Python 3.12-3.14 jobs passed all 353 tests with no skips, lint/build,
and fresh core/format artifact backup/restore checks in
[S06 CI](https://github.com/radioflyer28/MeldStore/actions/runs/35156240446).
See the [S06 verification record](s06-verification.md).

Snapshot the shared SQL catalog and referenced payloads under explicit quiescence.
Specify a portable application/library metadata export, since MeldDB logical
export excludes external tables. Define the application's contribution for its
own tables and migrations; generic code cannot infer domain serialization.

Test interrupted export/restore to fresh destinations, invalid manifests, retained
IDs, reference integrity, destination no-overwrite, and verified relocation.
Backup restore must cover application-owned SQL, not only library tables.

### S06a — SQLite tuning and concurrency

Implemented: see [SQLite settings and concurrency contracts](sqlite.md) and
[S06a verification](s06a-verification.md). Local verification: 392 passed with
4 Windows symlink-permission skips. All six Windows/Linux Python 3.12-3.14 jobs
passed 396 tests with all extras and no skips, lint/build and fresh installs in
[S06a CI](https://github.com/radioflyer28/MeldStore/actions/runs/35158912896).
The slice includes patched-runtime WAL enforcement, explicit read transactions,
an opt-in pending-queue index, migration ID seeks, and exclusive statistics and
checkpoint maintenance. It does not change the completed S06 acceptance record.

Baseline before S06a: the pinned MeldDB backend requests WAL for newly created catalogs but
preserves the journal mode of existing databases. The direct sqlite3 adapter does
not explicitly enable WAL. Both use foreign keys and synchronous FULL, with a
configurable five-second default lock timeout. Metadata indexes are generated
from schema declarations, and a representative query-plan test exists. Metadata
reads currently use write transactions; restored standalone snapshots can remain
in rollback mode. These are tuning gaps, not evidence of qualified concurrency.

1. Define explicit, consistent live-catalog journal-mode configuration through
   both adapters. Cover creation, existing databases, reopen, restore and transfer;
   verify the effective setting and report unsupported modes or lock conflicts.
   Document local-filesystem requirements and how existing catalogs adopt the
   policy. Keep synchronous FULL as the durability default and retain configurable
   lock timeouts; do not silently trade durability for throughput.
2. Introduce genuine read transactions for metadata-only operations, including
   stat/find, without changing explicit application write-transaction composition.
   Test snapshot consistency, read/write concurrency, failed transactions and
   write rejection in read-only contexts. Keep object-storage I/O outside SQL
   write transactions.
3. Preserve rollback DELETE mode for the separate maintenance/access-gate
   databases: their exclusion protocol depends on it. Keep detached backup
   artifacts standalone; apply live-catalog policy when opening a restored copy,
   not by mutating the backup. Re-test exclusive maintenance and backup/restore
   with concurrent participants and WAL-backed source catalogs.
4. Audit query plans for metadata filters, ordering/keyset pagination, application
   joins and foreign-key checks, migration batches, and lifecycle/cleanup queues.
   Add indexes only where representative plans and measurements justify their
   read benefit and write/storage cost. Preserve user-owned indexes and SQL
   constraints, and provide an explicit upgrade path for existing catalogs.
5. Evaluate planner-statistics maintenance (ANALYZE/PRAGMA optimize) and checkpoint
   behavior, including long-lived readers and WAL growth. Document explicit
   maintenance boundaries. Leave cache/mmap and other tuning at defaults unless
   measurements justify changes; avoid speculative global PRAGMAs.

Exit evidence: both adapters pass settings, read/write contention, maintenance
exclusion and restore tests on Windows/Linux. Record before/after query plans,
latency, SQL call counts and index costs using a deterministic metadata workload
with declared row counts and selectivity. Record SQLite/runtime versions and
checkpoint/WAL behavior. Distinguish process-crash tests from power-loss claims.
S08 must validate these choices against its integrated consumer workload; no
blanket performance claim follows from enabling WAL or passing one index test.

### S07 — S3

Implemented and qualified on pinned RustFS: [S3 API and operational limits](s3.md).
S3Storage uses explicit endpoint/bucket/prefix configuration, path-bound local
coordination, create-only publication, and verified whole-object reads. Offline
backup/transfer supports S3 payloads while the catalog remains local; destination
catalog publication follows verification of all copied payloads.

Acceptance changed September 16, 2026 by explicit user approval: a real
Docker-local RustFS or Garage S3-compatible service may satisfy S07 backend
qualification. Use loopback, ephemeral credentials, and an explicitly designated
disposable bucket/prefix. AWS-specific evidence is deferred; this change does
not mean AWS tests passed or confer AWS certification.

Exercise conditional PUT (including empty payloads), UploadPartCopy and
conditional CompleteMultipartUpload, competing create-only publications,
multipart list/abort remnants, stream verification, transport errors, uncertain
publication, pending deletion, and offline fresh-destination transfer. Include
S3-to-local backup and identity mismatch/no-adoption cases. Avoid relying on
rename atomicity or ETags as content digests. Record the exact backend image,
configuration and dependency versions, memory/disk behavior, and remaining gaps
in the [S07 verification record](s07-verification.md). All seven jobs passed in
[S07 CI](https://github.com/radioflyer28/MeldStore/actions/runs/35161604054):
502 all-extra tests per Windows/Linux matrix job, 21 separate live RustFS tests,
capability/outage probes, lint, builds and fresh artifact installs.

### S08 — consumers and release

Implemented and qualified: independent application examples, SQL integration
tests, private 100-file local/RustFS workloads, and Apache-2.0 packaging.
All seven Windows/Linux/RustFS CI jobs passed for implementation commit `c984645`.
See the [consumer guide](consumers.md)
and [S08 evidence and remaining release gates](s08-verification.md). No domain
entities are added to the library. Package release remains gated by the pinned
MeldDB dependency's licensing and distribution; no release is authorized here.

First ship a dataset/document example that works without cUAS imports. Separately
implement the cUAS SQL example with radar multi-track and aircraft single-track
truth fixtures, domain-owned constraints, and correlation queries. Keep original
files intact and retrieve each selected file once regardless of matched tracks.

Use representative 100–300 MB payloads and roughly 10–30 GB total; record actual
track counts, memory, disk staging, hash and import/retrieval timings, and SQL calls.
Validate S06a settings and index choices against the integrated workload, including
metadata growth and concurrent readers/writers; report regressions and limits.
Run wheel/sdist clean-install tests, both adapters, all optional handler sets, and
Windows/Linux checks. Mark macOS/PostgreSQL unqualified unless separately proven.
Select a license and resolve dependency distribution before package release.

Measured follow-up, not additional MVP scope: S08's 301,296-occurrence
application query chose a primary-key scan instead of its declared interval
index. Evaluate interval-index orientation and SQL bulk-insert approaches in
application benchmarks, with before/after plans, selectivity, write cost and
memory evidence. Do not turn a consumer's index layout into a MeldStore core
schema or tune global SQLite settings without measurements.

### S09 — MVP release preparation

Authorized closeout of the existing MVP, not a new feature milestone. Prepare
`0.1.0rc1`, merge the completed S07/S08 history, document qualification and
known limits, verify versioned wheel/sdist contents and fresh installations,
and resolve dependency licensing/distribution. See the
[release checklist](release.md). Actual package publication requires a separate
explicit approval; a version bump or green CI is not a release announcement.

PostgreSQL metadata plus remote payloads and cross-host coordination are deferred
to a future milestone, as agreed after S08. Keep the SQLite embedded deployment
and distinguish a remote object backend from a remotely shared catalog.

## Completion rules

Each slice records behavior changes, test commands/results, and remaining limits.
No green unit suite substitutes for real S3-compatible service or process recovery evidence. Do not
mark the MVP complete with missing integrated acceptance. Implementation is not
authorization to publish a package or use arbitrary cloud storage resources.

Next action: resolve dependency licensing/distribution before considering a
package release. Consumer qualification does not itself authorize publication.
AWS-specific qualification remains deferred.
