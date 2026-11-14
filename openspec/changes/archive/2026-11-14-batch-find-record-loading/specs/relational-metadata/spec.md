# Spec Delta

## ADDED Requirements

### Requirement: Bounded metadata page loading
The system SHALL load a find result page using bounded batches of selected record identifiers. For a fixed schema and encoding layout, metadata SQL work SHALL grow with the number of batches rather than requiring a separate retrieval sequence for each record. Existing limits, filters, ordering, schema-version selection and cursor behavior SHALL be preserved across all supported catalog adapters.

#### Scenario: A page fits within one batch
- **WHEN** a nonempty page grows while remaining within the same identifier batch and schema layout
- **THEN** record-loading SQL call count remains constant and every selected record is returned in query order

#### Scenario: A page spans multiple batches
- **WHEN** a valid page exceeds the internal identifier batch size
- **THEN** queries respect parameter bounds and the assembled result preserves every selected record exactly once in query order

#### Scenario: Selection is empty or invalid
- **WHEN** selection produces an empty page or the request violates the existing query contract
- **THEN** the result remains empty or the existing validation error is raised without unnecessary record loading

### Requirement: Find results retain one read snapshot and detached values
The system SHALL select and load each find page within one explicit read-only transaction, returning detached records with the existing metadata, version and optional descriptor fields. A failure while loading any batch SHALL fail the call without exposing a partial page. Find SHALL perform zero payload-storage I/O and SHALL preserve existing transaction outcome and quarantine handling.

#### Scenario: Another connection commits during loading
- **WHEN** a concurrent writer commits a metadata edit between page selection and subsequent batch reads on a snapshot-capable deployment
- **THEN** the page reflects its original read snapshot and a later find call observes the committed edit

#### Scenario: Returned values are modified
- **WHEN** a caller mutates nested metadata or descriptor values from a completed page
- **THEN** other returned records and subsequent reads retain their own independent values

#### Scenario: Loading is interrupted
- **WHEN** a database failure occurs after an earlier batch has loaded
- **THEN** the call raises the applicable error, exposes no partial page, and follows the existing transaction recovery contract

### Requirement: Batched reads preserve catalog verification
The system SHALL preserve each adapter's existing schema checks on every find call and verify encoding structures for nonempty selections. Validation may be reused only within that call. Legacy file records and catalogs with no encoding extension SHALL retain their existing readable form. Partial extensions, unsupported formats, structural drift and invalid selected object associations SHALL continue to fail under the existing error contracts.

#### Scenario: Legacy and encoded records are read
- **WHEN** a valid page contains file records and encoded records, including payloads prepared under an earlier schema version
- **THEN** every result retains its existing handler, descriptor presence, metadata and version semantics without rewriting stored data

#### Scenario: Catalog structure changes between calls
- **WHEN** structures covered by the existing adapter verification are damaged after one successful find call
- **THEN** the next applicable nonempty call detects the damage instead of reusing validation from the earlier call

#### Scenario: Selected data is inconsistent
- **WHEN** a selected record has missing or inconsistent object, prepared-payload or metadata associations
- **THEN** loading fails rather than silently dropping the selected record or returning a partial result

#### Scenario: Empty or invalid selection with damaged encodings
- **WHEN** encoding structures are damaged but selection is empty or query validation fails
- **THEN** find preserves its empty result or query validation error without inspecting the encoding extension
