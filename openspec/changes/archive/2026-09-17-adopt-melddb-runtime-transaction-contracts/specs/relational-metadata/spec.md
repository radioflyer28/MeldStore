# Spec Delta

## ADDED Requirements

### Requirement: Effective SQLite runtime policy
The system SHALL honor explicit WAL or DELETE journal policy for new and existing file-backed catalogs through either metadata adapter, validate effective journal mode, synchronous FULL and foreign-key enforcement before normal use, and reject unsupported runtime or lock conditions without silently changing policy. In-memory catalogs SHALL retain memory journaling. Failed opening SHALL release acquired access resources.

#### Scenario: Existing catalog changes journal policy
- **WHEN** an idle existing catalog is opened with an explicit different supported journal policy
- **THEN** the effective mode matches that policy and existing schema, identities and rows remain intact

#### Scenario: Runtime or lock prevents requested policy
- **WHEN** the loaded runtime cannot safely support the requested WAL policy or another participant prevents the mode change
- **THEN** opening fails before normal catalog use, without fallback or automatic retry, and acquired leases are released

#### Scenario: In-memory catalog is opened
- **WHEN** either adapter opens an in-memory catalog using existing constructor defaults
- **THEN** the effective mode remains memory with foreign keys enabled and no file-backed journal selection required

### Requirement: Compatible explicit SQLite maintenance
The system SHALL preserve explicit exclusive-maintenance, idle, file-backed and no-active-reader preconditions and SHALL return the existing statistics action and checkpoint fields `busy`, `log_frames`, and `checkpointed_frames` through either adapter. Busy or inactive checkpoint results MUST NOT be represented as verified truncation.

#### Scenario: Statistics and checkpoint requested
- **WHEN** an eligible caller requests optimize or analyze with passive or truncate checkpointing
- **THEN** the requested operations run and the result retains the existing public shape without exposing adapter-specific extra fields

#### Scenario: Reader prevents maintenance
- **WHEN** a participant or active payload reader conflicts with exclusive maintenance
- **THEN** maintenance is rejected before database maintenance or payload changes occur

### Requirement: Engine-enforced read transactions
The system SHALL enforce declared read-only SQL transactions through the database engine on both adapters, not through read/write keyword classification. Read-capable SQL such as a top-level read-only CTE SHALL be permitted when supported by the engine and ownership rules. Persistent mutation SHALL fail and poison the transaction even when the immediate error is caught. Read scopes SHALL not reserve SQLite's writer slot merely by beginning in WAL mode, and successful finalization SHALL restore the prior connection read-only state.

#### Scenario: Arbitrary SQL attempts mutation
- **WHEN** a read transaction attempts INSERT, persistent DDL, a supported WITH-prefixed write or trigger-mediated persistent mutation
- **THEN** the engine prevents mutation, durable state remains unchanged, and the failed scope cannot continue or commit

#### Scenario: Read-only CTE executes
- **WHEN** a declared read transaction executes a supported top-level CTE that only reads data
- **THEN** it returns detached query results without a first-keyword rejection

#### Scenario: Concurrent writer and later writable scope
- **WHEN** a WAL read snapshot remains open while a separate writer commits, and the reader subsequently ends successfully
- **THEN** the original snapshot stays consistent, the writer is not blocked by a reserved writer slot, and a later writable scope on the reader's connection can write

### Requirement: SQL ownership controls remain enforced
The system SHALL reject transaction and connection-control SQL through the catalog interface, including BEGIN, COMMIT, ROLLBACK, END, SAVEPOINT, RELEASE, VACUUM, PRAGMA, ATTACH and DETACH and supported disguises that bypass those controls. Binding validation, thread confinement, handle lifetime, nested-scope rejection and writable defaults SHALL remain unchanged.

#### Scenario: SQL tries to disable read protection
- **WHEN** application SQL attempts to change query-only state or invokes a control pragma through EXPLAIN or leading comments
- **THEN** the interface rejects the operation and poisons the active scope without changing connection policy

### Requirement: Structured transaction outcome failures
The system SHALL expose `TransactionOutcomeError` beneath `TransactionError`, with `CommitError` and `RollbackError` subclasses. Outcome errors SHALL retain `phase` (begin, commit, rollback or cleanup), `outcome` (unknown, rolled_back or committed), initiating failure and backend failure when present, and the adapter exception as cause where applicable. Confirmed rollback with successful cleanup SHALL re-raise the initiating application failure unchanged. Failed or uncertain commit SHALL raise `CommitError`; failed rollback SHALL raise `RollbackError`; uncertain begin or cleanup SHALL raise an outcome error reflecting its actual phase. No unconfirmed rollback SHALL be described as successful.

#### Scenario: Application fails and rollback succeeds
- **WHEN** an application exception leaves the scope and rollback and cleanup are confirmed
- **THEN** the same application exception is propagated and the catalog remains reusable

#### Scenario: Commit response is uncertain
- **WHEN** commit fails without a safely established final outcome
- **THEN** `CommitError` reports the available evidence and does not imply rollback or automatically retry

#### Scenario: Rollback also fails
- **WHEN** an initiating application or SQL failure is followed by rollback failure
- **THEN** `RollbackError` preserves both failures and an unknown outcome rather than replacing them with generic validation

#### Scenario: Cleanup fails after commit
- **WHEN** commit is confirmed but required read-only-state restoration fails
- **THEN** an outcome error reports phase cleanup and outcome committed, without suggesting that retry is safe

### Requirement: Uncertain catalogs require explicit recovery
The system SHALL make a catalog unusable except for close whenever begin recovery, commit, rollback or required cleanup cannot be established safely. Subsequent calls SHALL fail before database or object-storage I/O. Closing SHALL attempt database closure and release of every owned lease despite individual cleanup failures; primary transaction evidence SHALL remain available and secondary cleanup failures SHALL be inspectable. Recovery SHALL require explicit close, reopen and durable-state inspection; no automatic reconnect or retry is permitted.

#### Scenario: Interrupted boundary leaves uncertain state
- **WHEN** a begin, commit, rollback or cleanup boundary fails with uncertain state
- **THEN** metadata queries, transactions, snapshots, maintenance and blob operations reject reuse until the caller closes the handle

#### Scenario: Caller resolves uncertain publication
- **WHEN** the caller reopens after a commit outcome failure and queries the original caller-supplied blob ID
- **THEN** the durable state is available for an explicit retry decision without automatic duplicate publication or overwrite

#### Scenario: Closing encounters multiple failures
- **WHEN** the database close and one lease release fail after a transaction outcome failure
- **THEN** all remaining lease releases are attempted, the primary outcome is preserved, and secondary failures remain inspectable

### Requirement: Adapter-independent catalog compatibility
The system SHALL retain identical ordinary SQL catalog formats and application-owned relationships across adapters. Direct SQLite usage SHALL not import MeldDB or optional format packages. No adoption step SHALL migrate catalog schema, change IDs, rewrite payloads or alter backup format. Application and blob publication SQL SHALL retain one shared transaction, with payload I/O outside long-lived write scopes.

#### Scenario: Catalog reopens under the other adapter
- **WHEN** a catalog populated with application foreign keys under one adapter is reopened with the other
- **THEN** schema, IDs, metadata, guarded versions and relational constraints remain usable without conversion

#### Scenario: Existing payload is exported after adoption
- **WHEN** an existing blob is exported after the dependency update
- **THEN** its recorded size and XXH3-128 integrity checks still apply, existing destinations are not overwritten, and neither payload nor catalog format is changed

#### Scenario: Independent direct adapter
- **WHEN** MeldDB and optional format imports are blocked and direct SQLite is selected
- **THEN** catalog creation, read/write transactions, outcome handling and maintenance remain usable independently
