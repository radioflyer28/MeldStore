"""Explicit additive payload extension to the unchanged S01 identity schema."""

from .errors import SchemaConflictError
from .installation import _literal, _objects, _verify
from .schema import quote_identifier as q

DDL = {
    "ms_payload_format": "CREATE TABLE ms_payload_format (version INTEGER NOT NULL PRIMARY KEY CHECK (version=1))",
    "ms_payload_schemas": """CREATE TABLE ms_payload_schemas (
        schema_name TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        PRIMARY KEY (schema_name, schema_version),
        FOREIGN KEY (schema_name, schema_version) REFERENCES ms_schemas(name, version)
            ON UPDATE RESTRICT ON DELETE RESTRICT
    )""",
    "ms_prepared": """CREATE TABLE ms_prepared (
        token TEXT NOT NULL PRIMARY KEY,
        storage_id TEXT NOT NULL,
        object_key TEXT NOT NULL,
        byte_size INTEGER NOT NULL CHECK (typeof(byte_size)='integer' AND byte_size>=0),
        digest TEXT NOT NULL CHECK (length(digest)=32 AND digest NOT GLOB '*[^0-9a-f]*'),
        hash_algorithm TEXT NOT NULL CHECK (hash_algorithm='xxh3_128'),
        handler_id TEXT NOT NULL CHECK (handler_id='file'),
        handler_version INTEGER NOT NULL CHECK (handler_version=1),
        schema_name TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        UNIQUE (storage_id, object_key),
        FOREIGN KEY (schema_name, schema_version) REFERENCES ms_schemas(name, version)
            ON UPDATE RESTRICT ON DELETE RESTRICT
    )""",
    "ms_objects": """CREATE TABLE ms_objects (
        blob_id TEXT NOT NULL PRIMARY KEY REFERENCES ms_blobs(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        token TEXT NOT NULL UNIQUE REFERENCES ms_prepared(token) ON UPDATE RESTRICT ON DELETE RESTRICT
    )""",
    "ms_prepared_immutable": """CREATE TRIGGER ms_prepared_immutable BEFORE UPDATE ON ms_prepared
        BEGIN SELECT RAISE(ABORT, 'prepared descriptor is immutable'); END""",
    "ms_objects_immutable": """CREATE TRIGGER ms_objects_immutable BEFORE UPDATE ON ms_objects
        BEGIN SELECT RAISE(ABORT, 'object association is immutable'); END""",
    "ms_insert_publishing": """CREATE TRIGGER ms_insert_publishing BEFORE INSERT ON ms_blobs
        WHEN NEW.state != 'publishing'
        BEGIN SELECT RAISE(ABORT, 'new blobs must start publishing'); END""",
}


def guard(schema):
    name = schema.table_name + "_ready"
    sql = f"""CREATE TRIGGER {q(name)} BEFORE UPDATE OF state ON ms_blobs
        WHEN NEW.schema_name={_literal(schema.name)} AND NEW.state='ready'
        AND (NOT EXISTS (SELECT 1 FROM {q(schema.table_name)} m
            WHERE m.id=NEW.id AND m.schema_version=NEW.schema_version)
        OR NOT EXISTS (SELECT 1 FROM ms_objects o JOIN ms_prepared p ON o.token=p.token
            WHERE o.blob_id=NEW.id AND p.schema_name=NEW.schema_name
                AND p.schema_version=NEW.schema_version))
        BEGIN SELECT RAISE(ABORT, 'blob is not ready for publication'); END"""
    return name, sql


def install(schema, tx):
    objects = _objects(tx)
    if "ms_payload_format" not in objects:
        if any(name in objects for name in DDL):
            raise SchemaConflictError("Partial or conflicting payload extension")
        if tx.sql("SELECT id FROM ms_blobs WHERE state!='publishing' LIMIT 1"):
            raise SchemaConflictError("Cannot adopt existing ready/retired S01 fixture rows")
        for sql in DDL.values():
            tx.sql(sql)
        tx.sql("INSERT INTO ms_payload_format VALUES (1)")
    else:
        _verify(objects, DDL)
        if tx.sql("SELECT version FROM ms_payload_format") != [{"version": 1}]:
            raise SchemaConflictError("Unsupported payload extension version")
    name, sql = guard(schema)
    enrolled = tx.sql(
        "SELECT 1 FROM ms_payload_schemas WHERE schema_name=? AND schema_version=?",
        (schema.name, schema.version),
    )
    if enrolled:
        _verify(objects, {name: sql})
    else:
        if name in objects:
            raise SchemaConflictError("Publication guard exists without schema enrollment")
        if tx.sql(
            "SELECT id FROM ms_blobs WHERE schema_name=? AND state!='publishing' LIMIT 1",
            (schema.name,),
        ):
            raise SchemaConflictError("Cannot adopt ready/retired rows from an unenrolled schema")
        tx.sql(sql)
        tx.sql("INSERT INTO ms_payload_schemas VALUES (?, ?)", (schema.name, schema.version))
