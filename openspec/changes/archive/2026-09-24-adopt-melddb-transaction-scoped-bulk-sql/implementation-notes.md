# Implementation Notes

## Contract mapping

Shared change: `meld-platform-specs:add-transaction-scoped-bulk-sql-contract`

Pinned provider: MeldDB commit `fa74f11efffc0ab182984e949c0d09fe1e9a1244`

| Contract responsibility | MeldStore evidence |
| --- | --- |
| Consume only the public MeldDB bulk primitive | The MeldDB adapter delegates through the public transaction handle's `sql_many()` method. |
| Provide equivalent direct SQLite behavior | The direct adapter uses its owned SQLite connection's `executemany()` under the same MeldStore transaction wrapper. |
| Preserve ordered single-pass input and plain summaries | `Transaction.sql_many()` probes only for empty input, streams one iterator, validates submitted counts, and returns a detached dictionary with `parameter_sets` and `affected_rows`. |
| Preserve explicit ownership | Bulk execution exists only on a live writable transaction handle; there is no catalog-level convenience method. |
| Fail closed without retry or partial success | Existing transaction poisoning and rollback handle statement, iteration, binding, constraint, backend, and result-boundary failures. |
| Keep payload I/O outside bulk SQL | The method accepts only SQL and parameter sets; focused tests reject any object-storage call and prove mixed publication metadata rolls back atomically. |

The shared change deepens the compatibility dependency but does not move ownership:
MeldDB owns the database primitive; MeldStore owns adapter equivalence, exact pinning,
blob integration, and consumer qualification.

## Focused verification

- `uv --system-certs sync --frozen --all-extras`: resolved the exact published MeldDB revision.
- Dependency diff: only the MeldDB commit changed in `pyproject.toml` and `uv.lock`.
- `uv --system-certs run --frozen --all-extras pytest tests/test_bulk_sql.py tests/test_catalog.py -q`: 106 tests passed.

Final evidence is recorded in
`docs/melddb-transaction-scoped-bulk-sql-verification.md`. Both the local change
and coordinated shared change pass strict OpenSpec validation. Core-source review
found no cUAS, radar, aircraft, track UUID, or sensor-configuration concepts in
`src/meldstore`.
