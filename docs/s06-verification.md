# S06 verification

2026-09-16. Offline backup, restore, SQLite SQL export and verified local transfer.

## Local evidence

Windows Python 3.12.10, locked dependencies with all format extras installed.

- `uv run --extra test pytest tests/test_backup.py -q`: 53 passed, 2 Windows
  symlink-permission skips (55 new S06 cases).
- Final `uv run --extra test pytest -q`: **349 passed, 4 skipped**; the other
  two skips are the existing S04 symlink cases.
- `uv run --extra test ruff check .`: all checks passed.
- `uv build --quiet`: wheel and source distribution built.
- `uv run --frozen python tools/package_smoke.py`: fresh core-only wheel and
  sdist environments passed through both adapters, including application SQL
  references and an actual backup/restore of migrated metadata and live bytes.
- `uv run --frozen python tools/package_smoke.py --formats`: fresh wheel/sdist
  environments decoded bytes, NPZ, Blosc2, pandas, Polars and PyArrow objects
  from restored backups through both adapters.

## Acceptance exercised

- Whole shared-catalog restoration across adapters, preserving IDs, exact object
  descriptors, schema/concurrency versions, application FKs/indexes/views/triggers.
- Application FK restrictions remain enforced after restore; source state is
  unchanged by edits to the restored copy.
- Completed metadata migration and permanent retirement/cleanup history survive.
- Logical SQLite SQL export executes without MeldDB, including cyclic references,
  generated columns, WITHOUT ROWID, AUTOINCREMENT sequence state, rowids, embedded
  NUL text and binary values.
- Explicit maintenance/application requirements; refusal of unassociated prepared
  tokens, unpublished blobs, pending cleanup, active migrations and multi-root
  catalogs. Empty catalogs are supported.
- Malformed manifests, unsupported formats/algorithms, duplicate JSON keys,
  changed object lists, invalid identities, path traversal, damaged catalog/SQL,
  missing/corrupt payloads and corrupt source objects are rejected.
- Fresh destinations only; existing sentinel data remains untouched. Overlapping
  paths are rejected; symlink checks await CI privilege coverage locally.
- Abrupt process exit at catalog snapshot, copied backup object, pre-manifest,
  restored object and pre-catalog-publication boundaries, through both adapters.
  Failed destinations are not published; retrying to a fresh destination works.
- Offline transfer preserves source bytes and IDs; recreated access gates bind
  the destination root to its new catalog path.

## CI

Pending six Windows/Linux Python 3.12-3.14 jobs. The existing workflow runs core
and all-extras suites, lint/build, and fresh core/format artifact-install checks.

## Boundaries

See [backup contracts](backup.md). This is exclusive/offline, single-root, local
SQLite portability—not online backup, PostgreSQL migration or S3 qualification.
Applications supply their schema revision and required migration/runtime code.
Temporary/external/attached data and virtual-table extensions are outside the
logical export contract. Transfers are non-destructive copies, not automatic
application configuration changes. Partial destinations require inspection and
fresh-path retry. No power-loss certification, representative-scale throughput,
macOS or package-release claim is made. S07-S08 remain open.
