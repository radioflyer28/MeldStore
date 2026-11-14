# Design

## Context

See [proposal.md](proposal.md) for motivation. The released PostgreSQL 17 path uses pinned MeldDB, RustFS and a persistent coordinator shared by clients on one host. `tests/postgres_provider/test_deployment_postgres.py` already proves an integrated small-fixture cutover and recovery workflow. `tools/qualify_workload.py` measures large private cUAS inputs but selects only the two SQLite adapters. Its domain imports and private source selection are unsuitable for the new generic runner.

`tools/s3_lab.py` owns disposable loopback RustFS containers and disk-backed Docker volumes. `tools/postgres_lab.py` owns PostgreSQL provisioning, installed-provider provenance and required live tests. `tools/qualify_local.py` measures process peak RSS on Windows and Unix. Reuse those seams selectively; avoid copying pytest fixtures into a standalone tool or changing the existing private-workload interface.

## Goals / Non-Goals

**Goals:** comparable correctness and resource evidence, reproducible synthetic inputs, bounded generation and execution, and recoverable explicit lifecycle operations. Separate application fixture SQL, provider access, payload storage and measurements so each failure has an identifiable owner.

**Non-Goals:** production performance SLAs, general SQL benchmarking, new storage or catalog APIs, managed document/graph adoption, application-specific core schemas, automatic optimization, cache policies, distributed coordination, AWS qualification or release publication. The full run qualifies the named host and topology, not every installation.

## Decisions

### 1. Separate generic runner and bounded synthetic generator

Add `tools/qualify_representative.py` with reusable helpers under a tools-local workload package and tests under `tests/qualification/`. Generate valid Parquet through the existing optional PyArrow dependency, one bounded row group at a time. Seed the generator and record its algorithm version, codec/settings and library versions; byte-identical reproduction is scoped to those versions. Use sufficiently varied generated values to reach encoded targets without padding files with arbitrary bytes. Derive expected metadata and query results from the generation manifest, not queries against the tested catalog.

Defaults chosen for planning:

| Profile | Payload corpus | Application relationship rows | Concurrency |
| --- | --- | --- | --- |
| smoke | 4 files, 1-4 MB each | 2,000 | 1 and 2 clients |
| representative | 100 files, 100-300 MB each, target sizes spread across the range | 300,000 | 1, 2 and 4 clients |

MB means 1,000,000 encoded bytes. Check actual files before ingestion. Nonstandard counts/sizes are labeled customized and cannot inherit representative status. Synthetic generation runs separately from ingestion timing. No private input-root option is needed for this runner. Retain the existing S08 tool for separately authorized private work.

Alternative: extend the old cUAS runner. Rejected because its domain schema, RadarNet inspection and private inputs would obscure whether the generic workflow remains portable.

### 2. Application-owned relational fixtures and adapter parity

Use a generic BlobSchema with `source_id`, `config_id`, `start_time` and `end_time`, plus fixture-owned configuration rows and interval association/junction tables referencing `ms_blobs(id)`. These are tool fixtures, not shipped domain types. Use normalized UTC timestamps and deterministic IDs. Generate overlapping, disjoint and exact-boundary intervals with documented half-open semantics, multiple associations per intact file and duplicate-safe result ordering.

Run direct SQLite, MeldDB SQLite and MeldDB PostgreSQL sequentially against the same immutable source manifest and pinned RustFS backend, each with a fresh catalog/prefix/coordinator. Hold payload storage constant to isolate catalog differences. Query the same expected result sets, pages and version changes. The fixture owns portable SQL and explicit backend DDL differences. Database-only operations count storage calls to demonstrate zero payload I/O.

Use explicit transaction batches of 100, 1,000 and 10,000 relationship parameter sets (bounded by the profile's row count). Compare repeated `sql` with `sql_many` on a bounded metadata-only sample; bulk-load the main corpus without repeatedly copying payloads. Record native SQLite and PostgreSQL query plans for identical predicates at several selectivities. Evaluate alternative interval indexes only on disposable fixture tables, recording build/write cost and query changes. Do not alter library indexes or global tuning defaults in this change.

Alternative: PostgreSQL-only measurements would miss baseline regressions and provider overhead. A local/S3 Cartesian product is unnecessary here because S3 is held constant and existing local tests remain intact.

### 3. Independent clients and explicit failure accounting

Use separate worker processes with their own public Catalog/Store handles, all sharing the permitted single-host coordinator for that variant. Schedule bounded read/write workloads with fixed seeds, disjoint writes plus deliberately competing versioned edits. Report success, expected conflicts, unexpected errors and attempts separately. Never share a thread-confined provider handle or introduce hidden retries. Ensure resources are closed before exclusive maintenance.

Instrument harness-owned call boundaries per worker; avoid global monkeypatch counters shared across concurrent threads. SQL API invocation counts and parameter-set counts are not wire round-trip counts. Assert payload preparation/finalization outside SQL write scopes and verify transaction-wide rollback for an invalid relationship inserted after publication metadata.

### 4. Operational phases extend existing public workflows

For each adapter, verify full import, retrieval/export and catalog queries; then run metadata dry-run/no-change checks, a bounded backfill interrupted after a committed batch, and resumption with IDs/relationships/version oracles. Back up the whole catalog and all ready payloads, restore to a fresh destination, and verify every file's size and XXH3-128 digest plus application SQL. Use PostgreSQL native archive tooling for PostgreSQL, preserving existing ownership and quiescence requirements.

Run offline cutover from each SQLite adapter's populated fixture to a fresh PostgreSQL catalog and S3 prefix, preserving the source and verifying the full destination. Execute these variants serially and dispose only verified run-owned temporary targets when no longer needed to bound simultaneous copies. Record cutover cost separately from steady-state operations.

Use focused interruption probes within the populated store: one publication with a lost/ambiguous commit response, and one interrupted backup or restore before readiness. Implement fault injection in the qualification harness or existing test seams, never a public fault API. Reopen/inspect by stable ID and verify no duplicate, overwrite or premature readiness. The full corpus backs the operational phases; fault injection need not duplicate all 217 existing live-provider cases at full size.

### 5. Resource preflight, evidence lifecycle and cleanup

Default to smoke. Representative execution requires an explicit profile and fresh output directory, with a dry preflight showing projected simultaneous source, stored, backup, restored, cutover and staging copies. Check both host filesystems and Docker volume capacity; do not assume host free space equals Docker VM free space. Use disk-backed service storage for the full run. Declare configurable memory, disk reserve and runtime limits before execution and monitor them while running. If capacity cannot be established, fail preflight with an actionable explanation; never silently downsize.

Provision loopback services and ephemeral credentials using existing pinned image definitions. Refactor lifecycle helpers only as necessary, preserving old commands and CI behavior. Validate path containment and run ownership before cleanup; delete only resources named in that run's manifest. Keep reports and incomplete diagnostics on failure, with a separately documented cleanup command for leftover owned resources. Do not reuse production endpoints or automatically enumerate/delete unrelated Docker resources.

Write a versioned JSON report atomically with run/phase status initially incomplete. Mark complete only after every required oracle and metric passes. A deliberate fault scenario can pass after verified recovery; unexpected termination cannot. Store generated payloads and raw reports under ignored `.qualification/`; commit only reviewed, sanitized evidence summaries. Reject credentials, absolute personal paths and private identifiers from publishable reports.

### 6. Measurements are observations with defined boundaries

Record generation separately; end-to-end import/export/backup/cutover timings include integrity work. Report bytes/sec, operation counts, SQL API calls, parameter sets, storage calls, batch sizes and client counts. For metadata latency, use fixed warm-up followed by at least 100 timed samples per query/client configuration and report p50/p95, failures and sample counts. Label first-pass versus warmed runs; do not claim cold caches without controlling them. Collect query plans outside timed samples. Record instrumentation overhead and avoid presenting noisy CI results as an SLA.

Run adapter variants in fresh subprocesses to isolate process-lifetime RSS. Record worker peak RSS and sampled aggregate client memory with sampling interval; summed individual peaks are only an upper bound, not simultaneous memory. Sample staging/catalog/backup footprint separately from source corpus and Docker volume use. Include database/service resource statistics when available, clearly separated from client usage. Unavailable metrics carry reasons, never zeros. Missing client-memory or staging-disk evidence prevents full resource qualification.

Report exact MeldStore revision (and dirty state), MeldDB installed commit, lockfile identity, Python/client/codec versions, service image digests, native archive versions and non-identifying host capacity/OS/filesystem characteristics. Publish the exact commands and actual profile, not credentials or hostnames. No arbitrary throughput target is imposed before a baseline exists; correctness, resource limits, complete phases and evidence availability are the gates. Record bottlenecks and ranked follow-ups instead of optimizing to a single noisy run.

### 7. CI and cross-repository coordination

Add a required smoke execution to the existing PostgreSQL/RustFS CI path, preserving the existing provider conformance suite and no-skip gate. Keep pure harness tests portable across existing Windows/Linux jobs. Full 10-30 GB generation and multi-copy operational qualification are explicit lab runs, not ordinary PR jobs. Acceptance requires one completed representative comparison on a documented supported Windows or Linux host; Linux CI smoke does not establish full-scale Linux qualification when the full run occurred on Windows, or vice versa.

Use the existing exact MeldDB pin `01e27441d7512f85663c8a86986429d62a69bff9`. Review findings in the meld-platform workset. Existing shared contracts already assign public SQL/transaction behavior to MeldDB and lifecycle/recovery/qualification to MeldStore. No shared delta is needed solely for new measurements. If a provider defect blocks a required phase, retain the failed evidence, propose the fix in MeldDB, update the local-only shared contract when guarantees change, then qualify and adopt a new exact pin. Do not mark this milestone complete by dropping the failing scenario. Stronger PostgreSQL structural inspection remains separate.

## Risks / Trade-offs

- Synthetic data may differ from real compressibility/selectivity -> record distributions and encoded sizes, include varied interval selectivities, limit conclusions to the corpus.
- Backup/cutover create several 10-30 GB copies -> preflight the execution graph, run variants serially, monitor actual use and stop within reserves.
- Process and service sampling can miss short peaks -> report sampling interval and measurement type; retain OS peak RSS where available.
- Container startup or disk pressure may dominate smoke timings -> use smoke for correctness and harness validation, not capacity promises.
- A provider limitation could expand scope -> preserve evidence and coordinate a separately owned change before continuing; no private-provider workaround.

## Migration Plan

No persisted catalog or payload format changes. Land harness, tests and documentation, then enable required smoke CI. Run the representative profile explicitly after preflight, review evidence and record supported topology and follow-ups. Rollback removes the new harness/CI step without migrating user data. OpenSpec completion requires actual full-profile evidence; until then tasks and qualification status stay incomplete. No package publication is part of this plan.

## Execution refinement from qualification

The November 12 full attempt encountered host standby during timed queries and
S3 restore. Long runs now offer a process-scoped Windows idle-sleep hold and
record continuity evidence from an independent heartbeat. Gaps above 30 seconds
invalidate the run; explicit sleep and lid-close are not overridden. This adds
no persistence behavior, retries, support guarantees or shared-contract changes.
The owner confirmed uninterrupted execution on this machine for the rerun.

The fourth attempt reached cutover after successful full-size SQLite recovery,
then hit the public transfer API's default metadata row bound. The harness now
passes a finite workload-derived allowance to both preflight and transfer and
records it in evidence. Dedicated 300,000-row/small-payload live regressions
passed for both SQLite adapters. No public default or provider contract changed;
the full-size profile still must be rerun in its entirety.

The fifth attempt encountered a whole-response S3 timeout without host suspension.
Request timeout is now an explicit finite resource parameter recorded alongside
zero retries. Large-file diagnostics exercise a 300-second request budget before
a fresh full attempt; global time/memory/disk and correctness gates are retained.
This tunes a qualification deployment setting, not a library/provider default.
