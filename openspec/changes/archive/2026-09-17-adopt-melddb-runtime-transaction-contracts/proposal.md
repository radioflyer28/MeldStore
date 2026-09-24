# Proposal

## Why

MeldStore duplicates SQLite configuration, maintenance and transaction recovery around its MeldDB adapter, while its read-only SQL keyword allowlist cannot establish engine-enforced read-only behavior. The proposed MeldDB revision supplies explicit runtime and transaction contracts that let MeldStore delegate these concerns while preserving its independently implemented direct SQLite adapter.

## What Changes

- Pass journal policy to MeldDB, validate its detached live runtime report, and delegate SQLite maintenance without MeldStore-owned control connections on that adapter path.
- Enforce read transactions through each adapter's database engine; remove read/write keyword classification while retaining transaction/connection-control rejection and transaction poisoning.
- Add `TransactionOutcomeError` and `RollbackError`; retain `CommitError` and expose phase, outcome, initiating failure and backend failure with exception chaining.
- Quarantine catalogs after uncertain transaction finalization; close must attempt database and lease cleanup without discarding primary outcome evidence. Never automatically reconnect or retry.
- Preserve constructor defaults, the maintenance result shape, catalog schema, application SQL composition, blob identities, payload formats and backup boundaries.
- Advance only the exact MeldDB Git dependency and its uv lock entry after the required commit is publicly fetchable and qualified.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `relational-metadata`: runtime policy, engine-enforced read transactions, stable finalization errors, unusable-catalog behavior and cross-adapter interoperability.

## Impact

Implementation touches catalog opening/transactions/maintenance/close, exported error types, dependency metadata and focused catalog tests. Existing SQLite tuning, lifecycle, backup, export, handler and package tests provide regression coverage. All new fixtures remain synthetic.

The target is MeldStore main after `v0.1.0rc1`. Current dependency is `aad59aba7cb348b7f4e0607962db6ecd5dda8d31`; proposed dependency is `ae738562249efd3b4f3173f45358db1e7fd54141` (or a later explicitly reviewed revision containing its contracts). At planning time GitHub returned HTTP 422 for the proposed SHA and public main still identified the current pin. **Implementation is gated on upstream publication and an isolated exact-revision fetch.** This proposal does not authorize pushing MeldDB or changing dependencies now.

The shared `metadata-provider-contract` was reviewed read-only. Ownership and compatibility rules already cover this adoption, so no shared-store delta is proposed. Direct SQLite leases and independent verification remain intentional. PostgreSQL, AWS, multi-host coordination, read-only Catalog construction, blob features, package publication and unrelated dependency upgrades are excluded. Existing callers catching `CommitError` or `TransactionError` remain compatible; formerly rejected read-capable SQL may become accepted, and finalization failures gain more specific subclasses.
