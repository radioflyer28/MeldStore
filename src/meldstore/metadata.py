"""Shared guarded metadata edits; no payload access or transaction ownership."""

from collections.abc import Mapping

from .dialect import for_transaction
from .errors import ConflictError, ValidationError
from .schema import identifier


def changes(schema, values, expected_version):
    if type(expected_version) is not int or not 1 <= expected_version < 2**63 - 1:
        raise ValidationError("expected_version must be a positive, incrementable int64")
    if not isinstance(values, Mapping) or not values or values.keys() - schema.fields.keys():
        raise ValidationError("changes must be a nonempty mapping of declared metadata fields")
    result = {}
    for name, value in values.items():
        if schema.fields[name].immutable:
            raise ValidationError(f"Immutable metadata field: {name}")
        result[name] = schema.fields[name].normalize(value)
    return result


def update(tx, id, schema, values, expected_version):
    """Apply normalized values atomically after the caller's record validation."""
    dialect = for_transaction(tx)
    identifier(id)
    bind = dialect.bind
    rows = tx.sql(
        f"UPDATE {dialect.quote(dialect.table(schema))} SET "
        + ",".join(f"{dialect.quote(name)}={bind}" for name in values)
        + f", version=version+1 WHERE id={bind} AND schema_version={bind} AND version={bind}"
        + " RETURNING id",
        tuple(dialect.value(v) for v in (*values.values(), id, schema.version, expected_version)),
    )
    if not rows:
        raise ConflictError("Metadata changed before the guarded update")
