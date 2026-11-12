# PostgreSQL whole-store backup and operator-assisted restore

Current integrated status and tested limits: [PostgreSQL qualification](postgresql-verification.md).

PostgreSQL backups use a backend-tagged version-2 manifest, a native custom-format
archive (`catalog.pgcustom`) and verified payload files. Existing SQLite version-1
artifacts and `restore_backup` remain unchanged. A native database archive alone is
not a complete MeldStore backup. The workflow requires trusted application SQL and
a trusted artifact; native restore executes SQL from that artifact.

## Prerequisites

- PostgreSQL 17 and PostgreSQL 17 `pg_dump`, `pg_restore` and `psql` client tools.
  The operator explicitly provisions the source/target databases and credentials.
- One coordinating host with the persistent local coordinator and all application
  SQL writers/readers participating in the [exclusion protocol](postgresql-coordination.md).
- A role able to read the complete source database and create/restore its supported
  objects at the target. Review roles, extensions, collations, tablespaces, language
  runtimes and other external dependencies beforehand.
- A fresh dedicated target database, new coordinator directory and empty S3 prefix
  for restore. Never restore over an admitted store or use cleanup flags to replace it.

The native archive covers the dedicated database's ordinary SQL tables/data,
constraints, indexes, views, functions and triggers, including application schemas
outside `public`. PostgreSQL cluster roles and external services are not database
archive content. Ownership/ACL restoration is deliberately omitted; the operator
provisions and grants the target role explicitly. Foreign-table definitions do not
make remote foreign-server data part of the blob-store backup. Application
verification must cover dependencies and behavior the application relies on.

## Backup

```python
import os
from meldstore import Catalog, PostgresArchiveTools, Store

dsn = os.environ["SOURCE_DSN"]
tools = PostgresArchiveTools(dsn, timeout=600)
with Catalog.connect_postgres(dsn, storage=storage, maintenance=True) as catalog:
    store = Store(catalog, storage)
    manifest = store.backup(
        "new-backup-directory",
        application={"id": "my-application", "schema_revision": "3"},
        tools=tools,
    )
```

All participants must close before opening the exclusive Catalog. Backup rejects
unfinished prepared uploads, publishing records, pending deletions or metadata
migrations, damaged owned structure, disabled triggers and unvalidated constraints.
Resolve these explicitly first. Unreferenced orphans and incomplete multipart
uploads are not automatically collected or included as referenced payloads.

MeldStore exports a read-transaction snapshot and supplies it to `pg_dump`. This
ties the archive to the database/snapshot inspected through the Catalog, and
rejects a tool configured for a different database. It does not use provider-managed
logical export. After the native archive closes, it copies every ready payload,
verifying size/XXH3-128 at source and artifact. It writes the manifest last, with
archive/identity digests, object descriptors, SQL object/table-count inventory,
application declaration, and database/tool versions. Failed backup retains partial
files without a completed manifest. Payload copying is outside SQL write scopes.

Tool connection fields are passed in child environment variables, never stored in
the manifest or placed in DSN command-line arguments. Library errors redact tool
output and connection details. `prefix=()` is an optional explicit argv wrapper
for operator-controlled environments; it never invokes a shell. The disposable
lab uses Docker's installed PostgreSQL tools. Defaults use installed client tools
on PATH; executable names and timeout are explicit constructor options.

## Restore and verify

```python
from meldstore import restore_postgres_backup

target_dsn = os.environ["TARGET_DSN"]

def verify_application(tx):
    # Replace with the application's expected schema, joins, counts and values.
    return tx.sql("SELECT COUNT(*) AS n FROM app_links")[0]["n"] == expected_count

result = restore_postgres_backup(
    "new-backup-directory", target_dsn, "new-coordinator",
    tools=PostgresArchiveTools(target_dsn, timeout=600),
    verify_application=verify_application,
    bucket=os.environ["TARGET_BUCKET"], prefix=os.environ["TARGET_PREFIX"],
    config=target_s3_config,
)
assert result["destination_ready"]
```

The operator supplies client tools, target role/database, explicit storage location
and application verification. Restore validates the completed manifest and every
artifact file/payload before target mutation. It checks database/prefix/path
freshness and uses a random SQL probe to prove that the restore tools address that
same database. A failed probe retains evidence in the isolated target and never
starts native archive restoration.

`pg_restore` runs a single transaction with error-stop behavior and without
ownership/ACL restoration. Afterward MeldStore compares the archived catalog
identity, owned SQL structure, SQL inventory/table counts and payload descriptors
with the manifest. The application callback runs in a read-only transaction and
must return `True`. The restored database is still unavailable to Store admission.
MeldStore then copies payloads create-only under their original keys and verifies
them. Only after these checks does it publish a new physical binding to the fresh
coordinator/prefix. Public IDs, versions, object keys and logical storage identity
remain unchanged. There is no implicit client cutover.

If restore fails or is interrupted, retain the isolated destination for inspection;
it may contain restored SQL or copied objects but cannot be adopted automatically.
Use fresh destinations for a new attempt. A lost final commit response is uncertain:
explicitly reopen with the existing coordinator and inspect stable blob IDs and
application SQL before any cleanup. A successful reopen can establish that final
publication committed; it does not excuse checking application correctness.

After success, retrieve representative payloads and run application queries before
switching clients. Keep the source/artifact as the rollback reference. New writes
after cutover require explicit reconciliation before switching back.

## Qualified scope and evidence

The disposable lab uses pinned PostgreSQL 17.11/RustFS, the exact MeldDB Git pin,
synthetic fixtures and one Windows coordinating host. No AWS, multi-host, arbitrary
PostgreSQL-version or blanket MeldDB managed-schema portability claim follows.
The lab's native tool versions are recorded in its environment report; each backup
also records native versions. Use `uv run --frozen --all-extras python
tools/postgres_lab.py --with-rustfs` for the combined acceptance run.

Native recovery baseline `20261111T212102Z-c5f529`: 211 live tests passed with no
skips, including whole application SQL, another SQL schema, manifest-last failure,
corrupted/incomplete artifacts, wrong tool databases, occupied destinations and
interrupted restore admission. Existing SQLite backup tests passed 55 with two
Windows symlink-permission skips. Final integrated evidence is recorded separately.

PostgreSQL's [pg_dump documentation](https://www.postgresql.org/docs/17/app-pgdump.html)
describes custom archives and synchronized snapshots; [pg_restore documentation](https://www.postgresql.org/docs/17/app-pgrestore.html)
describes single-transaction, error-stop restoration. Application prerequisites and
MeldStore's catalog-plus-payload/readiness checks remain separate responsibilities.
