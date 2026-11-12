# Spec Delta

## MODIFIED Requirements

### Requirement: S3 coordination is single-host
The system SHALL require all participants in one S3-backed store to share one catalog and one persistent path-bound coordination directory on a single host. The catalog SHALL be a local SQLite catalog or the explicitly configured PostgreSQL catalog bound to the same coordinator and S3 prefix.

#### Scenario: New coordinator points at existing prefix
- **WHEN** a different local coordination directory is configured for an existing remote marker
- **THEN** initialization fails closed instead of adopting the remote store

#### Scenario: A PostgreSQL catalog is paired with the wrong storage binding
- **WHEN** a caller opens a PostgreSQL store with a catalog identity, coordinator, or S3 prefix that disagrees with its recorded binding
- **THEN** MeldStore rejects it before permitting payload mutation, maintenance, or publication
