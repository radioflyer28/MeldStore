"""Small dialect seam for generated SQL; caller SQL is never rewritten."""

from dataclasses import dataclass
from hashlib import sha256

from .errors import ValidationError
from .schema import quote_identifier


def postgres_name(name):
    """Bound internal names, keeping short existing names readable."""
    if len(name.encode("utf-8")) <= 48:
        return name
    return "ms_h_" + sha256(name.encode("utf-8")).hexdigest()[:40]


@dataclass(frozen=True)
class Dialect:
    postgres: bool = False

    @property
    def bind(self):
        return "%s" if self.postgres else "?"

    def table(self, schema):
        return postgres_name(schema.table_name) if self.postgres else schema.table_name

    def quote(self, name):
        quoted = quote_identifier(name)
        if self.postgres and len(name.encode("utf-8")) > 63:
            raise ValidationError("PostgreSQL field identifiers must fit in 63 UTF-8 bytes")
        return quoted.replace("%", "%%") if self.postgres else quoted

    def value(self, value):
        if self.postgres and isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise ValidationError("PostgreSQL text must be valid Unicode") from None
            if "\x00" in value:
                raise ValidationError("PostgreSQL text cannot contain NUL")
        return value

    def equal(self, column):
        return f"{column} IS NOT DISTINCT FROM {self.bind}" if self.postgres else f"{column} IS ?"

    def order(self, column, descending):
        result = column + (" DESC" if descending else " ASC")
        if self.postgres:
            result += " NULLS LAST" if descending else " NULLS FIRST"
        return result


SQLITE = Dialect()
POSTGRES = Dialect(postgres=True)


def for_transaction(tx):
    if hasattr(tx, "_dialect"):
        return tx._dialect
    return POSTGRES if tx._catalog.adapter == "postgres" else SQLITE
