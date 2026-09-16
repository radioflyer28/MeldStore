"""Explicit versioned codecs. No SQL, storage roots, or optional eager imports."""

import importlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from .errors import MeldStoreError, ValidationError
from .schema import identifier


class UnsupportedHandlerError(MeldStoreError):
    """The exact requested decoder is not registered."""


class MissingDependencyError(MeldStoreError):
    """An explicitly selected codec requires an optional dependency."""


def dependency(module, extra):
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise MissingDependencyError(f"Install meldstore[{extra}] for {module}") from exc


def descriptor_json(value):
    if not isinstance(value, Mapping):
        raise ValidationError("Handler descriptor must be a JSON object")
    try:
        text = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
        if json.loads(text) != value or len(text.encode()) > 65536:
            raise ValueError("Descriptor must round-trip as JSON within 64 KiB")
        return text
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValidationError("Invalid handler descriptor") from exc


@dataclass(frozen=True)
class Handler:
    """Trusted callbacks: write(value, path)->JSON dict; read(path, descriptor)->value.

    Writers validate inputs and close all file handles before returning. Readers
    return detached values, never lazy views of the temporary file.
    """

    id: str
    version: int
    write: object
    read: object

    def __post_init__(self):
        identifier(self.id)
        if self.id == "file" or type(self.version) is not int or not 0 < self.version < 2**63:
            raise ValidationError("Invalid handler ID/version; file is reserved")
        if not callable(self.write) or not callable(self.read):
            raise ValidationError("Handler callbacks must be callable")


class HandlerRegistry:
    def __init__(self, *, builtins=True):
        self._handlers = {}
        if builtins:
            for handler in BUILTINS:
                self.register(handler)

    def register(self, handler):
        if not isinstance(handler, Handler):
            raise ValidationError("Register a Handler declaration")
        key = (handler.id, handler.version)
        if key in self._handlers:
            raise ValidationError("Handler ID/version is already registered")
        self._handlers[key] = handler

    def get(self, id, version=1):
        if type(version) is not int:
            raise UnsupportedHandlerError("Encoding version must be an integer")
        try:
            return self._handlers[id, version]
        except (KeyError, TypeError) as exc:
            raise UnsupportedHandlerError(f"Unavailable handler: {id!r} version {version}") from exc


def _bytes_write(value, path):
    if type(value) is not bytes:
        raise ValidationError("bytes handler requires bytes")
    path.write_bytes(value)
    return {}


def _bytes_read(path, descriptor):
    if descriptor != {}:
        raise ValidationError("Unsupported bytes descriptor")
    return path.read_bytes()


def _array(value, *, blosc=False):
    np = dependency("numpy", "blosc2" if blosc else "numpy")
    if type(value) is not np.ndarray or value.dtype.hasobject or value.dtype.metadata:
        raise ValidationError("Expected an ndarray without objects or dtype metadata")
    if blosc and (value.dtype.kind not in "biufc" or not value.dtype.isnative):
        raise ValidationError("Blosc2 v1 supports native-endian numeric and boolean arrays")
    return np


def _npz_write(value, path):
    np = _array(value)
    with path.open("wb") as output:
        np.savez_compressed(output, value=value, allow_pickle=False)
    return {"format": "npz", "member": "value", "pickle": False}


def _npz_read(path, descriptor):
    if descriptor != {"format": "npz", "member": "value", "pickle": False}:
        raise ValidationError("Unsupported NPZ descriptor")
    np = dependency("numpy", "numpy")
    with np.load(path, allow_pickle=False) as archive:
        if archive.files != ["value"]:
            raise ValidationError("Expected a single-array NPZ")
        value = archive["value"]
    _array(value)
    return value


def _blosc_write(value, path):
    _array(value, blosc=True)
    blosc = dependency("blosc2", "blosc2")
    # Dense B2ND only, never the generic pickle/tensor/object loaders.
    path.write_bytes(blosc.asarray(value).to_cframe())
    return {"format": "b2nd", "dtype": value.dtype.str, "shape": list(value.shape)}


def _blosc_read(path, descriptor):
    blosc = dependency("blosc2", "blosc2")
    value = blosc.ndarray_from_cframe(path.read_bytes(), copy=True)[...]
    _array(value, blosc=True)
    if descriptor != {"format": "b2nd", "dtype": value.dtype.str, "shape": list(value.shape)}:
        raise ValidationError("Blosc2 array descriptor mismatch")
    return value


def _pandas_write(value, path):
    pd = dependency("pandas", "parquet")
    dependency("pyarrow", "parquet")
    if type(value) is not pd.DataFrame:
        raise ValidationError("pandas.parquet requires a pandas DataFrame")
    if not value.columns.is_unique or not all(type(c) is str for c in value.columns):
        raise ValidationError("Parquet v1 requires unique string column names")
    categories = {}
    for name in value.columns:
        column = value[name]
        if isinstance(column.dtype, pd.CategoricalDtype):
            labels = column.cat.categories
            if not all(type(label) is str for label in labels):
                raise ValidationError(
                    "Non-string categories are outside the lossless pandas subset"
                )
            categories[name] = {
                "values": list(labels),
                "dtype": str(labels.dtype),
                "ordered": column.cat.ordered,
            }
    descriptor = {
        "format": "parquet",
        "backend": "pandas",
        "parquet_version": "2.6",
        "categories": categories,
    }
    # Use pandas metadata for indexes and extension dtype restoration. Reject
    # lossy native round trips before uploading, not after storing a bad record.
    value.to_parquet(path, engine="pyarrow", index=None, version="2.6")
    restored = _pandas_read(path, descriptor)
    try:
        pd.testing.assert_frame_equal(value, restored, check_exact=True)
    except AssertionError as exc:
        raise ValidationError("DataFrame is outside the lossless pandas Parquet subset") from exc
    return descriptor


def _pandas_read(path, descriptor):
    descriptor = dict(descriptor)
    categories = descriptor.pop("categories", None)
    _parquet_descriptor(descriptor, "pandas")
    if not isinstance(categories, dict):
        raise ValidationError("Missing pandas categorical descriptor")
    pd = dependency("pandas", "parquet")
    dependency("pyarrow", "parquet")
    value = pd.read_parquet(path, engine="pyarrow")
    for name, spec in categories.items():
        labels = pd.Index(spec["values"], dtype=spec["dtype"])
        value[name] = pd.Categorical(value[name], categories=labels, ordered=spec["ordered"])
    return value


def _arrow_write(value, path):
    pa = dependency("pyarrow", "arrow")
    pq = dependency("pyarrow.parquet", "arrow")
    if type(value) is not pa.Table:
        raise ValidationError("pyarrow.parquet requires a PyArrow Table")
    pq.write_table(value, path, version="2.6", store_schema=True, use_compliant_nested_type=False)
    restored = pq.read_table(path)
    if not value.schema.equals(restored.schema, check_metadata=True) or not value.equals(restored):
        raise ValidationError("Table is outside the lossless Arrow Parquet subset")
    return {"format": "parquet", "backend": "pyarrow", "parquet_version": "2.6"}


def _arrow_read(path, descriptor):
    _parquet_descriptor(descriptor, "pyarrow")
    return dependency("pyarrow.parquet", "arrow").read_table(path)


def _polars_write(value, path):
    pl = dependency("polars", "polars")
    if type(value) is not pl.DataFrame:
        raise ValidationError("polars.parquet requires an eager Polars DataFrame")
    value.write_parquet(path)
    restored = pl.read_parquet(path)
    try:
        dependency("polars.testing", "polars").assert_frame_equal(value, restored, check_exact=True)
    except AssertionError as exc:
        raise ValidationError("DataFrame is outside the lossless Polars Parquet subset") from exc
    return {"format": "parquet", "backend": "polars", "parquet_version": "native"}


def _polars_read(path, descriptor):
    _parquet_descriptor(descriptor, "polars")
    return dependency("polars", "polars").read_parquet(path)


def _parquet_descriptor(descriptor, backend):
    if descriptor != {
        "format": "parquet",
        "backend": backend,
        "parquet_version": "native" if backend == "polars" else "2.6",
    }:
        raise ValidationError("Unsupported Parquet descriptor")


BUILTINS = (
    Handler("bytes", 1, _bytes_write, _bytes_read),
    Handler("numpy.npz", 1, _npz_write, _npz_read),
    Handler("numpy.blosc2", 1, _blosc_write, _blosc_read),
    Handler("pandas.parquet", 1, _pandas_write, _pandas_read),
    Handler("pyarrow.parquet", 1, _arrow_write, _arrow_read),
    Handler("polars.parquet", 1, _polars_write, _polars_read),
)
