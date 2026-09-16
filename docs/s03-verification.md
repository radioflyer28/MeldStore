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

All six Windows/Linux Python 3.12–3.14 jobs passed in
[S03 CI](https://github.com/radioflyer28/MeldStore/actions/runs/35119163433)
for implementation commit `fe94e6302c59388b86d161d8b9ec8c6d5b29346a`.
Each job runs both adapters, lint, builds, and fresh wheel/sdist installation
checks. The accompanying
[push run](https://github.com/radioflyer28/MeldStore/actions/runs/35119166301)
also passed. This record-only update does not change runtime code or tests.

These are SQLite metadata transaction and local-file results, not power-loss
certification. DDL rebuild and dry-run hold a writer transaction; there is no
online DDL claim. S04 lifecycle/recovery, S05 codecs, S06 backup, S07 real S3,
and S08 representative consumer/workload qualification remain outstanding.
PostgreSQL and macOS are unqualified. No package release is published.
