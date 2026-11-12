"""In-place PostgreSQL metadata evolution; no table replacement or payload I/O."""

import json

from . import migrations
from .dialect import POSTGRES, postgres_name
from .errors import ConflictError, SchemaConflictError
from .installation import _literal
from .postgres_extensions import publication_guard
from .postgres_schema import (
    TEXT,
    Object,
    create,
    guard,
    inspect,
    namespace_objects,
    schema_objects,
    table,
    verify,
)
from .schema import BlobSchema, Field
from .schema import quote_identifier as q

DDL = [
    table(
        "ms_metadata_layouts",
        f"schema_name {TEXT} NOT NULL PRIMARY KEY, "
        "layout_version BIGINT NOT NULL CHECK(layout_version=1)",
    ),
    table(
        "ms_migrations",
        f"name {TEXT} NOT NULL PRIMARY KEY, schema_name {TEXT} NOT NULL, "
        f"source_version BIGINT NOT NULL, target_version BIGINT NOT NULL, transform_id {TEXT} NOT NULL, "
        f"state {TEXT} NOT NULL CHECK(state IN ('running','complete')), "
        f"total_rows BIGINT NOT NULL, migrated_rows BIGINT NOT NULL DEFAULT 0, last_id {TEXT}, "
        "UNIQUE(schema_name,target_version), FOREIGN KEY(schema_name,source_version) "
        "REFERENCES ms_schemas(name,version), FOREIGN KEY(schema_name,target_version) "
        "REFERENCES ms_schemas(name,version)",
    ),
    Object(
        "ms_migrations_active",
        "index",
        "CREATE UNIQUE INDEX ms_migrations_active "
        "ON ms_migrations(schema_name) WHERE state='running'",
    ),
    table(
        "ms_migration_steps",
        f"blob_id {TEXT} NOT NULL PRIMARY KEY REFERENCES ms_blobs(id), "
        f"migration_name {TEXT} NOT NULL REFERENCES ms_migrations(name), expected_version BIGINT NOT NULL",
    ),
]


def union_fields(schemas):
    fields = {}
    for schema in schemas:
        for name, spec in schema.fields.items():
            POSTGRES.quote(name)
            if name in fields and fields[name].kind != spec.kind:
                raise SchemaConflictError("Changing a field type requires a new field name")
            fields[name] = Field(spec.kind)
    BlobSchema(schemas[-1].name, fields)  # Catch collisions across versions.
    return fields


def field_type(spec):
    return {
        "text": TEXT,
        "timestamp": TEXT,
        "int64": "BIGINT",
        "float": "DOUBLE PRECISION",
        "boolean": "BIGINT",
    }[spec.kind]


def checks(schemas):
    latest = schemas[-1]
    fields = union_fields(schemas)
    name = POSTGRES.table(latest)
    exprs = [
        f"schema_name={_literal(latest.name)}",
        "version>0",
        "schema_version IN (" + ",".join(str(s.version) for s in schemas) + ")",
    ]
    for key, spec in fields.items():
        col = q(key)
        if spec.kind == "boolean":
            exprs.append(f"{col} IN (0,1)")
        elif spec.kind == "float":
            exprs.append(
                f"{col} BETWEEN '-1.7976931348623157e308'::float8 AND '1.7976931348623157e308'::float8"
            )
        elif spec.kind == "timestamp":
            exprs.append(
                f"{col} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}T[0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}[.][0-9]{{6}}Z$'"
            )
    for schema in schemas:
        terms = [f"{q(key)} IS NOT NULL" for key, spec in schema.fields.items() if spec.required]
        terms += [f"{q(key)} IS NULL" for key in fields if key not in schema.fields]
        if terms:
            exprs.append(f"schema_version!={schema.version} OR (" + " AND ".join(terms) + ")")
    result = {
        postgres_name(name + f"_check_{i}"): f"CHECK ({expr})" for i, expr in enumerate(exprs)
    }
    result[postgres_name(name + "_blob_fk")] = (
        "FOREIGN KEY(id,schema_name,schema_version) REFERENCES ms_blobs(id,schema_name,schema_version) "
        "ON UPDATE NO ACTION ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"
    )
    return result


def layout(schemas):
    latest = schemas[-1]
    name = POSTGRES.table(latest)
    fields = union_fields(schemas)
    columns = [
        f"id {TEXT} NOT NULL PRIMARY KEY",
        f"schema_name {TEXT} NOT NULL DEFAULT {_literal(latest.name)}",
        "schema_version BIGINT NOT NULL",
        "version BIGINT NOT NULL DEFAULT 1",
    ]
    columns += [f"{q(key)} {field_type(spec)}" for key, spec in fields.items()]
    columns += [f"CONSTRAINT {q(key)} {definition}" for key, definition in checks(schemas).items()]
    immutable = [
        f"NEW.{q(key)} IS DISTINCT FROM OLD.{q(key)}"
        for key, spec in latest.fields.items()
        if spec.immutable
    ]
    unchanged = " OR ".join([f"OLD.schema_version!={latest.version}", *immutable])
    step = """EXISTS(SELECT 1 FROM ms_migration_steps s JOIN ms_migrations j ON j.name=s.migration_name
        WHERE s.blob_id=OLD.id AND s.expected_version=OLD.version AND j.state='running'
        AND j.schema_name=OLD.schema_name AND j.source_version=OLD.schema_version
        AND j.target_version=NEW.schema_version)"""
    result = [
        table(name, ", ".join(columns)),
        *guard(
            name + "_update",
            name,
            "UPDATE",
            "NEW.id IS DISTINCT FROM OLD.id OR NEW.schema_name IS DISTINCT FROM OLD.schema_name "
            "OR NEW.version!=OLD.version+1 "
            f"OR (NEW.schema_version=OLD.schema_version AND ({unchanged})) "
            f"OR (NEW.schema_version!=OLD.schema_version AND NOT ({step}))",
            "invalid metadata edit or migration",
        ),
        *guard(
            name + "_insert",
            name,
            "INSERT",
            f"NEW.version!=1 OR NEW.schema_version!={latest.version}",
            "insert requires latest schema and initial version",
        ),
        *guard(
            name + "_delete",
            name,
            "DELETE",
            f"OLD.schema_version!={latest.version}",
            "old metadata is frozen",
        ),
    ]
    for schema in schemas:
        for i, index in enumerate(schema.indexes):
            key = postgres_name(name + f"_v{schema.version}_i{i}")
            result.append(
                Object(
                    key,
                    "index",
                    f"CREATE {'UNIQUE ' if index.unique else ''}INDEX {q(key)} "
                    f"ON {q(name)} ("
                    + ",".join(q(f) for f in index.fields)
                    + f") WHERE schema_version={schema.version}",
                )
            )
    return result


def verify_schema(tx, name):
    if migrations.evolved(tx, name):
        verify(tx, DDL)
        if tx.sql(
            "SELECT layout_version FROM ms_metadata_layouts WHERE schema_name=%s", (name,)
        ) != [{"layout_version": 1}]:
            raise SchemaConflictError("Unsupported metadata layout")
        verify(tx, layout(migrations.declarations(tx, name)))
    else:
        verify(tx, schema_objects(migrations.declarations(tx, name)[0]))


def drop_objects(tx, objects):
    for kind in ("trigger", "index", "function"):
        for obj in objects:
            if obj.kind != kind:
                continue
            statement = f"DROP {kind.upper()} {q(obj.name)}"
            if kind == "trigger":
                statement += f" ON {q(obj.parent)}"
            elif kind == "function":
                statement += "()"
            tx.sql(statement.replace("%", "%%"))
            tx.sql("DELETE FROM ms_pg_layout WHERE name=%s", (obj.name,))


def incoming(tx, name):
    return tx.sql(
        """SELECT n.nspname AS schema,c.relname AS table_name,k.conname AS name,
        pg_catalog.pg_get_constraintdef(k.oid) AS definition,k.convalidated
        FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_catalog.pg_class p ON p.oid=k.confrelid
        WHERE p.relnamespace=current_schema()::regnamespace AND p.relname=%s
        ORDER BY n.nspname,c.relname,k.conname""",
        (name,),
    )


def evolve(tx, plan, schemas, old):
    name = POSTGRES.table(plan.source)
    new_schemas = [*schemas, plan.target]
    new = layout(new_schemas)
    old_shape = json.loads(
        tx.sql("SELECT shape FROM ms_pg_layout WHERE name=%s", (name,))[0]["shape"]
    )
    refs = incoming(tx, name)
    if plan.before_ddl:
        plan.before_ddl(tx)
        verify(tx, old)
    # Preserve the table and primary key, hence ordinary incoming ID foreign keys.
    # Never CASCADE; dependencies on an owned index must be coordinated explicitly.
    drop_objects(tx, old)
    for item in old_shape["constraints"]:
        if item["definition"].startswith(("CHECK", "FOREIGN KEY")):
            tx.sql(f"ALTER TABLE {q(name)} DROP CONSTRAINT {q(item['name'])}".replace("%", "%%"))
    old_fields = union_fields(schemas)
    for key, spec in union_fields(new_schemas).items():
        if key not in old_fields:
            sql = f"ALTER TABLE {q(name)} ADD COLUMN {q(key)} {field_type(spec)}"
        else:
            sql = f"ALTER TABLE {q(name)} ALTER COLUMN {q(key)} DROP NOT NULL"
        tx.sql(sql.replace("%", "%%"))
    tx.sql(f"ALTER TABLE {q(name)} ALTER COLUMN schema_version DROP DEFAULT")
    for key, definition in checks(new_schemas).items():
        tx.sql(f"ALTER TABLE {q(name)} ADD CONSTRAINT {q(key)} {definition}".replace("%", "%%"))
    create(tx, new[1:])
    shape = inspect(tx, new[0])
    if plan.after_ddl:
        plan.after_ddl(tx)
    if incoming(tx, name) != refs:
        raise SchemaConflictError(
            "Coordination hooks did not restore incoming foreign key definitions"
        )
    # Do not adopt application constraints as owned layout during the refresh.
    own = set(checks(new_schemas)) | {
        item["name"]
        for item in old_shape["constraints"]
        if item["definition"].startswith("PRIMARY KEY")
    }
    for category in ("constraints", "foreign_keys"):
        shape[category] = [item for item in shape[category] if item["name"] in own]
    shape["indexes"] = old_shape["indexes"]
    tx.sql(
        "UPDATE ms_pg_layout SET ddl=%s,shape=%s WHERE name=%s",
        (new[0].sql, json.dumps(shape, sort_keys=True), name),
    )
    verify(tx, new)


def initialize(store, tx, plan):
    from . import payload_catalog

    # One installer at a time; DDL gets a native lock before validation/readback.
    tx.sql("SELECT singleton FROM ms_format FOR UPDATE")
    name = POSTGRES.table(plan.source)
    tx.sql(f"LOCK TABLE {q(name)} IN ACCESS EXCLUSIVE MODE")
    store.catalog.install_schema(plan.source, tx=tx)
    payload_catalog.install(plan.source, tx)
    store._schema(plan.source, tx)
    names = namespace_objects(tx)
    if "ms_metadata_layouts" in names:
        verify(tx, DDL)
        jobs = tx.sql("SELECT * FROM ms_migrations WHERE name=%s", (plan.name,))
        if jobs:
            job = jobs[0]
            if (
                job["schema_name"],
                job["source_version"],
                job["target_version"],
                job["transform_id"],
            ) != (plan.source.name, plan.source.version, plan.target.version, plan.transform_id):
                raise SchemaConflictError(
                    "Migration name already identifies another transformation"
                )
            store._schema(plan.target, tx)
            verify_schema(tx, plan.source.name)
            return job
    else:
        create(tx, DDL)
    migrations.writable(tx, plan.source)
    if tx.sql(
        "SELECT 1 FROM ms_migrations WHERE schema_name=%s AND state='running'", (plan.source.name,)
    ):
        raise ConflictError("Finish the active migration first")
    if tx.sql(
        "SELECT 1 FROM ms_blobs WHERE schema_name=%s AND state!='ready' LIMIT 1",
        (plan.source.name,),
    ):
        raise ConflictError("Resolve unpublished or retiring blobs before migrating")
    schemas = migrations.declarations(tx, plan.source.name)
    old = (
        layout(schemas) if migrations.evolved(tx, plan.source.name) else schema_objects(plan.source)
    )
    verify(tx, old)
    layout([*schemas, plan.target])  # Preflight incompatible fields before registry mutation.
    tx.sql(
        "INSERT INTO ms_schemas VALUES(%s,%s,%s,%s)",
        (plan.target.name, plan.target.version, name, plan.target.definition),
    )
    evolve(tx, plan, schemas, old)
    old_guard = publication_guard(plan.source, evolved=migrations.evolved(tx, plan.source.name))
    verify(tx, old_guard)
    drop_objects(tx, old_guard)
    create(tx, publication_guard(plan.target, evolved=True))
    tx.sql(
        "INSERT INTO ms_metadata_layouts VALUES(%s,1) ON CONFLICT DO NOTHING", (plan.source.name,)
    )
    tx.sql("INSERT INTO ms_payload_schemas VALUES(%s,%s)", (plan.target.name, plan.target.version))
    count = tx.sql(
        f"SELECT COUNT(*) AS n FROM {q(name)} WHERE schema_version=%s", (plan.source.version,)
    )[0]["n"]
    tx.sql(
        "INSERT INTO ms_migrations(name,schema_name,source_version,target_version,transform_id,state,total_rows) "
        "VALUES(%s,%s,%s,%s,%s,'running',%s)",
        (
            plan.name,
            plan.source.name,
            plan.source.version,
            plan.target.version,
            plan.transform_id,
            count,
        ),
    )
    return migrations.status(tx, plan.name)
