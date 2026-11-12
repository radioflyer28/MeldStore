# Offline SQLite to PostgreSQL cutover

Current integrated status and tested limits: [PostgreSQL qualification](postgresql-verification.md).

`preflight_sqlite_to_postgres` and `transfer_sqlite_to_postgres` are explicit
offline operations. They require an exclusive source Catalog and a fresh dedicated
PostgreSQL database using `public`. Transfer additionally requires a fresh local
coordinator directory and empty explicit S3 prefix. The source remains unchanged;
the caller decides when to switch clients. PostgreSQL whole-store recovery and
integrated deployment qualification remain separate gates.

## Application SQL manifest

MeldStore translates its own catalog layout and preserves public blob IDs, schema
and concurrency versions, physical descriptors, encoding declarations, completed
migration history and retirement/cleanup history. It preserves logical storage ID
and object keys while assigning a new physical catalog/coordinator binding.
It never translates application SQL automatically.

Supply `SQLTransferManifest(application, source_objects, target_objects, ddl,
tables, verify)`. The application declaration contains nonempty `id` and
`schema_revision`. Source and target inventories map exact object names to kinds.
The source inventory must enumerate every external table, explicit index, view
and trigger. SQLite implicit autoindexes are excluded. The target inventory also
includes PostgreSQL-generated primary/unique indexes, functions and sequences.
Do not use the reserved `ms_` or `sqlite_` namespaces for application objects.

`ddl` is ordered, trusted PostgreSQL SQL using native bindings/quoting. It creates
the target application schema and constraints after MeldStore's tables are loaded.
Each source application table needs exactly one `TableTransfer(source, target,
fields, transform=None)`. Target tables must be distinct. `fields` maps target
column names to explicit MeldStore scalar `Field` declarations. The optional
transform receives a plain source row and returns exactly the target columns.
Transforms produce SQL values: booleans are 0/1 and timestamps canonical UTC text.
Use an explicit transform for unsupported application representations.

The same strict scalar checks apply as for metadata: no implicit coercion,
nonfinite floats, out-of-range integers, embedded NUL PostgreSQL text or overlong
PostgreSQL identifiers. Metadata/application rows are bounded by explicit
`max_rows` (default 100,000, maximum 1,000,000); payload bytes are copied in chunks.
Provide application tables in their dependency order. Deferred constraints may be
used where appropriate. `verify(tx)` must explicitly return `True` after checking
the application's joins, constraints and expected values. Library verification
also checks target inventory, transferred row counts and owned SQL structure.

For a complete executable manifest with tables, a foreign-key junction, view,
index and trigger, see `tests/postgres_provider/test_transfer_postgres.py`.

## Preflight and transfer

```python
import os
from meldstore import Catalog, LocalStorage, Store
from meldstore import preflight_sqlite_to_postgres, transfer_sqlite_to_postgres

with Catalog("source.sqlite", maintenance=True) as catalog:
    source = Store(catalog, LocalStorage("source-objects"))
    report = preflight_sqlite_to_postgres(
        source, os.environ["TARGET_DSN"], manifest=application_manifest,
    )
    result = transfer_sqlite_to_postgres(
        source, os.environ["TARGET_DSN"], "new-coordinator",
        manifest=application_manifest,
        bucket=os.environ["TARGET_BUCKET"], prefix=os.environ["TARGET_PREFIX"],
        config=s3_config,
    )
    assert result["destination_ready"]
```

All source clients, application SQL writers and payload readers must stop first.
The source access gate stays exclusive through snapshot, preflight and transfer.
The PostgreSQL target must be dedicated and quiesced; do not let unbound SQL clients
write to it during cutover. The final deployment uses the cooperative protocol in
[PostgreSQL coordination](postgresql-coordination.md).

Preflight snapshots the complete source catalog, validates lifecycle/SQL integrity,
audits application mappings and values, and executes the proposed target import
inside a rollback-only transaction. Pending constraints are forced through the
public provider validation API before rollback. Successful preflight leaves no
target tables or payloads. Application callbacks may run more than once: avoid
external side effects and nondeterministic transforms. Rollback cannot undo such
effects or make arbitrary trusted application SQL safe.

Transfer repeats preflight from its own quiesced source snapshot. It checks the
target database, prefix and local path before creating destination storage. Each
referenced payload is verified at source, copied create-only under its original
key, and verified at destination. Only then are owned/application SQL and the new
binding published in one database transaction. The local identity marker alone
cannot admit a client if that transaction rolls back. The returned result includes
the new catalog ID, logical storage ID, coordinator directory and application
declaration; it never includes connection or S3 credentials.

## Interruption, cutover and rollback

An interruption can retain copied objects, staging files or local identity evidence.
Preserve that destination for inspection. It is not automatically adopted, cleared,
overwritten or resumed; retry with fresh destinations. After an uncertain final
commit, explicitly reopen the target with its existing coordinator and inspect
stable blob IDs, descriptors and application joins. Do not infer rollback or delete
potentially referenced objects from a missing response.

Successful transfer leaves the source intact and does not switch application
configuration. Verify representative target retrieval and application queries,
then explicitly point clients at the target. Before target writes begin, the
unchanged source remains the rollback option. After target writes begin, returning
to the source requires explicit data reconciliation. Keep source and target
coordinator directories separate and never run two hosts against one binding.

Qualification: live run `20261111T211144Z-bdee2e` passed 195 tests with zero skips,
including both SQLite source adapters, versioned metadata, preserved application
SQL/history, copied-byte verification, occupied targets, interrupted publication
and destination tampering. This intermediate evidence covers cutover. Native recovery and final integrated
results are recorded in the current verification record linked above.
