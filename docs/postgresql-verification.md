# PostgreSQL deployment qualification

Publication checkpoint, November 12, 2026: the owner approved and published
[v0.1.0rc3](https://github.com/radioflyer28/MeldStore/releases/tag/v0.1.0rc3)
at commit 5318f1fa05980c3af859115fef7c4a56e21d20ad. The tag and both downloaded
artifacts matched the approved source and hashes. The preparation records below
retain their historical candidate status. No PyPI release was made.

The unreleased `add-postgresql-metadata-support` implementation completes its 13
roadmap tasks. This record qualifies the named experimental MeldStore deployment;
it does not promote MeldDB's broader PostgreSQL capability matrix or change the
published `0.1.0rc2` artifacts. No new release is published by this work.

## Exact environment

Local live run: `20261111T213648Z-166f7a`, November 11, 2026.

| Component | Qualified value |
| --- | --- |
| MeldDB | Installed Git revision `01e27441d7512f85663c8a86986429d62a69bff9` |
| PostgreSQL server | 17.11, Debian `17.11-1.pgdg13+2` |
| PostgreSQL image | `postgres:17@sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675` |
| Native clients | `pg_dump`, `pg_restore`, `psql` 17.11 from that image |
| RustFS image | `rustfs/rustfs:1.0.0-rc.6@sha256:97171b3d72cd47dc81000f92ea84de25608bfc35a94c965501afaeb5d99f6035` |
| Coordinator | One Windows host, Python 3.13.15, persistent local path-bound leases |
| Client dependencies | Psycopg / Psycopg binary 3.3.8; pytest 9.1.1 |
| Service access | Disposable Docker services bound to loopback; ephemeral credentials |

The provider fixture checks the installed package's Git provenance and rejects a
checkout shadowing the dependency. Metadata and payload services are remote to
the Python process, but every participating process shares the same local
coordinator on one host. Application SQL writers must participate in its exclusion
protocol. The lab removed its own PostgreSQL/RustFS containers and data volumes.

## Results

- Local live PostgreSQL/RustFS run: **217 passed, zero skipped**, 220.24 seconds.
- Full MeldStore regression suite: **654 passed, 250 skipped**. The skipped set
  comprises 217 separately qualified PostgreSQL cases, 27 opt-in S3 cases, and
  six Windows environment/permission cases. Both SQLite adapters passed.
- Ruff passed. The PostgreSQL change passed strict OpenSpec validation.
- Wheel and sdist builds passed with `uv`; both artifacts passed clean isolated
  installs without PostgreSQL and with PostgreSQL plus every format extra.
  Smoke checks exercised both SQLite adapters, lifecycle, migrations, recovery,
  and all installed handlers. They verified the exact Git dependency, licenses,
  optional Psycopg presence/absence, and lazy imports of PostgreSQL APIs.
- Artifact inspection excluded private payloads, native database archives,
  credentials, local qualification files and handoff history.
- Two subprocess tests prove mandatory acceptance fails for a missing database
  fixture or a skipped test. The live job also fails on zero collected cases.
- CI YAML and the mandatory PostgreSQL job configuration were checked locally.
  The Ubuntu live job subsequently passed in the November 12 closeout below.

The initial build retained 0.1.0rc2 solely for local verification. The closeout
prepares unpublished 0.1.0rc3; neither replaces a published artifact.

## Task audit

| Task | Implemented behavior and evidence |
| --- | --- |
| 1.1 | Exact-pin provider tests cover bindings, bulk results, read-only snapshots, controls, constraint/lock errors and disconnect outcomes. The original baseline exposed 27 failures; see the provider verification record. |
| 1.2 | MeldDB owns corrected control/disconnect handling and explicit pending constraint validation. Shared contracts are coordinated; the final installed pin passes the provider fixture. |
| 2.1 | Explicit optional PostgreSQL connection selection, close/lifecycle rules and redacted diagnostics; unchanged core and SQLite installs. |
| 2.2 | Native DDL/introspection, bounded identifiers, scalar/UTC parity, pagination, application foreign keys, guarded versions, drift detection and optional query indexes. |
| 2.3 | In-place metadata evolution and resumable transformations preserve IDs and application references. Tests cover deferred constraints in dry runs, interruption, concurrent resumption and guarded edits. |
| 2.4 | Persistent database/coordinator/prefix binding rejects mismatches and missing/copied markers. Shared/exclusive leases include readers, multiple clients and participating external SQL connections. |
| 2.5 | Real RustFS publication, retrieval/export and retirement; concurrent operations, rollback, backend/process termination, uncertain commits, quarantine, no overwrite and safe cleanup. |
| 3.1 | Explicit application SQL manifest and rollback-only preflight reject omitted mappings, unsupported values/objects and occupied targets. Synthetic junctions, views, indexes, triggers and constraints are verified. |
| 3.2 | Both SQLite adapters transfer to fresh PostgreSQL/S3 targets, preserving IDs, versions, keys and history. Payloads are verified before readiness; failure retains the source and withholds admission. |
| 4.1 | Native whole-database custom archives include application SQL, including another schema. Version-2 manifests publish last; unfinished lifecycle work and corrupt payloads are rejected. Existing SQLite artifact recovery still passes. |
| 4.2 | Explicit native tools, fresh targets and application verification drive restore. Tests reject incomplete/tampered artifacts, wrong tool databases, occupied destinations, interrupted copy and failed application checks. |
| 5.1 | One integrated workflow covers fresh deployment, evolved SQLite cutover, native archive/restore, SQL joins, verified bytes, preserved history and retirement/cleanup. It runs once for each SQLite source adapter. |
| 5.2 | Full regressions, Ruff, strict change validation, uv builds, isolated wheel/sdist installs and mandatory live-job negative controls passed. |

Tests live under `tests/postgres_provider/`; `test_deployment_postgres.py` is the
integrated workflow. `test_postgres_job.py` contains the CI negative controls.
Detailed provider and intermediate evidence remains in
[provider verification](postgresql-provider-verification.md),
[connections/catalog](postgresql.md), [migrations](postgresql-migrations.md),
[coordination](postgresql-coordination.md), [transfer](postgresql-transfer.md),
and [recovery](postgresql-recovery.md).

## Reproduction

With Docker running, from the MeldStore checkout:

```console
uv sync --frozen --all-extras
uv run --frozen --extra test pytest
uv run --frozen --extra test ruff check .
uv run --frozen --all-extras python tools/postgres_lab.py --with-rustfs
uv build --out-dir .qualification/postgresql-artifacts
uv run --frozen python tools/package_smoke.py --dist-dir .qualification/postgresql-artifacts
uv run --frozen python tools/package_smoke.py --dist-dir .qualification/postgresql-artifacts --postgres --formats
openspec validate --specs
```

Use a new artifact output directory or explicitly inspect previous contents;
the smoke tool rejects mixed versions/incomplete builds. Local environment JSON
and JUnit records are written under ignored `.qualification/postgres-provider/`.
They contain versions and outcomes, not credentials or private input datasets.

## Boundaries and coordinated specifications

MeldStore owns its catalog DDL, native archive orchestration, application SQL
transfer interface, payload verification and final admission. MeldDB owns public
SQL/transaction behavior, bulk execution and pending constraint validation.
The `meld-platform` OpenSpec workset contains both repositories plus the shared
`meld-platform-specs` store. The shared
`qualify-postgresql-metadata-recovery-contract` change records archive ownership
and independent exact-pin qualification; local specifications retain the detailed
MeldStore behavior. Syncing these contracts does not merge, push or release code.

AWS, Garage, multi-host/distributed coordination, live cutover, automatic adoption
of incomplete destinations, other PostgreSQL versions and general managed-schema
portability remain outside this qualification. PostgreSQL performance at the
earlier private S08 workload scale was not measured here; all new fixtures are
synthetic. Native restore requires trusted artifacts and explicit operator setup
of roles, privileges, extensions and external dependencies. XXH3 checks detect
corruption; they are not cryptographic signatures or an authenticity mechanism.

## GitHub closeout and rc3 candidate (2026-11-12)

The [initial closeout run](https://github.com/radioflyer28/MeldStore/actions/runs/43263968673)
passed all eight jobs at 7dc5c99b5f4dabbd6ef76ec64861c488430296d0. This includes
Windows/Linux Python 3.12–3.14 regressions, format handlers, lint, wheel/sdist and
isolated installations, standalone RustFS, and the new Linux PostgreSQL/RustFS job.
The live Linux job passed 217 tests with zero skips on Python 3.13.15, PostgreSQL
17.11 and the same pinned images/provider as the local run.

Candidate 0.1.0rc3 at c3641341f51551dbec2f81dd5f246f7486c713c6 updates the locked
optional dependencies certifi to 2026.10.18, msgpack to 1.2.3 and urllib3 to 2.8.0
for four reported GitHub security alerts. The [candidate CI run](https://github.com/radioflyer28/MeldStore/actions/runs/43265145823)
passed all eight jobs. Its live PostgreSQL/RustFS job passed 217 tests without
skips in 50.62 seconds. Local regressions after these fixes passed 654 with 250
expected skips; Ruff and core/all-format/postgres wheel and sdist installs passed.
Candidate publication remains gated on explicit approval; all eight candidate jobs passed.

MeldDB closeout commit 3ab9343c9b40e1c2f142a7af80c654ba9f69e42d passed all 12
[provider CI jobs](https://github.com/radioflyer28/melddb/actions/runs/43264553576),
including Windows/Linux/macOS Python 3.12–3.14 and PostgreSQL 17/18 proof jobs,
and was merged into main. Its [main run](https://github.com/radioflyer28/melddb/actions/runs/43265227157)
also passed all 12 jobs. CI exposed a preexisting SQLite test assumption about
WAL availability; the test now explicitly covers safe and unsafe runtimes while
checking that returned reports are independent data. Local MeldDB verification
passed 379 tests with 25 environment/backend skips and Ruff. No production code
changed relative to MeldStore's exact qualified 01e27441 pin, which remains intact.
These provider proofs do not expand MeldStore's PostgreSQL 17 deployment claim.

All PostgreSQL prerequisite changes are archived and their canonical specs synced.
The three-member meld-platform workset remains configured without an IDE opener.
The shared store is clean at local-only commit 39c0a46; it has no remote, as requested.
No private payloads, paths, credentials or local handoff history were committed.
