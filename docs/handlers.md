# Versioned format handlers (S05)

MeldStore stores whole immutable objects, not cached computations. Applications
explicitly select a handler allowed by their generic `BlobSchema`; no domain
entities, SQL relationships, or database calls belong in handlers.

```python
from meldstore import BlobSchema, Catalog, LocalStorage, Store, Text

schema = BlobSchema("dataset", {"title": Text()},
                    handlers=("file", "bytes", "pandas.parquet", "polars.parquet",
                              "pyarrow.parquet", "numpy.npz", "numpy.blosc2"))
with Catalog("catalog.sqlite") as catalog:
    store = Store(catalog, LocalStorage("objects"))
    store.install_schema(schema)
    record = store.put(b"example", schema=schema, metadata={"title": "demo"},
                       handler="bytes", id="example")
    assert store.get(record["id"]) == b"example"
```

`prepare(value, schema=..., handler=..., handler_version=1)` serializes, hashes
the **closed final file**, uploads, and journals a `PreparedValue`. Use the same
`finalize` / application SQL / `publish` shared transaction as files. `put` is the
convenience wrapper; metadata is validated before serialization/upload. Payload
validators receive the original structured value, whereas file validators receive
the private staged file path. Both operate outside the catalog transaction.

`get(id)` chooses the exact recorded handler ID/version and verifies the entire
object's size and XXH3-128 **before** invoking its decoder. A missing handler is
`UnsupportedHandlerError`, a missing optional import is `MissingDependencyError`.
Neither causes fallback. `stat` and `find` require no codec dependencies;
`materialize` remains available without a decoder. `get` for `file` raises an
explicit error directing callers to `materialize`; it never guesses a format.

Retries compare stored bytes, handler version, descriptor, schema and metadata,
not dataframe/array semantic equality. Formats are not promised byte-deterministic
across codec releases. Preserve the prepared token and use `resolve` following an
uncertain outcome. Do not repeatedly reserialize and assume the bytes are identical.

## Built-in v1 encodings

| Handler | Input / detached output | Optional extra | Contract |
| --- | --- | --- | --- |
| `bytes` | `bytes` | none | Exact bytes including empty values |
| `numpy.npz` | One NumPy ndarray | `numpy` | One `value` NPZ member; dtype/shape/values, no object dtype or pickle |
| `numpy.blosc2` | One NumPy ndarray | `blosc2` | Dense B2ND frame; native-endian numeric/boolean dtypes; dtype/shape/values |
| `pandas.parquet` | pandas DataFrame | `parquet` | PyArrow Parquet 2.6; native pandas index/dtype metadata plus categorical descriptor |
| `polars.parquet` | eager Polars DataFrame | `polars` | Native Polars writer/reader; no implicit lazy collection |
| `pyarrow.parquet` | PyArrow Table | `arrow` | Original Arrow schema/field metadata and supported nested values; Parquet 2.6 |

The existing `parquet` extra still installs pandas and PyArrow. `arrow` permits
PyArrow-only use; `polars` does not require pandas or PyArrow. Imports are lazy.
The core never imports NumPy, pandas, Polars, PyArrow, or Blosc2 just to open/query
a catalog. Narwhals is **not** a dependency; see the
[source-backed evaluation](narwhals-evaluation.md).

Array codecs reject ndarray subclasses, object-containing structured dtypes, and
dtype metadata. NPZ is a single array, not an arbitrary dictionary/archive API.
Blosc2 uses the dense ndarray cframe decoder, not generic object/tensor/pickle
loaders; object arrays are never encoded. Neither preserves strides, memory order,
views, device placement, or aliasing. Scalars and zero-sized dimensions are tested.

Each Parquet writer reads its staged result back and rejects an unequal native
round trip **before upload**. This is an extra whole-object decode and comparison,
not a claim that every possible dtype is supported. The tests cover pandas
RangeIndex, named row indexes, row MultiIndex, nullable integer/boolean/string,
ordered string categories (including unused categories and empty frames), and
time-zone columns. String category labels, their order and dtype are additionally
stored in the JSON descriptor because empty Parquet columns lose dictionaries.
Numeric categorical labels, duplicate/non-string columns, and column MultiIndex
are rejected. Datetime frequency/index subclasses, arbitrary objects, extension
dtypes, and DataFrame attributes are not universal preservation guarantees.

Polars tests cover null versus NaN, nested lists, fixed Enum categories and empty
frames; category-code/global-cache identity is not a guarantee. Arrow tests
compare schema and field metadata explicitly, including nested list field names
(writer uses `use_compliant_nested_type=False`). Arrow's equality check can reject
NaN-containing tables and unsupported dictionary/extension layouts; no silent
conversion is performed. Chunk layout, buffers, and identical Parquet byte layout
are not part of the contract. Cross-backend conversion is application work, not
an implicit `get` option. Unqualified inputs may raise native codec errors.

Whole-object codecs require sufficient memory and temporary disk. Parquet
validation holds the input and decoded result; Blosc2 cframes also use whole-file
byte buffers. Existing-file import/materialization retain their bounded-memory
transfer paths and **never re-encode Parquet**. File passthrough and serialized
Parquet are deliberately different handlers.

## Custom registration

```python
from meldstore import Handler, HandlerRegistry

def write_text(value, path):
    if type(value) is not str:
        raise TypeError("Expected text")
    path.write_text(value, encoding="utf-8")
    return {"charset": "utf-8"}

def read_text(path, descriptor):
    return path.read_text(encoding=descriptor["charset"])

handlers = HandlerRegistry()  # built-ins registered without importing extras
handlers.register(Handler("application.text", 1, write_text, read_text))
# Store(catalog, storage, handlers=handlers)
```

Declare `application.text` in the schema's allowed handlers. Registry lifetime is
explicit and per Store; register custom decoders again after reopening. Duplicate
ID/version registration fails. `HandlerRegistry(builtins=False)` starts empty.
The database never imports executable code named by a descriptor. Encoding version
is independent of schema version and metadata concurrency version.

Callbacks are trusted application code, not sandboxed plugins. Writers validate
their inputs, own no permanent path, and must close all handles before returning
a JSON object descriptor (canonical, finite JSON, at most 64 KiB). Readers return
fully detached objects before the verified temporary path is removed. Neither
callback may retain that path, issue SQL, or start background writes. A custom
codec owns its fidelity and safety policy; there is no implicit pickle fallback.
XXH3 verifies accidental corruption, not hostile file authenticity or safe parsing.

## Additive catalog contract

`Store.install_schema` explicitly installs `ms_encoding_format` version 1 and
`ms_encodings(token, encoding_id, encoding_version, descriptor)` alongside existing
S02-S04 tables. Existing tables, blob IDs, application FKs and physical upload
tokens are not rebuilt. Existing schemas still have immutable handler allowlists;
the upgrade does not add handlers to their declarations.

`ms_prepared.handler_id='file', handler_version=1` retain their physical byte-file
meaning. For structured payloads only, `ms_encodings` provides the logical codec.
`stat`/`find` project the logical values as `handler_id`, `handler_version`, and
`descriptor`; `PreparedValue` retains the base physical fields and adds
`encoding_id`, `encoding_version`, and canonical descriptor text. SQL consumers
can LEFT JOIN on token and COALESCE logical/physical handler columns. No companion
row means file passthrough.

The companion row is inserted **before** its physical journal in the same short
transaction, with a deferred FK. A trigger prohibits attaching it to an existing
token; UPDATE and DELETE are prohibited. Finalization, resolution and discard
compare both halves of the token. Descriptors remain through retirement/cleanup
for recovery, like the physical journal. This requires S05-aware writers; do not
downgrade code against an upgraded catalog. These are library-owned bookkeeping
tables, not an application SQL write API.
