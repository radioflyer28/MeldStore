# Proposal

## Why

The representative PostgreSQL workload measured 2.7-2.9 second median reads of a 100-record page and roughly 318,000 public SQL calls per client across 105 reads plus a few edit checks. Store.find currently loads each selected record separately, including repeated encoding-structure verification, making batched loading the next measured optimization.

## What Changes

- Load selected records and their metadata/encoding information with bounded parameterized SQL batches inside the existing read transaction.
- Preserve existing adapter-specific schema verification and verify encoding structures once per nonempty find call and reuse verified declarations within that call.
- Preserve ready-only visibility, exact schema-version selection, record shape, optional descriptor behavior, ordering, cursor validation, detached values and integrity errors.
- Prove query-count scaling, snapshot consistency, legacy catalog compatibility and zero payload I/O across direct SQLite, MeldDB SQLite and MeldDB PostgreSQL.
- Record a metadata-focused before/after comparison using the qualified baseline and reproducible synthetic fixtures.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `relational-metadata` gains explicit bounded page-loading and call-scoped validation requirements while retaining existing query behavior.

## Impact

Implementation is expected in src/meldstore/store.py and encodings.py, with focused parity tests alongside tests/test_metadata.py, tests/test_handlers.py and tests/postgres_provider/test_metadata_catalog.py. Reuse query.py's selection/order rules and the existing qualification measurement helpers.

This proposal is based on MeldStore commit 51bc084 and PR #2. Its planning branch is stacked on that reviewed qualification branch. MeldDB's pinned public read transactions and parameterized SQL provide the required operations. Review ownership through the meld-platform workset and retain the existing shared contract unless implementation uncovers a concrete provider guarantee gap. A new provider dependency would require a separately coordinated change and qualification.

Acceptance covers the current 1-1000 record limit, legacy file catalogs, encoded records and metadata schema evolution. Production work is limited to the read path and shared read assembly needed to preserve parity. The proposal carries no catalog-format migration or new public API.
