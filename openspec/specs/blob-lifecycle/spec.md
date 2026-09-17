# blob-lifecycle Specification

## Purpose
Defines immutable blob creation, publication, recovery, retirement, and deletion without hidden cache policy or payload replacement.

## Requirements

### Requirement: Blob publication is explicit
The system SHALL prepare payload bytes outside the catalog write transaction and SHALL make a blob visible only after its metadata and application-owned references successfully commit.

#### Scenario: Shared publication succeeds
- **WHEN** a caller finalizes a prepared payload, writes application SQL, and publishes through one transaction
- **THEN** the ready blob and application references become visible together after commit

#### Scenario: Transaction rolls back
- **WHEN** publication or an application constraint fails before commit
- **THEN** no ready blob record or application reference becomes visible

### Requirement: Blob identities and payloads are immutable
The system SHALL maintain stable public blob IDs and SHALL require a new blob identity for changed payload bytes.

#### Scenario: Caller retries with an existing ID
- **WHEN** a caller retries creation using the same ID and matching committed content
- **THEN** the system allows the caller to resolve the existing outcome without creating a duplicate

#### Scenario: Existing ID describes different content
- **WHEN** a caller attempts to create different content under an existing ID
- **THEN** the system reports a conflict

### Requirement: Retirement respects SQL references
The system SHALL retire a blob through an explicit transaction, enforce application-owned restrictive references, and defer physical deletion to resumable maintenance.

#### Scenario: Referenced blob is retired
- **WHEN** an application foreign key still restricts the blob
- **THEN** retirement fails and the ready blob remains available

#### Scenario: Physical deletion fails
- **WHEN** object removal fails after successful retirement
- **THEN** cleanup remains pending and can be retried without resurrecting the blob

### Requirement: Recovery is explicit
The system SHALL report prepared, publishing, orphaned, missing, corrupt, and pending-deletion states without silently adopting or deleting uncertain objects.

#### Scenario: Publication outcome is uncertain
- **WHEN** a process loses the commit response
- **THEN** the caller can resolve state by public ID before retry or cleanup
