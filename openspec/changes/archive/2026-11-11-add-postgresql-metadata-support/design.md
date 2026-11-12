# Design

## Context

MeldStore's catalog wrapper already separates public transaction ownership from the SQLite driver. Its schema installation, lifecycle triggers, migration inspection, backup, and access leases still assume SQLite. MeldDB exposes `connect`, explicit transactions, raw SQL, and bulk SQL for its experimental PostgreSQL backend. The current backend uses a repeatable-read read-only transaction for reads. MeldDB's physical backup is SQLite-specific and its managed logical import excludes MeldStore and application tables.

## Goals / Non-Goals

- Qualify PostgreSQL 17 and the existing pinned RustFS lab with one coordinating host. Keep catalog and payload failure handling observable to the caller.
- Prove fresh deployment first, then a verified offline migration and a PostgreSQL backup/restore drill. Keep support qualification gated on all three.
- Defer multi-host coordination, live replication or cutover, arbitrary SQL/DDL translation, and wider service qualification.

## Decisions

### Qualify the provider at its public boundary

Use MeldDB's `connect`, transaction scopes, and public `sql`/`sql_many` against a dedicated PostgreSQL database. Keep Psycopg behind an optional dependency and the provider adapter. A provider conformance test should cover parameter binding, engine-enforced read-only snapshots, transaction-control rejection, bulk summaries, and structured outcomes after connection loss. Check provider error mapping for constraints, lock/deadlock/serialization failures, and transport loss. Route an actual provider gap into a MeldDB-local change and revise the shared contract when the guarantees change. Advance the exact pin only with focused evidence. A direct Psycopg runtime adapter would duplicate the provider boundary and needs its own proposal if the existing interface cannot meet it.

### Select the PostgreSQL connection explicitly

Task 2.1 adds `Catalog.connect_postgres(dsn, timeout=5.0, connect_timeout=5)` and
the optional `postgres` extra. Keep filesystem construction unchanged for SQLite;
reject recognizable DSNs there before file creation. Require explicit host/database/user
and do not infer a coordinator or database identity from a secret-bearing DSN.
Use public MeldDB SQL and transaction interfaces through a redaction boundary;
Psycopg is used only for native connection-parameter parsing at this layer.
Safe diagnostics retain categories, SQLSTATE and structured outcome evidence while
omitting provider messages and raw causes that can contain credentials or SQL data.
The connection-only slice originally rejected schema installation and blob binding.
Task 2.2 enables explicit catalog schema installation; blob binding remains gated
until its owning task implements that contract. Opening creates no persistent marker.

### Port catalog behavior by dialect

Add a narrow dialect seam for library SQL, DDL, introspection, and schema upgrades. Use engine-native constraints, triggers, and PostgreSQL catalog inspection. Application SQL uses the selected backend's documented native binding convention. Build library placeholders explicitly, since textual rewriting can misread literals and dialect operators. Test transaction/session control, including PostgreSQL `SET`, `RESET`, comments, and multi-statement input, at the raw SQL boundary.

Keep the common scalar normalization and API result types. Preserve canonical UTC timestamp text and bytewise ordering where SQLite behavior depends on it. Allocate stable, bounded internal SQL identifiers and test collisions. Preflight unsupported application identifiers and values, including overlong UTF-8 identifiers and embedded NULs, before the destination is modified. Require verified constraints and versioned, resumable metadata changes on the PostgreSQL path. Use guarded updates and appropriate row locking for write invariants previously serialized by SQLite's writer lock.

See [PostgreSQL transaction isolation](https://www.postgresql.org/docs/17/transaction-iso.html), [SQL identifiers](https://www.postgresql.org/docs/17/sql-syntax-lexical.html#SQL-SYNTAX-IDENTIFIERS), and [Psycopg parameter binding](https://www.psycopg.org/psycopg3/docs/basic/params.html).

### Record the installed PostgreSQL structure explicitly

Task 2.2 keeps SQLite DDL untouched and adds native PostgreSQL catalog, payload,
encoding, and retirement definitions. `ms_pg_layout` records each owned object's
generated DDL and engine-normalized structure atomically with installation.
Verification compares live PostgreSQL catalogs against that record and the current
DDL definition, including columns/defaults/collations, constraints/index validity,
trigger enablement, and trigger function bodies/settings. Application-owned
tables and additive indexes/constraints are retained. This detects accidental
drift, not malicious rewriting of both the SQL objects and registry. Metadata
versioning in task 2.3 evolves the layouts explicitly.

`BlobSchema.table_name_for(adapter)` exposes deterministic bounded PostgreSQL
names while retaining the existing `table_name` SQLite contract. Long internal
names use a 160-bit truncated SHA-256 name allocation; registration checks reject
collisions. This does not add entry fingerprints or change XXH3 payload hashing.
Reject user field identifiers exceeding 63 UTF-8 bytes before DDL rather than
allowing PostgreSQL truncation. Use BIGINT/DOUBLE PRECISION and C-collated text,
including canonical UTC timestamp text and integer 0/1 booleans. Generated SQL
uses native placeholders, explicit null placement, and null-safe keyset equality.

Schema installers serialize on the format row once installed. Guarded metadata
updates retain a single conditional SQL update, with competing connection tests.
Actual Store metadata methods are tested with an explicit SQL-only storage stub;
the production storage-binding gate remains closed. Concurrent lifecycle writes
and real payload operations are still acceptance work in tasks 2.4/2.5.

The shared provider contract was reviewed for this slice. It already assigns
catalog DDL to MeldStore and supplies the public transaction/SQL behavior used
here. No new provider guarantee or pin change is introduced, so no shared-store
delta or MeldDB implementation change is needed for task 2.2.

### Bind the coordinator explicitly

Use a persistent, non-secret catalog identity and the existing single-host coordination model to bind the dedicated database, local directory, and S3 prefix. Verify the binding before enabling writes or destructive maintenance. Keep access leases in the local coordinator directory with explicit quiescence covering every participating process and payload reader. Direct application SQL writers must follow the same maintenance protocol. Cross-host admission and distributed leases require their own design and evidence.

### Make cutover explicit and recoverable

Inventory a quiesced SQLite store using its whole-catalog backup and validate supported source state before touching the destination. Require a fresh dedicated PostgreSQL database and a fresh RustFS prefix with a separate coordinator directory. Create the PostgreSQL catalog using MeldStore's DDL and import its owned rows using explicit ordered mappings. Require an application manifest declaring the expected source SQL objects, destination DDL, transfer, and verification. Check omissions, constraints, row counts, and representative joins before marking the destination usable. Copy or stage payloads create-only and verify size and XXH3-128 before completing destination publication. Preserve keys and logical storage identity using the fresh-replica mechanism. An interrupted attempt can leave isolated staging work for inspection. It cannot overwrite the source or an existing destination. Keep operator cutover separate from transfer completion.

### Recover from a whole-store artifact

Take a PostgreSQL-native archive of the dedicated database while the coordinator and application SQL writers are quiesced, then copy and verify ready payloads before publishing the checksummed manifest. Use PostgreSQL client tools for an operator-assisted restore of a trusted artifact to a fresh, isolated target. Record archive format, engine/tool versions, and application restore prerequisites. Validate the restored application SQL and exact payload descriptors before admitting clients. Keep the current SQLite artifact format and restore path. The native archive has database scope, so document prerequisites for roles, privileges and any external dependencies. [PostgreSQL archive tooling](https://www.postgresql.org/docs/17/app-pgdump.html)

## Risks / Trade-offs

- PostgreSQL write concurrency can expose races masked by SQLite's writer lock. Test competing publication, guarded edits, and retirement with independent connections.
- A network failure at commit leaves outcome evidence incomplete. Quarantine, reopen, and look up the original operation identity before cleanup or retry.
- A fresh storage prefix requires temporary capacity for a second copy. Estimate capacity during preflight and report incomplete staging explicitly.
- Catalog backup plus payload copy depends on application cooperation during the maintenance window. Test lease conflicts and document how to stop external SQL writers.
- Application DDL can use backend-specific behavior. Require an explicit mapping and verification from the application owner.

## Migration Plan

1. Qualify the exact MeldDB revision and make any provider contract changes in their owning repos.
2. Build and qualify the opt-in adapter on a fresh PostgreSQL 17 and RustFS deployment, including failure injection.
3. Demonstrate offline SQLite cutover with ordinary SQL references, then demonstrate backup and restore of the resulting PostgreSQL store.
4. Publish the tested topology and limitations after the integrated qualification. Before cutover, retain the SQLite source as the fallback. Once PostgreSQL accepts writes, switching back requires an explicit data reconciliation plan.


## Task 2.3 implementation decisions

PostgreSQL evolves the existing metadata table with ALTER TABLE, preserving its
primary key and ordinary incoming ID foreign keys. Replace only owned checks,
indexes and guards, retain application SQL, and refresh the owned layout registry
within the same transaction. Do not CASCADE; dependencies on replaced indexes
require explicit application coordination. Validate hook preservation of owned
objects and incoming FK definitions. Initial schema DDL has a native exclusive
table lock, while resumable batches serialize on the migration row and lock source
rows. Shared normalization, callbacks, guarded updates and progress logic remain
in the common runner. No payload I/O is performed.

On November 11 the owner explicitly approved a new MeldDB provider method after
the dry-run constraint gap was identified. PostgreSQL deferred constraints cannot
be validated by a rollback alone, and the existing public SQL boundary rejects SET.
MeldDB now owns explicit transaction constraint validation; it forces deferred
checks without committing and leaves PostgreSQL constraints immediate. MeldStore
preserves failure poisoning and redaction and invokes this before intentional
dry-run rollback. SQLite validation retains its existing timing. No anonymous
block/private-driver workaround is used.

The provider change is add-transaction-constraint-validation and the shared-store
change is add-transaction-constraint-validation-contract. The exact implementation
pin is 01e27441d7512f85663c8a86986429d62a69bff9. Qualification includes deferred
application foreign keys, uniqueness and constraint triggers, process exit,
concurrent resumptions/guarded edits, and existing SQLite backup/migration behavior.
Callbacks and nontransactional external effects cannot be undone by a dry run.


## Task 2.4 implementation decisions

Explicit `bind_postgres_storage(storage, initialize=True)` bootstraps a quiesced,
dedicated public-schema catalog after the first schema installation. The catalog
must contain no blobs/prepared payloads and the prefix only its S3 marker. Store
identity is recorded in an immutable SQL row and a create-only local JSON marker;
the existing local rollback-mode access gate uses the random catalog ID as owner.
No DSN or credentials are persisted. Failed initialization retains evidence and
cannot be repaired/adopted by a later ordinary open.

Reopens use `connect_postgres(dsn, storage=storage, maintenance=False)` or explicit
binding on an idle SQL-only handle. Verify local/remote storage markers before SQL,
then the registry/layout and binding in SQL. Reopens share the initialization gate;
bootstrap alone requires exclusive initialization. Lifetime shared/exclusive leases
retain the existing single-host maintenance protocol. `storage_access()` lets an
application retain admission through separate SQL connections and payload readers.
See docs/postgresql-coordination.md for the cooperative writer protocol and limits.

This uses existing MeldDB public guarantees and leaves the shared provider contract
and exact pin unchanged. No cleanup, cutover or archive qualification follows from
admission alone; task 2.5 and the recovery tasks remain required.


## Tasks 3.1/3.2 implementation decisions

Application SQL transfer uses an explicit SQLTransferManifest: exact source/target
object inventories, native target DDL, ordered table mappings with strict scalar
fields and optional transforms, plus a required verification callback. Preflight
uses a detached SQLite snapshot and rollback-only target import with forced pending
constraints. It rejects omitted mappings, incompatible values and occupied targets.
Owned layouts are regenerated natively; original IDs, metadata versions, logical
storage identity, object keys and completed lifecycle/migration history persist.

Transfer holds source exclusion, validates a fresh database/prefix/coordinator,
copies and verifies bytes before SQL publication, then commits owned/application
SQL and the physical destination binding together. Failures retain unadoptable
evidence; retries require fresh destinations. No automatic cutover or source delete
occurs. Detailed API/protocol and test evidence: docs/postgresql-transfer.md.

Shared recovery boundary: qualify-postgresql-metadata-recovery-contract in
meld-platform-specs. This contract owns archive/qualification sequencing only;
implementation and tests remain in MeldStore.


## Tasks 4.1/4.2 implementation decisions

PostgresArchiveTools wraps explicit PostgreSQL 17 native client argv, with libpq
connection options in the child environment and generic redacted failures.
Backup supplies a public-SQL exported read snapshot to pg_dump, tying native
archive creation to the inspected Catalog. Native archive output streams to disk;
payload copies and hashes occur outside SQL write scopes. Backend-tagged v2
manifests publish last and record whole SQL inventory/counts and client/engine
versions. Existing SQLite v1 paths remain unchanged.

Operator-assisted restore requires verified complete artifacts, fresh database,
prefix and coordinator, explicit tools, and a read-only application verifier.
A random public-SQL probe checks the tool connection before pg_restore writes.
Native restore uses one transaction; SQL structure/inventory/descriptors and
application checks precede verified payload copies and final physical rebinding.
Failed targets retain unadoptable evidence; uncertain final commits require reopen.
Ownership/ACL/cluster-role and external dependency provisioning remain explicit
operator prerequisites. See docs/postgresql-recovery.md for procedures and limits.
