# Spec Delta

## ADDED Requirements

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
