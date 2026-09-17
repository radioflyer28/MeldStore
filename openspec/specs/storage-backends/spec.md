# storage-backends Specification

## Purpose
Defines local and S3-compatible immutable object storage while keeping catalog and coordination ownership explicit and backend qualification bounded.

## Requirements

### Requirement: Object publication is create-only
The system SHALL publish payloads under fresh immutable keys and SHALL reject an existing destination rather than overwrite it.

#### Scenario: Competing publication targets one key
- **WHEN** two participants attempt to publish the same destination key
- **THEN** at most one succeeds and the stored object is complete

### Requirement: Storage I/O is outside write transactions
The system MUST perform payload staging, hashing, upload, download, and verification outside long-lived catalog write transactions.

#### Scenario: Storage call is attempted in transaction
- **WHEN** a caller invokes payload I/O while its catalog transaction is active
- **THEN** the system rejects the operation and prevents commit of the failed transaction

### Requirement: S3 coordination is single-host
The system SHALL require all participants in one S3-backed store to share one local catalog and one persistent path-bound coordination directory on a single host.

#### Scenario: New coordinator points at existing prefix
- **WHEN** a different local coordination directory is configured for an existing remote marker
- **THEN** initialization fails closed instead of adopting the remote store

### Requirement: S3 configuration excludes persisted credentials
The system SHALL keep credentials as runtime input and SHALL omit them from catalogs, identity markers, backup manifests, and operation results.

#### Scenario: Store is reopened
- **WHEN** an application reopens an S3-backed store
- **THEN** it must supply credentials again while stored identity information remains non-secret

### Requirement: Backend qualification is named
The system MUST distinguish tested behavior on pinned RustFS from unqualified AWS, Garage, multi-host, or generic S3 behavior.

#### Scenario: Compatible-service tests pass
- **WHEN** the pinned RustFS qualification suite succeeds
- **THEN** the project may claim that RustFS version but not universal S3 or AWS certification
