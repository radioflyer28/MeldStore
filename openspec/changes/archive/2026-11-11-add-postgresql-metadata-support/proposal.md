# Proposal

## Why

MeldStore can already store payloads remotely, but its production workflow still requires an embedded SQLite catalog on the coordinating host. A PostgreSQL catalog would make both catalog and payloads remotely accessible to that coordinator and give existing SQLite users a verified migration path.

## What Changes

- Add explicit, opt-in PostgreSQL metadata through MeldDB's public transaction and SQL interfaces. Qualify PostgreSQL 17 with pinned RustFS for the first release of this deployment.
- Preserve ordinary SQL tables, stable blob IDs, the documented `ms_blobs(id)` foreign-key target, explicit transactions, bulk SQL, and existing blob integrity and lifecycle guarantees.
- Provide a single-host coordination model with explicit binding of the PostgreSQL catalog, persistent local coordinator, and S3 prefix.
- Provide an offline cutover from a quiesced SQLite store to a fresh PostgreSQL catalog and RustFS prefix. MeldStore owns migration of its catalog data. Applications supply PostgreSQL DDL and data transfer for their own SQL objects and relationships.
- Qualify backup and recovery for the PostgreSQL deployment, including application-owned SQL and verified payloads. Use a PostgreSQL-native catalog archive with an operator-assisted restore.
- Keep both embedded SQLite adapters and their artifact format supported. Treat multi-host coordination, live cutover, and other PostgreSQL or S3 deployments as separate qualification work.

## Capabilities

### New Capabilities

- `postgresql-metadata`: Opt-in PostgreSQL catalog behavior, SQLite cutover, and named deployment qualification.

### Modified Capabilities

- `storage-backends`: Allow a remote PostgreSQL catalog for the single-host S3 coordinator.
- `backup-and-transfer`: Define whole-catalog PostgreSQL backup and verified restore alongside the existing SQLite artifact.

## Impact

MeldStore owns backend-specific catalog DDL, schema inspection, access coordination, transfer, and recovery. Its PostgreSQL client dependency is optional. Review the exact MeldDB revision against the shared metadata-provider contract, and coordinate any new provider guarantees in `meld-platform-specs` before relying on them. Full MeldDB managed-schema portability is a separate milestone.
