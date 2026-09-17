# backup-and-transfer Specification

## Purpose
Defines offline, verified backup, restore, and relocation of the whole catalog-plus-payload store without automatic cutover or source deletion.

## Requirements

### Requirement: Backup captures catalog and referenced payloads
The system SHALL create an offline artifact containing a physical SQLite snapshot, portable SQL export, application declaration, identity metadata, and every ready referenced payload verified at source and destination.

#### Scenario: Application SQL exists
- **WHEN** the shared catalog contains application-owned tables, triggers, indexes, and foreign keys
- **THEN** physical backup preserves them and the SQL export represents their supported SQLite schema and data

#### Scenario: Lifecycle is unfinished
- **WHEN** publishing, migration, prepared-token, or pending-deletion work remains
- **THEN** backup refuses to create a completed artifact until the caller resolves it

### Requirement: Completed artifacts are manifest-last
The system SHALL publish a bounded, checksummed manifest only after catalog and payload copies complete successfully.

#### Scenario: Backup process exits early
- **WHEN** interruption occurs before completion
- **THEN** the destination lacks the completed manifest and is not accepted as a valid backup

### Requirement: Restore uses a fresh destination
The system SHALL validate the complete artifact and copy all payloads before publishing a fresh destination catalog, preserving public IDs, versions, keys, encodings, metadata, and application SQL.

#### Scenario: Restore destination exists
- **WHEN** the requested destination already exists or overlaps protected source paths
- **THEN** restore refuses it without overwriting or merging content

### Requirement: Transfer is a non-destructive offline copy
The system SHALL implement transfer through verified backup and restore, leave the source unchanged, and require applications to perform cutover explicitly.

#### Scenario: Transfer completes
- **WHEN** a local or S3 source is transferred to a fresh supported destination
- **THEN** the returned destination preserves logical identities and the application decides whether to switch consumers

#### Scenario: Transfer response is uncertain
- **WHEN** the final response is lost after destination publication
- **THEN** the caller inspects and validates the destination rather than retrying with overwrite
