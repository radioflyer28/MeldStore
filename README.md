# MeldStore

Generic blob storage with relational metadata, built around MeldDB, obstore,
and XXH3-128 verification.

Status: S01-S07, including S06a, implemented and verified on Windows/Linux.
S07 qualifies the pinned RustFS backend. S08 consumer qualification passed with
100-file, 16.37 GB local and RustFS workloads; package release remains gated by
dependency licensing/distribution. No package has been released. See the S08
record for measured limits rather than assuming unrestricted production readiness.

MeldStore stores immutable local or S3-compatible payloads with user-defined
SQL metadata schemas. Applications own their domain tables and relationships.
The library has no caching policies and no built-in cUAS concepts.

- [MVP specification](docs/mvp.md)
- [Implementation plan](docs/implementation-plan.md)
- [Public schema and transaction contracts](docs/contracts.md)
- [Local file API and guarantees](docs/local-files.md)
- [Metadata queries, guarded edits, and migrations](docs/metadata.md)
- [Lifecycle, recovery, and exclusive maintenance](docs/lifecycle.md)
- [Format handlers and dataframe compatibility](docs/handlers.md)
- [Narwhals evaluation](docs/narwhals-evaluation.md)
- [S05 verification evidence](docs/s05-verification.md)
- [Offline backup, restore, and local transfer](docs/backup.md)
- [S06 verification evidence](docs/s06-verification.md)
- [SQLite settings, concurrency, and tuning](docs/sqlite.md)
- [S06a verification evidence](docs/s06a-verification.md)
- [S3 configuration, recovery, backup, and offline transfer](docs/s3.md)
- [S07 verification evidence](docs/s07-verification.md)
- [Generic and separate SQL application examples](docs/consumers.md)
- [S08 qualification evidence and release gates](docs/s08-verification.md)
- [Release-candidate notes](CHANGELOG.md)
- [MVP release checklist](docs/release.md)
- [Contributor guidance](AGENTS.md)

The import package is `meldstore`. Python 3.12+ is the initial target.
MeldDB SQL and a direct SQLite adapter share the same relational schema.
Default WAL mode requires a SQLite runtime containing the WAL-reset fix (normally
3.51.3+); check SQLite's version, not only Python's. See the SQLite guide above
for supported backports and explicit rollback mode.
Local and S3 storage use obstore. S3 requires a local catalog and one persistent
coordination directory shared by all participants on a single host.
On September 16, 2026, S07 acceptance was amended to permit a real Docker-local
RustFS or Garage S3-compatible service. This does not establish AWS S3 support:
AWS-specific qualification is deferred and remains unpassed.
Runtime dependencies are locked. MeldDB is pinned to a public source commit;
fresh installation requires Git. Optional extras are `parquet` (pandas), `arrow`,
`polars`, `numpy`, and `blosc2`. Narwhals was evaluated but is not required.

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
uv run --all-extras pytest
uv build
uv run python tools/package_smoke.py
uv run python tools/package_smoke.py --formats
```

The tests exercise both adapters. GitHub Actions runs Windows/Linux checks and
fresh wheel/sdist installs for the previously verified slices. S07 final test
totals and CI evidence are recorded in its verification document. Use the S3 guide for implemented APIs;
illustrative sketches in the MVP specification are not an API reference.

## License

MeldStore is licensed under [Apache-2.0](LICENSE). Dependencies retain their own
licenses. The pinned MeldDB dependency's licensing and distribution requirements
remain a release gate; selecting MeldStore's license does not resolve that gate.
No package release is authorized by this development work.
