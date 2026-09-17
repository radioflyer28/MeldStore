# format-handlers Specification

## Purpose
Defines explicit, versioned serialization handlers while keeping optional data-format dependencies outside the core import path.

## Requirements

### Requirement: Handler selection is explicit
The system SHALL require an allowed registered handler for structured values and SHALL record its ID, implementation version, encoding version, and validated descriptor.

#### Scenario: Schema disallows a handler
- **WHEN** a caller chooses a handler not allowed by the blob schema
- **THEN** serialization is rejected before blob publication

### Requirement: Existing files preserve bytes
The system SHALL import existing files without re-encoding or changing their byte content.

#### Scenario: Parquet file is imported as a file
- **WHEN** a caller uses existing-file passthrough for a Parquet input
- **THEN** later verified export has the same size and digest as the original

### Requirement: Built-in structured handlers enforce safe subsets
The system SHALL provide explicit handlers for bytes, pandas/Polars/PyArrow Parquet, single-array NumPy NPZ, and dense native Blosc2 arrays, and SHALL reject unsupported object arrays or preservation cases.

#### Scenario: NumPy object array is supplied
- **WHEN** a caller supplies an object-dtype array to a built-in array handler
- **THEN** the handler rejects it without using pickle

#### Scenario: Supported dataframe round-trips
- **WHEN** a supported dataframe is encoded and decoded with its native handler
- **THEN** its documented index, schema, dtype, nullability, category, and timezone properties are preserved

### Requirement: Custom handlers are application supplied
The system SHALL allow explicit custom handler registration without serializing executable code or granting handlers ownership of SQL or permanent storage locations.

#### Scenario: Backup is reopened
- **WHEN** a store containing a custom encoding is restored in another process
- **THEN** metadata and bytes remain inspectable but decoding requires the application to register the matching handler
