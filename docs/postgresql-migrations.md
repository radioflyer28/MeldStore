# PostgreSQL metadata migrations

Task 2.3 implements the SQL migration path behind the existing `Store.migrate`
and `Store.migration_status` APIs. PostgreSQL Store construction remains gated on
task 2.4's persistent coordinator/storage binding. Qualification here uses a
deliberate SQL-only storage stub, synthetic rows, and no payload bytes. Do not
bypass the production gate to deploy this path yet.

## Declarations and execution

Use the existing `MetadataMigration(name, source, target, transform_id, transform,
before_ddl=None, after_ddl=None)` declaration. Source and target are consecutive
versions of the same BlobSchema with unchanged handler/validator contracts.
Schema and migration names identify durable definitions; a reused migration name
must match its source/target versions and transformation ID.

The transformation receives ordinary normalized Python values, including bools
and aware UTC datetimes. It returns metadata conforming to the target declaration.
Unknown fields, incompatible field types, PostgreSQL-overlong field identifiers,
invalid values and embedded NULs fail explicitly. A changed field type needs a
new field name. Removed fields remain physical columns for older versions; each
version's constraints enforce required values and absence of undeclared values.

`migrate(plan, batch_size=100, max_batches=None, dry_run=False)` initializes the
target layout in an explicit transaction and commits bounded batches separately.
`max_batches=0` installs the layout/job without transforming rows. Each batch
commits transformed metadata, blob schema version, metadata concurrency version,
and the checkpoint together. On a failed or interrupted batch, earlier batches
remain committed and the failed batch rolls back. Reopen and submit the same
plan to resume. Never automatically retry an unknown commit outcome.

The SQL migration path performs no payload I/O. Blob IDs, object associations,
descriptors and digests remain unchanged. Old and new metadata versions can be
read concurrently; old-version rows are frozen for edits/deletion once the new
layout is installed. Latest-version rows remain editable with expected-version
checks. A transformed row increments its concurrency version exactly once.

## PostgreSQL layout and application relationships

PostgreSQL uses `ALTER TABLE` in place, preserving the metadata table and primary
key identities. Ordinary incoming references to `ms_blobs(id)` and the metadata
table's `id` continue working without SQLite's table-rebuild coordination hooks.
Application tables, views, indexes, triggers and constraints remain application-owned.
The migration refreshes only its owned guards, version-specific checks and indexes,
and its recorded structural layout. No CASCADE is used.

Dependencies on a library-owned index being replaced can require explicit DDL
coordination. Optional before/after hooks run transactionally; incoming foreign-key
definitions must be restored and the hooks cannot alter owned schema objects.
A dependency or constraint failure rolls back initialization, including hook SQL.
Callbacks and hooks must not perform external side effects. PostgreSQL sequence
increments, network calls, and filesystem writes do not become reversible merely
because surrounding SQL rolls back.

Initialization locks the format row and takes a native exclusive metadata-table
lock for DDL. It releases those locks when initialization commits. Batch workers
serialize on the migration row and lock selected source rows. Independent resumptions
cannot duplicate transformations; guarded edits on already migrated rows retain
their versions. Lock waits obey the configured provider timeout; there is no
implicit retry. Keep transformation work short and bounded.

## Dry-run constraint validation

A dry run uses one write transaction for initialization and every simulated batch.
It validates pending constraints, then intentionally rolls back schema, registry,
rows, transactional trigger effects and checkpoints. Existing committed progress
is retained when dry-running a partially completed job. Success describes the
observed database state, not a guarantee of a future run or commit.

MeldDB's public `Transaction.validate_constraints()` is the provider boundary.
MeldStore exposes it on its own transaction handle with error translation/redaction
and poisoning. PostgreSQL forces all pending deferred FK, unique/exclusion and
constraint-trigger checks and leaves them immediate for that transaction. SQLite
checks foreign keys without changing their timing. The method never commits,
reconnects or retries; it requires a live write transaction. Raw transaction/session
controls remain rejected. Lost connections retain unknown outcomes and quarantine.

The providing revision is
`01e27441d7512f85663c8a86986429d62a69bff9`, published on MeldDB's
`codex/transaction-constraint-validation` branch. Both pyproject.toml and uv.lock
retain an exact Git dependency. The shared OpenSpec change is
`add-transaction-constraint-validation-contract` in meld-platform-specs;
MeldDB's owning change is `add-transaction-constraint-validation`.

Full payload deployment, cutover and backup/restore acceptance remain separate
gates; this SQL migration qualification does not expand those support claims.


## Qualification — November 11, 2026

- Mandatory PostgreSQL lab `20261111T202225Z-ada0d2`: **138 passed, no skips**.
  This includes the unchanged provider baseline, catalog parity, and 18 new
  migration/provider-adoption checks. PostgreSQL 17.11 uses the pinned image
  recorded in the provider verification guide; all disposable containers were removed.
- Full `uv run --extra test pytest -q --tb=short`, with all optional formats
  installed from the frozen lock: **652 passed, 171 skipped**. The skips are the
  138 separately passed opt-in PostgreSQL tests and 33 existing environment cases.
- Focused SQLite backup/migration regressions: 82 passed, 2 environment skips.
  Detached SQLite artifact queries now explicitly identify their dialect; this
  fixed the backup regression found while generalizing migration inspection.
- Ruff and strict MeldStore/MeldDB/shared-change validation passed. Canonical
  shared-spec validation passes with its pre-existing long bulk-requirement warning.
- uv wheel and sdist builds passed. Both artifacts passed clean-install smoke
  tests with core-only dependencies and with PostgreSQL plus every optional format.
  Both SQLite adapters exercised installed constraint validation, metadata
  migrations, file I/O and backup/restore. Core installs contained no Psycopg.
- Provider implementation qualification: 96 focused SQLite/PostgreSQL checks
  passed with 22 backend-specific skips; the full MeldDB SQLite suite passed 378
  with 25 skips. MeldDB core remains dependency-free.
- Synthetic SQL data only; no private Parquet data or private source identifiers
  were used. No PostgreSQL payload-storage or recovery support is inferred.

Task 2.3 is complete. Next is task 2.4: bind the dedicated PostgreSQL catalog,
persistent local coordinator and RustFS prefix, and qualify access/maintenance.
MeldDB's prerequisite is committed and pushed on its feature branch. MeldStore
and shared-store changes remain local and uncommitted; none of these changes
has been merged, archived or released.
