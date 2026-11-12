"""SQL-only retirement, durable cleanup intent and uncertain-outcome inspection."""

from . import encodings
from .dialect import for_transaction
from .errors import (
    ConflictError,
    SchemaConflictError,
    ValidationError,
)
from .installation import _objects as _sqlite_objects
from .installation import _verify
from .schema import BlobSchema, identifier
from .schema import quote_identifier as q

DDL = {
    "ms_lifecycle_format": "CREATE TABLE ms_lifecycle_format (version INTEGER NOT NULL PRIMARY KEY CHECK(version=1))",
    "ms_lifecycle_schemas": "CREATE TABLE ms_lifecycle_schemas (schema_name TEXT NOT NULL PRIMARY KEY)",
    "ms_retired": """CREATE TABLE ms_retired (
        blob_id TEXT NOT NULL PRIMARY KEY, token TEXT NOT NULL UNIQUE REFERENCES ms_prepared(token) ON DELETE RESTRICT,
        schema_name TEXT NOT NULL, schema_version INTEGER NOT NULL,
        metadata_version INTEGER NOT NULL CHECK(typeof(metadata_version)='integer' AND metadata_version>0),
        FOREIGN KEY(schema_name,schema_version) REFERENCES ms_schemas(name,version) ON DELETE RESTRICT)""",
    "ms_gc": """CREATE TABLE ms_gc (
        storage_id TEXT NOT NULL, object_key TEXT NOT NULL, token TEXT UNIQUE,
        blob_id TEXT UNIQUE REFERENCES ms_retired(blob_id) ON DELETE RESTRICT,
        reason TEXT NOT NULL CHECK(reason IN ('retired','discarded','orphan')),
        state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','done')),
        last_error TEXT, PRIMARY KEY(storage_id,object_key))""",
    "ms_state_transition": """CREATE TRIGGER ms_state_transition BEFORE UPDATE OF state ON ms_blobs
        WHEN NEW.state!=OLD.state AND NOT ((OLD.state='publishing' AND NEW.state='ready')
        OR (NEW.state='pending_delete' AND EXISTS(SELECT 1 FROM ms_retired WHERE blob_id=OLD.id)))
        BEGIN SELECT RAISE(ABORT,'invalid lifecycle transition'); END""",
    "ms_no_resurrection": """CREATE TRIGGER ms_no_resurrection BEFORE INSERT ON ms_blobs
        WHEN EXISTS(SELECT 1 FROM ms_retired WHERE blob_id=NEW.id)
        BEGIN SELECT RAISE(ABORT,'retired blob IDs cannot be reused'); END""",
    "ms_retire_identity": """CREATE TRIGGER ms_retire_identity BEFORE DELETE ON ms_blobs
        WHEN OLD.state!='pending_delete' OR NOT EXISTS(SELECT 1 FROM ms_retired WHERE blob_id=OLD.id)
        BEGIN SELECT RAISE(ABORT,'blob retirement requires queued deletion'); END""",
    "ms_retire_object": """CREATE TRIGGER ms_retire_object BEFORE DELETE ON ms_objects
        WHEN NOT EXISTS(SELECT 1 FROM ms_retired WHERE blob_id=OLD.blob_id AND token=OLD.token)
        BEGIN SELECT RAISE(ABORT,'object association requires explicit retirement'); END""",
    "ms_unrevoked_object": """CREATE TRIGGER ms_unrevoked_object BEFORE INSERT ON ms_objects
        WHEN EXISTS(SELECT 1 FROM ms_gc WHERE token=NEW.token)
        BEGIN SELECT RAISE(ABORT,'prepared token is revoked'); END""",
    "ms_fresh_object_key": """CREATE TRIGGER ms_fresh_object_key BEFORE INSERT ON ms_prepared
        WHEN EXISTS(SELECT 1 FROM ms_gc WHERE storage_id=NEW.storage_id AND object_key=NEW.object_key)
        BEGIN SELECT RAISE(ABORT,'object key was queued for cleanup'); END""",
    "ms_gc_identity": """CREATE TRIGGER ms_gc_identity BEFORE UPDATE ON ms_gc
        WHEN NEW.storage_id IS NOT OLD.storage_id OR NEW.object_key IS NOT OLD.object_key
        OR NEW.token IS NOT OLD.token OR NEW.blob_id IS NOT OLD.blob_id OR NEW.reason IS NOT OLD.reason
        OR (OLD.state='done' AND NEW.state!='done')
        BEGIN SELECT RAISE(ABORT,'cleanup identity is immutable'); END""",
    "ms_gc_retain": """CREATE TRIGGER ms_gc_retain BEFORE DELETE ON ms_gc
        BEGIN SELECT RAISE(ABORT,'cleanup history must be retained'); END""",
    "ms_retired_update": """CREATE TRIGGER ms_retired_update BEFORE UPDATE ON ms_retired
        BEGIN SELECT RAISE(ABORT,'retirement identity is immutable'); END""",
    "ms_retired_delete": """CREATE TRIGGER ms_retired_delete BEFORE DELETE ON ms_retired
        BEGIN SELECT RAISE(ABORT,'retirement history must be retained'); END""",
}


def _objects(tx):
    if for_transaction(tx).postgres:
        from .postgres_schema import namespace_objects

        return namespace_objects(tx)
    return _sqlite_objects(tx)


def lock(tx, id=None):
    """Serialize short lifecycle writes; optionally lock the metadata version."""
    if not for_transaction(tx).postgres:
        return
    tx.sql("SELECT singleton FROM ms_format FOR UPDATE")
    if id is not None:
        rows = tx.sql("SELECT schema_name FROM ms_blobs WHERE id=%s FOR UPDATE", (id,))
        if rows:
            dialect = for_transaction(tx)
            name = dialect.quote(dialect.table(BlobSchema(rows[0]['schema_name'], {})))
            tx.sql(f"SELECT id FROM {name} WHERE id=%s FOR UPDATE", (id,))


def verify(tx):
    if for_transaction(tx).postgres:
        from .postgres_extensions import LIFECYCLE, retirement_guard
        from .postgres_schema import verify as verify_postgres

        verify_postgres(tx, LIFECYCLE)
        if tx.sql("SELECT version FROM ms_lifecycle_format") != [{"version": 1}]:
            raise SchemaConflictError("Unsupported lifecycle extension")
        for row in tx.sql("SELECT schema_name FROM ms_lifecycle_schemas"):
            verify_postgres(tx, retirement_guard(BlobSchema(row['schema_name'], {})))
        return
    _verify(_objects(tx), DDL)
    if tx.sql("SELECT version FROM ms_lifecycle_format") != [{"version": 1}]:
        raise SchemaConflictError("Unsupported lifecycle extension")
    objects = _objects(tx)
    for row in tx.sql("SELECT schema_name FROM ms_lifecycle_schemas"):
        name, sql = schema_guard(BlobSchema(row["schema_name"], {}))
        _verify(objects, {name: sql})


def install(tx):
    if tx._catalog.adapter == "postgres":
        from .postgres_extensions import LIFECYCLE, install_group

        return install_group(tx, LIFECYCLE, "ms_lifecycle_format")

    objects = _objects(tx)
    if "ms_lifecycle_format" in objects:
        verify(tx)
    else:
        if any(key in objects for key in DDL):
            raise SchemaConflictError("Partial lifecycle extension")
        if tx.sql("SELECT 1 FROM ms_blobs WHERE state='pending_delete' LIMIT 1"):
            raise SchemaConflictError("Resolve legacy pending_delete rows before installing S04")
        for sql in DDL.values():
            tx.sql(sql)
        tx.sql("INSERT INTO ms_lifecycle_format VALUES(1)")


def schema_guard(schema):
    name = schema.table_name + "_retire"
    return (
        name,
        f"""CREATE TRIGGER {q(name)} BEFORE DELETE ON {q(schema.table_name)}
        WHEN NOT EXISTS(SELECT 1 FROM ms_retired WHERE blob_id=OLD.id)
        BEGIN SELECT RAISE(ABORT,'metadata deletion requires explicit retirement'); END""",
    )


def install_schema(schema, tx):
    bind = for_transaction(tx).bind
    if tx._catalog.adapter == "postgres":
        from .postgres_extensions import install_retirement

        return install_retirement(schema, tx)

    # Enrollment is separate so missing guards on already-enrolled schemas fail.
    name, sql = schema_guard(schema)
    objects = _objects(tx)
    if tx.sql(f"SELECT 1 FROM ms_lifecycle_schemas WHERE schema_name={bind}", (schema.name,)):
        _verify(objects, {name: sql})
    else:
        if name in objects:
            raise SchemaConflictError("Retirement guard exists without enrollment")
        tx.sql(sql)
        tx.sql(f"INSERT INTO ms_lifecycle_schemas VALUES({bind})", (schema.name,))


def _version(value):
    if type(value) is not int or not 1 <= value < 2**63:
        raise ValidationError("expected_version must be a positive int64")


def retired(tx, id):
    bind = for_transaction(tx).bind
    rows = tx.sql(
        f"SELECT r.*,g.state,g.last_error,g.storage_id,g.object_key FROM ms_retired r JOIN ms_gc g ON g.blob_id=r.blob_id WHERE r.blob_id={bind}",
        (id,),
    )
    return rows[0] if rows else None


def delete(store, id, expected_version, tx):
    bind = for_transaction(tx).bind
    identifier(id)
    _version(expected_version)
    lock(tx, id)
    verify(tx)
    previous = retired(tx, id)
    if previous:
        if previous["storage_id"] != store.storage.storage_id:
            raise ValidationError("Retirement belongs to another storage root")
        if previous["metadata_version"] != expected_version:
            raise ConflictError("Retirement concurrency version differs")
        return previous
    row = store._record(id, tx, ready_only=False)
    if row["storage_id"] != store.storage.storage_id:
        raise ValidationError("Blob belongs to another storage root")
    if row["version"] != expected_version:
        raise ConflictError("Metadata concurrency version is stale")
    if "ms_migrations" in _objects(tx) and tx.sql(
        f"SELECT 1 FROM ms_migrations WHERE schema_name={bind} AND state='running'", (row["schema_name"],)
    ):
        raise ConflictError("Finish the schema migration before retiring its blobs")
    tx.sql(
        f"INSERT INTO ms_retired VALUES ({bind},{bind},{bind},{bind},{bind})",
        (id, row["token"], row["schema_name"], row["schema_version"], row["version"]),
    )
    tx.sql(
        f"INSERT INTO ms_gc(storage_id,object_key,token,blob_id,reason) VALUES({bind},{bind},{bind},{bind},'retired')",
        (row["storage_id"], row["object_key"], row["token"], id),
    )
    tx.sql(f"UPDATE ms_blobs SET state='pending_delete' WHERE id={bind}", (id,))
    table = for_transaction(tx).table(BlobSchema(row["schema_name"], {}))
    tx.sql(f"DELETE FROM {for_transaction(tx).quote(table)} WHERE id={bind}", (id,))
    tx.sql(f"DELETE FROM ms_objects WHERE blob_id={bind}", (id,))
    # Real DELETE enforces application FKs, including deferred ones at commit.
    tx.sql(f"DELETE FROM ms_blobs WHERE id={bind}", (id,))
    return retired(tx, id)


def discard(store, prepared, tx):
    bind = for_transaction(tx).bind
    from .store import PreparedFile

    lock(tx)
    verify(tx)
    if not isinstance(prepared, PreparedFile) or prepared.storage_id != store.storage.storage_id:
        raise ValidationError("Expected this storage root's PreparedFile")
    if not encodings.matches(tx, prepared):
        raise ValidationError("Prepared token is unknown or altered")
    if tx.sql(f"SELECT 1 FROM ms_objects WHERE token={bind}", (prepared.token,)):
        raise ConflictError("Prepared token belongs to a blob; resolve that blob first")
    if tx.sql(f"SELECT 1 FROM ms_retired WHERE token={bind}", (prepared.token,)):
        raise ConflictError("Prepared token belongs to a retired blob")
    tx.sql(
        f"INSERT INTO ms_gc(storage_id,object_key,token,reason) VALUES({bind},{bind},{bind},'discarded') ON CONFLICT DO NOTHING",
        (prepared.storage_id, prepared.object_key, prepared.token),
    )
    return tx.sql(f"SELECT * FROM ms_gc WHERE token={bind}", (prepared.token,))[0]


def resolve(store, id, prepared, schema, metadata, tx):
    bind = for_transaction(tx).bind
    from .store import PreparedFile

    identifier(id)
    if not isinstance(prepared, PreparedFile) or prepared.storage_id != store.storage.storage_id:
        raise ValidationError("Expected this storage root's PreparedFile")
    store._schema(schema, tx)
    values = schema.normalize_metadata(metadata)
    if (prepared.schema_name, prepared.schema_version) != (schema.name, schema.version):
        raise ValidationError("Token and schema differ")
    journal = tx.sql(f"SELECT * FROM ms_prepared WHERE token={bind}", (prepared.token,))
    if journal and not encodings.matches(tx, prepared):
        raise ConflictError("Prepared token descriptor differs")
    prior = retired(tx, id)
    if prior:
        if prior["token"] != prepared.token:
            raise ConflictError("ID belongs to another retired operation")
        return {"outcome": "retired", "retirement": prior}
    if tx.sql(f"SELECT 1 FROM ms_blobs WHERE id={bind}", (id,)):
        row = store._record(id, tx, ready_only=False)
        if (row["token"], row["schema_name"], row["schema_version"], row["metadata"]) != (
            prepared.token,
            schema.name,
            schema.version,
            values,
        ):
            raise ConflictError("ID exists with different object or metadata")
        return {"outcome": row["state"], "record": row}
    if tx.sql(f"SELECT 1 FROM ms_objects WHERE token={bind}", (prepared.token,)):
        raise ConflictError("Token is associated with another ID")
    if tx.sql(f"SELECT 1 FROM ms_gc WHERE token={bind}", (prepared.token,)):
        return {"outcome": "discarded"}
    if not journal:
        return {"outcome": "unknown"}
    return {"outcome": "prepared"}
