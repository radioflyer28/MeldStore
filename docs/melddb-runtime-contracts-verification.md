# MeldDB runtime and transaction adoption verification

Date: 2026-09-17

This record covers the local OpenSpec change
`adopt-melddb-runtime-transaction-contracts`. It changes adapter/runtime behavior,
not the persisted catalog schema, blob descriptors, payload bytes, or backup
format.

## Adopted dependency

- Repository: `https://github.com/radioflyer28/melddb.git`
- Exact commit: `ae738562249efd3b4f3173f45358db1e7fd54141`
- The commit was fetched by exact SHA into an isolated Git repository before the
  MeldStore pin changed.
- Installed `direct_url.json` reports that same repository and commit.
- The `pyproject.toml` and `uv.lock` diff changes only the prior MeldDB SHA to
  this SHA; it contains no local path and no unrelated dependency update.
- Installed MeldDB and MeldStore distributions both report Apache-2.0 and include
  their packaged license files.

## Local evidence

Runtime: Windows, Python 3.13.15, SQLite 3.53.1, MeldDB 0.1.0rc1 at the commit
above, obstore 0.11.1, and xxhash 3.8.1.

- `uv run --frozen --all-extras pytest`: 580 passed, 33 expected skips.
  The suite includes both catalog adapters, migrations, lifecycle/recovery,
  backup/restore, file export, application-owned foreign keys, and every optional
  payload handler.
- `uv run --frozen --extra test ruff check .`: passed.
- Focused cross-adapter/backup compatibility proof: 59 passed, 2 expected skips.
  Existing ordinary SQL schema, IDs, metadata versions, application constraints,
  payload bytes, and XXH3-128 digests reopened without migration.
- Fresh wheel and sdist builds passed isolated core smoke tests through both
  adapters. Both artifacts also passed isolated all-format smoke tests for bytes,
  NumPy NPZ, native Blosc2 arrays, and pandas/Polars/PyArrow Parquet handlers.
- Disposable Docker-local RustFS qualification passed all 27 live tests plus
  conditional-write, multipart, outage/recovery, 32 MiB, and 256 MiB synthetic
  probes. The container and test-data volumes were removed by the lab.

No package was uploaded, published release artifact was overwritten, or private
workload data was used. A private-path/credential scan found only synthetic test
filenames and explicit test-only credentials. The unrelated `.pi/` working-tree
directory and the shared platform-spec store were not modified by this change.

## CI and remaining limits

CI has not run for this unpushed branch; the local commands above are the current
evidence and should be repeated by the normal Windows/Linux/RustFS jobs after a
future push. The private large-file workload was intentionally not rerun because
this is a metadata/runtime-only change.

Support claims remain SQLite and single-host coordination. The RustFS result does
not qualify AWS S3, Garage, PostgreSQL metadata, cross-host coordination, macOS,
or power-loss durability. Those remain separate future qualifications.
