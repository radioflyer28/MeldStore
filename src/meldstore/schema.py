"""Immutable generic metadata declarations; no persistence or domain models."""

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

from .errors import ValidationError

RESERVED = frozenset({"id", "schema_name", "schema_version", "version"})


def identifier(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValidationError("Identifiers must be nonempty text without NUL")
    try:
        if len(value.encode("utf-8")) > 128:
            raise ValidationError("Identifiers must fit in 128 UTF-8 bytes")
    except UnicodeEncodeError as exc:
        raise ValidationError("Identifiers must be valid Unicode") from exc
    return value


def quote_identifier(value: str) -> str:
    """Quote a declared identifier, never a SQL expression."""
    return '"' + identifier(value).replace('"', '""') + '"'


@dataclass(frozen=True)
class Field:
    kind: str
    required: bool = False
    immutable: bool = False

    def __post_init__(self):
        if self.kind not in {"text", "int64", "float", "boolean", "timestamp"}:
            raise ValidationError(f"Unknown field kind: {self.kind!r}")
        if type(self.required) is not bool or type(self.immutable) is not bool:
            raise ValidationError("required and immutable must be bool")

    def normalize(self, value: Any) -> Any:
        if value is None:
            if self.required:
                raise ValidationError("Required field cannot be null")
            return None
        valid = False
        if self.kind == "text":
            valid = type(value) is str
        elif self.kind == "int64":
            valid = type(value) is int and -(2**63) <= value < 2**63
        elif self.kind == "float":
            valid = type(value) is float and math.isfinite(value)
        elif self.kind == "boolean":
            valid = type(value) is bool
            if valid:
                return int(value)
        elif self.kind == "timestamp":
            valid = isinstance(value, datetime) and value.utcoffset() is not None
            if valid:
                try:
                    return (
                        value.astimezone(timezone.utc)
                        .isoformat(timespec="microseconds")
                        .replace("+00:00", "Z")
                    )
                except (OverflowError, ValueError) as exc:
                    raise ValidationError("Timestamp is outside UTC datetime range") from exc
        if not valid:
            raise ValidationError(f"Expected {self.kind}; implicit coercion is disabled")
        return value


def Text(*, required=False, immutable=False) -> Field:
    return Field("text", required, immutable)


def Integer(*, required=False, immutable=False) -> Field:
    return Field("int64", required, immutable)


def Float(*, required=False, immutable=False) -> Field:
    return Field("float", required, immutable)


def Boolean(*, required=False, immutable=False) -> Field:
    return Field("boolean", required, immutable)


def Timestamp(*, required=False, immutable=False) -> Field:
    return Field("timestamp", required, immutable)


@dataclass(frozen=True, init=False)
class Index:
    fields: tuple[str, ...]
    unique: bool

    def __init__(self, *fields: str, unique: bool = False):
        if not fields or len(set(fields)) != len(fields) or type(unique) is not bool:
            raise ValidationError("Index needs distinct fields and a boolean unique flag")
        for name in fields:
            identifier(name)
        object.__setattr__(self, "fields", tuple(fields))
        object.__setattr__(self, "unique", unique)


@dataclass(frozen=True)
class BlobSchema:
    name: str
    fields: Mapping[str, Field]
    version: int = 1
    indexes: tuple[Index, ...] = ()
    handlers: tuple[str, ...] = ("file",)
    validator_id: str | None = None
    payload_validator: Callable[[Any], None] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        identifier(self.name)
        # Hex-encoded SQL names remain within the public identifier limit.
        if len(self.name.encode("utf-8")) > 48:
            raise ValidationError("Schema names must fit in 48 UTF-8 bytes")
        if type(self.version) is not int or not 1 <= self.version < 2**63:
            raise ValidationError("Schema version must be a positive int64")
        if not isinstance(self.fields, Mapping):
            raise ValidationError("fields must be a mapping")
        seen = set(RESERVED)
        for name, spec in self.fields.items():
            identifier(name)
            key = name.translate(
                str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
            )
            if key in seen or name.lower().startswith("ms_"):
                raise ValidationError(f"Reserved or duplicate field: {name!r}")
            seen.add(key)
            if not isinstance(spec, Field):
                raise ValidationError("Field values must be Field declarations")
        indexes = tuple(self.indexes)
        if any(not isinstance(index, Index) for index in indexes):
            raise ValidationError("indexes must contain Index declarations")
        if len(set(indexes)) != len(indexes):
            raise ValidationError("Duplicate index")
        for index in indexes:
            if any(name not in self.fields for name in index.fields):
                raise ValidationError("Index refers to an undeclared field")
        if isinstance(self.handlers, str):
            raise ValidationError("handlers must be a sequence of IDs")
        handlers = tuple(self.handlers)
        for handler in handlers:
            identifier(handler)
        if not handlers or len(set(handlers)) != len(handlers):
            raise ValidationError("At least one distinct handler ID is required")
        if (self.validator_id is None) != (self.payload_validator is None):
            raise ValidationError("Payload validation requires both validator_id and callable")
        if self.validator_id is not None:
            identifier(self.validator_id)
            if not callable(self.payload_validator):
                raise ValidationError("payload_validator must be callable")
        object.__setattr__(self, "fields", MappingProxyType(dict(sorted(self.fields.items()))))
        object.__setattr__(
            self, "indexes", tuple(sorted(indexes, key=lambda x: (x.fields, x.unique)))
        )
        object.__setattr__(self, "handlers", tuple(sorted(handlers)))

    @property
    def table_name(self) -> str:
        return "ms_data_" + self.name.encode("utf-8").hex()

    @property
    def definition(self) -> str:
        """Canonical full declaration, not a digest or executable Python serialization."""
        return json.dumps(
            {
                "name": self.name,
                "version": self.version,
                "fields": {
                    name: {
                        "kind": spec.kind,
                        "required": spec.required,
                        "immutable": spec.immutable,
                    }
                    for name, spec in self.fields.items()
                },
                "indexes": [{"fields": i.fields, "unique": i.unique} for i in self.indexes],
                "handlers": self.handlers,
                "validator_id": self.validator_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    def normalize_metadata(self, values: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(values, Mapping) or values.keys() - self.fields.keys():
            raise ValidationError("Metadata must be a mapping of declared fields only")
        return {name: spec.normalize(values.get(name)) for name, spec in self.fields.items()}

    def validate_payload(self, value: Any) -> None:
        """Explicit application callback; never implicitly invoked by catalog SQL."""
        if self.payload_validator is not None:
            self.payload_validator(value)
