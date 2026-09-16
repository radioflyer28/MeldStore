# MeldStore

Generic blob storage with relational metadata, built around MeldDB, obstore,
and XXH3-128 verification.

Status: S04 lifecycle and recovery implemented; the full MVP is still in development.
No package has been released or qualified for application data.

MeldStore stores immutable local files with user-defined
SQL metadata schemas. Applications own their domain tables and relationships.
The library has no caching policies and no built-in cUAS concepts.

- [MVP specification](docs/mvp.md)
- [Implementation plan](docs/implementation-plan.md)
- [Public schema and transaction contracts](docs/contracts.md)
- [Local file API and guarantees](docs/local-files.md)
- [Metadata queries, guarded edits, and migrations](docs/metadata.md)
- [Lifecycle, recovery, and exclusive maintenance](docs/lifecycle.md)
- [Contributor guidance](AGENTS.md)

The import package is `meldstore`. Python 3.12+ is the initial target.
MeldDB SQL and a direct SQLite adapter share the same relational schema.
Local storage uses obstore; S3 and structured handlers remain planned.
Runtime dependencies are locked. MeldDB is pinned to a public source commit;
fresh installation requires Git. Optional extras are `parquet`, `numpy`, and
`blosc2`; codec implementation follows in S05.

## Local file workflow

```python
from meldstore import BlobSchema, Catalog, LocalStorage, Store, Text

dataset = BlobSchema(
    "dataset",
    {"title": Text(required=True)},
)
with Catalog("catalog.sqlite", adapter="melddb") as catalog:
    store = Store(catalog, LocalStorage("objects"))
    store.install_schema(dataset)
    record = store.import_file(
        "existing.parquet", schema=dataset, metadata={"title": "example"}, id="sample"
    )
    with store.materialize(record["id"]) as verified_file:
        print(verified_file)  # Use the verified temporary file within this block.
```

Switch to `adapter="sqlite"` to use the same catalog without MeldDB APIs.
Existing files are preserved byte-for-byte. `stat`/`find` query SQL without reading
objects. Explicit `prepare_file`/`finalize`/`publish` compose blob creation with
application SQL in one shared commit. See the API guide for retry semantics,
temporary space requirements, and recovery limits. Do not manually populate or
garbage-collect blob bookkeeping tables/objects.

## Development

```console
uv sync --frozen --extra test
uv run --extra test pytest
uv run --extra test ruff check .
uv build
uv run python tools/package_smoke.py
```

The tests exercise both adapters. GitHub Actions runs Windows/Linux checks and
fresh wheel/sdist installs. Examples in the MVP specification beyond S04 remain
proposed APIs, not implemented functionality.

## License

A distribution license has not been selected. Do not publish package releases
until licensing and dependency distribution requirements are resolved.
