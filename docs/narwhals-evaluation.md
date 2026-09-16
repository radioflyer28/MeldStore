# Narwhals evaluation for S05 Parquet handlers

Research date: 2026-09-16. Scope: eager `pandas.DataFrame`, `polars.DataFrame`,
and `pyarrow.Table`. This is a documentation assessment, not an executed
compatibility qualification. Official rolling documentation was consulted;
the supported dependency versions still need their own tests.

## Recommendation

Use direct, optional native handlers for S05. Keep Narwhals as a possible
optional dispatcher if accepting Narwhals-wrapped inputs becomes a demonstrated
workflow. It is worth considering, but does not remove the need for separate
backend serialization policies and tests. Do not make it a core dependency or
use a common dataframe conversion as the persistence contract.

| Approach | Benefit | Cost / limitation |
| --- | --- | --- |
| Native pandas / Polars / PyArrow handlers | Explicit engine, index, schema, and writer options; native return type | Three small adapters and separate qualification matrices |
| Optional Narwhals dispatcher, then native handler | Can normalize wrapper acceptance and backend identification | Adds an optional dependency; still needs all native policies |
| Narwhals `write_parquet` as the shared serializer | Very small common write surface | Documented signature accepts only a destination; no index, engine, compression, or schema options |

Narwhals explicitly supports the three eager input types and exposes
`implementation` and `to_native`. Its writer accepts paths or `BytesIO`.
Its reader accepts an explicit backend and forwards reader kwargs. Thus the
asymmetry between reader and writer options is material here; the API does not
establish a common preservation guarantee. This is an architectural inference
from the [DataFrame API](https://narwhals-dev.github.io/narwhals/api-reference/dataframe/)
and [top-level API](https://narwhals-dev.github.io/narwhals/api-reference/narwhals/).
If added later, use strict eager-only acceptance, reject series/lazy inputs,
and allowlist these three backends rather than accepting every supported plugin.

## Preservation findings

**pandas indexes and MultiIndex.** Arrow tracks pandas index information in
schema metadata: `preserve_index=None` represents `RangeIndex` in metadata and
other indexes in physical columns; `True` materializes every index. Use
`Table.from_pandas` with an explicit policy, or pandas' PyArrow engine with an
explicit `index` policy. Retain pandas metadata on read. Named/unnamed row
MultiIndex levels need tests for values, names, and types; column MultiIndex
is a separate qualification case, not implied by row-index support. Avoid
resetting indexes or converting through Polars as an implementation shortcut.
[Arrow pandas integration](https://arrow.apache.org/docs/python/pandas.html#handling-pandas-indexes),
[pandas writer](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_parquet.html).

**pandas nullable/category/timezone columns.** The PyArrow engine documents
nullable integer/string preservation, but explicitly warns that non-string
categoricals deserialize as primitive types. Index-level names must be strings;
duplicate/non-string column names and arbitrary Python objects have documented
limitations. Arrow maps categoricals to dictionary arrays and timezone-aware
timestamps to timestamp types carrying a timezone; nullable dtype restoration
depends on pandas-origin metadata, or an explicit `types_mapper` for other Arrow
inputs. Therefore test nullable integer/boolean/string, ordered and unordered
string categories including unused categories, numeric categories, and timezone
units/DST cases separately. Reject or document unsupported cases rather than
claiming all pandas dtypes round-trip.
[pandas Parquet caveats](https://pandas.pydata.org/docs/user_guide/io.html#parquet),
[Arrow pandas integration](https://arrow.apache.org/docs/python/pandas.html).

**PyArrow metadata.** Write the original table directly with
`pyarrow.parquet.write_table`, retaining `store_schema=True`. Arrow stores its
schema under `ARROW:schema`; its reader uses this to restore information such as
timezone and duration that the physical Parquet representation alone does not
retain. Set timestamp precision/version policy deliberately; do not enable
silent timestamp truncation. This improves fidelity, not universal Arrow type
coverage. Validate application schema metadata and field metadata with
`schema.equals(..., check_metadata=True)` as well as values/types; schema
equality ignores metadata by default. Include dictionary and nested fields,
and separately qualify extension types. Do not promise identical chunk layout,
buffers, or serialized bytes.
[Arrow writer](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.write_table.html),
[Arrow schema equality](https://arrow.apache.org/docs/python/generated/pyarrow.Schema.html#pyarrow.Schema.equals).

**Polars semantics.** Prefer `DataFrame.write_parquet` / `read_parquet` using
the native engine. PyArrow is an explicit alternative (`use_pyarrow=True`),
with different supported options; it should be a separately tested policy.
Polars has no pandas index/MultiIndex, distinguishes null from floating NaN,
and distinguishes fixed ordered `Enum` categories from dynamic `Categorical`.
Test those distinctions, datetime units/timezones, nested values, empty frames,
and row/column order using Polars-native assertions. Category code identity or
global category-map identity should not be a storage promise. Cross-engine
readability does not establish preservation of every native semantic.
[Polars writer](https://docs.pola.rs/api/python/stable/reference/api/polars.DataFrame.write_parquet.html),
[index model](https://docs.pola.rs/user-guide/migration/pandas/),
[null versus NaN](https://docs.pola.rs/user-guide/expressions/missing-data/),
[Categorical and Enum](https://docs.pola.rs/user-guide/expressions/categorical-data-and-enums/).

## S05 contract implications

Keep handler identity/version and native output backend explicit. Serialize to
the handler's local destination or buffer; let MeldStore own upload and integrity
checks. Keep dependencies lazy and optional. Missing dependencies and unsupported
values should fail clearly. Document same-backend, tested dtype subsets;
cross-backend conversion is a separate operation. In particular, wrapper
support is not evidence of Parquet fidelity, and native APIs also need the
preservation tests above. No code, dependency, or handler changes are proposed
as part of this note.
