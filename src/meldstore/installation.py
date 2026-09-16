"""Transactional format-1 installation; explicit evolution lives in migrations."""

from .errors import SchemaConflictError, ValidationError
from .schema import BlobSchema
from .schema import quote_identifier as q

FORMAT_VERSION = 1
CORE = {
    "ms_format": """CREATE TABLE ms_format (
        singleton INTEGER NOT NULL PRIMARY KEY CHECK (singleton = 1),
        version INTEGER NOT NULL CHECK (version = 1)
    )""",
    "ms_schemas": """CREATE TABLE ms_schemas (
        name TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version > 0),
        table_name TEXT NOT NULL,
        definition TEXT NOT NULL,
        PRIMARY KEY (name, version)
    )""",
    "ms_blobs": """CREATE TABLE ms_blobs (
        id TEXT NOT NULL PRIMARY KEY CHECK (typeof(id) = 'text' AND length(id) > 0),
        schema_name TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'publishing'
            CHECK (state IN ('publishing', 'ready', 'pending_delete')),
        UNIQUE (id, schema_name, schema_version),
        FOREIGN KEY (schema_name, schema_version) REFERENCES ms_schemas(name, version)
            ON UPDATE RESTRICT ON DELETE RESTRICT
    )""",
    "ms_blob_identity": """CREATE TRIGGER ms_blob_identity BEFORE UPDATE ON ms_blobs
        WHEN NEW.id IS NOT OLD.id OR NEW.schema_name IS NOT OLD.schema_name
        BEGIN SELECT RAISE(ABORT, 'blob identity is immutable'); END""",
}


def _literal(value):
    return "'" + value.replace("'", "''") + "'"


def _objects(tx):
    return {
        row["name"]: row["sql"]
        for row in tx.sql("SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    }


def _verify(objects, expected):
    for name, ddl in expected.items():
        if objects.get(name) != ddl:
            raise SchemaConflictError(f"Missing or modified MeldStore SQL object: {name}")


def _column(name, spec):
    column = q(name)
    sql_type = {
        "text": "TEXT",
        "timestamp": "TEXT",
        "int64": "INTEGER",
        "float": "REAL",
        "boolean": "INTEGER",
    }[spec.kind]
    check = {
        "text": f"typeof({column}) = 'text'",
        "int64": f"typeof({column}) = 'integer'",
        "float": f"typeof({column}) = 'real' AND abs({column}) <= 1.7976931348623157e308",
        "boolean": f"typeof({column}) = 'integer' AND {column} IN (0, 1)",
        "timestamp": f"typeof({column}) = 'text' AND {column} GLOB "
        "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T"
        "[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'",
    }[spec.kind]
    null = " NOT NULL" if spec.required else ""
    return f"{column} {sql_type}{null} CHECK ({column} IS NULL OR ({check}))"


def _schema_objects(schema):
    name = schema.table_name
    table = q(name)
    columns = [
        '"id" TEXT NOT NULL PRIMARY KEY',
        f'"schema_name" TEXT NOT NULL DEFAULT {_literal(schema.name)} '
        f'CHECK ("schema_name" = {_literal(schema.name)})',
        '"schema_version" INTEGER NOT NULL DEFAULT 1 CHECK ("schema_version" = 1)',
        '"version" INTEGER NOT NULL DEFAULT 1 '
        'CHECK (typeof("version") = \'integer\' AND "version" > 0)',
    ]
    columns.extend(_column(n, spec) for n, spec in schema.fields.items())
    columns.append(
        'FOREIGN KEY ("id", "schema_name", "schema_version") '
        "REFERENCES ms_blobs(id, schema_name, schema_version) "
        "ON UPDATE RESTRICT ON DELETE RESTRICT"
    )
    result = {name: f"CREATE TABLE {table} (" + ", ".join(columns) + ")"}
    immutable = ["id", "schema_name", "schema_version"] + [
        n for n, spec in schema.fields.items() if spec.immutable
    ]
    predicate = " OR ".join(f"NEW.{q(n)} IS NOT OLD.{q(n)}" for n in immutable)
    guard = name + "_update"
    result[guard] = f"""CREATE TRIGGER {q(guard)} BEFORE UPDATE ON {table}
        WHEN ({predicate}) OR NEW.version != OLD.version + 1
        BEGIN SELECT RAISE(ABORT, 'immutable field or invalid concurrency version'); END"""
    guard = name + "_insert"
    result[guard] = f"""CREATE TRIGGER {q(guard)} BEFORE INSERT ON {table}
        WHEN NEW.version != 1
        BEGIN SELECT RAISE(ABORT, 'initial concurrency version must be 1'); END"""
    for number, index in enumerate(schema.indexes):
        index_name = name + f"_i{number}"
        unique = "UNIQUE " if index.unique else ""
        result[index_name] = (
            f"CREATE {unique}INDEX {q(index_name)} ON {table} ("
            + ", ".join(q(n) for n in index.fields)
            + ")"
        )
    return result


def install(schema, tx):
    if not isinstance(schema, BlobSchema):
        raise ValidationError("Expected a BlobSchema declaration")
    objects = _objects(tx)
    if "ms_format" not in objects:
        if any(name.lower().startswith("ms_") for name in objects):
            raise SchemaConflictError("Reserved ms_ namespace already contains SQL objects")
        for ddl in CORE.values():
            tx.sql(ddl)
        tx.sql("INSERT INTO ms_format VALUES (1, ?)", (FORMAT_VERSION,))
    else:
        _verify(objects, CORE)
        if tx.sql("SELECT singleton, version FROM ms_format") != [
            {"singleton": 1, "version": FORMAT_VERSION}
        ]:
            raise SchemaConflictError("Unsupported or damaged catalog format")
    existing = tx.sql(
        "SELECT definition, table_name FROM ms_schemas WHERE name = ? AND version = ?",
        (schema.name, schema.version),
    )
    expected = _schema_objects(schema)
    if existing:
        if existing != [{"definition": schema.definition, "table_name": schema.table_name}]:
            raise SchemaConflictError("Schema name/version already has a different definition")
        from . import migrations

        if migrations.evolved(tx, schema.name):
            migrations.verify(tx, schema.name)
        else:
            _verify(objects, expected)
        return schema.table_name
    if schema.version != 1:
        raise SchemaConflictError("Use an explicit MetadataMigration to install a new version")
    if any(name in objects for name in expected):
        raise SchemaConflictError("Schema SQL names already exist")
    tx.sql(
        "INSERT INTO ms_schemas (name, version, table_name, definition) VALUES (?, ?, ?, ?)",
        (schema.name, schema.version, schema.table_name, schema.definition),
    )
    for ddl in expected.values():
        tx.sql(ddl)
    return schema.table_name
