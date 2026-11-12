# Tasks

## 1. Adopt the Upstream Contract

- [x] 1.1 Update the exact MeldDB Git dependency to `fa74f11efffc0ab182984e949c0d09fe1e9a1244`, regenerate `uv.lock` with `uv`, and verify `uv sync --frozen --all-extras` resolves that revision without unrelated dependency changes
- [x] 1.2 Review the pinned MeldDB bulk SQL surface against the local delta and coordinated `meld-platform-specs` change `add-transaction-scoped-bulk-sql-contract`; record the mapping in the change implementation notes and verify repository ownership remains separated

## 2. Implement Transaction-Scoped Bulk SQL

- [x] 2.1 Extend the live transaction wrapper and adapter-construction seam with bulk execution while omitting a catalog-level convenience API; verify API tests reject non-transaction, expired, wrong-thread, and read-only use before consuming parameter input
- [x] 2.2 Implement the direct SQLite `executemany()` path with cursor cleanup, ordered single-pass counting, empty-input handling, result-producing statement rejection, and affected-row normalization; verify focused direct-adapter tests pass
- [x] 2.3 Delegate the MeldDB adapter to the pinned transaction `sql_many()` API and validate/copy its plain completion summary; verify focused MeldDB-adapter tests cover valid, unavailable-count, and malformed-result behavior
- [x] 2.4 Route bulk failures through existing SQL controls, error translation, transaction poisoning, rollback, and outcome handling; verify iterator, binding, constraint, driver, control-SQL, `SELECT`, and `RETURNING` failures cannot commit partial work

## 3. Qualify Portability and Storage Separation

- [x] 3.1 Add parameterized cross-adapter conformance tests for ordered generators, empty input, application-owned foreign keys, shared publication metadata rollback, and reopen-through-the-other-adapter compatibility; verify both adapter variants pass with identical observable results
- [x] 3.2 Extend import-isolation and storage-boundary tests to prove the direct SQLite path does not import MeldDB or optional handlers and bulk SQL performs no object-storage reads, writes, deletes, or overwrites; verify the focused isolation tests pass

## 4. Update the Measured Consumer and Documentation

- [x] 4.1 Replace the synthetic cUAS example's per-occurrence SQL loop with one transaction-scoped bulk call per recording while keeping all domain concepts in example code; verify example correctness tests produce the same rows and relationships
- [x] 4.2 Update the qualification tool to report fixture size, adapter, public SQL-call counts, elapsed time, and peak memory without a machine-dependent speed threshold; run the synthetic fixture through both adapters and verify no private paths or datasets are required or recorded
- [x] 4.3 Document the public transaction API, plain result shape, explicit ownership and failure semantics, payload-I/O boundary, exact MeldDB pin, and measured evidence; verify documentation examples execute and the implementation record distinguishes bulk-insert gains from deferred interval-query tuning

## 5. Complete Release-Quality Verification

- [x] 5.1 Run the focused bulk SQL, catalog, consumer, and import-boundary tests with frozen `uv` environments and verify all new scenarios pass under both metadata adapters
- [x] 5.2 Run `uv run --frozen --all-extras pytest` and `uv run --frozen --extra test ruff check .`, and verify the complete suite and lint checks pass
- [x] 5.3 Run `uv build` plus `uv run --frozen python tools/package_smoke.py` with and without `--formats`, and verify fresh wheel and source-distribution installs pass through both metadata adapters
- [x] 5.4 Run `openspec validate adopt-melddb-transaction-scoped-bulk-sql --strict`, reconcile the final implementation against coordinated shared change `add-transaction-scoped-bulk-sql-contract`, and verify every local and shared requirement has recorded evidence before either change is archived
