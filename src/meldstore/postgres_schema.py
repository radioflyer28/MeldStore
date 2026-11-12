"""Native PostgreSQL catalog DDL and structural drift inspection.

An installation records engine-normalized layouts, not SQL text guessed from a
deparser. Every verification compares live catalogs to those installation records
and the current generated DDL. These records detect accidental drift; they are
not an authenticity boundary against a database owner rewriting both together.
"""

import json
from dataclasses import dataclass

from .dialect import POSTGRES, postgres_name
from .errors import SchemaConflictError, ValidationError
from .installation import _literal
from .schema import BlobSchema
from .schema import quote_identifier as q

TEXT = 'TEXT COLLATE "C"'


@dataclass(frozen=True)
class Object:
    name: str
    kind: str
    sql: str
    parent: str | None = None


def table(name, columns):
    return Object(name, "table", f"CREATE TABLE {q(name)} ({columns})")


def guard(name, parent, event, condition, message):
    function = postgres_name(name + "_fn")
    body = (
        "BEGIN IF "
        + condition
        + " THEN RAISE EXCEPTION USING ERRCODE = '23514', "
        + "MESSAGE = "
        + _literal(message)
        + "; END IF; RETURN "
        + ("OLD" if event == "DELETE" else "NEW")
        + "; END"
    )
    return [
        Object(
            function,
            "function",
            f"CREATE FUNCTION {q(function)}() RETURNS trigger LANGUAGE plpgsql AS {_literal(body)}",
        ),
        Object(
            name,
            "trigger",
            f"CREATE TRIGGER {q(name)} BEFORE {event} ON {q(parent)} "
            f"FOR EACH ROW EXECUTE FUNCTION {q(function)}()",
            parent,
        ),
    ]


LAYOUT = table(
    "ms_pg_layout", f"name {TEXT} PRIMARY KEY, ddl {TEXT} NOT NULL, shape {TEXT} NOT NULL"
)
CORE = [
    LAYOUT,
    table(
        "ms_format",
        "singleton BIGINT NOT NULL PRIMARY KEY CHECK(singleton=1), "
        "version BIGINT NOT NULL CHECK(version=1)",
    ),
    table(
        "ms_schemas",
        f"name {TEXT} NOT NULL, version BIGINT NOT NULL CHECK(version>0), "
        f"table_name {TEXT} NOT NULL, definition {TEXT} NOT NULL, PRIMARY KEY(name,version)",
    ),
    table(
        "ms_blobs",
        f"id {TEXT} NOT NULL PRIMARY KEY CHECK(length(id)>0), "
        f"schema_name {TEXT} NOT NULL, schema_version BIGINT NOT NULL, "
        f"state {TEXT} NOT NULL DEFAULT 'publishing' "
        "CHECK(state IN ('publishing','ready','pending_delete')), "
        "UNIQUE(id,schema_name,schema_version), FOREIGN KEY(schema_name,schema_version) "
        "REFERENCES ms_schemas(name,version) ON UPDATE RESTRICT ON DELETE RESTRICT",
    ),
    *guard(
        "ms_blob_identity",
        "ms_blobs",
        "UPDATE",
        "NEW.id IS DISTINCT FROM OLD.id OR NEW.schema_name IS DISTINCT FROM OLD.schema_name",
        "blob identity is immutable",
    ),
]


def column(name, spec):
    col = q(name)
    kind = {
        "text": TEXT,
        "timestamp": TEXT,
        "int64": "BIGINT",
        "float": "DOUBLE PRECISION",
        "boolean": "BIGINT",
    }[spec.kind]
    check = {
        "boolean": f"{col} IN (0,1)",
        "float": f"{col} BETWEEN '-1.7976931348623157e308'::float8 AND '1.7976931348623157e308'::float8",
        "timestamp": f"{col} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}T[0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}[.][0-9]{{6}}Z$'",
    }.get(spec.kind)
    return (
        col
        + " "
        + kind
        + (" NOT NULL" if spec.required else "")
        + (f" CHECK({check})" if check else "")
    )


def schema_objects(schema):
    name = POSTGRES.table(schema)
    columns = [
        f"id {TEXT} NOT NULL PRIMARY KEY",
        f"schema_name {TEXT} NOT NULL DEFAULT {_literal(schema.name)} "
        f"CHECK(schema_name={_literal(schema.name)})",
        "schema_version BIGINT NOT NULL DEFAULT 1 CHECK(schema_version=1)",
        "version BIGINT NOT NULL DEFAULT 1 CHECK(version>0)",
        *(column(n, spec) for n, spec in schema.fields.items()),
        "FOREIGN KEY(id,schema_name,schema_version) REFERENCES ms_blobs(id,schema_name,schema_version) "
        "ON UPDATE RESTRICT ON DELETE RESTRICT",
    ]
    immutable = ["id", "schema_name", "schema_version"] + [
        n for n, spec in schema.fields.items() if spec.immutable
    ]
    predicate = " OR ".join(f"NEW.{q(n)} IS DISTINCT FROM OLD.{q(n)}" for n in immutable)
    result = [
        table(name, ", ".join(columns)),
        *guard(
            name + "_update",
            name,
            "UPDATE",
            predicate + " OR NEW.version != OLD.version+1",
            "immutable field or invalid concurrency version",
        ),
        *guard(name + "_insert", name, "INSERT", "NEW.version != 1", "initial version must be 1"),
    ]
    for number, index in enumerate(schema.indexes):
        index_name = postgres_name(name + f"_i{number}")
        result.append(
            Object(
                index_name,
                "index",
                f"CREATE {'UNIQUE ' if index.unique else ''}INDEX {q(index_name)} "
                f"ON {q(name)} (" + ",".join(q(n) for n in index.fields) + ")",
            )
        )
    return result


def namespace_objects(tx):
    """Include functions/triggers as well as relations; reject partial adoption."""
    rows = tx.sql("""SELECT c.relname AS name FROM pg_catalog.pg_class c
        WHERE c.relnamespace=current_schema()::regnamespace
        UNION SELECT p.proname FROM pg_catalog.pg_proc p
        WHERE p.pronamespace=current_schema()::regnamespace
        UNION SELECT t.tgname FROM pg_catalog.pg_trigger t
        JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
        WHERE c.relnamespace=current_schema()::regnamespace AND NOT t.tgisinternal""")
    return {r["name"] for r in rows}


def inspect(tx, obj):
    if obj.kind == "table":
        relation = tx.sql(
            """SELECT relkind, relpersistence, relrowsecurity, relforcerowsecurity,
            relispartition FROM pg_catalog.pg_class
            WHERE relnamespace=current_schema()::regnamespace AND relname=%s""",
            (obj.name,),
        )
        columns = tx.sql(
            """SELECT a.attname AS name, pg_catalog.format_type(a.atttypid,a.atttypmod) AS type,
            a.attnotnull, a.attidentity, a.attgenerated, co.collname AS collation,
            pg_catalog.pg_get_expr(d.adbin,d.adrelid) AS default_value
            FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
            LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            LEFT JOIN pg_catalog.pg_collation co ON co.oid=a.attcollation
            WHERE c.relnamespace=current_schema()::regnamespace AND c.relname=%s
            AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""",
            (obj.name,),
        )
        constraints = tx.sql(
            """SELECT con.conname AS name, con.convalidated,
            pg_catalog.pg_get_constraintdef(con.oid) AS definition
            FROM pg_catalog.pg_constraint con JOIN pg_catalog.pg_class c ON c.oid=con.conrelid
            WHERE c.relnamespace=current_schema()::regnamespace AND c.relname=%s
            ORDER BY con.conname COLLATE "C" """,
            (obj.name,),
        )
        # Constraint indexes and internal FK triggers must still be usable/enabled.
        indexes = tx.sql(
            """SELECT i.relname AS name, x.indisvalid, x.indisready, x.indislive,
            pg_catalog.pg_get_indexdef(i.oid) AS definition
            FROM pg_catalog.pg_index x JOIN pg_catalog.pg_class c ON c.oid=x.indrelid
            JOIN pg_catalog.pg_class i ON i.oid=x.indexrelid
            JOIN pg_catalog.pg_constraint con ON con.conindid=i.oid AND con.conrelid=c.oid
            WHERE c.relnamespace=current_schema()::regnamespace AND c.relname=%s
            ORDER BY i.relname COLLATE "C" """,
            (obj.name,),
        )
        fk = tx.sql(
            """SELECT con.conname AS name, t.tgtype, t.tgenabled,
            pg_catalog.pg_get_triggerdef(t.oid) AS definition
            FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
            JOIN pg_catalog.pg_constraint con ON con.oid=t.tgconstraint
            WHERE c.relnamespace=current_schema()::regnamespace AND c.relname=%s
            AND t.tgisinternal ORDER BY con.conname COLLATE "C",t.tgtype""",
            (obj.name,),
        )
        # Internal trigger OID-derived names are not stable across restore.
        for row in fk:
            row.pop("definition")
        return {
            "relation": relation,
            "columns": columns,
            "constraints": constraints,
            "indexes": indexes,
            "foreign_keys": fk,
        }
    if obj.kind == "index":
        return tx.sql(
            """SELECT pg_catalog.pg_get_indexdef(c.oid) AS definition,
            i.indisvalid, i.indisready, i.indislive FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_index i ON i.indexrelid=c.oid
            WHERE c.relnamespace=current_schema()::regnamespace AND c.relname=%s""",
            (obj.name,),
        )
    if obj.kind == "function":
        return tx.sql(
            """SELECT p.prosrc,p.prosecdef,p.provolatile,p.proisstrict,p.proleakproof,
            p.proconfig, l.lanname, pg_catalog.format_type(p.prorettype,NULL) AS return_type
            FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang
            WHERE p.pronamespace=current_schema()::regnamespace AND p.proname=%s
            AND p.pronargs=0""",
            (obj.name,),
        )
    return tx.sql(
        """SELECT pg_catalog.pg_get_triggerdef(t.oid) AS definition, t.tgenabled
        FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
        WHERE c.relnamespace=current_schema()::regnamespace AND c.relname=%s
        AND t.tgname=%s AND NOT t.tgisinternal""",
        (obj.parent, obj.name),
    )


def _matches(actual, expected, obj):
    if obj.kind != "table":
        return actual == expected
    # Applications may add constraints and indexes, but never remove ours.
    for key in ("constraints", "indexes", "foreign_keys"):
        if any(row not in actual[key] for row in expected[key]):
            return False
    return all(actual[k] == expected[k] for k in ("relation", "columns"))


def verify(tx, objects):
    for obj in objects:
        rows = tx.sql("SELECT ddl,shape FROM ms_pg_layout WHERE name=%s", (obj.name,))
        if len(rows) != 1 or rows[0]["ddl"] != obj.sql:
            raise SchemaConflictError(f"Missing or modified PostgreSQL layout: {obj.name}")
        try:
            expected = json.loads(rows[0]["shape"])
            matches = _matches(inspect(tx, obj), expected, obj)
        except (KeyError, TypeError, ValueError):
            matches = False
        if not matches:
            raise SchemaConflictError(f"Missing or modified PostgreSQL SQL object: {obj.name}")


def create(tx, objects):
    occupied = namespace_objects(tx)
    names = [obj.name for obj in objects]
    if len(set(names)) != len(names) or occupied.intersection(names):
        raise SchemaConflictError("PostgreSQL SQL name collision or partial installation")
    for obj in objects:
        tx.sql(obj.sql.replace("%", "%%"))
    # Capture after the complete group: later FKs add internal triggers to parents.
    for obj in objects:
        tx.sql(
            "INSERT INTO ms_pg_layout VALUES(%s,%s,%s)",
            (obj.name, obj.sql, json.dumps(inspect(tx, obj), sort_keys=True)),
        )


def install(schema, tx):
    if not isinstance(schema, BlobSchema):
        raise ValidationError("Expected a BlobSchema declaration")
    for name in schema.fields:
        POSTGRES.quote(name)  # Preflight before any DDL; never let PostgreSQL truncate.
    names = namespace_objects(tx)
    if "ms_format" not in names:
        if any(n.lower().startswith("ms_") for n in names):
            raise SchemaConflictError("Reserved ms_ namespace already contains SQL objects")
        create(tx, CORE)
        tx.sql("INSERT INTO ms_format VALUES(1,1)")
    else:
        if "ms_pg_layout" not in names:
            raise SchemaConflictError("Missing PostgreSQL layout registry")
        # Serialize concurrent schema installers without changing session settings.
        tx.sql("SELECT singleton FROM ms_format FOR UPDATE")
        verify(tx, CORE)
        if tx.sql("SELECT singleton,version FROM ms_format") != [{"singleton": 1, "version": 1}]:
            raise SchemaConflictError("Unsupported or damaged catalog format")
    name = POSTGRES.table(schema)
    expected = schema_objects(schema)
    rows = tx.sql(
        "SELECT table_name,definition FROM ms_schemas WHERE name=%s AND version=%s",
        (schema.name, schema.version),
    )
    if rows:
        if rows != [{"table_name": name, "definition": schema.definition}]:
            raise SchemaConflictError("Schema name/version already has a different definition")
        from .postgres_migrations import verify_schema

        verify_schema(tx, schema.name)
        return name
    if schema.version != 1:
        raise SchemaConflictError("Use an explicit MetadataMigration to install a new version")
    if tx.sql("SELECT name FROM ms_schemas WHERE table_name=%s", (name,)):
        raise SchemaConflictError("Schema SQL table name collision")
    create(tx, expected)
    tx.sql(
        "INSERT INTO ms_schemas VALUES(%s,%s,%s,%s)",
        (schema.name, schema.version, name, schema.definition),
    )
    return name
