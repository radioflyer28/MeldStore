"""PostgreSQL payload/encoding/lifecycle DDL. Payload operations remain gated."""

from .dialect import POSTGRES
from .errors import SchemaConflictError
from .installation import _literal
from .postgres_schema import TEXT, Object, create, guard, namespace_objects, table, verify
from .schema import quote_identifier as q

GC_INDEX = Object("ms_gc_pending", "index",
                  "CREATE INDEX ms_gc_pending ON ms_gc(storage_id,object_key) WHERE state='pending'")

PAYLOAD = [
    table("ms_payload_format", "version BIGINT NOT NULL PRIMARY KEY CHECK(version=1)"),
    table(
        "ms_payload_schemas",
        f"schema_name {TEXT} NOT NULL, schema_version BIGINT NOT NULL, "
        "PRIMARY KEY(schema_name,schema_version), FOREIGN KEY(schema_name,schema_version) "
        "REFERENCES ms_schemas(name,version) ON UPDATE RESTRICT ON DELETE RESTRICT",
    ),
    table(
        "ms_prepared",
        f"token {TEXT} NOT NULL PRIMARY KEY, storage_id {TEXT} NOT NULL, "
        f"object_key {TEXT} NOT NULL, byte_size BIGINT NOT NULL CHECK(byte_size>=0), "
        f"digest {TEXT} NOT NULL CHECK(digest ~ '^[0-9a-f]{{32}}$'), "
        f"hash_algorithm {TEXT} NOT NULL CHECK(hash_algorithm='xxh3_128'), "
        f"handler_id {TEXT} NOT NULL CHECK(handler_id='file'), "
        "handler_version BIGINT NOT NULL CHECK(handler_version=1), "
        f"schema_name {TEXT} NOT NULL, schema_version BIGINT NOT NULL, "
        "UNIQUE(storage_id,object_key), FOREIGN KEY(schema_name,schema_version) "
        "REFERENCES ms_schemas(name,version) ON UPDATE RESTRICT ON DELETE RESTRICT",
    ),
    table(
        "ms_objects",
        f"blob_id {TEXT} NOT NULL PRIMARY KEY REFERENCES ms_blobs(id) "
        f"ON UPDATE RESTRICT ON DELETE RESTRICT, token {TEXT} NOT NULL UNIQUE "
        "REFERENCES ms_prepared(token) ON UPDATE RESTRICT ON DELETE RESTRICT",
    ),
    *guard(
        "ms_prepared_immutable", "ms_prepared", "UPDATE", "TRUE", "prepared descriptor is immutable"
    ),
    *guard(
        "ms_objects_immutable", "ms_objects", "UPDATE", "TRUE", "object association is immutable"
    ),
    *guard(
        "ms_insert_publishing",
        "ms_blobs",
        "INSERT",
        "NEW.state != 'publishing'",
        "new blobs must start publishing",
    ),
]
ENCODING = [
    table("ms_encoding_format", "version BIGINT NOT NULL PRIMARY KEY CHECK(version=1)"),
    table(
        "ms_encodings",
        f"token {TEXT} NOT NULL PRIMARY KEY REFERENCES ms_prepared(token) "
        "ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED, "
        f"encoding_id {TEXT} NOT NULL CHECK(length(encoding_id)>0 AND encoding_id!='file'), "
        "encoding_version BIGINT NOT NULL CHECK(encoding_version>0), "
        f"descriptor {TEXT} NOT NULL CHECK(length(descriptor)<=65536)",
    ),
    *guard(
        "ms_encoding_insert",
        "ms_encodings",
        "INSERT",
        "EXISTS(SELECT 1 FROM ms_prepared WHERE token=NEW.token)",
        "encoding must precede journal",
    ),
    *guard("ms_encoding_update", "ms_encodings", "UPDATE", "TRUE", "encoding is immutable"),
    *guard("ms_encoding_delete", "ms_encodings", "DELETE", "TRUE", "encoding is retained"),
]
LIFECYCLE = [
    table("ms_lifecycle_format", "version BIGINT NOT NULL PRIMARY KEY CHECK(version=1)"),
    table("ms_lifecycle_schemas", f"schema_name {TEXT} NOT NULL PRIMARY KEY"),
    table(
        "ms_retired",
        f"blob_id {TEXT} NOT NULL PRIMARY KEY, token {TEXT} NOT NULL UNIQUE "
        f"REFERENCES ms_prepared(token) ON DELETE RESTRICT, schema_name {TEXT} NOT NULL, "
        "schema_version BIGINT NOT NULL, metadata_version BIGINT NOT NULL CHECK(metadata_version>0), "
        "FOREIGN KEY(schema_name,schema_version) REFERENCES ms_schemas(name,version) ON DELETE RESTRICT",
    ),
    table(
        "ms_gc",
        f"storage_id {TEXT} NOT NULL, object_key {TEXT} NOT NULL, token {TEXT} UNIQUE, "
        f"blob_id {TEXT} UNIQUE REFERENCES ms_retired(blob_id) ON DELETE RESTRICT, "
        f"reason {TEXT} NOT NULL CHECK(reason IN ('retired','discarded','orphan')), "
        f"state {TEXT} NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','done')), "
        f"last_error {TEXT}, PRIMARY KEY(storage_id,object_key)",
    ),
    *guard(
        "ms_state_transition",
        "ms_blobs",
        "UPDATE OF state",
        "NEW.state!=OLD.state AND NOT ((OLD.state='publishing' AND NEW.state='ready') "
        "OR (NEW.state='pending_delete' AND EXISTS(SELECT 1 FROM ms_retired WHERE blob_id=OLD.id)))",
        "invalid lifecycle transition",
    ),
    *guard(
        "ms_no_resurrection",
        "ms_blobs",
        "INSERT",
        "EXISTS(SELECT 1 FROM ms_retired WHERE blob_id=NEW.id)",
        "retired IDs cannot be reused",
    ),
    *guard(
        "ms_retire_identity",
        "ms_blobs",
        "DELETE",
        "OLD.state!='pending_delete' OR NOT EXISTS(SELECT 1 FROM ms_retired WHERE blob_id=OLD.id)",
        "retirement requires queued deletion",
    ),
    *guard(
        "ms_retire_object",
        "ms_objects",
        "DELETE",
        "NOT EXISTS(SELECT 1 FROM ms_retired WHERE blob_id=OLD.blob_id AND token=OLD.token)",
        "object association requires retirement",
    ),
    *guard(
        "ms_unrevoked_object",
        "ms_objects",
        "INSERT",
        "EXISTS(SELECT 1 FROM ms_gc WHERE token=NEW.token)",
        "prepared token is revoked",
    ),
    *guard(
        "ms_fresh_object_key",
        "ms_prepared",
        "INSERT",
        "EXISTS(SELECT 1 FROM ms_gc WHERE storage_id=NEW.storage_id AND object_key=NEW.object_key)",
        "object key was queued for cleanup",
    ),
    *guard(
        "ms_gc_identity",
        "ms_gc",
        "UPDATE",
        "NEW.storage_id IS DISTINCT FROM OLD.storage_id OR NEW.object_key IS DISTINCT FROM OLD.object_key "
        "OR NEW.token IS DISTINCT FROM OLD.token OR NEW.blob_id IS DISTINCT FROM OLD.blob_id "
        "OR NEW.reason IS DISTINCT FROM OLD.reason OR (OLD.state='done' AND NEW.state!='done')",
        "cleanup identity is immutable",
    ),
    *guard("ms_gc_retain", "ms_gc", "DELETE", "TRUE", "cleanup history must be retained"),
    *guard("ms_retired_update", "ms_retired", "UPDATE", "TRUE", "retirement identity is immutable"),
    *guard(
        "ms_retired_delete", "ms_retired", "DELETE", "TRUE", "retirement history must be retained"
    ),
]


def publication_guard(schema, *, evolved=False):
    name = POSTGRES.table(schema)
    return guard(
        name + "_ready",
        "ms_blobs",
        "UPDATE OF state",
        f"NEW.schema_name={_literal(schema.name)} AND NEW.state='ready' AND ("
        f"NOT EXISTS(SELECT 1 FROM {q(name)} m WHERE m.id=NEW.id "
        "AND m.schema_version=NEW.schema_version) OR NOT EXISTS(SELECT 1 FROM ms_objects o "
        "JOIN ms_prepared p ON o.token=p.token WHERE o.blob_id=NEW.id "
        f"AND p.schema_name=NEW.schema_name AND p.schema_version{'<=' if evolved else '='}NEW.schema_version))",
        "blob is not ready for publication",
    )


def retirement_guard(schema):
    name = POSTGRES.table(schema)
    return guard(
        name + "_retire",
        name,
        "DELETE",
        "NOT EXISTS(SELECT 1 FROM ms_retired WHERE blob_id=OLD.id)",
        "metadata deletion requires retirement",
    )


def install_group(tx, objects, format_table):
    tx.sql("SELECT singleton FROM ms_format FOR UPDATE")
    if format_table in namespace_objects(tx):
        verify(tx, objects)
        if tx.sql(f"SELECT version FROM {q(format_table)}") != [{"version": 1}]:
            raise SchemaConflictError("Unsupported PostgreSQL catalog extension")
    else:
        if format_table == "ms_payload_format" and tx.sql(
            "SELECT id FROM ms_blobs WHERE state!='publishing' LIMIT 1"
        ):
            raise SchemaConflictError("Cannot adopt ready/retired catalog-only fixtures")
        if format_table == "ms_lifecycle_format" and tx.sql(
            "SELECT id FROM ms_blobs WHERE state='pending_delete' LIMIT 1"
        ):
            raise SchemaConflictError("Resolve pending deletion before installing lifecycle")
        create(tx, objects)
        tx.sql(f"INSERT INTO {q(format_table)} VALUES(1)")


def install_payload(schema, tx):
    install_group(tx, PAYLOAD, "ms_payload_format")
    from .migrations import evolved

    objects = publication_guard(schema, evolved=evolved(tx, schema.name))
    if tx.sql(
        "SELECT 1 FROM ms_payload_schemas WHERE schema_name=%s AND schema_version=%s",
        (schema.name, schema.version),
    ):
        verify(tx, objects)
    else:
        if tx.sql(
            "SELECT id FROM ms_blobs WHERE schema_name=%s AND state!='publishing' LIMIT 1",
            (schema.name,),
        ):
            raise SchemaConflictError("Cannot adopt ready/retired rows from an unenrolled schema")
        create(tx, objects)
        tx.sql("INSERT INTO ms_payload_schemas VALUES(%s,%s)", (schema.name, schema.version))


def install_retirement(schema, tx):
    objects = retirement_guard(schema)
    if tx.sql("SELECT 1 FROM ms_lifecycle_schemas WHERE schema_name=%s", (schema.name,)):
        verify(tx, objects)
    else:
        create(tx, objects)
        tx.sql("INSERT INTO ms_lifecycle_schemas VALUES(%s)", (schema.name,))
