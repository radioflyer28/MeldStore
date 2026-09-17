# Proposal

## Why

MeldStore 0.1.0rc1 has completed MVP qualification, but its contracts are spread
across design and verification documents. A concise OpenSpec baseline gives future
changes a canonical behavioral starting point without importing consumer-specific rules.

## What Changes

- Record the current generic blob-store contract as OpenSpec requirements.
- Separate durable guarantees from backend qualification and benchmark evidence.
- Preserve existing implementation, public interfaces, and release artifacts unchanged.

## Capabilities

### New Capabilities

- `blob-lifecycle`: Immutable blob publication, stable identities, recovery, retirement, and deletion.
- `relational-metadata`: User-defined versioned metadata schemas, SQL interoperability, queries, edits, and migrations.
- `verified-retrieval`: XXH3-128 integrity, verified materialization, persistent export, and explicit decoding.
- `format-handlers`: Built-in and custom serialization contracts with optional dependencies.
- `storage-backends`: Local and S3-compatible object storage with single-host coordination boundaries.
- `backup-and-transfer`: Offline backup, restore, and storage relocation while preserving IDs and application SQL.

### Modified Capabilities

None.

## Impact

Only OpenSpec planning artifacts are added. No payloads, private workload details,
domain entities, runtime dependencies, public APIs, or package artifacts change.
