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


def inspect(tx):
    """Verify the optional extension in this transaction; return whether installed."""
    if for_transaction(tx).postgres:
        from .postgres_extensions import ENCODING
        from .postgres_schema import namespace_objects

        objects = namespace_objects(tx)
        names = (obj.name for obj in ENCODING)
    else:
        objects = _objects(tx)
        names = DDL
    if "ms_encoding_format" not in objects:
        if any(name in objects for name in names):
            raise SchemaConflictError("Partial encoding extension")
        return False
    verify(tx)
    return True


def read(tx, token):
    if not inspect(tx):
        return None
    bind = for_transaction(tx).bind
    rows = tx.sql(f"SELECT * FROM ms_encodings WHERE token={bind}", (token,))
    return rows[0] if rows else None


# Bound transient descriptor text separately from the returned decoded page.
_BATCH_BYTES = 1024 * 1024


def _descriptor_groups(ids, records):
    group, used = [], 0
    for id in ids:
        row = records.get(id)
        size = (row.pop("descriptor_bytes") or 0) if row is not None else 0
        # Healthy descriptors are at most 64 KiB through the public writer.
        # SQLite length(TEXT) stops at NUL, so malformed SQL-written data can
        # be much larger. The query below withholds that raw text.
        size = min(size, _BATCH_BYTES)
        if group and used + size > _BATCH_BYTES:
            yield tuple(group)
            group, used = [], 0
        group.append(id)
        used += size
    if group:
        yield tuple(group)


def batches(tx, ids, records, *, installed):
    """Yield in-order ID groups and their bounded raw encoding rows."""
    if not installed:
        yield ids, {}
        return
    bind = for_transaction(tx).bind
    for group in _descriptor_groups(ids, records):
        placeholders = ",".join(bind for _ in group)
        rows = {row["token"]: row for row in tx.sql(
            "SELECT e.token, e.encoding_id, e.encoding_version, "
            f"CASE WHEN {descriptor_size(tx)} <= {_BATCH_BYTES} "
            "THEN e.descriptor END AS descriptor "
            "FROM ms_encodings e JOIN ms_objects o ON e.token=o.token "
            f"WHERE o.blob_id IN ({placeholders})", group
        )}
        yield group, rows


def descriptor_size(tx):
    if for_transaction(tx).postgres:
        return "octet_length(e.descriptor)"
    return "length(CAST(e.descriptor AS BLOB))"


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
