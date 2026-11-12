# postgresql-metadata Specification

## Purpose

Define the PostgreSQL metadata deployment for MeldStore and the verified offline transition from an embedded SQLite catalog.

## Requirements

### Requirement: PostgreSQL catalog selection is explicit
MeldStore SHALL offer an opt-in PostgreSQL catalog through MeldDB with explicit connection and coordination configuration. It SHALL preserve both SQLite catalog adapters and keep connection secrets out of persistent metadata, artifacts, and operation diagnostics.

#### Scenario: PostgreSQL configuration is unusable
- **WHEN** the optional dependency is missing, the server is unavailable, or catalog identity validation fails
- **THEN** opening fails without choosing another backend or performing payload I/O

### Requirement: PostgreSQL catalog SQL remains application-owned
MeldStore SHALL expose ordinary SQL metadata and the stable `ms_blobs(id)` reference target. Applications SHALL retain ownership of their schema and migrations. The PostgreSQL catalog SHALL preserve public schema, query, and guarded-edit behavior, including bounded pagination, normalized UTC timestamps, and monotonic metadata versions.

#### Scenario: Metadata-only query or edit uses PostgreSQL
- **WHEN** a caller filters, pages, or conditionally updates metadata
- **THEN** results and conflicts follow the documented catalog contract without reading or rewriting payloads

#### Scenario: Versioned metadata backfill is interrupted
- **WHEN** an application-provided transformation is interrupted after a committed batch
- **THEN** the catalog resumes at durable progress and preserves public IDs, application references, and completed changes

### Requirement: PostgreSQL transactions preserve publication atomicity
MeldStore SHALL compose catalog and application SQL in one explicit transaction through MeldDB's public interface. It SHALL enforce read-only scopes, reject caller control of transaction and connection state, and preserve the shared `sql_many` result and rollback contract.

#### Scenario: Bulk application write violates a foreign key
- **WHEN** application inserts share a transaction with blob publication metadata and one bulk row fails
- **THEN** all SQL changes roll back, no blob becomes ready, and no partial bulk success is reported

#### Scenario: Read scope attempts a write
- **WHEN** a caller attempts persistent mutation or bulk execution in a read-only transaction
- **THEN** the operation fails and the transaction cannot commit

### Requirement: PostgreSQL uncertain publication requires inspection
MeldStore SHALL quarantine a catalog handle after an unresolved transaction outcome and require explicit reopen and inspection by stable blob ID before retry or cleanup. It SHALL retain the established prepare, finalize, and publish boundary, with verified payload I/O outside SQL write scopes.

#### Scenario: Publication commit response is lost
- **WHEN** the coordinator loses the commit response after staging a verified object
- **THEN** it can reopen and distinguish durable publication from incomplete work without overwriting the object, reporting false success, or collecting a potentially referenced object

### Requirement: SQLite cutover uses a fresh verified destination
MeldStore SHALL provide offline cutover from a quiesced SQLite store to an explicitly selected fresh PostgreSQL catalog and fresh S3 prefix. It SHALL verify payloads and preserve logical blob IDs, object keys, descriptors, metadata and lifecycle history. Applications SHALL provide destination SQL definitions and a verified transfer for their data and constraints.

#### Scenario: Application relationships survive cutover
- **WHEN** a quiesced store with application-owned foreign keys and junction rows is transferred
- **THEN** the destination returns the same IDs, metadata, relationships, and verified payload bytes before the operator explicitly switches clients

#### Scenario: Transfer fails or the target is already occupied
- **WHEN** migration is interrupted, payload integrity fails, an application mapping is missing, or the target is occupied
- **THEN** MeldStore leaves the source available for recovery, neither overwrites an occupied target nor marks an incomplete destination ready, and reports how to inspect an uncertain outcome

### Requirement: PostgreSQL deployment claims require integrated evidence
MeldStore SHALL name the PostgreSQL version, exact MeldDB revision, S3-compatible service, and coordinator model actually qualified. It SHALL require an integrated transaction, cutover, backup, and interrupted-publication recovery run before claiming support for that deployment.

#### Scenario: Upstream PostgreSQL proof passes
- **WHEN** MeldDB passes its PostgreSQL suite but the MeldStore deployment gates remain open
- **THEN** MeldStore still reports the deployment as unqualified
