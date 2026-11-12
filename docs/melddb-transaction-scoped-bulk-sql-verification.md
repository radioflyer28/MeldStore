# MeldDB Transaction-Scoped Bulk SQL Adoption

Verified on Windows on 2026-09-24 against MeldDB commit
`fa74f11efffc0ab182984e949c0d09fe1e9a1244`.

## Contract boundary

MeldDB owns the transaction-scoped bulk SQL primitive. MeldStore exposes it
through its live transaction facade, delegates through the public MeldDB
transaction API, and implements the same observable behavior with its independent
direct SQLite adapter. The coordinated cross-repository contract is
`meld-platform-specs:add-transaction-scoped-bulk-sql-contract`.

MeldStore does not add a catalog-level convenience operation, implicit
transactions, chunking or retries. Bulk SQL accepts only a statement and an
ordered, single-pass parameter iterable. It returns a detached dictionary with
`parameter_sets` and `affected_rows`; an unavailable driver count is represented
by `None`. Payload staging, transfer, hashing and verification remain outside the
SQL transaction.

## Behavioral evidence

The cross-adapter suite covers:

- ordered one-pass generators, empty input, and unavailable affected-row counts;
- live/write/thread ownership and the absence of a catalog-level bulk method;
- control SQL and result-producing `SELECT`/`RETURNING` rejection;
- iterator, binding, driver, malformed-summary, and relational-constraint failures;
- poisoning after a caught failure and rollback of all earlier transaction work;
- application-owned foreign keys and conversion-free reopen under the other adapter;
- rollback of mixed blob-publication metadata and application rows; and
- direct-SQLite import isolation and absence of object-storage calls.

Focused verification:

- `pytest tests/test_bulk_sql.py tests/test_catalog.py tests/test_consumers.py -q`:
  128 tests passed under the frozen all-extras environment.
- MeldDB's focused upstream suite: 16 bulk SQL tests passed, and all seven
  canonical MeldDB OpenSpec capabilities passed strict validation.

## Synthetic workload evidence

The application-owned cUAS fixture remains example and qualification code, not a
MeldStore schema. It now submits occurrence rows with one bulk call per recording.
The checked-in test creates its own 992-byte, two-row Parquet file and runs through
both metadata adapters; no private dataset, source path, identifier or payload is
recorded.

| Observation | Direct SQLite | MeldDB adapter |
| --- | ---: | ---: |
| Files / Parquet rows / occurrences | 1 / 2 / 2 | 1 / 2 / 2 |
| Bulk API calls / parameter sets | 1 / 2 | 1 / 2 |
| Total import-and-retrieval public SQL calls | 145 | 144 |
| 100-query public SQL calls | 100 | 100 |
| Total elapsed time | 0.295 s | 0.316 s |
| Final peak process RSS | 123,416,576 B | 124,026,880 B |

Times and process peaks are observations from one tiny synthetic run, not portable
performance guarantees. The earlier S08 result of 307,580 public calls remains the
pre-bulk private-workload baseline. A comparable authorized rerun would be needed
to quantify improvement on that workload. Its interval-query plan and index tuning
remain a separate follow-up.

## Release-quality gates

- `uv --system-certs run --frozen --all-extras pytest -q`: 622 passed and 33
  documented skips.
- `uv --system-certs run --frozen --extra test ruff check .`: passed.
- `uv --system-certs build --out-dir dist/bulk-sql-contract`: built a fresh wheel
  and source distribution.
- Core installed-artifact smoke tests passed for both artifacts through both
  metadata adapters.
- All-format installed-artifact smoke tests passed for bytes, NumPy NPZ, native
  Blosc2 arrays, pandas, Polars and PyArrow through both adapters.
- Archive inspection rejected private binary data and local handoff material; no
  Parquet, database, NPZ or Blosc2 payload was packaged.

The `--system-certs` option addresses this host's TLS trust configuration and does
not alter the locked dependency graph. `pyproject.toml` and `uv.lock` changed only
from the prior exact MeldDB commit to the published bulk-SQL commit.
