# MeldStore 0.1.0rc2

This GitHub prerelease updates MeldStore's MeldDB integration contracts while
preserving the generic blob-store model and the `0.1.0rc1` storage formats.

## Install with uv

Requires Python 3.12+, Git, and a SQLite runtime containing the WAL-reset fix
(normally SQLite 3.51.3+; see the repository's SQLite guide).

```console
uv add "meldstore @ https://github.com/radioflyer28/MeldStore/releases/download/v0.1.0rc2/meldstore-0.1.0rc2-py3-none-any.whl"
```

Optional format extras are `parquet`, `arrow`, `polars`, `numpy`, and `blosc2`.
The exact Apache-2.0 MeldDB Git revision
`adfc87fb9933412e67a2d4b7de316cd6db552d9c` installs automatically. This is not
a PyPI release.

## Changes since 0.1.0rc1

- Adopt MeldDB's public runtime, transaction, maintenance, and application-owned
  SQL contracts at the MeldDB adapter boundary.
- Add declared read transactions, including read-only top-level CTE support.
- Add structured transaction outcomes, rollback-specific errors, fail-closed
  catalog quarantine, and exhaustive close/lease cleanup.
- Qualify mixed application/blob transactions, external-table inspection,
  conversion-free direct-SQLite reopen, and external-table physical snapshots.
- Keep catalog-plus-payload backup and portable SQL export independent from
  MeldDB managed logical export.

## Compatibility and limits

Blob IDs, metadata versions, payload hashes, catalog schema, payload layout, and
backup formats are unchanged. Support remains synchronous SQLite/single-host,
including with RustFS payload storage. PostgreSQL metadata, cross-host
coordination, AWS/Garage/macOS qualification, and move semantics remain deferred.
XXH3-128 is an integrity checksum, not an authenticity signature.
