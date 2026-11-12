# Spec Delta

## MODIFIED Requirements

### Requirement: Backup captures catalog and referenced payloads
The system SHALL create an offline artifact containing the whole catalog, an application declaration, identity metadata, and every ready referenced payload verified at source and destination. For SQLite the artifact SHALL include a physical snapshot and portable SQL export. For PostgreSQL it SHALL include a PostgreSQL-native archive of the dedicated catalog database.

#### Scenario: Application SQL exists
- **WHEN** the shared catalog contains application-owned tables, triggers, indexes, and foreign keys
- **THEN** the catalog artifact preserves the supported SQL objects and data, and the SQLite SQL export represents their supported SQLite schema and data

#### Scenario: Lifecycle is unfinished
- **WHEN** publishing, migration, prepared-token, or pending-deletion work remains
- **THEN** backup refuses to create a completed artifact until the caller resolves it

### Requirement: Provider backup is a whole-catalog foundation
When the MeldDB SQLite adapter is selected, the system SHALL use MeldDB's physical SQLite backup primitive to capture the complete shared SQL database, including MeldStore and application-owned external tables. MeldStore MUST NOT represent MeldDB's managed logical export as a complete catalog or blob-store backup; MeldStore remains responsible for its portable SQL export, payload copying, integrity verification, lifecycle checks, and manifest-last publication.

#### Scenario: Shared catalog is physically snapshotted
- **WHEN** a MeldStore catalog containing application-owned tables, indexes, triggers, foreign keys, and data is snapshotted through the MeldDB SQLite adapter
- **THEN** the detached physical snapshot contains those SQL objects and data and opens independently as a valid SQLite database

#### Scenario: Complete backup is requested
- **WHEN** a caller creates a complete MeldStore backup while using the MeldDB SQLite adapter
- **THEN** MeldStore builds the artifact from the whole-database snapshot, its own portable SQL export, and verified payload copies without substituting MeldDB's managed logical export

#### Scenario: Only the provider snapshot exists
- **WHEN** an operator takes only the MeldDB physical database backup
- **THEN** the result is identified as a catalog-only snapshot and is not represented as a complete MeldStore catalog-plus-payload backup

## ADDED Requirements

### Requirement: PostgreSQL recovery uses a fresh verified destination
MeldStore SHALL provide an operator-assisted recovery procedure from a completed PostgreSQL catalog-plus-payload artifact. The procedure SHALL restore to a fresh isolated database and storage prefix, validate application SQL and payload integrity, and require explicit operator cutover.

#### Scenario: PostgreSQL archive and payloads restore together
- **WHEN** the operator restores a completed PostgreSQL artifact with application-owned SQL into a fresh destination
- **THEN** the restored catalog preserves supported objects, IDs, versions, and constraints, and the operator can retrieve verified payloads before switching clients

#### Scenario: Only the database archive is available
- **WHEN** an operator supplies only the PostgreSQL database archive or payload verification fails
- **THEN** the procedure rejects the input as a complete restore and withholds destination readiness
