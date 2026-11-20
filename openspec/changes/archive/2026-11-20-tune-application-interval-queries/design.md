# Design

## Context

See proposal.md for motivation. `tools/qualification/workload.py::metadata_workload` currently measures four selections, first with `(start_time,end_time)` and then with an additional `(end_time,start_time)` index on the same database. It captures plans, five warm-ups and 100 samples, then times rollback-only inserts. `fixture.py` supplies the deterministic relationship generator, strict overlap query, independent expected counts and full row verification.

The existing full runner also creates and moves real Parquet payloads. Its cost and fixed sequential index order are inappropriate for isolating metadata tuning. The find benchmark already establishes a precedent for explicitly fabricated SQL-only catalog fixtures. `Catalog` exposes no SQLite cache-size setting, and raw SQL rejects session controls and diagnostic `EXPLAIN ANALYZE`. Existing plain EXPLAIN, application DDL, explicit transactions and SQLite maintenance suffice for this experiment.

## Goals / Non-Goals

**Goals**

- Explain query behavior with reproducible application-owned index/statistics comparisons and visible read/write/space tradeoffs.
- Keep all timed reads and writes on public catalog/transaction APIs with independent correctness checks.
- Produce useful guidance even when no layout improves every selection.

**Non-Goals**

- New core indexes, query rewrites, a new query API or provider connection controls.
- Payload qualification, production database mutation, general cold-cache claims or cross-host performance rankings.
- A universal index recommendation, a mandatory latency SLA or a public cache-tuning API.

## Decisions

### 1. Add a separate metadata runner and reuse the relational fixture

Add `tools/qualify_intervals.py` and focused `tools/qualification/interval_*.py` modules. Reuse deterministic relationship generation, normalized UTC timestamp strings, the join query and independent oracle. Seed only the synthetic blob metadata necessary to satisfy public SQL references, using bounded explicit transactions. Follow `find_fixture.py` conventions for visibly fabricated catalog state; never present it as an operational store or claim existing payloads.

Full metadata uses 100 synthetic blob references and 300,000 relationships. Smoke uses 4 references and 2,000 relationships. All three adapters are mandatory in the full profile. A caller changing counts or selecting adapters receives custom-profile evidence. Fixtures, service data and reports use fresh owned destinations; a storage sentinel and operation instrumentation reject any payload I/O in measured phases. No RustFS service or Parquet files are required.

Alternative considered: extending the full payload runner. A separate entry point avoids recopying 20 GB and preserves its existing acceptance matrix.

### 2. Fix the primary index and statistics matrix before running

Compare four exact layouts on `app_intervals`; primary keys and join tables remain identical.

| Layout | Application interval indexes |
| --- | --- |
| start | `(start_time,end_time)` |
| start-plus-end | `(start_time,end_time)` and `(end_time,start_time)` |
| covering-start | `(start_time,end_time,config_id,blob_id)` |
| covering-end | `(end_time,start_time,config_id,blob_id)` |

Covering variants replace, rather than supplement, the original start index. Use ordinary multi-column B-tree definitions on both engines. Report the actual chosen plan; a covering definition does not promise index-only execution. Specialized PostgreSQL INCLUDE, range types/GiST, partial indexes, hints and forced scans are separate follow-ups if evidence justifies them.

For every layout compare freshly loaded, not explicitly analyzed data with explicitly analyzed data. Build each treatment from an equivalent fresh fixture. Record the observed statistics state before and after measurement, rather than assuming load order proves it. Prevent background analyze from silently changing the untreated PostgreSQL fixture through isolated service bootstrap settings, record those settings and label them as experimental. Apply the same vacuum/visibility preparation to both PostgreSQL treatments if any is used, without inadvertently analyzing the untreated one. Refresh analyzed statistics after data and index creation, before timing.

Run three independently prepared rounds in a recorded seeded order that varies layouts and query order. Each full-profile query cell has five warm-ups and 40 timed calls per round, retaining 120 measured samples in total and per-round distributions. Smoke uses one round, one warm-up and three measured calls and never qualifies the full profile.

### 3. Control what the public APIs expose and disclose the rest

Hold effective memory settings fixed within each adapter across all primary comparisons. Read SQLite settings using supported read-only pragma table queries and record page size, cache size, mmap size where available, runtime version and journal mode. Keep the catalog's existing WAL/FULL policy. Do not mutate private connections or claim a SQLite cache sweep that the public API cannot support.

Use a dedicated pinned PostgreSQL 17 service with explicit startup memory/resource settings. Record effective shared buffers, work memory, planner cache estimate, parallel-worker settings and container limits using read-only SQL/inspection. Fixed service budgets belong to the experiment configuration and must be validated before measurement. Cross-adapter results remain separate observations because their memory models and runtimes differ.

Retain warm-query labels. Do not flush OS caches or equate connection reopen with a cold cache. A fresh fixture, warm-up and repeated order controls reduce confounding without claiming to eliminate OS effects. Query timing excludes fixture construction, oracle calculation, plan collection and instrumentation setup. It includes the complete public catalog call and result materialization. Compare returned rows immediately after each timed call.

Alternative considered: changing provider/session controls to sweep every cache size. That adds an API contract without demonstrated need. Record the current limitation and use controlled fixed budgets first.

### 4. Preserve the existing query semantics and add boundary coverage

Retain the four existing narrow/broad selections and joins through `app_configs` and `ms_blobs`, grouped and ordered by blob ID. Prepare expected results outside timing from the generator, not SQL under test. Add targeted correctness-only cases for no matches, an interval ending exactly at the lower boundary, one starting exactly at the upper boundary, and adjacent microsecond timestamps. Use positive-duration query windows and verify the strict overlap rule (`start_time < query_end AND end_time > query_start`). Check exact IDs, counts and ordering.

Verify complete fixture rows before read timing and after all write probes. A speed gain never compensates for incorrect data, missing cases or payload I/O.

### 5. Record costs without confusing rollback with commit throughput

Collect plain PostgreSQL EXPLAIN or SQLite EXPLAIN QUERY PLAN through public SQL outside timing. Do not use blocked EXPLAIN ANALYZE syntax or a private driver workaround. Record exact DDL, build duration and physical database/index allocation using available read-only diagnostics. If per-index allocation is unavailable, retain whole-database measurements with their limitations instead of reporting zero.

After read measurements, insert and deliberately roll back up to 10,000 deterministic extra relationships in batches of 100, 1,000 and 10,000, repeated three times in the full profile. Keep row generation outside the timed scope and report the full transaction boundary including rollback. Verify original persisted rows afterward. Read phases precede these writes so rollback-related page/cache effects do not contaminate read comparisons. Repeat write probes in a recorded order and disclose their warm-state limitation.

This cost approximates application index-maintenance overhead. It does not measure durable commit throughput. Report medians, p95, sample counts, units and per-round ratios; retain raw timings so variation is inspectable. Recommend a layout only for the measured selection mix when the improvement repeats across rounds and its write/space tradeoff is documented. Mixed or noisy outcomes are valid results, not a failed benchmark or an excuse to cherry-pick a cell.

### 6. Make completion and resource ownership explicit

Version the interval evidence schema independently. Record exact package/source fingerprints including imported paths checked internally, harness/helper fingerprints, lockfile/provider identity, Python/database/service versions, data-generation parameters, matrix, randomized schedule, settings, correctness and cleanup status. Publish only sanitized relative source labels and synthetic IDs. Keep credentials in process environment, never commands or reports.

Reuse bounded execution, ownership labels, atomic report writing and sanitization from the existing qualification tooling. Add only the PostgreSQL-only service orchestration needed here, with collision refusal, loopback binding, explicit ownership checks and finite subprocess/startup/operation deadlines. No ambient DSN or pre-existing database is eligible. A default full-run wall budget of four hours is explicit and may be raised by the operator; exceeding it leaves incomplete evidence without downgrading the profile. Preflight disk/memory limits and stop on their exhaustion. Cleanup only run-owned disposable resources, preserve reports, and mark completion after successful cleanup.

The completion validator enumerates every required adapter/layout/statistics/round/query and write-probe case. A partial report, missing metric required for a conclusion, wrong provenance, failed oracle or failed teardown cannot become successful evidence. Optional unavailable metrics carry a reason and narrow the conclusion.

### 7. Resume only validated treatment checkpoints

Evidence schema v2 records session IDs, UTC boundaries, fixed resource settings,
source/runtime attestations and per-treatment checksums. After every complete
worker result, database cleanup, continuity check and correctness validation,
atomically save the complete schedule prefix. `PAUSE` in the output directory or
`--pause-after N` stops at the next treatment boundary; Ctrl+C stops the worker
and discards that incomplete treatment. A normal caught failure can preserve
previous checkpoints only when cleanup and continuity checks succeed.

`--resume-from <report.json>` reads a stopped v2 report and starts fresh owned
resources in a new output directory. Revalidate every retained cell against the
independent oracle, schedule, fingerprints, settings and checksums; require the
same profile, seed, runtime, source, service image and memory/disk limits.
The time budget restarts per explicitly recorded session. Never edit a source
report, reuse its database, merge arbitrary reports, drop a slow cell, or reuse
partial samples. Reject active/unclean reports, legacy v1 reports, changed
provenance and malformed histories. Abrupt termination remains ineligible for ordinary resume. The explicit recovery
procedure below can produce a separately audited stopped prefix.

Completion still requires the entire original matrix and all session cleanups.
Session boundaries and cross-session pairs are visible in summaries; paused or
failed evidence never qualifies. Host load and OS cache remain uncontrolled,
so resumed results are disclosed as multi-session observations, not a claim of
uninterrupted measurement. The already-running v1 benchmark remains untouched.

### 8. Keep repository ownership explicit

This change resides in MeldStore and extends its workload qualification specification. The shared `metadata-provider-contract` already covers public SQL, explicit transactions, application-owned relationships and independent qualification. Record that review in the verification documentation; do not duplicate requirements in `meld-platform-specs`. A discovered provider defect becomes a separate MeldDB/shared-contract change with exact-pin requalification as appropriate. PostgreSQL structural inspection remains an independent MeldDB roadmap item.

## Risks / Trade-offs

- Broad SQLite scans make the matrix slow -> preflight a smoke run, enforce a visible finite budget and preserve partial evidence without making an acceptance claim.
- Statistics, vacuum and OS caching can confound apparent wins -> isolate treatments, record preparation/effective settings, vary order across rounds and report warm-state limits.
- Covering indexes cost space and writes -> include build, allocation and rollback-probe measurements for every layout.
- Synthetic timestamp/config distributions are not universal -> preserve the known fixture and publish its distribution; advise applications to rerun with their own synthetic representative distribution.
- Fabricated catalog data could be mistaken for usable storage -> only fresh benchmark resources, explicit metadata-only report labels and no production import/export path.
- A fixed-memory study does not choose optimal cache budgets -> document the limitation; do not widen core API scope to complete an optional tuning sweep.

## Migration Plan

No catalog or public API migration. Ship the runner, tests and application guidance together. Run focused regressions and the required uv pytest/Ruff checks, all-adapter live smoke, then the full metadata matrix. Update the roadmap only from completed evidence. If results are inconclusive, publish that outcome and leave the relevant tuning question open. Removing the tools reverts the change without modifying user databases.


### 9. Recover an interrupted prefix explicitly

User-authorized November 20 amendment: add `tools/recover_intervals.py` as a
separate offline orchestration tool. Keep measured sources, dependencies and HEAD
unchanged; attest the recovery tool independently in an additive v2 audit record.
Initially support Windows CIM process inspection only; other platforms fail closed.
Require exclusive operator use, inspect all Python/uv processes before and after
cleanup, reject active interval parents/workers or inaccessible command lines,
and serialize recovery attempts with an exclusive source-root lock. A stale lock
requires inspection, never automatic lock stealing. Do not launch concurrent runs.

Revalidate every saved treatment and session against the existing oracle,
checksums, exact current provenance and service/settings contract. Only an open
incomplete v2 session with at least one accepted treatment is eligible. Verify
canonical source paths, session/manifest identity and worker controls; reject
links/junctions. Delete only validated run-owned Docker resources and generated
scratch after liveness checks. Preserve source report bytes, logs and controls.

Write a new report to a fresh destination. Close its measurement session at the
last checkpoint timestamp, with explicit recovered end reason; record actual
cleanup/recovery time separately. Preserve the saved guard snapshot exactly.
Continuity after the last accepted checkpoint is unknown and excluded, not
retroactively asserted. Audit original state, source/ownership/tool hashes,
process inspections, retained count and cleanup. Existing resume validation must
accept the new stopped prefix before report publication. Never reuse incomplete
samples or databases. A failure leaves no published eligible report; retry only
after resolving the cause with a fresh output. Normal resume remains unchanged.
