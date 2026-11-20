# representative-workload-qualification Specification

## Purpose

Define reproducible workload qualification for generic blob storage with relational metadata, distinguishing correctness, measured capacity and the deployment actually exercised.

## Requirements

### Requirement: Workload profiles declare actual scale
The qualification runner SHALL distinguish smoke, representative and customized runs. Representative qualification SHALL require 100 valid synthetic Parquet files, each 100-300 MB in encoded decimal bytes, and a manifest recording seed, generation parameters, actual sizes, row counts and expected integrity values. Smoke or reduced runs MUST NOT satisfy representative qualification.

#### Scenario: Small profile completes
- **WHEN** the smoke profile passes every scenario
- **THEN** its report identifies smoke coverage and leaves representative qualification unproven

#### Scenario: Generated corpus misses the requested scale
- **WHEN** a representative corpus has fewer than 100 files or a file outside the declared size range
- **THEN** the runner rejects representative qualification even if all performed operations succeed

### Requirement: Qualification owns only disposable resources
The runner SHALL require explicit representative selection, preflight host and service storage capacity against planned copies, and enforce declared resource limits. It SHALL operate only on fresh run-owned catalogs, storage prefixes and coordinator directories. It SHALL reject occupied or overlapping protected destinations and never silently reduce the requested workload.

#### Scenario: Full run lacks resources
- **WHEN** resource preflight cannot establish sufficient capacity or execution exceeds a configured limit
- **THEN** the run fails or remains incomplete, reports the limiting resource, and does not claim a smaller run qualified the requested profile

#### Scenario: Destination already contains data
- **WHEN** an operator supplies an occupied catalog or storage destination
- **THEN** the runner refuses it without overwriting data or cleaning resources it does not own

### Requirement: Adapter comparisons use a common correctness oracle
The runner SHALL compare direct SQLite, MeldDB SQLite and MeldDB PostgreSQL using the same synthetic corpus and application-owned SQL fixtures. It SHALL verify payload sizes and XXH3-128 digests, metadata, IDs, versions, foreign keys, interval-query results and pagination against expected values independent of the adapter under test. Domain tables SHALL remain outside the library core.

#### Scenario: Fast query returns incorrect data
- **WHEN** a timed query or retrieval disagrees with the independent expected result
- **THEN** the run fails correctness regardless of its measured performance

#### Scenario: Metadata-only workload runs
- **WHEN** interval joins, pagination or metadata edits execute
- **THEN** qualification checks that no payload storage I/O occurred during those operations

### Requirement: Bulk and concurrent workloads preserve explicit transactions
The runner SHALL exercise bounded bulk SQL and concurrent independent clients on one host, recording batch sizes, client counts, successes, conflicts and failures. It SHALL verify shared application/publication transaction rollback and enforce payload I/O outside SQL write scopes. It MUST NOT treat one bulk API call as proof of one network round trip.

#### Scenario: Bulk batch violates a relationship
- **WHEN** a bulk application write fails a foreign key after earlier shared SQL writes
- **THEN** qualification verifies complete transaction rollback and absence of falsely ready blobs

#### Scenario: Concurrent edits compete
- **WHEN** clients update the same metadata version
- **THEN** qualification checks successful version advancement and stale-edit conflicts without losing updates or counting conflicts as successful work

### Requirement: Operational phases verify preserved data
The runner SHALL qualify metadata migration dry runs and interruption/resumption, offline SQLite-to-PostgreSQL cutover, and complete catalog-plus-payload backup/restore. It SHALL verify preserved IDs, versions, application relationships and payload integrity at fresh destinations, source preservation, and explicit destination readiness before counting each phase as successful.

#### Scenario: Migration resumes
- **WHEN** a migration stops after a durable batch and later resumes
- **THEN** verification checks durable progress, unchanged IDs and relationships, and the expected final metadata without double-applying transformations

#### Scenario: Restored payload is corrupt
- **WHEN** catalog restoration succeeds but payload verification fails
- **THEN** the phase fails and cannot report a ready or qualified restored store

### Requirement: Interrupted operations remain distinguishable from completion
The runner SHALL exercise interrupted publication and interrupted backup or restore using documented recovery boundaries. It SHALL distinguish a failed run from a deliberately interrupted scenario whose recovery has been verified. A partial evidence file MUST NOT be interpreted as completed qualification.

#### Scenario: Publication outcome is uncertain
- **WHEN** publication is interrupted around its commit boundary
- **THEN** the scenario reopens and inspects the original stable ID before deciding recovery and verifies no duplicate publication or overwrite

#### Scenario: Runner terminates unexpectedly
- **WHEN** execution stops before all required phases finish
- **THEN** any surviving report remains incomplete and cannot confer qualification

### Requirement: Measurements retain provenance and limitations
The runner SHALL emit versioned machine-readable evidence and a human summary containing exact code and dependency revisions, service versions, topology, actual workload counts, correctness results, timings, query plans, client memory and staging disk measurements. It SHALL identify sampling methods, unavailable metrics and warm-up conditions, and exclude credentials, private paths and private identifiers.

#### Scenario: Metric is unavailable
- **WHEN** the platform cannot measure a requested resource
- **THEN** the report records unavailable with a reason rather than zero, and makes no qualification claim for that unmeasured resource

#### Scenario: Query latency is summarized
- **WHEN** the report gives latency percentiles or throughput
- **THEN** it includes units, sample counts, warm-up treatment, operation boundaries and success/error counts sufficient to interpret them

### Requirement: Qualification claims match completed evidence
A representative claim SHALL require all declared adapters and required phases to pass at representative scale, with client memory and staging disk evidence. CI SHALL run a required smoke profile that fails on missing prerequisites or skipped required phases. Reports SHALL limit claims to tested versions and topology; they MUST NOT imply AWS, multi-host or general MeldDB PostgreSQL support.

#### Scenario: Live-service CI lacks prerequisites
- **WHEN** a required service, dependency or workload phase is unavailable
- **THEN** the job fails rather than passing through skipped qualification

#### Scenario: Only one adapter passes the full profile
- **WHEN** another declared adapter is incomplete or incorrect
- **THEN** the report preserves the completed adapter's observations but does not claim the comparison or milestone is fully qualified

### Requirement: Interval experiments declare metadata-only scope
The interval experiment runner SHALL distinguish smoke, full metadata and custom profiles. A full metadata result SHALL require 100 synthetic blob references and 300,000 application relationship rows on direct SQLite, MeldDB SQLite and MeldDB PostgreSQL 17. It MUST NOT satisfy representative payload qualification or assert payload existence or integrity.

#### Scenario: Full metadata experiment completes
- **WHEN** all declared interval cases pass at full metadata scale
- **THEN** the report identifies completed metadata evidence and leaves full payload qualification unchanged

#### Scenario: Reduced profile completes
- **WHEN** an operator selects fewer rows, adapters or cases
- **THEN** the report identifies a smoke or custom result and cannot claim full metadata acceptance

### Requirement: Interval comparisons control experimental treatments
The runner SHALL compare declared index layouts and statistics treatments on equivalent isolated fixtures. It SHALL record exact index definitions, effective database memory settings, statistics preparation, query parameters, warm-ups, repeated sample counts and execution order. Unavailable controls and uncontrolled operating-system caches MUST be disclosed; reopening a connection MUST NOT be reported as a cold-cache test.

#### Scenario: Candidate layout benefits from a different preparation
- **WHEN** paired cases have different data, memory settings or unplanned statistics changes
- **THEN** the runner rejects their controlled comparison and reports the mismatched treatment

#### Scenario: A cache control is unavailable
- **WHEN** a public adapter cannot change an intended memory setting
- **THEN** the runner records the effective fixed setting and restricts its comparison to that setting without accessing a private connection

### Requirement: Interval tuning preserves relational results
Every timed interval query SHALL match an independently generated ordered result, including grouped counts, on the same application-owned SQL data. Correctness cases SHALL cover strict interval-overlap boundaries, empty results and narrow and broad selections. The runner SHALL verify fixture integrity and assert no payload-storage I/O during measured metadata operations.

#### Scenario: Boundary comparison is changed
- **WHEN** a query includes an interval that only touches the query boundary
- **THEN** the independent oracle rejects the result even if that query is faster

#### Scenario: A broad join loses rows
- **WHEN** an index or query variant returns wrong IDs or grouped counts
- **THEN** the experiment fails correctness and excludes that variant from recommendations

### Requirement: Interval guidance includes index costs and uncertainty
Experiment evidence SHALL include latency distributions, query plans, index build time, available index or database space measurements, and bounded insertion costs under explicit transactions. Rollback probes SHALL verify unchanged persisted rows. Guidance SHALL describe selection and write-cost tradeoffs per adapter and report inconclusive results without imposing application indexes or tuning defaults on library users.

#### Scenario: An index improves only narrow reads
- **WHEN** a layout improves narrow queries but worsens broad queries, insertion cost or space
- **THEN** guidance reports those tradeoffs and limits any recommendation to the measured workload

#### Scenario: A write probe rolls back
- **WHEN** a timed batch exits by deliberate rollback
- **THEN** the runner verifies the original rows and labels the cost as insertion plus rollback, not durable commit throughput

### Requirement: Interval evidence completion requires owned reproducible runs
The interval runner SHALL use fresh run-owned resources, finite execution budgets and sanitized versioned evidence with source, dependency and service provenance. Completion SHALL require the declared adapter/treatment/case matrix, correctness checks and successful cleanup. Missing cases, failed checks or interruption MUST leave evidence failed or incomplete, never silently reduce the matrix or overwrite an existing destination.

#### Scenario: An existing destination is supplied
- **WHEN** the destination is occupied or overlaps protected input resources
- **THEN** the runner refuses it without changing those resources

#### Scenario: An interval run stops early
- **WHEN** the runtime budget expires or service cleanup fails
- **THEN** partial results retain their provenance and failure state and cannot confer completed metadata qualification

#### Scenario: Evidence contains credentials or private paths
- **WHEN** evidence serialization encounters sensitive environment values
- **THEN** publication rejects or sanitizes them before writing a shareable artifact


### Requirement: Interval checkpoints preserve evidence across explicit sessions
The runner SHALL checkpoint only complete validated treatments and resume only
stopped, cleaned v2 evidence into fresh owned resources. It SHALL revalidate results
and provenance, rerun interrupted treatments in full, and disclose session boundaries.
It MUST reject incompatible or damaged checkpoints and MUST NOT qualify partial matrices.

#### Scenario: Operator pauses and resumes
- **WHEN** a pause is requested and owned cleanup succeeds
- **THEN** resume preserves validated completed treatments and starts the first missing treatment in fresh resources with a new recorded session

#### Scenario: A worker is interrupted
- **WHEN** interruption occurs before a treatment is validated and checkpointed
- **THEN** that treatment contributes no samples and must run again in full

#### Scenario: A checkpoint is incompatible or damaged
- **WHEN** source, runtime, profile, seed, fixed budgets, service, schedule, samples, checksums or session history differ from the declared contract
- **THEN** resume refuses before launching workers and leaves the source evidence unchanged

#### Scenario: A source report is active, unclean or legacy
- **WHEN** the source lacks a confirmed clean stop or uses the legacy evidence format
- **THEN** the runner refuses resume without rewriting the source or claiming completed qualification


### Requirement: Interrupted prefixes require explicit verified recovery
An offline recovery command SHALL preserve the original report and validate its
accepted v2 prefix against unchanged measured provenance. It SHALL verify stopped
runners/workers, canonical ownership and cleanup before publishing fresh resumable
evidence. It MUST fail closed on uncertainty and discard incomplete treatments.
Ordinary resume SHALL still reject unclean reports.

#### Scenario: An interrupted prefix is recoverable
- **WHEN** all retained treatments validate, processes are stopped and owned cleanup succeeds
- **THEN** a separate report retains those treatments, closes measurement at the last verified checkpoint, records later recovery explicitly and passes existing stopped-checkpoint validation

#### Scenario: Recovery cannot establish safety or integrity
- **WHEN** processes are active or uninspectable, ownership or provenance differs, paths escape through links, evidence is damaged or cleanup fails
- **THEN** recovery refuses to publish an eligible checkpoint and preserves the source report


### Requirement: Recovery distinguishes measurement from later cleanup
Recovered reports SHALL attest source and recovery-tool digests, original
interruption state, retained count, process checks and later cleanup time. The
measurement session SHALL end at its last verified checkpoint with an explicit
recovery reason and unchanged guard evidence. Recovery MUST NOT assert subsequent
continuity or qualify partial matrices.

#### Scenario: Cleanup occurs after an unobserved interruption
- **WHEN** recovery verifies cleanup later than the last accepted treatment
- **THEN** measurement and cleanup timestamps remain separate and the intervening continuity is explicitly unknown
