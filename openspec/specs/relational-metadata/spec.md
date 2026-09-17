# relational-metadata Specification

## Purpose
Defines generic, versioned relational metadata that applications can query and reference with ordinary SQL through either supported SQLite adapter.

## Requirements

### Requirement: User-defined scalar schemas
The system SHALL allow applications to register named, versioned schemas with validated scalar fields, nullability, indexes, immutability, and allowed handlers.

#### Scenario: Identical schema is registered again
- **WHEN** an application registers the same name, version, and definition
- **THEN** registration succeeds idempotently

#### Scenario: Definition conflicts
- **WHEN** an application reuses a name and version with a different definition
- **THEN** the system rejects the conflict without changing the catalog

### Requirement: Metadata remains ordinary relational SQL
The system SHALL expose stable documented blob identities and schema tables so application-owned SQL tables can use foreign keys and joins without graph or document APIs.

#### Scenario: Application row references a blob
- **WHEN** an application inserts its row and blob publication in one shared transaction
- **THEN** the SQL foreign key is enforced atomically through both metadata adapters

### Requirement: Queries do not read payloads
The system SHALL provide bounded typed metadata filtering, ordering, and pagination without performing object-storage I/O.

#### Scenario: Metadata is searched
- **WHEN** a caller invokes stat or find with valid predicates
- **THEN** results come from the catalog and no payload is materialized

### Requirement: Metadata edits use optimistic concurrency
The system SHALL require the expected metadata version for guarded edits and SHALL increment the version only on a successful change.

#### Scenario: Stale edit is attempted
- **WHEN** an edit supplies an outdated metadata version
- **THEN** the system reports a conflict and preserves the current row

### Requirement: Schema migration is explicit and resumable
The system SHALL migrate metadata with named application transformations, dry-run support, bounded atomic batches, progress, and resumption without implicit read-time conversion.

#### Scenario: Migration resumes after interruption
- **WHEN** a previously committed batch exists and migration restarts
- **THEN** completed records are skipped and remaining records continue from durable progress
