# MeldStore

Generic blob storage with relational metadata, built around MeldDB, obstore,
and XXH3-128 verification.

Status: project scaffold and MVP plan. Storage APIs are not implemented yet.
No package has been released or qualified for application data.

MeldStore will store immutable files and structured values with user-defined
SQL metadata schemas. Applications own their domain tables and relationships.
The library has no caching policies and no built-in cUAS concepts.

- [MVP specification](docs/mvp.md)
- [Implementation plan](docs/implementation-plan.md)
- [Contributor guidance](AGENTS.md)

The import package is `meldstore`. Python 3.12+ is the initial target.
MeldDB SQL and a direct SQLite adapter will share the same relational schema.
Local/S3 storage uses obstore; optional handlers cover Parquet, NPZ, and Blosc2.
Runtime dependencies will be pinned during the first implementation slice;
the scaffold does not yet declare a usable storage installation.

## Development

```console
uv sync --extra test
uv run --extra test pytest
uv run --extra test ruff check .
uv build
```

Until S01 adds behavior tests, pytest has no suite to run. CI and a dependency
lockfile are S01 deliverables. Examples in the specification are proposed APIs.

## License

A distribution license has not been selected. Do not publish package releases
until licensing and dependency distribution requirements are resolved.
