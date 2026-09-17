# Spec Delta

## Purpose

Defines exact-byte integrity verification and the distinct temporary, decoded, and persistent retrieval forms exposed to callers.

## ADDED Requirements

### Requirement: Stored bytes use XXH3-128 integrity
The system SHALL record exact stored byte size and lowercase XXH3-128 digest after serialization completes and SHALL treat a mismatch as corruption, not absence.

#### Scenario: Stored object is corrupted
- **WHEN** retrieved bytes differ in size or digest from the catalog record
- **THEN** the system raises an integrity error before exposing successful retrieval

### Requirement: Materialization is verified and scoped
The system SHALL download or copy a complete object to a temporary local file, verify it, and expose that file only for the materialization context lifetime.

#### Scenario: Remote object materializes
- **WHEN** a caller materializes an S3-backed blob
- **THEN** the complete local temporary file is verified before the context yields it

### Requirement: Decoding follows recorded encoding
The system SHALL decode only with the exact registered handler and encoding version recorded for the blob and SHALL fail explicitly when that implementation is unavailable.

#### Scenario: Optional decoder is absent
- **WHEN** a caller requests decoded data without the required optional dependency
- **THEN** the system reports the missing dependency and does not fall back to pickle or another format

### Requirement: Persistent file export is create-only
The system SHALL copy exact verified stored bytes to a caller-selected local filename, require an existing parent, and refuse every existing destination without deleting or changing the source blob.

#### Scenario: Export succeeds
- **WHEN** a ready blob is exported to a new filename on a supported local filesystem
- **THEN** the resulting persistent file matches the catalog size and digest

#### Scenario: Export destination exists
- **WHEN** the destination is an existing file, directory, or dangling symlink
- **THEN** export reports a conflict and leaves the destination and blob unchanged
