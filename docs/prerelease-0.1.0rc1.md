# MeldStore 0.1.0rc1

First GitHub prerelease of the generic blob-store MVP. Stores immutable files
and structured values with user-defined relational metadata, local/S3-compatible
payload storage, and XXH3-128 integrity verification. No caching policies or
built-in application domain model.

## Install with uv

Requires Python 3.12+, Git, and a SQLite runtime containing the WAL-reset fix
(normally SQLite 3.51.3+; see the repository's SQLite guide for supported backports).
In a Python project:

```console
uv add "meldstore @ https://github.com/radioflyer28/MeldStore/releases/download/v0.1.0rc1/meldstore-0.1.0rc1-py3-none-any.whl"
```

Optional format extras: `parquet` (pandas), `arrow`, `polars`, `numpy`, `blosc2`.
For example, replace `meldstore @` with `meldstore[arrow,numpy] @` above.
Both wheel and source distribution are attached. The exact Apache-2.0 MeldDB
Git revision `aad59aba7cb348b7f4e0607962db6ecd5dda8d31` installs automatically.
This is not a PyPI release. MeldStore is also Apache-2.0.

## Included

- Existing-file import and persistent `export_file(id, destination)`: verified
  exact bytes, no overwrites or automatic source deletion.
- Relational metadata through MeldDB/SQLite or direct sqlite3; explicit SQL
  transactions, stable IDs, guarded edits, indexes and resumable migrations.
- Optional Parquet, NPZ and native Blosc2 array handlers, plus custom handlers.
- Verified retrieval, explicit lifecycle recovery, offline backup/restore and
  storage transfer. Examples keep all domain-specific relationships outside core.

## Qualification and limits

The implementation passed seven Windows/Linux Python 3.12–3.14 and RustFS
[CI jobs](https://github.com/radioflyer28/MeldStore/actions/runs/35242305047).
Local verification passed 563 tests plus 27 separate real RustFS cases,
lint/build and fresh core/all-format wheel/sdist installs. S08 separately
qualified 100-file, 16.37 GB local and RustFS workloads; these are not export
benchmarks. Only synthetic test data is distributed.

Catalog and S3 coordination remain single-host/local. PostgreSQL metadata,
cross-host coordination, AWS/Garage/macOS qualification and move semantics are
deferred. Export needs a hard-link-capable local destination filesystem and an
existing parent directory. Whole-object verification needs temporary disk space.
XXH3-128 detects accidental corruption; it is not an authenticity signature.
Process-crash tests are not power-loss certification. This prerelease is intended
for application trials, not an unrestricted production-support guarantee.
