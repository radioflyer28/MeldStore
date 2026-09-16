"""Explicit, resumable metadata evolution using public transactional SQL only."""

import json
from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from .errors import ConflictError, NotFoundError, SchemaConflictError, ValidationError
from .installation import _column, _literal, _objects, _schema_objects, _verify
from .schema import BlobSchema, Field, Index, identifier
from .schema import quote_identifier as q


@dataclass(frozen=True)
class MetadataMigration:
    name: str
    source: BlobSchema
    target: BlobSchema
    transform_id: str
    transform: Callable
    before_ddl: Callable | None = None
    after_ddl: Callable | None = None

    def __post_init__(self):
        identifier(self.name)
        identifier(self.transform_id)
        if not isinstance(self.source, BlobSchema) or not isinstance(self.target, BlobSchema):
            raise ValidationError("Migration requires source and target BlobSchema declarations")
        if self.source.name != self.target.name or self.target.version != self.source.version + 1:
            raise ValidationError("Migration must advance one version of the same schema")
        if (self.source.handlers, self.source.validator_id) != (
            self.target.handlers,
            self.target.validator_id,
        ):
            raise ValidationError("Metadata migration cannot change the payload contract")
        if not callable(self.transform):
            raise ValidationError("transform must be callable")
        if (self.before_ddl is None) != (self.after_ddl is None) or any(
            hook is not None and not callable(hook) for hook in (self.before_ddl, self.after_ddl)
        ):
            raise ValidationError("Provide both callable DDL coordination hooks or neither")


DDL = {
    "ms_metadata_layouts": "CREATE TABLE ms_metadata_layouts (schema_name TEXT NOT NULL PRIMARY KEY, layout_version INTEGER NOT NULL CHECK (layout_version=1))",
    "ms_migrations": """CREATE TABLE ms_migrations (
        name TEXT NOT NULL PRIMARY KEY, schema_name TEXT NOT NULL,
        source_version INTEGER NOT NULL, target_version INTEGER NOT NULL,
        transform_id TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN ('running','complete')),
        total_rows INTEGER NOT NULL, migrated_rows INTEGER NOT NULL DEFAULT 0,
        last_id TEXT, UNIQUE(schema_name,target_version),
        FOREIGN KEY(schema_name,source_version) REFERENCES ms_schemas(name,version),
        FOREIGN KEY(schema_name,target_version) REFERENCES ms_schemas(name,version))""",
    "ms_migrations_active": "CREATE UNIQUE INDEX ms_migrations_active ON ms_migrations(schema_name) WHERE state='running'",
    "ms_migration_steps": """CREATE TABLE ms_migration_steps (
        blob_id TEXT NOT NULL PRIMARY KEY REFERENCES ms_blobs(id),
        migration_name TEXT NOT NULL REFERENCES ms_migrations(name),
        expected_version INTEGER NOT NULL)""",
}
# Registry names are not unique across versions; layouts reference a logical name,
# not one particular registry row. Version membership is enforced by metadata FKs.


def decode(definition):
    data = json.loads(definition)
    data["fields"] = {name: Field(**spec) for name, spec in data["fields"].items()}
    data["indexes"] = tuple(Index(*i["fields"], unique=i["unique"]) for i in data["indexes"])
    if data["validator_id"] is not None:
        data["payload_validator"] = _unavailable_validator
    return BlobSchema(**data)


def _unavailable_validator(_):
    raise ValidationError("A stored declaration does not contain executable validators")


def declarations(tx, name):
    return [
        decode(row["definition"])
        for row in tx.sql(
            "SELECT definition FROM ms_schemas WHERE name=? ORDER BY version", (name,)
        )
    ]


def evolved(tx, name):
    return "ms_metadata_layouts" in _objects(tx) and bool(
        tx.sql("SELECT 1 FROM ms_metadata_layouts WHERE schema_name=?", (name,))
    )


def writable(tx, schema):
    if tx.sql("SELECT MAX(version) AS v FROM ms_schemas WHERE name=?", (schema.name,)) != [
        {"v": schema.version}
    ]:
        raise SchemaConflictError("Old schema versions are read-only after migration starts")


def layout(schemas):
    latest = schemas[-1]
    name, table = latest.table_name, q(latest.table_name)
    union = {}
    for schema in schemas:
        for key, spec in schema.fields.items():
            if key in union and union[key].kind != spec.kind:
                raise SchemaConflictError("Changing a field type requires a new field name")
            union[key] = Field(spec.kind)
    # Catch case-insensitive collisions across otherwise individually valid versions.
    BlobSchema(latest.name, union)
    columns = [
        '"id" TEXT NOT NULL PRIMARY KEY',
        f'"schema_name" TEXT NOT NULL DEFAULT {_literal(latest.name)} CHECK (schema_name={_literal(latest.name)})',
        f'"schema_version" INTEGER NOT NULL CHECK (schema_version IN ({",".join(str(s.version) for s in schemas)}))',
        "\"version\" INTEGER NOT NULL DEFAULT 1 CHECK (typeof(version)='integer' AND version>0)",
        *(_column(key, spec) for key, spec in union.items()),
    ]
    for schema in schemas:
        checks = [f"{q(key)} IS NOT NULL" for key, spec in schema.fields.items() if spec.required]
        checks += [f"{q(key)} IS NULL" for key in union if key not in schema.fields]
        if checks:
            columns.append(f"CHECK (schema_version!={schema.version} OR ({' AND '.join(checks)}))")
    columns.append(
        "FOREIGN KEY(id,schema_name,schema_version) REFERENCES ms_blobs(id,schema_name,schema_version) ON UPDATE NO ACTION ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"
    )
    result = {name: f"CREATE TABLE {table} (" + ", ".join(columns) + ")"}
    immutable = [
        f"NEW.{q(key)} IS NOT OLD.{q(key)}" for key, spec in latest.fields.items() if spec.immutable
    ]
    unchanged = " OR ".join([f"OLD.schema_version!={latest.version}", *immutable])
    step = """EXISTS (SELECT 1 FROM ms_migration_steps s JOIN ms_migrations j ON j.name=s.migration_name
        WHERE s.blob_id=OLD.id AND s.expected_version=OLD.version AND j.state='running'
        AND j.schema_name=OLD.schema_name AND j.source_version=OLD.schema_version
        AND j.target_version=NEW.schema_version)"""
    result[name + "_update"] = f"""CREATE TRIGGER {q(name + "_update")} BEFORE UPDATE ON {table}
        WHEN NEW.id IS NOT OLD.id OR NEW.schema_name IS NOT OLD.schema_name
        OR NEW.version != OLD.version+1
        OR (NEW.schema_version=OLD.schema_version AND ({unchanged}))
        OR (NEW.schema_version!=OLD.schema_version AND NOT ({step}))
        BEGIN SELECT RAISE(ABORT, 'invalid metadata edit or migration'); END"""
    result[name + "_insert"] = f"""CREATE TRIGGER {q(name + "_insert")} BEFORE INSERT ON {table}
        WHEN NEW.version!=1 OR NEW.schema_version!={latest.version}
        BEGIN SELECT RAISE(ABORT, 'insert requires latest schema and initial version'); END"""
    result[name + "_delete"] = f"""CREATE TRIGGER {q(name + "_delete")} BEFORE DELETE ON {table}
        WHEN OLD.schema_version!={latest.version}
        BEGIN SELECT RAISE(ABORT, 'old metadata is frozen for migration'); END"""
    for schema in schemas:
        for number, index in enumerate(schema.indexes):
            key = name + f"_v{schema.version}_i{number}"
            result[key] = (
                f"CREATE {'UNIQUE ' if index.unique else ''}INDEX {q(key)} ON {table} ("
                + ",".join(q(f) for f in index.fields)
                + f") WHERE schema_version={schema.version}"
            )
    return result


def verify(tx, name):
    objects = _objects(tx)
    _verify(objects, DDL)
    _verify(objects, layout(declarations(tx, name)))


def _incoming(tx, table):
    return tx.sql(
        """SELECT m.name, f.id, f.seq, f.[table], f.[from], f.[to],
        f.on_update, f.on_delete, f.match FROM sqlite_master m,
        pragma_foreign_key_list(m.name) f WHERE m.type='table' AND f.[table]=? COLLATE NOCASE
        ORDER BY m.name,f.id,f.seq""",
        (table,),
    )


def _rebuild(tx, plan, old, new):
    table = plan.source.table_name
    if tx.sql(
        "SELECT name FROM sqlite_temp_master WHERE type='trigger' AND tbl_name=? COLLATE NOCASE",
        (table,),
    ):
        raise SchemaConflictError(
            "Remove application TEMP triggers explicitly before metadata rebuild"
        )
    incoming = _incoming(tx, table)
    child_definitions = {row["name"]: _objects(tx)[row["name"]] for row in incoming}
    if incoming and plan.before_ddl is None:
        raise SchemaConflictError(
            "Incoming metadata foreign keys require explicit before_ddl/after_ddl coordination hooks; ms_blobs references need no hooks"
        )
    extras = [
        row["sql"]
        for row in tx.sql(
            "SELECT name,sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name",
            (table,),
        )
        if row["name"] not in old
    ]
    if plan.before_ddl:
        plan.before_ddl(tx)
    if _incoming(tx, table):
        raise SchemaConflictError(
            "Coordination hook must remove incoming metadata foreign keys before rebuild"
        )
    backup = "ms_rebuild_" + uuid4().hex
    tx.sql(f"CREATE TABLE {q(backup)} AS SELECT * FROM {q(table)}")
    columns = [row["name"] for row in tx.sql("SELECT name FROM pragma_table_info(?)", (table,))]
    tx.sql(f"DROP TABLE {q(table)}")
    tx.sql(new[table])
    names = ",".join(q(key) for key in columns)
    tx.sql(f"INSERT INTO {q(table)} ({names}) SELECT {names} FROM {q(backup)}")
    tx.sql(f"DROP TABLE {q(backup)}")
    for key, sql in new.items():
        if key != table:
            tx.sql(sql)
    for sql in extras:
        tx.sql(sql)
    if plan.after_ddl:
        plan.after_ddl(tx)
    if _incoming(tx, table) != incoming:
        raise SchemaConflictError(
            "Coordination hooks did not restore incoming foreign key definitions"
        )
    _verify(_objects(tx), child_definitions)
    _verify(_objects(tx), new)
    if tx.sql("SELECT * FROM pragma_foreign_key_check"):
        raise SchemaConflictError("Foreign key check failed after metadata rebuild")


def status(tx, name):
    if "ms_migrations" not in _objects(tx):
        raise NotFoundError("No metadata migrations installed")
    rows = tx.sql("SELECT * FROM ms_migrations WHERE name=?", (name,))
    if not rows:
        raise NotFoundError(f"No migration: {name}")
    return rows[0]


def _initialize(store, tx, plan):
    from . import payload_catalog

    store.catalog.install_schema(plan.source, tx=tx)
    payload_catalog.install(plan.source, tx)
    store._schema(plan.source, tx)
    objects = _objects(tx)
    if "ms_metadata_layouts" in objects:
        _verify(objects, DDL)
        rows = tx.sql("SELECT * FROM ms_migrations WHERE name=?", (plan.name,))
        if rows:
            job = rows[0]
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
            verify(tx, plan.source.name)
            return job
    else:
        if any(key in objects for key in DDL):
            raise SchemaConflictError("Partial migration extension")
        for sql in DDL.values():
            tx.sql(sql)
    writable(tx, plan.source)
    if tx.sql(
        "SELECT 1 FROM ms_migrations WHERE schema_name=? AND state='running'", (plan.source.name,)
    ):
        raise ConflictError("Finish the active migration first")
    if tx.sql(
        "SELECT 1 FROM ms_blobs WHERE schema_name=? AND state!='ready' LIMIT 1", (plan.source.name,)
    ):
        raise ConflictError("Resolve unpublished or retiring blobs before migrating")
    schemas = declarations(tx, plan.source.name)
    old = layout(schemas) if evolved(tx, plan.source.name) else _schema_objects(plan.source)
    _verify(objects, old)
    new = layout([*schemas, plan.target])
    tx.sql(
        "INSERT INTO ms_schemas VALUES (?,?,?,?)",
        (plan.target.name, plan.target.version, plan.target.table_name, plan.target.definition),
    )
    _rebuild(tx, plan, old, new)
    tx.sql("INSERT OR IGNORE INTO ms_metadata_layouts VALUES (?,1)", (plan.source.name,))
    name, sql = payload_catalog.guard(plan.target, evolved=True)
    tx.sql(f"DROP TRIGGER {q(name)}")
    tx.sql(sql)
    tx.sql("INSERT INTO ms_payload_schemas VALUES (?,?)", (plan.target.name, plan.target.version))
    count = tx.sql(
        f"SELECT COUNT(*) AS n FROM {q(plan.source.table_name)} WHERE schema_version=?",
        (plan.source.version,),
    )[0]["n"]
    tx.sql(
        "INSERT INTO ms_migrations (name,schema_name,source_version,target_version,transform_id,state,total_rows) VALUES (?,?,?,?,?,'running',?)",
        (
            plan.name,
            plan.source.name,
            plan.source.version,
            plan.target.version,
            plan.transform_id,
            count,
        ),
    )
    return status(tx, plan.name)


def _batch(store, tx, plan, batch_size):
    job = status(tx, plan.name)
    if job["state"] == "complete":
        return job
    if tx.sql(
        "SELECT 1 FROM ms_blobs WHERE schema_name=? AND state!='ready' LIMIT 1", (plan.source.name,)
    ):
        raise ConflictError("Resolve unpublished or retiring blobs before resuming")
    table = q(plan.source.table_name)
    # Keep the resume boundary sargable: a nullable-parameter OR makes SQLite
    # scan from the start of the index for every batch.
    boundary = "" if job["last_id"] is None else " AND id>?"
    params = (plan.source.version,)
    if job["last_id"] is not None:
        params += (job["last_id"],)
    rows = tx.sql(
        f"SELECT * FROM {table} WHERE schema_version=?{boundary} ORDER BY id LIMIT ?",
        (*params, batch_size),
    )
    all_fields = {key for schema in declarations(tx, plan.source.name) for key in schema.fields}
    for row in rows:
        inputs = {key: spec.from_sql(row[key]) for key, spec in plan.source.fields.items()}
        values = plan.target.normalize_metadata(plan.transform(inputs))
        values = {key: values.get(key) for key in sorted(all_fields)}
        tx.sql(
            "INSERT INTO ms_migration_steps VALUES (?,?,?)", (row["id"], plan.name, row["version"])
        )
        changed = tx.sql(
            f"UPDATE {table} SET "
            + ",".join(f"{q(key)}=?" for key in values)
            + ("," if values else "")
            + "schema_version=?,version=version+1 WHERE id=? AND schema_version=? AND version=? RETURNING id",
            (*values.values(), plan.target.version, row["id"], plan.source.version, row["version"]),
        )
        if not changed:
            raise ConflictError("Migration row changed unexpectedly")
        tx.sql("UPDATE ms_blobs SET schema_version=? WHERE id=?", (plan.target.version, row["id"]))
        tx.sql("DELETE FROM ms_migration_steps WHERE blob_id=?", (row["id"],))
    last = rows[-1]["id"] if rows else job["last_id"]
    remaining = tx.sql(
        f"SELECT 1 FROM {table} WHERE schema_version=? LIMIT 1", (plan.source.version,)
    )
    if remaining and not rows:
        raise ConflictError("Source rows exist behind the checkpoint; catalog needs inspection")
    tx.sql(
        "UPDATE ms_migrations SET migrated_rows=migrated_rows+?,last_id=?,state=? WHERE name=?",
        (len(rows), last, "running" if remaining else "complete", plan.name),
    )
    return status(tx, plan.name)


class _DryRunRollback(Exception):
    def __init__(self, result):
        self.result = result


def run(store, plan, *, batch_size=100, max_batches=None, dry_run=False):
    store.catalog.require_idle()
    if not isinstance(plan, MetadataMigration):
        raise ValidationError("Expected a MetadataMigration")
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValidationError("batch_size must be between 1 and 1000")
    if max_batches is not None and (type(max_batches) is not int or max_batches < 0):
        raise ValidationError("max_batches must be a nonnegative integer or None")
    if type(dry_run) is not bool or (dry_run and max_batches is not None):
        raise ValidationError("dry_run requires boolean True and no max_batches limit")
    if dry_run:
        try:
            with store.catalog.transaction() as tx:
                result = _initialize(store, tx, plan)
                while result["state"] != "complete":
                    result = _batch(store, tx, plan, batch_size)
                if tx.sql("SELECT * FROM pragma_foreign_key_check"):
                    raise SchemaConflictError("Dry-run foreign key validation failed")
                raise _DryRunRollback(dict(result, dry_run=True))
        except _DryRunRollback as rolled_back:
            return rolled_back.result
    with store.catalog.transaction() as tx:
        result = _initialize(store, tx, plan)
    batches = 0
    while result["state"] != "complete" and (max_batches is None or batches < max_batches):
        with store.catalog.transaction() as tx:
            result = _batch(store, tx, plan, batch_size)
        batches += 1
    return result
