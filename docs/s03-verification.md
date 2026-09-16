# S03 verification record

Local verification: Windows, Python 3.12.10, 2026-09-16.

| Command | Result |
| --- | --- |
| `uv run --extra test pytest` | 188 passed (22.72 seconds) |
| `uv run --extra test ruff check .` | All checks passed |
| `uv build` | Wheel and source distribution built |
| `uv run python tools/package_smoke.py` | Both adapters passed from both freshly installed artifacts |

S03 adds 41 tests to the 147-test S02 baseline. Evidence includes:

- UTC normalization and microsecond interval boundaries; actual composite-index
  query plans; scalar predicates, strict type/bound validation, and null-aware
  deterministic keyset cursors.
- Metadata-only guarded edits, stale-version conflicts, immutable fields, shared
  transaction rollback, and direct SQL concurrency guards.
- v1/v2 coexistence with version-conditional required fields; v3 logical field
  removal; unchanged payload descriptors and successful verified materialization.
- Dry-run rollback of DDL/data and validation of deferred application FKs;
  atomic failed batches, durable progress, and repeat/resume behavior.
- Abrupt child-process exit after a partial batch: prior checkpoint survives,
  partial work rolls back, and the other adapter resumes to completion.
- Application index/trigger/view preservation and stable `ms_blobs` references;
  direct metadata FK refusal, explicit coordination, failed-hook rollback,
  case-insensitive FK target detection, and TEMP-trigger refusal.
- Fresh wheel/sdist imports, migration dry-run/live execution, guarded update,
  typed query, and verified materialization outside the source checkout.

Cross-platform CI verification is pending. The existing workflow covers
Windows/Linux Python 3.12–3.14 and runs both adapters in each job.

These are SQLite metadata transaction and local-file results, not power-loss
certification. DDL rebuild and dry-run hold a writer transaction; there is no
online DDL claim. S04 lifecycle/recovery, S05 codecs, S06 backup, S07 real S3,
and S08 representative consumer/workload qualification remain outstanding.
PostgreSQL and macOS are unqualified. No package release is published.
