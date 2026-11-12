"""Additive, sealed logical encodings over the legacy physical-file journal."""

from dataclasses import asdict, fields

from .dialect import for_transaction
from .errors import SchemaConflictError
from .installation import _objects, _verify

DDL = {
    "ms_encoding_format": "CREATE TABLE ms_encoding_format (version INTEGER PRIMARY KEY CHECK(version=1))",
    "ms_encodings": """CREATE TABLE ms_encodings (
        token TEXT NOT NULL PRIMARY KEY REFERENCES ms_prepared(token)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
        encoding_id TEXT NOT NULL CHECK(length(encoding_id)>0 AND encoding_id!='file'),
        encoding_version INTEGER NOT NULL CHECK(typeof(encoding_version)='integer' AND encoding_version>0),
        descriptor TEXT NOT NULL CHECK(length(descriptor)<=65536)
    )""",
    "ms_encoding_insert": """CREATE TRIGGER ms_encoding_insert BEFORE INSERT ON ms_encodings
        WHEN EXISTS(SELECT 1 FROM ms_prepared WHERE token=NEW.token)
        BEGIN SELECT RAISE(ABORT, 'encoding must precede physical journal'); END""",
    "ms_encoding_update": """CREATE TRIGGER ms_encoding_update BEFORE UPDATE ON ms_encodings
        BEGIN SELECT RAISE(ABORT, 'encoding is immutable'); END""",
    "ms_encoding_delete": """CREATE TRIGGER ms_encoding_delete BEFORE DELETE ON ms_encodings
        BEGIN SELECT RAISE(ABORT, 'encoding is retained for recovery'); END""",
}


def install(tx):
    if tx._catalog.adapter == "postgres":
        from .postgres_extensions import ENCODING, install_group

        return install_group(tx, ENCODING, "ms_encoding_format")

    objects = _objects(tx)
    if "ms_encoding_format" not in objects:
        if any(name in objects for name in DDL):
            raise SchemaConflictError("Partial encoding extension")
        for sql in DDL.values():
            tx.sql(sql)
        tx.sql("INSERT INTO ms_encoding_format VALUES(1)")
    else:
        verify(tx)


def verify(tx):
    if for_transaction(tx).postgres:
        from .postgres_extensions import ENCODING
        from .postgres_schema import verify as verify_postgres

        verify_postgres(tx, ENCODING)
        if tx.sql("SELECT version FROM ms_encoding_format") != [{"version": 1}]:
            raise SchemaConflictError("Unsupported encoding extension")
        return
    _verify(_objects(tx), DDL)
    if tx.sql("SELECT version FROM ms_encoding_format") != [{"version": 1}]:
        raise SchemaConflictError("Unsupported encoding extension")


def read(tx, token):
    bind = for_transaction(tx).bind
    if tx._catalog.adapter == "postgres":
        from .postgres_extensions import ENCODING
        from .postgres_schema import namespace_objects
        from .postgres_schema import verify as verify_postgres

        objects = namespace_objects(tx)
        if "ms_encoding_format" not in objects:
            if any(obj.name in objects for obj in ENCODING):
                raise SchemaConflictError("Partial encoding extension")
            return None
        verify_postgres(tx, ENCODING)
        if tx.sql("SELECT version FROM ms_encoding_format") != [{"version": 1}]:
            raise SchemaConflictError("Unsupported encoding extension")
        rows = tx.sql("SELECT * FROM ms_encodings WHERE token=%s", (token,))
        return rows[0] if rows else None
    objects = _objects(tx)
    if "ms_encoding_format" not in objects:
        if any(name in objects for name in DDL):
            raise SchemaConflictError("Partial encoding extension")
        return None
    verify(tx)
    rows = tx.sql(f"SELECT * FROM ms_encodings WHERE token={bind}", (token,))
    return rows[0] if rows else None


def physical(prepared):
    from .store import PreparedFile

    return {field.name: getattr(prepared, field.name) for field in fields(PreparedFile)}


def matches(tx, prepared):
    bind = for_transaction(tx).bind
    from .store import PreparedValue

    if tx.sql(f"SELECT * FROM ms_prepared WHERE token={bind}", (prepared.token,)) != [physical(prepared)]:
        return False
    row = read(tx, prepared.token)
    if isinstance(prepared, PreparedValue):
        return row == {
            key: asdict(prepared)[key]
            for key in ("token", "encoding_id", "encoding_version", "descriptor")
        }
    return row is None
