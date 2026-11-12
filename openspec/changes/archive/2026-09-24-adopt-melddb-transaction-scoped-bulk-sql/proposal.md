# Proposal

## Why

MeldStore's transaction facade currently executes one parameter set per public SQL call. S08 qualification observed 307,580 public SQL calls in the representative workload, dominated by 301,296 inserts into an application-owned relationship table. MeldDB now provides a qualified transaction-scoped bulk SQL primitive at commit `fa74f11efffc0ab182984e949c0d09fe1e9a1244`; adopting that contract gives both MeldStore metadata adapters one explicit, portable way to execute repeated parameterized statements without weakening transaction ownership or introducing domain behavior.

## What Changes

- Add `Transaction.sql_many(statement, parameter_sets)` as a generic, write-transaction-only API that consumes ordered parameter sets once and returns a plain execution summary.
- Implement equivalent behavior in the direct SQLite adapter through native `executemany()` and in the MeldDB adapter by delegating to MeldDB's public transaction-scoped bulk SQL API.
- Preserve existing SQL controls, error translation, failure poisoning, rollback, and explicit transaction ownership; do not add a `Catalog.sql_many()` convenience method, implicit chunking, retry, or payload I/O.
- Pin MeldStore to MeldDB commit `fa74f11efffc0ab182984e949c0d09fe1e9a1244` and regenerate the lockfile with `uv`.
- Add cross-adapter conformance tests and synthetic qualification evidence, including the application-owned occurrence-insert path that motivated the change. The fixture remains an example workload and does not add cUAS concepts to MeldStore's core model.
- Adopt the coordinated `meld-platform-specs` change `add-transaction-scoped-bulk-sql-contract`, which records MeldDB's primitive ownership and MeldStore's pinning, adapter-equivalence, and qualification responsibilities without moving blob or application-schema ownership.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `relational-metadata`: Add explicit transaction-scoped bulk SQL execution with adapter-independent ordering, result, validation, and rollback semantics for application-owned relational data.

## Impact

- Extends the public `Transaction` API and the internal adapter seam while remaining backward compatible for existing callers.
- Updates the exact MeldDB Git revision in `pyproject.toml` and `uv.lock`; no new runtime dependency is introduced.
- Adds focused tests, documentation, and workload measurements for both supported SQLite metadata adapters.
- Does not change the persisted MeldStore schema, blob APIs, payload keys or bytes, handlers, hashes, backup artifacts, storage backends, or current SQLite-only support boundary.
- Keeps interval-query tuning, PostgreSQL metadata support, multi-host coordination, and project-specific correlation schemas outside this change.
