# Design

## Context

See [proposal.md](proposal.md) for motivation. MeldStore currently pins MeldDB commit `ae738562249efd3b4f3173f45358db1e7fd54141` and already uses only public connection, transaction, SQL, maintenance, and physical-backup APIs. MeldStore owns the `ms_*` schema and application integration contract; its direct SQLite adapter uses the same database without importing MeldDB. Existing tests cover ordinary SQL, shared application foreign keys, transaction rollback, cross-adapter reopen, and application objects in backup artifacts, but they do not directly qualify MeldDB's newly documented external-table inspection and managed logical-export boundary.

MeldDB commit `adfc87fb9933412e67a2d4b7de316cd6db552d9c` adds the upstream application-owned SQL contract and tests. The shared `metadata-provider-contract` remains reference-only and already assigns database primitives to MeldDB and blob/catalog-plus-payload behavior to MeldStore.

## Goals / Non-Goals

**Goals:**

- Pin and qualify the exact published MeldDB contract revision.
- Add gap-only evidence at MeldStore's adapter seam rather than duplicating MeldDB's internal test suite.
- Prove that MeldStore/application SQL stays external to MeldDB management and remains portable to direct SQLite.
- Prove that physical snapshots preserve the whole SQL catalog while complete backup remains a MeldStore responsibility.
- Keep dependency, behavior, and verification evidence auditable with `uv`.

**Non-Goals:**

- Changing the MeldStore schema, public API, payload layout, hash, serialization, or object-storage behavior.
- Moving application schema or migration ownership into MeldStore or MeldDB.
- Using MeldDB managed documents, graph relationships, managed migrations, or logical export for MeldStore state.
- Adding PostgreSQL metadata support, multi-host coordination, online backup, or a generic external-table migration framework.
- Changing copy-on-read, `materialize`, `export_file`, or move semantics.

## Decisions

### Pin the exact upstream commit

Update the MeldDB Git dependency and lockfile from `ae738562249efd3b4f3173f45358db1e7fd54141` to the full published commit `adfc87fb9933412e67a2d4b7de316cd6db552d9c`. Keep the exact Git pin until MeldDB has an approved versioned distribution contract.

Alternative: track a branch or floating Git reference. Rejected because it makes qualification non-reproducible and can change runtime behavior without a MeldStore review.

### Test the public boundary, not MeldDB internals

Use MeldDB's public `open()`, `inspect()`, transaction, SQL, and `backup()` behavior against a real MeldStore catalog. After MeldStore closes the catalog, inspection will verify that `ms_*` and test application tables are external, with no managed objects or migrations. Existing cross-adapter tests will be extended only where necessary to bind this classification to shared rollback and conversion-free reopen behavior.

Alternative: inspect MeldDB private metadata tables or MeldStore's private connection field. Rejected because that would couple the consumer to the layout the contract is intended to hide.

### Keep ownership and migration seams unchanged

MeldStore continues to create and migrate its own ordinary SQL tables through transaction-owned parameterized SQL. Applications continue to own their domain tables and migrations and target the documented `ms_blobs(id)` foreign-key seam. No adoption marker or data migration is added because the persisted format does not change.

Alternative: register MeldStore tables as MeldDB-managed objects. Rejected because it would break direct SQLite portability, blur release ownership, and contradict both local and shared contracts.

### Qualify physical snapshot and complete backup separately

Focused tests will create application-owned schema objects and data, take a catalog snapshot through `adapter="melddb"`, and inspect the detached SQLite database. A complete `Store.backup()` test will verify the existing MeldStore artifact path and guard against substitution of MeldDB's managed logical export. MeldStore's `catalog.sql`, payload copies, checksums, lifecycle checks, and manifest-last publication remain unchanged.

Alternative: use MeldDB's managed logical export as MeldStore's portable catalog export. Rejected because that format intentionally excludes application-owned external tables and is therefore incomplete for MeldStore recovery.

### Treat this as qualification-first implementation

The expected production changes are the exact dependency pin and documentation. Tests provide the new behavioral evidence. Runtime code changes are permitted only if the pinned revision exposes a real compatibility defect; any broader behavior change requires revisiting the proposal and deltas.

### Keep the shared workstore as the coordination authority

Use the registered `meld-platform-specs` workstore and its `metadata-provider-contract` to check cross-repository ownership, transaction, backup, pinning, and backend-qualification boundaries during implementation and archive review. The current shared contract already requires the behavior this MeldStore change adopts, so this change does not edit the shared store. If implementation reveals a boundary change rather than a consumer qualification gap, stop and create a coordinated shared-store delta before proceeding.

Alternative: duplicate the shared contract into MeldStore or silently update the workstore as part of this repository-specific change. Rejected because the shared store is reference-only context and each repository retains its own implementation change history.

## Risks / Trade-offs

- **The upstream commit includes changes beyond this single contract** → Review the dependency diff and run focused plus full frozen-environment verification before accepting the lock update.
- **Tests could duplicate guarantees already covered indirectly** → Add only assertions that bind MeldDB's public external-table and physical-backup contracts to MeldStore; retain existing broader cross-adapter tests.
- **External-table inspection can be sensitive to SQLite implementation tables** → Assert the required MeldStore/application tables are present and managed object/migration sets are empty instead of over-constraining unrelated native tables.
- **A dependency-only rollback can desynchronize evidence docs** → Keep the pin, lockfile, compatibility document, and changelog update in one atomic implementation change.
- **Physical catalog snapshots may be mistaken for complete blob backups** → Preserve explicit terminology and tests distinguishing `Catalog.snapshot()` from `Store.backup()`.

## Migration Plan

1. Update the exact MeldDB Git revision and regenerate `uv.lock` with `uv`.
2. Run focused contract tests under both metadata adapters, then the complete frozen test, lint, build, and installed-wheel smoke checks.
3. Publish the pin and its compatibility evidence together. No catalog or payload migration is required.
4. If qualification fails, restore the prior pin and lockfile; existing catalogs and payloads require no rollback because this change performs no persisted-data migration.
