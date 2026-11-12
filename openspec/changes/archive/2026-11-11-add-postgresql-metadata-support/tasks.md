# Tasks

Current status: all 13 tasks complete; see [final qualification](../../../../docs/postgresql-verification.md).
The dated checkpoints below retain intermediate status as historical evidence.

## 1. Qualify the MeldDB provider

- [x] 1.1 Run a PostgreSQL 17 conformance fixture at the current exact MeldDB pin. Record pass/fail evidence for raw and bulk bindings, read-only snapshots, connection and transaction controls, constraint and busy errors, and structured outcomes on disconnect.
- [x] 1.2 Resolve any provider gaps in MeldDB and any changed guarantees in the shared store before relying on them. Verify the chosen revision against the same fixture and document the exact pin and dependency scope.

## 2. Qualify a fresh PostgreSQL catalog

- [x] 2.1 Add explicit PostgreSQL selection and the optional dependency. Test open, close, configuration errors, secret redaction, and unchanged core installs and both SQLite adapters. Document the connection and deployment inputs.
- [x] 2.2 Port owned DDL, introspection, queries, and guards. Run parity fixtures for scalar and UTC values, bounded identifier mapping, query order and pagination, external foreign keys, version conflicts, and schema drift.
- [x] 2.3 Implement PostgreSQL metadata versioning and resumable transformations. Verify dry-run, interruption/resume, concurrent guarded edits, and preserved application foreign keys.
- [x] 2.4 Bind the dedicated database and pinned RustFS prefix to the persistent local coordinator. Test wrong bindings, concurrent clients and leases, and exclusive maintenance with readers. Document the protocol for participating external SQL writers.
- [x] 2.5 Test real PostgreSQL 17 and pinned RustFS publication, verified retrieval, and retirement. Inject interruption and lost commit responses. Verify rolled-back bulk relationships, quarantine and reopen, no overwrite, and no unsafe cleanup.

## 3. Verify offline SQLite cutover

- [x] 3.1 Implement a preflight and transfer path for the MeldStore-owned catalog and the application-supplied SQL migration manifest. Test a synthetic foreign-key and junction fixture plus rejection of missing mappings, incompatible values, and occupied targets before mutation.
- [x] 3.2 Transfer and verify payloads create-only, then complete destination publication explicitly. Exercise quiescence, interruption, tampering, source preservation, retained IDs and object keys, and verified retrieval. Document cutover and rollback decisions.

## 4. Verify PostgreSQL whole-store recovery

- [x] 4.1 Implement a backend-tagged, manifest-last PostgreSQL artifact path. Verify the native archive includes external SQL objects, payloads are verified, unfinished lifecycle work is rejected, and existing SQLite artifacts still restore.
- [x] 4.2 Document and execute an operator-assisted restore to a fresh database and prefix. Test application constraints and joins, verified payloads, incomplete or tampered artifacts, and occupied destinations.

## 5. Gate the integrated deployment

- [x] 5.1 Run the fresh deployment, SQLite cutover, and PostgreSQL restore as one synthetic end-to-end workflow with the pinned PostgreSQL/RustFS setup. Record exact versions, supported topology, recovery results, and deferred backend claims.
- [x] 5.2 Run uv-based full tests and Ruff, strict OpenSpec validation for this change, wheel and sdist builds, and clean install smoke tests with and without the PostgreSQL extra. Verify both SQLite adapters still pass and the PostgreSQL job cannot silently skip the live acceptance fixture.

## Provider qualification checkpoint (2026-11-11)

Tasks 1.1 and 1.2 evidence: [PostgreSQL provider verification](../../../../docs/postgresql-provider-verification.md).
The baseline pin yielded 34 passed and 27 failed live checks. Corrected published
MeldDB revision bda3ca95fb6f5431e521162087bec97009074840 passes all 61 unchanged
checks with no skips and is pinned in pyproject.toml and uv.lock. The shared
provider safety contract is synced in meld-platform-specs. The MeldDB and shared
changes remain active for review; no merge or release is included.

Tasks 2.1 through 2.3 are complete; task 2.4 is next. PostgreSQL catalog, cutover and whole-store recovery remain
unqualified until their own acceptance work is complete.

## PostgreSQL connection selection checkpoint (2026-11-11)

Task 2.1 implementation and evidence: [PostgreSQL connection API](../../../../docs/postgresql.md).
Explicit connection selection, optional dependency, safe diagnostics and SQL-only
transactions are verified. Schema installation and storage binding are gated until
their remaining tasks are implemented. No full PostgreSQL deployment claim is made.


## PostgreSQL SQL catalog checkpoint (2026-11-11)

Task 2.2 implementation and evidence: [Native catalog SQL](../../../../docs/postgresql.md).
Native catalog/payload/encoding/retirement DDL and structural inspection,
bounded names, shared scalar/UTC query compilation, and guarded metadata edits
pass parity testing. The live lab passed 120 checks without skips; the final
regression suite passed 650 with 153 expected opt-in/environment skips, and Ruff
and strict OpenSpec validation passed. The production Store binding gate remains
closed; SQL-only storage-stub tests are not payload deployment qualification.
No shared provider guarantee or exact dependency pin changed in this slice.

Next: task 2.3, PostgreSQL versioned metadata and resumable transformations.
Do not bypass storage admission or claim concurrent lifecycle/recovery support
from the isolated SQL tests. No archive, commit, push, merge, or release is included.


## PostgreSQL migration checkpoint (2026-11-11)

Task 2.3 implementation/evidence: [PostgreSQL migrations](../../../../docs/postgresql-migrations.md).
In-place versioned layouts and the shared resumable runner preserve metadata IDs,
application references and descriptors. Tests cover dry-run rollback including
deferred application constraints, process interruption, batch failures, concurrent
resumptions/guarded edits, hook failures, empty/long-name schemas and schema drift.
Live qualification passed 138 without skips; full regressions passed 652 with
171 expected skips. Ruff, strict spec validation and clean wheel/sdist installs
with and without optional PostgreSQL dependencies passed.

The owner approved the required provider API addition. MeldDB's public constraint
validation is pinned at 01e27441d7512f85663c8a86986429d62a69bff9 and qualified from
installed Git source. The shared add-transaction-constraint-validation-contract
change is synced into metadata-provider-contract. Both provider and shared changes
remain active. Only the MeldDB prerequisite was committed/pushed; no merge/release.

Next: task 2.4. Production PostgreSQL Store admission remains gated, and SQL-only
test stubs are not qualification of payload publication, cutover or recovery.


## PostgreSQL coordinator checkpoint (2026-11-11)

Task 2.4 implemented: docs/postgresql-coordination.md. Persistent catalog/local/S3
binding, fail-closed initialization/reopen, concurrent admission, external SQL
participation, Store readers and exclusive leases are qualified with real services.
Run 20261111T205135Z-0b2f74: 152 passed, zero skipped on PostgreSQL 17.11 and the
pinned RustFS image. Full suite: 652 passed, 185 expected environment/opt-in skips;
Ruff and strict change validation passed. No provider guarantee or pin changed.
Next: task 2.5. Publication, cutover and whole-store recovery remain unqualified.


## PostgreSQL lifecycle checkpoint (2026-11-11)

Task 2.5 implemented and qualified: docs/postgresql.md. Live run
20261111T205951Z-18491d passed 165 tests without skips, covering real RustFS and
PostgreSQL publication/retrieval/retirement, bulk rollback, concurrency, actual
backend/process termination, uncertain commits, no-overwrite and safe cleanup.
Shared lifecycle regressions passed 652 with 198 expected skips; Ruff passed.
Tasks 3.1 onward remain open. Shared recovery coordination is tracked by
qualify-postgresql-metadata-recovery-contract in meld-platform-specs.


## Offline cutover checkpoint (2026-11-11)

Tasks 3.1/3.2 implemented: docs/postgresql-transfer.md. Preflight/application SQL
mapping, native owned-layout transfer, verified create-only payload copy and final
destination publication are covered on both SQLite source adapters. Live run
20261111T211144Z-bdee2e passed 195 tests with no skips. Interrupted transfers and
tampered payloads leave the source unchanged and prevent destination admission.
Tasks 4.1 onward remain open; final integrated/package checks are still required.

## Final integrated qualification (2026-11-11)

Tasks 4.1, 4.2, 5.1 and 5.2 are complete. Final live run
20261111T213648Z-166f7a passed 217 tests with zero skips. Full regressions passed
654 with 250 expected opt-in/environment skips; Ruff and strict change validation
passed. Wheel/sdist clean installs passed without PostgreSQL and with PostgreSQL
plus every handler extra, preserving the exact MeldDB Git dependency. CI negative
controls reject missing/skipped acceptance. The new remote CI job has not run.
See docs/postgresql-verification.md for the requirement-by-requirement audit and
scope. No private payloads or paths, package publication, merge or push is included.
