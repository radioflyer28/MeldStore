# PostgreSQL and S3 coordination

Current integrated status and tested limits: [PostgreSQL qualification](postgresql-verification.md).

Task 2.4 implements identity binding and cooperative exclusion for one dedicated
PostgreSQL 17 database and one S3 prefix on one coordinating host. This is still
experimental: publication/retirement qualification, transfer, whole-store recovery
and integrated deployment acceptance remain open. Do not infer AWS or multi-host
support from the pinned Docker-local RustFS tests.

## Initialize once

Provision a dedicated database and a persistent local coordinator directory.
Every participant must use that same directory on the same host. It must reside
on a local filesystem with working SQLite rollback-mode locks, not a network share
or an ephemeral container filesystem. The bound deployment uses the database's
`public` schema. SQL-only catalogs can still use other schemas.

```python
import os
from meldstore import BlobSchema, Catalog, S3Storage, Store, Text

schema = BlobSchema("dataset", {"label": Text()})
storage = S3Storage(
    os.environ["MELDSTORE_BUCKET"],
    prefix=os.environ["MELDSTORE_PREFIX"],
    coordination_directory=os.environ["MELDSTORE_COORDINATOR"],
    config={
        "endpoint": os.environ["MELDSTORE_ENDPOINT"],
        "region": "us-east-1",
        "access_key_id": os.environ["MELDSTORE_ACCESS_KEY"],
        "secret_access_key": os.environ["MELDSTORE_SECRET_KEY"],
    },
)

# Explicit first-time bootstrap, with all application clients stopped.
with Catalog.connect_postgres(os.environ["MELDSTORE_DSN"]) as catalog:
    catalog.install_schema(schema)
    catalog_id = catalog.bind_postgres_storage(storage, initialize=True)
    store = Store(catalog, storage)
    store.install_schema(schema)
```

Initialization requires an installed, empty blob catalog, an unused local binding,
and a prefix containing only its existing S3 identity marker. It creates an opaque,
random catalog ID; records that ID, storage ID, coordinator ID and explicit
endpoint/bucket/prefix in `public.ms_store_binding` and `meldstore-postgres.json`;
and binds `meldstore-access.sqlite` to the catalog ID. SQL guards prevent ordinary
updates/deletes of the binding. Structural verification checks the owned SQL
layout on admission. The record contains no DSN, database password, S3 credential,
or entry fingerprint. The coordinator ID retains S3Storage's canonical path binding.

This is not a distributed transaction. S3 marker reads and inventory happen before
SQL transactions. The small local binding file is flushed before the initialization
commit; failed or interrupted initialization retains its evidence. No payload bytes
are transferred by binding. A lost commit response quarantines the Catalog as usual.
Reopen can succeed only if the existing database, local marker and gate agree.
Missing, partial or mismatched artifacts are not repaired or adopted automatically.
Preserve them for operator inspection; do not delete a gate to clear contention.

## Reopen and maintain

```python
with Catalog.connect_postgres(os.environ["MELDSTORE_DSN"], storage=storage) as catalog:
    store = Store(catalog, storage)
    rows = store.find(schema=schema)

# Stop/close all participants first, then explicitly acquire exclusive admission.
with Catalog.connect_postgres(
    os.environ["MELDSTORE_DSN"], storage=storage, maintenance=True,
) as catalog:
    catalog.require_maintenance(storage.root)
    # Run the appropriate qualified maintenance procedure here.
```

The factory verifies the existing binding and closes its connection on failed
admission. `bind_postgres_storage(storage)` is the equivalent explicit operation
on an idle SQL-only handle. Calling it again verifies the same binding; switching
roots or admission mode requires closing and reopening. `Store` checks the actual
S3Storage object as well as its path. Ordinary participants hold shared leases for
the entire Catalog lifetime. Maintenance holds an exclusive lease. Conflicts fail
with `BusyError`, without polling, retrying or silently stealing the gate. Multiple
ordinary clients can be admitted concurrently. Process exit releases OS locks.

Closing a Catalog while a Store materialization or `storage_access()` scope is
active fails. An exclusive Catalog cannot perform maintenance during its own
reader scope. Actual PostgreSQL cleanup/backup operations are still subsequent
tasks; acquiring exclusive admission alone does not qualify those operations.

## Participating external SQL writers and readers

SQL cannot force an arbitrary remote client to take a local filesystem lease.
Every direct SQL client, maintenance script and application migration must obey
this protocol. Uncooperative clients invalidate the maintenance guarantee.

1. On the coordinating host, construct S3Storage with the same persistent
   directory and explicit location; open a bound Catalog with `storage=storage`.
2. Keep that Catalog open before opening a separate SQL connection. Hold
   `catalog.storage_access()` through the separate connection's close and through
   every derived payload reader's exit. Use native transactions and bindings.
3. Change only application-owned SQL and documented metadata operations. Do not
   modify the library binding, lifecycle bookkeeping, markers or gate files.
4. For exclusive maintenance, stop new work and close all participants, including
   separate SQL connections and payload readers. Open a new bound Catalog with
   `maintenance=True`. Run maintenance only after `require_maintenance` succeeds.
5. Close the maintenance handle before admitting normal clients again. Resolve
   uncertain outcomes explicitly before destructive work.

```python
with Catalog.connect_postgres(os.environ["MELDSTORE_DSN"], storage=storage) as gate:
    with gate.storage_access():
        with application_connect(os.environ["MELDSTORE_DSN"]) as sql:
            # Application SQL transaction(s); close sql before leaving this scope.
            use_application_tables(sql)
```

A SQL-only `connect_postgres(dsn)` does not participate in the bound deployment
until `bind_postgres_storage` succeeds. Reserve that mode for isolated SQL tests
and explicitly quiesced bootstrap. Do not use it to bypass maintenance exclusion.
Do not copy a coordinator directory to another host or treat a cloned database as
an independently usable store. Backup restore and intentional rebinding require
their separate verified workflow; identity records are not a malicious-owner
security boundary.

## Qualification

Run `uv run --frozen --all-extras python tools/postgres_lab.py --with-rustfs`.
The lab starts pinned PostgreSQL and RustFS on loopback with ephemeral credentials,
creates disposable databases/prefixes, requires the exact installed MeldDB Git pin,
rejects skipped mandatory cases and removes its own containers/volumes. The default
lab without `--with-rustfs` remains the SQL/provider-only check.

Coverage includes mismatched databases/prefixes, copied/missing markers, shared
clients, external SQL participation, actual verified Store materialization,
exclusive maintenance, abrupt subprocess exit, initialization rollback/uncertain
commit, occupied-target rejection, and no remote I/O in SQL transactions.
This intermediate coordinator fixture used synthetic SQL for its reader test.
Subsequent lifecycle and integrated qualification covers the actual APIs; see the
current verification record linked above.

Provider review: this slice uses the existing public transaction/SQL boundary and
local MeldStore leases. It adds no MeldDB capability or cross-repository guarantee.
The exact MeldDB pin and shared metadata-provider contract therefore stay unchanged.


November 11 evidence: run `20261111T205135Z-0b2f74` passed 152 live tests with
zero skips on PostgreSQL 17.11 and pinned RustFS. Full regressions passed 652
with 185 expected opt-in/environment skips. Ruff and strict OpenSpec validation
passed. These counts include the prior provider/catalog/migration checks.
