# S04 verification record

Local verification: Windows, Python 3.12.10, 2026-09-16.

| Command | Result |
| --- | --- |
| `uv run --extra test pytest` | 233 passed, 2 skipped (43.60 seconds) |
| `uv run --extra test ruff check .` | All checks passed |
| `uv build` | Wheel and source distribution built |
| `uv run python tools/package_smoke.py` | Both adapters passed from both fresh artifact installs |

The two local skips require permission to create symlinks on Windows. All 235
tests are collected; 47 lifecycle cases extend the S03 baseline. Cross-platform
CI is pending, including Linux execution of the symlink tests.

Evidence covers:

- Real SQL reference restrictions on retirement, shared application rollback,
  stale versions, retirement of ready/publishing records, retained tombstones,
  and no physical deletion until exclusive cleanup.
- Explicit prepared-token revocation; matching ready/publishing/prepared/retired
  outcome resolution; mismatched token/metadata rejection; SQL-only lifecycle
  operations without object I/O.
- Lost commit responses injected before and after publication/retirement commits,
  atomic application rows, quarantined connections, and fresh-connection inspection.
- Cross-process exclusion for normal connections, materialized readers,
  maintenance, and cooperative standalone SQL drivers. Process exit releases gates.
- Abrupt process termination before/after retirement commit and between object
  deletion and cleanup-result commit, followed by safe restart/retry.
- Reporting of missing/corrupt/live/protected/orphan/staging objects, explicit
  bounded cleanup keys, failure persistence, missing-object idempotence, and
  refusal of live references or catalogs with broken foreign keys.
- S02 additive adoption without metadata table rebuild; S03 migration/retirement
  interoperability; refusal to silently reinstall an enrolled retirement guard.
- Fresh wheel/sdist environments performing import, migration, edit, verification,
  retirement, exclusive reconciliation, cleanup, and final deletion inspection.

No new dependencies or MeldDB-core changes were required. Gates use separate
SQLite rollback-mode files; all blob I/O still uses obstore and XXH3-128.

Qualification limits: no power-loss certification, noncooperative SQL/filesystem
consumer exclusion, network-filesystem locking, online maintenance, real S3,
backup/restore, codecs, PostgreSQL, or macOS qualification. First S04 adoption
requires all legacy consumers stopped and a single owning catalog per root.
No package release is published. S05 format handlers is next.
