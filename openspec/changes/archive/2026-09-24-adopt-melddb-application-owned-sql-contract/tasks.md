# Tasks

## 1. Coordinate and Pin the Contract

- [x] 1.1 Re-read MeldDB commit `adfc87fb9933412e67a2d4b7de316cd6db552d9c`'s application-owned SQL and recovery contracts together with the registered `meld-platform-specs` workstore's `metadata-provider-contract`, record that the shared ownership boundary is unchanged, and verify the shared workstore remains clean with no required delta.
- [x] 1.2 Update `pyproject.toml` to pin the full published MeldDB commit `adfc87fb9933412e67a2d4b7de316cd6db552d9c`, regenerate `uv.lock` with `uv`, and verify the dependency diff changes only the MeldDB source revision with no local path or unrelated package update.

## 2. Qualify Application-Owned SQL

- [x] 2.1 Add a public-API test that opens an installed MeldStore catalog with MeldDB, verifies required `ms_*` and application tables appear as external tables, and verifies no MeldDB-managed objects or migrations were created.
- [x] 2.2 Extend focused tests for both `sqlite` and `melddb` adapters to prove an application-owned constraint failure rolls back the shared metadata transaction and leaves no ready blob, then verify the catalog reopens through the other adapter and plain `sqlite3` without adoption or conversion.
- [x] 2.3 Run `uv run --frozen --all-extras pytest tests/test_catalog.py tests/test_consumers.py` and verify both adapters pass the external-table, rollback, and reopen scenarios.

## 3. Qualify Snapshot and Backup Boundaries

- [x] 3.1 Add a MeldDB-adapter snapshot test with application-owned tables, indexes, triggers, foreign keys, and data, then verify the detached SQLite snapshot preserves those objects, passes integrity and foreign-key checks, and is described only as a catalog snapshot.
- [x] 3.2 Add or extend a complete-backup test proving `Store.backup()` still creates MeldStore's physical catalog snapshot, portable `catalog.sql`, verified payload copies, and manifest-last artifact without invoking or substituting MeldDB's managed logical export.
- [x] 3.3 Run `uv run --frozen --all-extras pytest tests/test_backup.py` and verify catalog-only snapshot and complete catalog-plus-payload backup behavior pass under the applicable adapters.

## 4. Document Adoption Evidence

- [x] 4.1 Update `docs/contracts.md` and `docs/backup.md` to name MeldStore/application tables as application-owned external SQL, state that each owner controls its migrations, and distinguish MeldDB physical backup from managed logical export and complete MeldStore backup; verify links and examples match current APIs.
- [x] 4.2 Update the MeldDB compatibility evidence, README/changelog references where appropriate, and the exact revision record to `adfc87fb9933412e67a2d4b7de316cd6db552d9c`; verify the record cites focused and full command results without claiming PostgreSQL, online backup, or multi-host support.
- [x] 4.3 Reconcile the completed evidence against the shared `metadata-provider-contract`; if implementation changed a cross-repository responsibility, stop and create a separate workstore delta, otherwise verify the registered `meld-platform-specs` workstore remains unmodified.

## 5. Full Verification and Packaging

- [x] 5.1 Run `uv run --frozen --all-extras pytest` and verify the complete suite passes with only documented expected skips.
- [x] 5.2 Run `uv run --frozen --extra test ruff check .` and verify Ruff reports no errors.
- [x] 5.3 Build fresh wheel and sdist artifacts with `uv build`, install the wheel in isolated core and all-format environments, and verify catalog creation, both metadata adapters, blob round trips, application-owned SQL, and backup smoke checks pass against the pinned MeldDB revision.
- [x] 5.4 Run strict OpenSpec validation for `adopt-melddb-application-owned-sql-contract`, inspect the final diff for private paths or payload data, and verify only intended MeldStore files changed while the shared workstore remains clean.
