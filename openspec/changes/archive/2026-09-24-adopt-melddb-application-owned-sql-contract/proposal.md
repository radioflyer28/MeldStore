# Proposal

## Why

MeldStore already stores its catalog and application relationships as ordinary SQL, but its pinned MeldDB revision predates MeldDB's explicit contract for application-owned tables. Adopting and qualifying the published contract now makes schema ownership, transaction composition, inspection, and backup boundaries verifiable rather than relying on compatible behavior by convention.

## What Changes

- Pin MeldStore to MeldDB commit `adfc87fb9933412e67a2d4b7de316cd6db552d9c`, which formalizes application-owned SQL table behavior and recovery boundaries.
- Qualify that MeldStore's `ms_*` tables and application tables remain application-owned external SQL objects, are never adopted into MeldDB's managed layout, and remain directly usable through SQLite.
- Qualify shared transactions across MeldStore publication SQL and application-owned constraints using both metadata adapters.
- Qualify that MeldDB-backed physical snapshots include the complete shared SQLite database while MeldStore's own backup format remains responsible for portable SQL export, payload copying, integrity verification, and artifact completion.
- Reconcile the adoption against the shared `meld-platform-specs` `metadata-provider-contract`; no shared-store delta is expected because ownership and compatibility boundaries remain unchanged.
- Document the ownership and recovery boundary and record focused compatibility evidence for the exact pinned revision.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `relational-metadata`: Require the MeldDB adapter to treat MeldStore and application tables as application-owned external SQL, preserve shared transaction behavior, and maintain direct-SQLite compatibility without managed-schema adoption.
- `backup-and-transfer`: Require MeldDB-backed physical snapshots to preserve the complete shared SQL catalog while explicitly excluding MeldDB managed logical export from MeldStore's complete backup path.

## Impact

- Updates the exact MeldDB Git revision in `pyproject.toml` and `uv.lock`; no new runtime dependency is introduced.
- Adds focused cross-adapter tests around external-object classification, shared rollback, reopen compatibility, and whole-database snapshots.
- Updates catalog, backup, and compatibility documentation and evidence.
- Uses the registered `meld-platform-specs` workstore as the cross-repository coordination source while keeping repository-specific planning and implementation in MeldStore.
- Does not change public blob APIs, persisted MeldStore schema, payload keys or bytes, handlers, hashes, backup artifact format, or current SQLite-only support boundary.
