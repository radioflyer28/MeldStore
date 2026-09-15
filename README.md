# MeldStore

Generic blob storage with relational metadata, built around MeldDB, obstore,
and XXH3-128 verification.

Status: S01 relational foundation implemented. Payload storage APIs are not implemented yet.
No package has been released or qualified for application data.

MeldStore will store immutable files and structured values with user-defined
SQL metadata schemas. Applications own their domain tables and relationships.
The library has no caching policies and no built-in cUAS concepts.

- [MVP specification](docs/mvp.md)
- [Implementation plan](docs/implementation-plan.md)
- [Public schema and transaction contracts](docs/contracts.md)
- [Contributor guidance](AGENTS.md)

The import package is `meldstore`. Python 3.12+ is the initial target.
MeldDB SQL and a direct SQLite adapter share the same relational schema.
Local/S3 storage uses obstore; optional handlers cover Parquet, NPZ, and Blosc2.
Runtime dependencies are locked. MeldDB is pinned to a public source commit;
fresh installation requires Git. Optional extras are `parquet`, `numpy`, and
`blosc2`; codec implementation follows in S05.

## Relational foundation

```python
from meldstore import BlobSchema, Catalog, Index, Text, Timestamp

dataset = BlobSchema(
    "dataset",
    {"title": Text(required=True), "captured_at": Timestamp()},
    indexes=(Index("captured_at"),),
)
with Catalog("catalog.sqlite", adapter="melddb") as catalog:
    table_name = catalog.install_schema(dataset)
    with catalog.transaction() as tx:
        tx.sql(
            "CREATE TABLE IF NOT EXISTS notes ("
            "blob_id TEXT REFERENCES ms_blobs(id) ON DELETE RESTRICT, note TEXT)"
        )
```

Switch to `adapter="sqlite"` to use the same catalog without MeldDB APIs.
S01 does not yet provide a safe blob creation API; do not manually populate
the blob bookkeeping tables. S02 adds import, publication, and retrieval.

## Development

```console
uv sync --frozen --extra test
uv run --extra test pytest
uv run --extra test ruff check .
uv build
uv run python tools/package_smoke.py
```

The tests exercise both adapters. GitHub Actions runs Windows/Linux checks and
fresh wheel/sdist installs. Examples in the MVP specification beyond S01 remain
proposed APIs, not implemented functionality.

## License

A distribution license has not been selected. Do not publish package releases
until licensing and dependency distribution requirements are resolved.
