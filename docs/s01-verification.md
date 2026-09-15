# S01 verification — 2026-09-15

Implementation commit: `c5d7867bfb69bf9a25a8a854367fb99dddd10c9d`.

## Executed evidence

- Local Windows, Python 3.12.10: `uv run --extra test pytest` — **93 passed**.
- `uv run --extra test ruff check .` — all checks passed.
- `uv build` — wheel and source distribution built successfully.
- `uv run python tools/package_smoke.py` — each artifact installed in an isolated
  environment, outside the source tree; schema install/reinstall passed through
  both adapters.
- [GitHub Actions run 35025377982](https://github.com/radioflyer28/MeldStore/actions/runs/35025377982)
  — **all six jobs passed**: Windows/Linux, Python 3.12/3.13/3.14. Each job ran
  the conformance suite, Ruff, build, and clean wheel/sdist installation checks.

## Behavior covered

Generic typed declarations and canonical definitions; explicit payload callback;
unknown/reserved fields; timestamp normalization; strict Python value validation;
SQL stored-value checks; repeat/conflicting installs; unusual quoted identifiers;
unique indexes and cross-schema constraints; application FK joins and restrictive
deletion; atomic DDL/data rollback; failed/nested/expired/foreign/wrong-thread
transaction rejection; deferred commit constraint failure; SQL concurrency guards;
stale writes from a separate process; cross-adapter reopen; plain sqlite3 access;
startup with MeldDB and codec imports blocked for the direct adapter; no managed
MeldDB tables. Tests also verify library DDL/guard drift is not silently repaired.

## Tooling notes

The local dependency install required uv's system certificate trust option;
certificate verification was not disabled. The GitHub-workflows skill prompted
live syntax/action verification and CI checks, but its `ci_monitor.cjs` script
was absent. Read-only GitHub CLI inspection was used as the fallback. Action
refs were verified upstream and pinned to full commits before the successful run.

## Boundaries

This is the S01 relational foundation only. No blob preparation, upload,
publication readiness, retrieval, hashing, migration runner, codec execution,
backup, or cleanup is implemented. The lifecycle is a documented contract for
later slices. obstore/xxhash and optional format dependency sets are declared and
locked, not functionally qualified. Process-level stale-write checks are not
crash/power-loss recovery tests. S3, PostgreSQL and macOS remain unqualified.
No package release or distribution license is implied.

S02 is next: the first local file import/reopen/query/materialize/verify workflow.
