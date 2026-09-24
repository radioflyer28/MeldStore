# MeldDB application-owned SQL contract adoption

Date: 2026-09-23

This record covers MeldStore's OpenSpec change
`adopt-melddb-application-owned-sql-contract` and the upstream MeldDB contract at
commit `adfc87fb9933412e67a2d4b7de316cd6db552d9c`.

## Contract reconciliation

The upstream `application-owned-sql` and `portability-and-recovery` specifications
were reviewed together with the shared `meld-platform-specs`
`metadata-provider-contract`. Their ownership boundary is unchanged:

- MeldDB owns explicit connection, transaction, parameterized SQL, inspection,
  and physical SQLite backup primitives.
- MeldStore owns its ordinary `ms_*` schema and migrations, blob lifecycle,
  payload integrity and storage, portable catalog export, and complete
  catalog-plus-payload recovery.
- Applications own their domain tables, migrations, constraints, indexes,
  triggers, and relationships to MeldStore's documented public SQL targets.
- MeldDB managed logical export is not a complete MeldStore backup because it
  intentionally excludes application-owned external SQL.

The adoption does not change cross-repository responsibilities, public blob APIs,
persisted catalog or backup formats, payload bytes, or the SQLite-only support
boundary. The repository-specific proposal and implementation therefore remain in
MeldStore. The shared workstore was clean during reconciliation and requires no
delta for this change.

## Adopted dependency

- Repository: `https://github.com/radioflyer28/melddb.git`
- Exact commit: `adfc87fb9933412e67a2d4b7de316cd6db552d9c`
- `pyproject.toml` and `uv.lock` contain the same full Git revision and no local
  dependency path.
- Regenerating the lockfile changed only MeldDB's source revision; it introduced
  no unrelated package update.

## Focused evidence

Local runtime: Windows, Python 3.13.15, pytest 9.1.1.

- The three new external-table and mixed-transaction cases passed, including
  both metadata adapters for rollback/reopen behavior.
- `uv run --frozen --all-extras pytest tests/test_catalog.py tests/test_consumers.py`:
  91 passed.
- The two new catalog-snapshot and managed-export exclusion cases passed.
- `uv run --frozen --all-extras pytest tests/test_backup.py`: 55 passed and 2
  existing Windows symlink-permission skips.

The focused tests use synthetic generic data only. They do not change payload
formats or claim PostgreSQL, online-backup, multi-host, AWS, Garage, or macOS
qualification.

## Full qualification

- `uv run --frozen --all-extras pytest`: 585 passed and 33 documented skips.
  The skipped cases are the existing Windows symlink-permission and live-S3
  qualification cases; no new contract test skipped.
- `uv run --frozen --extra test ruff check .`: passed.
- `uv build --out-dir dist/0.1.0rc1`: built a fresh wheel and source
  distribution.
- The wheel and source distribution each passed isolated core installation
  smoke tests covering both metadata adapters, blob import/materialization/export,
  application-owned foreign keys, maintenance, and complete backup/restore.
- The wheel and source distribution each passed isolated all-format installation
  smoke tests for bytes, NumPy NPZ, native Blosc2 arrays, and pandas, Polars, and
  PyArrow Parquet handlers through both metadata adapters.

No private workload or Parquet data was used. This qualification does not publish
the package or expand support beyond SQLite metadata and single-host coordination.
