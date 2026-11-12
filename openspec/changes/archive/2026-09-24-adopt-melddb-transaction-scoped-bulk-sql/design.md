# Design

## Context

See [proposal.md](proposal.md) for motivation. MeldStore exposes ordinary SQL through a transaction wrapper shared by two SQLite catalog adapters. The direct adapter currently injects a single-statement executor backed by `sqlite3.Connection.execute()`, while the MeldDB adapter injects the pinned MeldDB transaction's `sql()` method. Both routes share MeldStore's ownership checks, control-SQL filtering, failure poisoning, and error translation.

MeldDB commit `fa74f11efffc0ab182984e949c0d09fe1e9a1244` adds a public transaction-owned `sql_many()` contract with ordered single-pass consumption, plain completion summaries, and fail-closed behavior. The coordinated `meld-platform-specs` change `add-transaction-scoped-bulk-sql-contract` makes that dependency explicit: MeldDB owns the database primitive, while MeldStore owns exact pinning, adapter equivalence, and consumer qualification. It does not move blob or application-schema ownership.

## Goals / Non-Goals

**Goals:**

- Expose one generic MeldStore bulk SQL operation with equivalent observable behavior under both metadata adapters.
- Reuse MeldStore's existing transaction ownership, SQL policy, failure, and error boundaries.
- Keep the direct SQLite adapter independent of MeldDB imports and optional format packages.
- Reduce per-row public SQL dispatch in the representative synthetic workload while preserving identical relational results.
- Keep the dependency pin, lockfile, tests, documentation, and qualification evidence reproducible through `uv`.

**Non-Goals:**

- Adding implicit transactions, a catalog-level bulk convenience method, automatic batching, chunk sizing, retries, progress callbacks, or cancellation checkpoints.
- Defining a cUAS schema, track-correlation model, or any other application domain in MeldStore core.
- Performing payload reads or writes during bulk SQL, changing publication ordering, or changing blob and backup formats.
- Tuning interval queries or indexes, converting existing internal migrations wholesale, or promising a universal throughput threshold.
- Qualifying PostgreSQL metadata, multi-host writers, or distributed transaction semantics.

## Decisions

### Extend only the live transaction handle

Add `sql_many(statement, parameter_sets)` to MeldStore's public transaction object and deliberately omit `Catalog.sql_many()`. The transaction method first enforces live-handle, thread, and write ownership, then applies the same statement-policy checks used by ordinary SQL before obtaining the parameter iterator.

This keeps commit ownership visible and guarantees that read-only or control-SQL rejection cannot trigger caller generator side effects. It also lets bulk application rows compose with ordinary catalog publication SQL under one existing commit outcome.

Alternative: mirror the standalone `Catalog.sql()` convenience. Rejected because bulk writes commonly need composition and a one-call transaction would obscure transaction scope while offering little convenience over an explicit context.

### Inject an adapter-specific bulk executor beside the existing executor

Extend the transaction wrapper's adapter seam with a bulk executor. The direct SQLite transaction supplies a wrapper around `Connection.executemany()`; the MeldDB transaction supplies the pinned upstream transaction's public `sql_many()` method. MeldStore performs common ownership and policy validation, while each adapter remains responsible for its driver mechanics.

The direct wrapper closes its cursor in all paths, rejects a cursor that exposes result columns, and normalizes negative or unavailable row counts to `None`. The MeldDB wrapper accepts the upstream plain result and validates its documented `parameter_sets` and `affected_rows` fields before returning a detached MeldStore-owned dictionary. An empty iterable returns `{"parameter_sets": 0, "affected_rows": 0}` before either adapter executor is called.

Alternative: loop over the existing single-row executor in shared Python. Rejected because it preserves per-row dispatch overhead, may accidentally materialize results repeatedly, and bypasses the purpose of the upstream contract.

Alternative: expose the raw adapter cursor or MeldDB result object. Rejected because callers need one stable plain-data boundary across adapters.

### Preserve ordered single-pass streaming without implicit chunking

After pre-consumption validation, obtain one iterator, probe only its first item to identify empty input, and pass a chained stream containing that item and the remainder to the adapter executor. Count parameter sets as the adapter consumes them; do not call `len()`, copy the source, pre-validate every binding, rewind, split, or retry it.

This supports generators and bounded-memory execution while making interruption semantics explicit. Driver-level buffering remains an adapter implementation detail and is not represented as MeldStore chunking.

Alternative: materialize all bindings to simplify counting. Rejected because workload size is caller-controlled and the API is intended to support large streams without duplicate memory.

### Fail closed through the existing transaction state machine

Run the new method through the same `require_transaction`, `_check_sql`, exception translation, and `_failed` state used by ordinary SQL. A read transaction, bad statement, iterator error, binding error, constraint failure, driver failure, malformed upstream summary, or result-producing statement poisons the transaction. If the caller catches the immediate error, subsequent work and commit still fail, and scope exit rolls back all prior SQL.

Do not report a partial count on failure and do not retry uncertain work. Commit and rollback outcome failures continue through the existing structured outcome-error path; this change does not introduce a second transaction state machine.

Alternative: return completed-row counts alongside an error. Rejected because that could be mistaken for durable partial success even though the enclosing transaction must roll back.

### Keep SQL bulk execution separate from blob storage

The API accepts only SQL and bindings. It does not call obstore, handlers, hashing, materialization, export, or publication helpers. Existing flows continue to stage and verify payloads outside long-lived SQL write transactions, then use a short transaction for publication and application-owned relationships.

The synthetic cUAS qualification fixture may replace its per-occurrence `tx.sql()` loop with one `tx.sql_many()` call per recording, but that remains example code and measurement evidence. No track, sensor, aircraft, or configuration concepts enter the package's catalog schema or public core API.

Alternative: add a blob-plus-related-rows batch publication API. Rejected because it would mix storage I/O and application-domain relationships into the generic metadata primitive.

### Pin and qualify the exact upstream contract

Update `pyproject.toml` and `uv.lock` to MeldDB commit `fa74f11efffc0ab182984e949c0d09fe1e9a1244`. Add one cross-adapter conformance suite covering success, empty input, single-pass order, unavailable row counts, ownership, read scopes, control SQL, result-producing SQL, constraints, binding failures, and interrupted iterators. Retain direct-adapter import-isolation coverage.

Update the synthetic qualification tool to record correctness, public SQL-call reduction, elapsed time, and peak memory without setting a machine-dependent runtime threshold. Private datasets and paths are neither required nor recorded; any authorized local rerun is supplementary evidence only. Interval-query planning remains a separate measured follow-up.

Alternative: track MeldDB's branch or tag. Rejected because the shared contract requires an exact revision until a versioned distribution contract is approved.

### Coordinate through the shared workstore without moving local implementation

Use `meld-platform-specs` change `add-transaction-scoped-bulk-sql-contract` as the cross-repository compatibility source. This MeldStore change remains authoritative for its public transaction facade, direct SQLite behavior, dependency pin, tests, and release evidence; MeldDB's archived local change remains authoritative for the upstream primitive. Reconcile all three before archive so later changes to bulk ownership, ordering, result shape, failure, or retry semantics trigger coordinated review.

## Risks / Trade-offs

- **SQLite and MeldDB could report affected rows differently** → Normalize unknown or negative counts to `None`, preserve zero for empty input, and test the same public result shape through both adapters.
- **A generator can fail after the driver has applied some rows** → Poison the transaction and rely on scope rollback; never expose a success summary from the failed operation.
- **A result-producing statement could be silently discarded by a driver** → Inspect the adapter result boundary and reject any statement for which returned columns are exposed; maintain focused `SELECT` and `RETURNING` tests on both adapters.
- **A future upstream result-shape change could leak through silently** → Validate the pinned MeldDB summary at the adapter seam and fail the transaction on malformed output.
- **Measurements could overstate general performance gains** → Report fixture size, adapter, call counts, elapsed time, and peak memory as evidence rather than a portable guarantee.
- **Updating the Git pin can include unrelated upstream changes** → Review the exact dependency range and run focused plus full frozen-environment tests, lint, build, and installed-wheel smoke checks.

## Migration Plan

1. Add the transaction API and both adapter executors without changing catalog schema or persisted data.
2. Update the exact MeldDB Git pin and regenerate only the dependency lock state with `uv`.
3. Run focused cross-adapter conformance tests, then the complete frozen test and lint suites.
4. Run the synthetic qualification workload and record comparable correctness and measurement evidence without private paths or data.
5. Build the distributions and run core and all-format installed-wheel smoke tests because the dependency metadata changes.
6. Publish the API, pin, documentation, and evidence together. Existing catalogs and payloads require no migration.

Rollback is a code-and-dependency revert to the prior release state. Catalogs written with bulk SQL remain ordinary SQLite catalogs and need no data rollback; callers using the new API must return to per-statement execution when downgrading.
