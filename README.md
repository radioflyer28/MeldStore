# MeldStore

Generic blob storage with relational metadata, built around MeldDB, obstore,
and XXH3-128 verification.

Status: S01-S09, including S06a, implemented and verified on Windows/Linux.
Version `0.1.0rc1` is the first GitHub prerelease; it is not a PyPI release.
S07 qualifies pinned RustFS. S08 passed 100-file, 16.37 GB local and RustFS
workloads. See the verification records for measured limits rather than assuming
unrestricted production readiness.

MeldStore stores immutable local or S3-compatible payloads with user-defined
SQL metadata schemas. Applications own their domain tables and relationships.
The library has no caching policies and no built-in cUAS concepts.

- [MVP specification](docs/mvp.md)
- [Implementation plan](docs/implementation-plan.md)
- [Public schema and transaction contracts](docs/contracts.md)
- [Local file API and guarantees](docs/local-files.md)
- [Persistent file export and copy workflows](docs/file-export.md)
- [Metadata queries, guarded edits, and migrations](docs/metadata.md)
- [Lifecycle, recovery, and exclusive maintenance](docs/lifecycle.md)
- [Format handlers and dataframe compatibility](docs/handlers.md)
- [Narwhals evaluation](docs/narwhals-evaluation.md)
- [S05 verification evidence](docs/s05-verification.md)
- [Offline backup, restore, and local transfer](docs/backup.md)
- [S06 verification evidence](docs/s06-verification.md)
- [SQLite settings, concurrency, and tuning](docs/sqlite.md)
- [S06a verification evidence](docs/s06a-verification.md)
- [MeldDB runtime and transaction adoption evidence](docs/melddb-runtime-contracts-verification.md)
- [MeldDB application-owned SQL contract evidence](docs/melddb-application-owned-sql-contract-verification.md)
- [S3 configuration, recovery, backup, and offline transfer](docs/s3.md)
- [S07 verification evidence](docs/s07-verification.md)
- [Generic and separate SQL application examples](docs/consumers.md)
- [S08 qualification evidence and release gates](docs/s08-verification.md)
- [Release-candidate notes](CHANGELOG.md)
- [MVP release checklist](docs/release.md)
- [S09 export and packaging verification](docs/s09-verification.md)
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

## Install the prerelease

Python 3.12+, uv, Git, and a patched SQLite runtime are required (see above).
In your Python project:

```console
uv add "meldstore @ https://github.com/radioflyer28/MeldStore/releases/download/v0.1.0rc1/meldstore-0.1.0rc1-py3-none-any.whl"
```

For optional formats, use `meldstore[parquet,numpy,blosc2,polars,arrow]` before
the `@` in that requirement. The wheel installs the exact tested MeldDB Git
revision automatically; do not substitute an unrelated index package.

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
    exported = store.export_file(record["id"], "exported.parquet")  # Must not exist.
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
licenses. The pinned MeldDB dependency is also Apache-2.0, approved separately
by its owner. Dependency distribution remains a release gate; see the checklist.
The owner approved the `0.1.0rc1` GitHub prerelease; PyPI publication remains separate.
