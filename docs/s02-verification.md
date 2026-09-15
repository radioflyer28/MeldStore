# S02 verification — 2026-09-15

Implementation commit: `366a9d3ab8c710901389e15a5f2f7e69978715c0`.

## Executed evidence

- Windows Python 3.12.10: `uv run --extra test pytest` — **147 passed**.
- `uv run --extra test ruff check .` — **all checks passed**.
- `uv build` — wheel and source archive built successfully. The archive includes
  documentation and the qualification script used by its tests.
- `uv run python tools/package_smoke.py` — clean installs of both artifacts
  outside the source tree; each ran schema install, file import, and verified
  materialization through MeldDB and direct SQLite.
- [CI run 35028995579](https://github.com/radioflyer28/MeldStore/actions/runs/35028995579)
  — **all six jobs passed**, Windows/Linux on Python 3.12/3.13/3.14. Every job ran
  the entire suite, lint, builds, and clean artifact installs.

## S02 acceptance coverage

The 54 new cases add real LocalStore transfers to S01's 93 tests. They cover:

- Exact-byte import/reopen/materialization, known XXH3-128 vectors, empty files,
  multipart files, source preservation, and temporary-result lifetime.
- Sequential caller-ID retries without another upload, changed-content conflicts,
  exact prepared-token replay, tampered-token rejection and one-token/one-blob binding.
- SQL-only stat/find, bounded equality/keyset queries, ready-only visibility,
  application FK rows and publication guards in the shared transaction.
- Atomic application rollback, poisoning on caught validation errors, and rejecting
  storage work inside a catalog transaction.
- Corrupt/missing objects failing before path exposure, damaged catalog associations
  distinguished from absence, wrong storage-root rejection and no-overwrite promotion.
- Explicit schema enrollment detecting removed publication guards on reinstall,
  and prepared-token replay after reopening with the other catalog adapter.
- Abrupt child-process exits after upload, preparation, finalization,
  publication-before-commit, and after commit. Only committed records and their
  application references become visible; unfinished uploads remain non-ready.

Tests were developed against the public file/SQL boundaries from the plan. Failing
tests exposed retry behavior, catalog damage incorrectly reported as absence,
obstore's local missing-file exception mapping, and silent guard repair; fixes were
verified through both adapters. Fault injection uses process termination and the
external obstore boundary, not a simulated database.

## Bounded-memory qualification

`uv run python tools/qualify_local.py --adapter melddb` and the same command with
`--adapter sqlite` each transferred and byte-compared a synthetic **128 MiB** file
on the local Windows machine. The digest was
`d138824514899b7b317d2512f67edd3f` for both.

| Adapter | Import | Materialize + compare | Additional peak RSS |
| --- | --- | --- | --- |
| MeldDB | 0.263 s | 0.453 s | 13,819,904 bytes (~13.2 MiB) |
| sqlite3 | 0.379 s | 0.479 s | 15,982,592 bytes (~15.2 MiB) |

These are single local observations, including OS buffering, not a throughput or
durability guarantee. The regression suite runs the same 128 MiB scenario in a
fresh process and requires additional peak RSS below 96 MiB to catch accidental
whole-file buffering. It passed on all six CI jobs. This does not qualify the
full S08 multi-file workload, every allocator/backend, or arbitrary file sizes.

## Boundaries and tooling

S02 adds an explicitly installed payload extension without rebuilding S01 public
identity/metadata tables. All payload relationships are SQL. It does not add
NoSQL/graph dependencies, domain entities, caching policies, or entry signatures.

Process-exit tests do not certify power-loss durability or complete recovery.
Unreferenced prepared/staged objects are retained; reconciliation, uncertain
outcome tooling, deletion and exclusive cleanup remain S04. Codecs, S3, backups,
metadata migrations and relocation remain later slices. PostgreSQL/macOS are
unqualified. No package release or license decision is implied.

The GitHub-workflows skill's monitoring script remains absent; read-only GitHub
CLI inspection was used as the fallback. The existing pinned CI workflow was not
changed. Verification was rerun after the final source changes before committing.

Next: S03 metadata queries, optimistic edits, and explicit resumable evolution.
