"""Bounded scalar SQL predicates and deterministic, null-aware keyset ordering."""

from collections.abc import Mapping
from dataclasses import dataclass

from .errors import ValidationError
from .schema import Text
from .schema import quote_identifier as q


@dataclass(frozen=True)
class Predicate:
    field: str
    op: str
    value: object = None


@dataclass(frozen=True)
class Order:
    field: str
    descending: bool = False


@dataclass(frozen=True)
class FindCursor:
    schema_name: str
    schema_version: int
    order: tuple[Order, ...]
    values: tuple


def field(schema, name):
    if name == "id":
        return Text(required=True)
    if not isinstance(name, str) or name not in schema.fields:
        raise ValidationError("Query references an undeclared field")
    return schema.fields[name]


def column(name):
    return "b.id" if name == "id" else "m." + q(name)


def ordering(schema, order_by):
    if not isinstance(order_by, (tuple, list)) or len(order_by) > 8:
        raise ValidationError("order_by must contain at most 8 Order declarations")
    order = tuple(order_by) or (Order("id"),)
    names = set()
    for i, item in enumerate(order):
        if not isinstance(item, Order) or type(item.descending) is not bool:
            raise ValidationError("Expected Order(field, descending=bool)")
        field(schema, item.field)
        if item.field in names or (item.field == "id" and i != len(order) - 1):
            raise ValidationError("Ordering fields must be distinct; id must be last")
        names.add(item.field)
    return order if "id" in names else (*order, Order("id"))


def cursor(record, schema, order_by=()):
    if (record["schema_name"], record["schema_version"]) != (schema.name, schema.version):
        raise ValidationError("Cursor record belongs to another schema version")
    order = ordering(schema, order_by)
    values = tuple(record["id"] if o.field == "id" else record["metadata"][o.field] for o in order)
    return FindCursor(schema.name, schema.version, order, values)


def compile_query(schema, *, where, predicates, order_by, after, limit):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValidationError("limit must be an integer between 1 and 1000")
    where = {} if where is None else where
    if not isinstance(where, Mapping) or not isinstance(predicates, (list, tuple)):
        raise ValidationError("where must be a mapping; predicates must be a sequence")
    terms = [Predicate(name, "eq", value) for name, value in where.items()] + list(predicates)
    if len(terms) > 64:
        raise ValidationError("At most 64 predicates are supported")
    clauses = ["b.state='ready'", "b.schema_name=?", "b.schema_version=?", "m.schema_version=?"]
    params = [schema.name, schema.version, schema.version]
    operators = {"eq": "=", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}
    for term in terms:
        if not isinstance(term, Predicate) or not isinstance(term.op, str):
            raise ValidationError("Expected Predicate(field, op, value)")
        spec, col = field(schema, term.field), column(term.field)
        if term.op in {"is_null", "not_null"}:
            if term.value is not None:
                raise ValidationError("Null predicates do not take a value")
            clauses.append(col + (" IS NULL" if term.op == "is_null" else " IS NOT NULL"))
        elif term.op in {"in", "not_in"}:
            if not isinstance(term.value, (tuple, list)) or not 1 <= len(term.value) <= 100:
                raise ValidationError("Membership needs 1..100 values")
            if any(value is None for value in term.value):
                raise ValidationError("Use an explicit null predicate instead of null membership")
            clauses.append(
                col
                + (" IN (" if term.op == "in" else " NOT IN (")
                + ",".join("?" for _ in term.value)
                + ")"
            )
            params.extend(spec.normalize(value) for value in term.value)
        elif term.op in operators:
            if term.value is None:
                if term.op not in {"eq", "ne"}:
                    raise ValidationError("Null has no scalar range ordering")
                clauses.append(col + (" IS NULL" if term.op == "eq" else " IS NOT NULL"))
            else:
                clauses.append(f"{col} {operators[term.op]} ?")
                params.append(spec.normalize(term.value))
        else:
            raise ValidationError("Unsupported scalar operator")
    order = ordering(schema, order_by)
    if after is not None:
        if isinstance(after, str) and order == (Order("id"),):
            after = FindCursor(schema.name, schema.version, order, (after,))
        if (
            not isinstance(after, FindCursor)
            or (after.schema_name, after.schema_version, after.order)
            != (schema.name, schema.version, order)
            or len(after.values) != len(order)
        ):
            raise ValidationError("Cursor schema/order does not match this query")
        values = []
        for item, value in zip(order, after.values):
            if value is not None:
                spec = field(schema, item.field)
                value = spec.normalize(spec.from_sql(value))
            elif item.field == "id" or field(schema, item.field).required:
                raise ValidationError("Required sort field cannot have a null cursor")
            values.append(value)
        branches = []
        for i, item in enumerate(order):
            pieces = [f"{column(o.field)} IS ?" for o in order[:i]]
            params.extend(values[:i])
            col, value = column(item.field), values[i]
            if value is None:
                pieces.append("0" if item.descending else col + " IS NOT NULL")
            else:
                pieces.append(f"({col} < ? OR {col} IS NULL)" if item.descending else f"{col} > ?")
                params.append(value)
            branches.append("(" + " AND ".join(pieces) + ")")
        clauses.append("(" + " OR ".join(branches) + ")")
    params.append(limit)
    sql = f"SELECT b.id FROM ms_blobs b JOIN {q(schema.table_name)} m ON b.id=m.id WHERE "
    sql += " AND ".join(clauses) + " ORDER BY "
    sql += ",".join(column(o.field) + (" DESC" if o.descending else " ASC") for o in order)
    return sql + " LIMIT ?", params
