# Tasks

## 1. Reproducible fixtures and experiment profiles

- [x] 1.1 Add smoke/full/custom metadata profile definitions and a deterministic matrix for three adapters, four layouts, two statistics treatments and the declared rounds/samples. Verify profile validation tests reject silent reductions and enumerate every required cell.
- [x] 1.2 Add a SQL-only interval fixture using the existing relationship generator and independent oracle, with synthetic catalog references and bounded explicit transactions. Verify exact rows and grouped results on direct SQLite and MeldDB SQLite, plus tests for no matches, strict boundaries and adjacent microseconds.
- [x] 1.3 Document the synthetic scope, index DDL, profile sizes and timestamp/overlap semantics in docs/interval-query-tuning.md. Verify examples match the generated matrix and clearly separate metadata evidence from payload qualification.

## 2. Controlled execution and disposable services

- [x] 2.1 Implement isolated layout/statistics preparation, public maintenance/SQL calls, effective-setting capture and seeded execution order. Verify tests detect unintended indexes, mismatched settings, altered data and unplanned statistics changes without private connection access.
- [x] 2.2 Add PostgreSQL-only disposable-service support using the pinned image, loopback binding, environment credentials, recorded fixed memory settings and controlled background analyze. Verify collision refusal, resource ownership, finite startup/command deadlines and cleanup failures with focused tests.
- [x] 2.3 Add public-API read measurements, plan capture, storage-I/O rejection, build/allocation measurements and bounded insert/rollback probes. Verify wrong-result injection fails, warm-ups are excluded, unavailable metrics have reasons and rollback preserves complete fixture rows.
- [x] 2.4 Add the CLI with fresh output validation, resource preflight and finite wall/disk/memory budgets. Verify interruption and occupied/overlapping destination tests, and document runnable uv commands and effective-setting limitations alongside the implementation.

## 3. Evidence and repeatable acceptance

- [x] 3.1 Implement versioned atomic interval evidence, source/helper/lock/provider attestations and the strict completion validator. Verify damaged reports fail for missing matrix cells, wrong provenance, altered settings, insufficient samples, failed correctness or teardown, and test credential/private-path exclusion.
- [x] 3.2 Add focused all-adapter live smoke coverage to the existing CI qualification setup, requiring PostgreSQL prerequisites rather than skipping them. Verify the smoke command passes with the real pinned service and that it cannot claim full metadata or payload qualification; document the command and completion fields.
- [x] 3.3 Implement per-round summaries and application tradeoff tables retaining raw samples, plans, read latency, build/space and rollback insertion costs. Verify summary fixtures preserve regressions, missing measurements and inconclusive outcomes instead of selecting only favorable cells.

## 4. Integrated verification and measured guidance

- [x] 4.1 Run uv run --frozen --extra test pytest and uv run --frozen --extra test ruff check ., plus the live all-adapter interval smoke and affected existing qualification regressions. Record commands, results and any actual skips in docs/interval-query-verification.md; run uv build/package smoke if packaging changes.
- [x] 4.2 Execute the full 100-reference/300,000-relationship metadata matrix across all adapters, layouts and statistics treatments with three rounds and the declared samples. Verify completion from the final sanitized artifact after cleanup; retain failed/incomplete evidence and leave this task open if acceptance is unmet.
- [x] 4.3 Publish the measured application-owned SQL guidance and update docs/implementation-plan.md and docs/representative-workloads-verification.md with links to evidence, supported conclusions and remaining tuning questions. Verify every recommendation is traceable to completed comparable cases, disclose fixed cache settings, preserve the pending corrected full-payload rerun, and record the shared-contract review with the unchanged provider pin.

## 5. Resumable checkpoint amendment

- [x] 5.1 Add versioned validated checkpoints, explicit pause/resume options, fresh-resource resumption and visible session provenance without modifying the running v1 benchmark.
- [x] 5.2 Verify interruption/retry, checkpoint corruption/incompatibility, cleanup failures, active/legacy source refusal and all-adapter pause/resume; run required uv pytest/Ruff checks and document limitations and commands.

## 6. Verified interrupted-run recovery amendment

- [x] 6.1 Implement explicit offline interrupted-prefix recovery, immutable original evidence, strict process/ownership/provenance checks, fresh output and auditable measurement-boundary versus cleanup semantics.
- [x] 6.2 Verify corruption, active/unknown processes, unsafe paths, cleanup failure and successful recover/resume; run required uv tests/Ruff and document commands and platform limits before recovering the saved full run.

## Workflow follow-up

- Review the completed change and evidence, then sync the local capability delta and archive through OpenSpec.
- Commit/publish through the normal review workflow when authorized; keep meld-platform-specs local-only and exclude benchmark databases, private files and credentials.
