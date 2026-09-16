"""Additive, sealed logical encodings over the legacy physical-file journal."""

from dataclasses import asdict, fields

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
    _verify(_objects(tx), DDL)
    if tx.sql("SELECT version FROM ms_encoding_format") != [{"version": 1}]:
        raise SchemaConflictError("Unsupported encoding extension")


def read(tx, token):
    objects = _objects(tx)
    if "ms_encoding_format" not in objects:
        if any(name in objects for name in DDL):
            raise SchemaConflictError("Partial encoding extension")
        return None
    verify(tx)
    rows = tx.sql("SELECT * FROM ms_encodings WHERE token=?", (token,))
    return rows[0] if rows else None


def physical(prepared):
    from .store import PreparedFile

    return {field.name: getattr(prepared, field.name) for field in fields(PreparedFile)}


def matches(tx, prepared):
    from .store import PreparedValue

    if tx.sql("SELECT * FROM ms_prepared WHERE token=?", (prepared.token,)) != [physical(prepared)]:
        return False
    row = read(tx, prepared.token)
    if isinstance(prepared, PreparedValue):
        return row == {
            key: asdict(prepared)[key]
            for key in ("token", "encoding_id", "encoding_version", "descriptor")
        }
    return row is None
