# S05 verification

2026-09-16. Format handlers and additive logical encoding descriptors.

## Local evidence

Windows, Python 3.12.10. Both MeldDB and direct sqlite3 adapters. All format
extras installed from `uv.lock`: NumPy 2.5.3, pandas 3.0.5, PyArrow 23.0.1,
Polars 1.44.2, Blosc2 3.12.2.

- `uv run --extra test pytest -q`: **296 passed, 2 skipped**. The two existing
  S04 symlink cases require Windows symlink privileges in this environment.
- `uv run --extra test ruff check .`: all checks passed.
- `uv build`: wheel and source distribution built successfully.
- `uv run --frozen python tools/package_smoke.py`: fresh core-only wheel/sdist
  environments exercised both adapters and the file/migration/deletion workflow.
- `uv run --frozen python tools/package_smoke.py --formats`: fresh wheel/sdist
  environments exercised every built-in handler through both adapters, including
  close/reopen before decoding. No import from the source checkout.

The suite adds 63 cases over S04: empty/exact bytes, retries, custom decoder
version availability, verification before decoding, lazy imports, missing extras,
descriptor tampering and sealing, lifecycle retention, migration, S02 additive
upgrade, post-header-rewrite hashing, interrupted serialization, explicit handler
allowlists, SQL transaction exclusion, and same-backend format round trips.
Array cases cover scalar, empty dimensions, strided, boolean and complex arrays;
object dtype is rejected and NPZ loading explicitly refuses pickle. Parquet cases
cover indexes, nullable types, string categories, timezone columns, nested values,
empty frames/tables, Arrow field/schema metadata, and Polars Enum/null/NaN.

The first tests exposed empty pandas category loss and Arrow list-child renaming.
The implemented categorical descriptor and explicit Arrow writer policy resolve
those cases; lossy numeric pandas categoricals are explicitly rejected.

## CI

Pending: six Windows/Linux Python 3.12-3.14 jobs. Each runs the core suite, full
all-extras suite, lint/build and fresh core/format wheel/sdist smoke tests.

## Limits

See [handler contracts](handlers.md) for the deliberately tested dtype subsets,
write-time Parquet round-trip validation cost, whole-object memory requirements,
trusted custom callback contract, and no byte-determinism promise across codec
versions. Narwhals was [evaluated](narwhals-evaluation.md), not added.

No new S3, backup/restore, PostgreSQL, macOS, representative-scale performance,
power-loss or package-release qualification is claimed. S06-S08 remain open.
