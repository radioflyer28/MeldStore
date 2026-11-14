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
