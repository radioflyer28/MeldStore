# Experimental PostgreSQL catalog connections

The unreleased `add-postgresql-metadata-support` change adds opt-in PostgreSQL
metadata, application SQL, resumable migrations, single-host coordinator binding,
verified offline SQLite cutover and native whole-store recovery. Deployment
qualification is deliberately limited to the versions and topology in the
[verification record](postgresql-verification.md). Existing SQLite catalog APIs
remain supported; the published 0.1.0rc2 artifacts do not include this change.

## Install and connect

Use `uv sync --extra postgres` in the checkout, or install the `postgres` extra
from the chosen MeldStore artifact. The extra installs Psycopg binary; the core
installation does not. Both retain the exact MeldDB Git dependency.

```python
import os
from meldstore import Catalog

# A PostgreSQL URI or libpq keyword DSN supplied at runtime.
with Catalog.connect_postgres(
    os.environ["MELDSTORE_DATABASE_DSN"],
    timeout=5.0,
    connect_timeout=5,
) as catalog:
    rows = catalog.sql("SELECT current_database() AS database", write=False)
```

The DSN must explicitly name `host` or `hostaddr`, `dbname`, and `user`. A DSN
service indirection is rejected. Standard libpq connection fields such as TLS
options and password/password-file settings are passed through. An omitted port
uses libpq's configured/default port. Supply an explicit port when identifying a
particular deployment. `connect_timeout` is a positive integer in seconds and
overrides that DSN field. `timeout` is a finite lock-wait timeout in seconds, at
least 0.001. No automatic reconnection, fallback backend or retry is performed.

Use the explicit factory. The filesystem constructor rejects recognizable
PostgreSQL DSNs before creating files. `adapter='postgres'` in that constructor
is not supported. `catalog.adapter` is `postgres`; `catalog.path` is `None`.
No DSN is exposed as a public path, stored in a coordination file, or used as a
catalog identity. Opening only opens/inspects the database through MeldDB; it
does not install the MeldStore schema or create managed objects.

## SQL and failure behavior

`catalog.sql`, `catalog.transaction`, `tx.sql` and `tx.sql_many` use the same
explicit ownership model as SQLite. PostgreSQL application SQL uses `%s` or
`%(name)s` bindings. Read scopes are engine-enforced and native SQL control
validation is delegated to MeldDB's qualified public interface. Closing releases
the connection; closed, expired, nested and cross-thread use remains invalid.

Connection/open failures use `CatalogConnectionError`. Invalid configuration or
missing extras use `ValidationError`. Driver text can contain credentials or SQL
values, so PostgreSQL diagnostics use generic messages and sanitized causes.
Safe cause evidence includes error category (`provider_type`) and SQLSTATE when
available. Structured transaction errors retain `phase`, `outcome`, sanitized
`initiating_error` and `backend_error`; quarantine and explicit reopen remain
mandatory after an unknown outcome. These diagnostics deliberately omit raw
driver messages, arguments and tracebacks. SQLite diagnostic behavior is unchanged.

Redaction covers library diagnostics, not caller-created log messages, source
code containing literal credentials, introspection of private provider state,
or server-side PostgreSQL logs. Keep credentials in runtime secret configuration.

## Deployment inputs and current gates

The target is a dedicated PostgreSQL 17 database with an application role having
the privileges needed for application SQL. Use TLS/authentication appropriate to
the server; the disposable qualification lab binds only to loopback and uses
ephemeral credentials. Provision the database and role explicitly beforehand.

The experimental blob deployment requires one persistent local coordination
directory on one host, a non-secret catalog identity, and an explicit RustFS
endpoint/bucket/prefix binding. See [coordinator initialization and external SQL
participation](postgresql-coordination.md). SQL-only opening does not establish
that binding. First install a schema, explicitly initialize the binding, and then
construct Store. Reopens use `connect_postgres(dsn, storage=storage)`; unbound
Store creation still fails. SQLite-specific snapshot/tuning APIs remain unavailable
on this connection. Use [native PostgreSQL backup/recovery](postgresql-recovery.md)
and the ordinary Store exclusive lifecycle maintenance operations.

The [qualification record](postgresql-verification.md) covers publication,
cutover, native archive recovery and interruption in the named single-host
deployment. Multi-host coordination, AWS and broader PostgreSQL versions remain
unqualified. Applications must follow the coordinator protocol even when their
SQL connection and payload service are remote.

## Verification

```powershell
uv run --frozen --extra test --extra postgres pytest tests/test_postgres_config.py
uv run --frozen --extra test --extra postgres python tools/postgres_lab.py
```

The lab now runs the unchanged provider fixture plus catalog selection tests,
using the same exact provider revision and PostgreSQL image recorded in
[provider qualification](postgresql-provider-verification.md). It rejects skipped
mandatory tests and removes only its disposable server. Public tests use synthetic
SQL data and credentials; no private payloads are involved.

### Task 2.1 evidence — November 11, 2026

- Configuration, missing-extra and redaction tests: 24 passed.
- Mandatory PostgreSQL 17.11 run `20261111T173407Z-5e21fd`: 69 passed, zero
  failures/skips (61 unchanged provider checks plus 8 catalog checks).
- Full regression suite: 646 passed, 102 skipped. Those skips include the 69 live
  checks that passed separately and the existing 33 optional/environment cases.
- Ruff and strict OpenSpec change validation passed.
- uv wheel/sdist builds and clean-install smoke runs passed both with and without
  `--postgres`. Every artifact/extra combination exercised both SQLite adapters;
  core installs had no Psycopg, and importing MeldStore did not eagerly load it.
- The exact MeldDB dependency remains
  `bda3ca95fb6f5431e521162087bec97009074840`. Optional Psycopg is 3.3.8 in the
  qualification environment (Windows, Python 3.13.15).
- All disposable PostgreSQL containers were removed. No payload data was used.

This is the historical task 2.1 checkpoint. The task 2.2 checkpoint below extends
the SQL foundation; coordinator binding and deployment acceptance remain open.


## Native catalog schema and SQL metadata foundation

`catalog.install_schema(schema)` now installs PostgreSQL-native version-1
catalog tables and returns the actual SQL metadata table name. Applications may
reference `ms_blobs(id)` through ordinary foreign keys and compose their SQL
with installation in the same explicit transaction. Registration is idempotent
for an identical declaration and rejects incompatible reuse or implicit evolution.

```python
from meldstore import BlobSchema, Text, Timestamp

schema = BlobSchema("dataset", {"label": Text(required=True), "captured_at": Timestamp()})
table_name = catalog.install_schema(schema)
assert table_name == schema.table_name_for("postgres")
```

Keep using the returned name for SQL integrations. `schema.table_name` retains
the existing SQLite name; `table_name_for(adapter)` gives the backend-specific
name. PostgreSQL internal names longer than 48 UTF-8 bytes become `ms_h_` plus
40 hexadecimal SHA-256 characters. This is only deterministic identifier
allocation, **not a blob digest or database-entry signature**. Registry and
SQL namespace checks reject collisions rather than adopting another schema.
Declared field names over PostgreSQL's 63-byte limit are rejected before DDL;
they are never silently truncated. Existing SQLite limits and names are unchanged.
See PostgreSQL's [identifier rules](https://www.postgresql.org/docs/17/sql-syntax-lexical.html).

Metadata uses BIGINT, DOUBLE PRECISION, and `TEXT COLLATE "C"`; booleans retain
the shared integer 0/1 SQL representation. Timestamps remain canonical UTC text
`YYYY-MM-DDTHH:MM:SS.ffffffZ`. Strict Python normalization is shared; SQL checks
reject invalid booleans, nonfinite floats, and malformed timestamp shapes.
Calendar validation still occurs at the Python boundary. PostgreSQL text bindings
reject embedded NULs and invalid Unicode before generated query/edit execution.
Native SQL writers remain subject to their engine's type-coercion rules.

The shared query compiler generates native placeholders and explicit null
ordering, with bytewise text order and null-safe cursor comparisons. It does
not rewrite caller SQL or placeholder-like text in identifiers/values. Both
SQLite adapters retain their ordering and keyset-pagination behavior. Guarded
edits use a single `UPDATE ... WHERE version=... RETURNING id`; competing
PostgreSQL writers produce one successful increment and one stale-version conflict.
Native triggers enforce immutable fields and initial/monotonic metadata versions.

The PostgreSQL-only `ms_pg_layout` registry records the generated DDL and the
engine-normalized structure installed in that transaction. Verification reads
PostgreSQL catalogs for columns, defaults, collations, constraints, indexes,
trigger enablement, and trigger function bodies/settings. It rejects missing or
altered owned objects, while preserving application tables and additive indexes
and constraints. These records detect accidental schema drift; they are not an
authenticity check against an owner who rewrites both the objects and registry.
Do not modify this registry manually. Future format/migration work must explicitly
update its owned layouts. Schema inspection performs no DDL and works in read scopes.

Native payload, encoding, and retirement DDL/guards are also implemented. The
`Store.stat/find/update_metadata` SQL paths use the dialect and are tested using
a deliberately isolated storage stub with synthetic descriptors. This is a
SQL-only test harness, not an enabled PostgreSQL Store constructor or evidence
that payloads exist. Actual payload preparation/publication, concurrent lifecycle
operations, storage binding, migration execution, and recovery still require
their remaining task gates. In particular, these tests do not qualify concurrent
publication/retirement or authorize bypassing the constructor's binding gate.


### Task 2.2 evidence — November 11, 2026

- Mandatory lab run `20261111T175448Z-1c7a78`: **120 passed, zero skips**
  (61 unchanged provider checks, 8 connection checks, 51 SQL catalog/parity checks).
- PostgreSQL 17.11, image
  `postgres:17@sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675`;
  MeldDB pin remains `bda3ca95fb6f5431e521162087bec97009074840`.
- Both SQLite adapters participated in parity fixtures. Coverage includes scalar
  and UTC values, null/tie pagination, bytewise text ordering, native bindings,
  quoted/long identifiers and forced allocation collisions, FK composition,
  uniqueness and version guards, atomic rollback, reopen, read-only inspection,
  competing updates, modified schemas/functions/triggers, and SQL-only
  publication/encoding/retirement constraints.
- Final `uv run --extra test pytest -q --tb=short`: **650 passed, 153 skipped**.
  The skips are 120 opt-in lab cases (passed separately) plus 33 existing
  optional/environment cases. The encoding-verifier import-shadowing regression
  found during implementation was fixed before this final run.
- `uv run --extra test ruff check .`, strict OpenSpec validation, and diff
  whitespace checks passed. No packaging/dependency changes were introduced by
  task 2.2; integrated package/deployment acceptance remains task 5.2.
- Qualification used Windows/Python 3.13.15/Psycopg 3.3.8, synthetic SQL only.
  All disposable containers were removed. No private Parquet data was used.

Task 2.3 is next: PostgreSQL metadata versioning and resumable transformations.
Task 2.2 makes no new MeldDB/provider guarantee; the existing shared contract
was reviewed and remains applicable. These changes remain local, uncommitted,
and unarchived; PostgreSQL payload deployment is still unqualified.


## PostgreSQL migration implementation

See [metadata migrations](postgresql-migrations.md) for task 2.3, in-place schema
evolution, resume/locking behavior, deferred-constraint validation, and the new
exact provider pin. Earlier checkpoints above retain the revisions they tested.


### Task 2.5 evidence — November 11, 2026

PostgreSQL publication, retrieval/export and retirement now use native bindings.
Short lifecycle writes serialize on the catalog format row; retirement also locks
the target metadata row before validating its version. Uploads/downloads/deletion
remain outside SQL write transactions. This favors straightforward single-host
correctness over maximum concurrent lifecycle throughput; no automatic retry is
introduced. Caller transactions remain the commit/rollback boundary.

Run `20261111T205951Z-18491d` passed 165 live tests with no skips on the exact
provider pin, PostgreSQL 17.11 and pinned RustFS. Added tests cover bytes/empty
payloads/files/export, application FK restriction and bulk rollback, concurrent
same-ID retries and discard/publication, lost publication/retirement commit replies,
real backend termination, subprocess exits before/after publication commit,
no-overwrite, corruption, drift-blocked cleanup and resumable deletion failures.
Full regressions after the shared lifecycle port passed 652 with 198 expected
opt-in/environment skips; Ruff passed. The final export path adjustment is covered
by the successful live run. Task 5.2 repeats the full suite on the final implementation.

The shared `qualify-postgresql-metadata-recovery-contract` change coordinates
PostgreSQL-native whole-database archive ownership and independent qualification.
The implementation remains in this repository. Cutover, archive recovery and
integrated deployment gates are still open; this is not yet a deployment claim.
