# Proposal

## Why

The representative workload found that adding an end-first application index coincided with much slower narrow SQLite interval queries, while broad PostgreSQL joins used scans. The sequential measurements did not control statistics or cache history, so applications need reproducible comparisons before choosing indexes.

## What Changes

- Add a metadata-only interval experiment runner using the existing deterministic application SQL fixture, with a full profile of 100 synthetic blob references and 300,000 relationship rows across direct SQLite, MeldDB SQLite and MeldDB PostgreSQL 17.
- Compare explicit start-first, start-plus-end-first, and covering index layouts using isolated databases, controlled statistics treatments, recorded effective memory settings, warm-up and repeated measurements.
- Verify every query against an independent oracle, including empty results and strict overlap boundaries. Measure index build/space costs and bounded insert/rollback costs alongside query latency and plans.
- Emit complete, sanitized evidence with exact source/dependency provenance, and publish application-owned SQL guidance that reports tradeoffs and inconclusive outcomes.
- Add validated treatment checkpoints, graceful pause requests and explicit multi-session resume into fresh destinations; never reuse partial timing samples or silently import legacy reports.
- Add explicit verified interrupted-prefix recovery with immutable source evidence, process/ownership checks and separately attested cleanup; never infer continuity beyond the last checkpoint.
- Keep experiments separate from full payload qualification. No library index defaults, public API changes or new provider capabilities are proposed.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `representative-workload-qualification`: Add requirements for controlled metadata-only interval experiments and application tuning evidence, without relaxing full representative payload acceptance.

## Impact

MeldStore owns the runner, synthetic fixtures, regression tests and guidance under `tools/`, `tests/`, and `docs/`. Reuse `tools/qualification/fixture.py` for relational data and its oracle, and existing measurement, resource ownership and evidence conventions. The expected entry point is `tools/qualify_intervals.py` with supporting qualification modules.

The `meld-platform` workset and shared `metadata-provider-contract` were reviewed. Existing public SQL and explicit transactions suffice. Session control remains prohibited through provider SQL. Benchmark-only PostgreSQL settings belong to a disposable service's bootstrap; unavailable SQLite tuning controls must be recorded rather than bypassed. MeldDB's exact pin and shared contract stay unchanged unless implementation uncovers a demonstrated provider gap, which requires a separately scoped coordinated change.

This work does not qualify payload integrity, AWS, multi-host coordination or the pending corrected 20.04 GB rerun. Synthetic catalog rows stay in disposable benchmark databases, and public evidence contains no private payloads, identifiers, paths or credentials.
