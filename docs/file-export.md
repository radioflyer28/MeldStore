# Copy files into and out of MeldStore

`import_file` copies an existing file into the store without re-encoding it.
`export_file` copies a ready blob's exact stored bytes to a persistent local file:

```python
record = store.import_file("input.parquet", schema=dataset, metadata={"title": "sample"})
exported = store.export_file(record["id"], "output.parquet")
# exported is an absolute pathlib.Path; it remains usable after Catalog closes.
```

The destination parent must already exist. Any existing destination, including
a directory or dangling symlink, raises `ConflictError`; nothing is overwritten.
Destinations inside the store's managed root or at its catalog/access-gate files
are forbidden. Supply a local path, not an S3 URL. Both local and S3-backed blobs
can be exported. Import/export leave the original, stored blob and metadata intact.

Export verifies the downloaded/materialized bytes and the staged destination
copy against the recorded size and XXH3-128 digest before publishing the filename.
It does not deserialize or change formats: an NPZ blob exports an NPZ file,
regardless of the chosen filename extension. File timestamps, permissions and
catalog metadata are not exported. Use the backup API for catalog-plus-payload
preservation. `materialize` remains the temporary, context-managed alternative.

## Filesystem and failure contract

The destination must be on a local filesystem supporting hard links. A private
temporary directory is created beside it; a hard link publishes the complete
verified file create-only, including when concurrent exporters race. There is
no overwrite option or non-atomic fallback on unsupported filesystems.

Copying and hashing use bounded buffers, but disk space is required for one full
materialized blob plus one full destination-side copy. Export holds the existing
reader/access protection and requires an idle catalog, outside SQL transactions.

Ordinary failures clean up staging. A process crash can leave a private
`.meldstore-export-*` directory; inspect it before explicit cleanup. The final
filename is either absent or a complete published file in the tested process-crash
cases. An error after publication may still leave that complete file: inspect
its size/hash before retrying. This is not power-loss certification or a defense
against hostile mutation of the destination directory.

## Moves are deferred

This release provides copy-in and copy-out, not move operations or a file CLI.
Move-in would need to resolve uncertain import outcomes and source changes before
deleting the original. Move-out would need to report a successful export separately
from deletion blocked by SQL references or delayed physical cleanup. Such a future
API should expose resumable copy/verify/delete stages, not promise an atomic move.
