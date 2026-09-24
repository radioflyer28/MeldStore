# Spec Delta

## ADDED Requirements

### Requirement: Provider backup is a whole-catalog foundation
When the MeldDB adapter is selected, the system SHALL use MeldDB's physical SQLite backup primitive to capture the complete shared SQL database, including MeldStore and application-owned external tables. MeldStore MUST NOT represent MeldDB's managed logical export as a complete catalog or blob-store backup; MeldStore remains responsible for its portable SQL export, payload copying, integrity verification, lifecycle checks, and manifest-last publication.

#### Scenario: Shared catalog is physically snapshotted
- **WHEN** a MeldStore catalog containing application-owned tables, indexes, triggers, foreign keys, and data is snapshotted through the MeldDB adapter
- **THEN** the detached physical snapshot contains those SQL objects and data and opens independently as a valid SQLite database

#### Scenario: Complete backup is requested
- **WHEN** a caller creates a complete MeldStore backup while using the MeldDB adapter
- **THEN** MeldStore builds the artifact from the whole-database snapshot, its own portable SQL export, and verified payload copies without substituting MeldDB's managed logical export

#### Scenario: Only the provider snapshot exists
- **WHEN** an operator takes only the MeldDB physical database backup
- **THEN** the result is identified as a catalog-only snapshot and is not represented as a complete MeldStore catalog-plus-payload backup
