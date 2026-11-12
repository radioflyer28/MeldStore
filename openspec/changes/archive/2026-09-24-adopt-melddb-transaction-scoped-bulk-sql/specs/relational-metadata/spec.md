# Spec Delta

## ADDED Requirements

### Requirement: Transaction-scoped bulk SQL is adapter-independent
The system SHALL allow callers to apply one parameterized, non-result-producing SQL statement to an ordered, single-pass iterable of parameter sets through a live write transaction under either supported metadata adapter. It SHALL return a plain summary containing `parameter_sets`, the number consumed, and `affected_rows`, the backend-reported count or an explicit unavailable value. Empty input SHALL return zero for both fields without executing the statement. The system MUST NOT create, split, commit, retry, or continue a transaction implicitly, and no catalog-level convenience API SHALL hide transaction ownership.

#### Scenario: Ordered bulk execution succeeds
- **WHEN** a caller supplies multiple valid parameter sets through a live write transaction
- **THEN** the statement is applied in input order and the result reports the consumed count plus the affected-row count or an explicit unavailable value

#### Scenario: Empty bulk input is supplied
- **WHEN** a caller supplies an empty parameter-set iterable through a live write transaction
- **THEN** the system reports zero parameter sets and zero affected rows without executing the statement

#### Scenario: Bulk execution lacks an explicit writable owner
- **WHEN** bulk SQL is requested through a read-only, expired, wrong-thread, or non-transaction handle
- **THEN** the system rejects it before consuming parameter sets or performing database I/O and does not create a transaction on the caller's behalf

#### Scenario: Catalog is reopened under the other adapter
- **WHEN** application-owned rows are bulk-written through one metadata adapter and the catalog is reopened through the other
- **THEN** the same rows, ordering fields, foreign keys, and relational constraints remain usable without conversion

### Requirement: Bulk SQL fails atomically at the metadata boundary
The system MUST apply existing SQL ownership controls, binding rules, error translation, transaction poisoning, and rollback behavior to bulk SQL. Statement and transaction eligibility SHALL be checked before parameter consumption. Failure during parameter iteration, binding, database execution, constraint enforcement, or result-boundary validation SHALL make the transaction unusable and MUST NOT report partial success or retry any parameter set. Bulk SQL MUST NOT perform blob-storage I/O, publish a blob implicitly, or overwrite payload objects.

#### Scenario: Parameter production is interrupted
- **WHEN** a single-pass parameter source raises after yielding one or more parameter sets
- **THEN** the transaction becomes unusable and rollback removes all SQL changes attempted in that transaction

#### Scenario: A row violates relational integrity
- **WHEN** any parameter set violates an application-owned foreign key, uniqueness rule, check, or trigger after earlier work in the same transaction
- **THEN** the transaction becomes unusable and rollback removes the earlier bulk, ordinary SQL, and blob-publication metadata changes

#### Scenario: Caller catches a bulk failure
- **WHEN** a caller catches a binding, constraint, iterator, or backend error inside the transaction block
- **THEN** later SQL and commit are rejected and the complete transaction rolls back

#### Scenario: Unsupported SQL is supplied
- **WHEN** a caller submits transaction-control SQL or a statement that produces rows, including through a returning clause
- **THEN** the system rejects the unsupported boundary, exposes no partial result set, and the failed transaction cannot commit

#### Scenario: Application metadata is inserted in bulk
- **WHEN** bulk SQL changes only ordinary relational metadata associated with existing or staged blobs
- **THEN** no payload is read, copied, replaced, or deleted and blob publication remains an explicit separate operation
