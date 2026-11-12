"""SQL-only parity fixtures. No payloads are claimed to exist by these fixtures."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

from meldstore import (
    BlobSchema,
    Boolean,
    Catalog,
    ConflictError,
    ConstraintError,
    Float,
    Index,
    Integer,
    Order,
    Predicate,
    SchemaConflictError,
    Text,
    Timestamp,
    ValidationError,
    encodings,
    lifecycle,
    metadata,
    payload_catalog,
    query,
)
from meldstore.dialect import for_transaction
from meldstore.postgres_schema import namespace_objects


@pytest.fixture(params=["sqlite", "melddb", "postgres"])
def catalog(request, tmp_path, pg):
    catalog = (
        Catalog.connect_postgres(pg.dsn)
        if request.param == "postgres"
        else Catalog(tmp_path / "catalog.db", adapter=request.param)
    )
    with catalog:
        yield catalog


@pytest.fixture
def schema():
    return BlobSchema(
        "測定 'data",
        {
            "label": Text(required=True),
            "number": Integer(),
            "score": Float(),
            "at": Timestamp(),
            "fixed": Text(immutable=True),
            "ok": Boolean(),
        },
        indexes=(Index("label", "at"),),
    )


def seed(catalog, schema, id, **values):
    with catalog.transaction() as tx:
        d = for_transaction(tx)
        b = d.bind
        values = schema.normalize_metadata({"label": "a", **values})
        tx.sql(
            f"INSERT INTO ms_blobs(id,schema_name,schema_version,state) VALUES({b},{b},1,'ready')",
            (id, schema.name),
        )
        tx.sql(
            f"INSERT INTO {d.quote(d.table(schema))}(id,"
            + ",".join(d.quote(n) for n in values)
            + ") VALUES("
            + ",".join(b for _ in range(len(values) + 1))
            + ")",
            (id, *values.values()),
        )


def find(catalog, schema, **kwargs):
    args = dict(where=None, predicates=(), order_by=(), after=None, limit=100)
    args.update(kwargs)
    with catalog.transaction(write=False) as tx:
        d = for_transaction(tx)
        sql, params = query.compile_query(schema, dialect=d, **args)
        records = []
        for row in tx.sql(sql, params):
            fields = tx.sql(
                f"SELECT * FROM {d.quote(d.table(schema))} WHERE id={d.bind}", (row["id"],)
            )[0]
            records.append(
                {
                    "id": row["id"],
                    "schema_name": schema.name,
                    "schema_version": 1,
                    "version": fields["version"],
                    "metadata": {n: fields[n] for n in schema.fields},
                }
            )
        return records


def edit(catalog, schema, id, expected=1, **values):
    with catalog.transaction() as tx:
        with tx.operation():
            metadata.update(tx, id, schema, metadata.changes(schema, values, expected), expected)


def test_scalar_utc_and_predicate_parity(catalog, schema):
    catalog.install_schema(schema)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    seed(catalog, schema, "one", at=start, number=-(2**63), score=1.5, ok=True)
    seed(catalog, schema, "two", at=start + timedelta(microseconds=1), number=2**63 - 1, ok=False)
    seed(catalog, schema, "null")
    rows = find(
        catalog,
        schema,
        predicates=[
            Predicate("at", "gte", start.astimezone(timezone(timedelta(hours=-5)))),
            Predicate("at", "lt", start + timedelta(microseconds=1)),
            Predicate("number", "in", [-(2**63), 2**63 - 1]),
            Predicate("ok", "eq", True),
        ],
    )
    assert [r["id"] for r in rows] == ["one"]
    assert rows[0]["metadata"]["at"] == "2026-01-01T00:00:00.000000Z"
    assert type(rows[0]["metadata"]["ok"]) is int
    assert [r["id"] for r in find(catalog, schema, predicates=[Predicate("at", "is_null")])] == [
        "null"
    ]
    for field, value in [
        ("number", True),
        ("score", float("nan")),
        ("at", start.replace(tzinfo=None)),
    ]:
        with pytest.raises(ValidationError):
            find(catalog, schema, where={field: value})


@pytest.mark.parametrize(
    "descending,expected", [(False, ["a", "b", "c", "d"]), (True, ["d", "b", "c", "a"])]
)
def test_null_tie_and_cursor_parity(catalog, schema, descending, expected):
    catalog.install_schema(schema)
    for id, number in zip("abcd", [None, 1, 1, 2]):
        seed(catalog, schema, id, number=number)
    order = (Order("number", descending),)
    ids, after = [], None
    for _ in range(6):
        page = find(catalog, schema, order_by=order, limit=1, after=after)
        if not page:
            break
        ids.append(page[0]["id"])
        after = query.cursor(page[0], schema, order)
    assert ids == expected


def test_bytewise_text_order(catalog, schema):
    catalog.install_schema(schema)
    labels = ["ä", "Z", "a", "é", "A", "z"]
    for i, label in enumerate(labels):
        seed(catalog, schema, str(i), label=label)
    rows = find(catalog, schema, order_by=(Order("label"),))
    assert [r["metadata"]["label"] for r in rows] == sorted(labels)


def test_install_guards_versions_and_foreign_keys(catalog, schema):
    name = catalog.install_schema(schema)
    assert name == schema.table_name_for(catalog.adapter)
    assert catalog.install_schema(schema) == name
    seed(catalog, schema, "one", fixed="immutable")
    catalog.sql("CREATE TABLE app_reference(id TEXT PRIMARY KEY REFERENCES ms_blobs(id))")
    catalog.sql("INSERT INTO app_reference VALUES('one')")
    edit(catalog, schema, "one", number=2)
    assert find(catalog, schema)[0]["version"] == 2
    with pytest.raises(ConflictError):
        edit(catalog, schema, "one", number=3)
    with pytest.raises(ConstraintError):
        catalog.sql("INSERT INTO app_reference VALUES('missing')")
    for assignment in [
        "number=3",
        "fixed='changed',version=version+1",
        "version=0",
        "schema_version=2,version=version+1",
        "id='other',version=version+1",
    ]:
        with pytest.raises(ConstraintError):
            catalog.sql(f'UPDATE "{name}" SET {assignment}')
    with pytest.raises(ConstraintError):
        with catalog.transaction() as tx:
            tx.sql(f'DELETE FROM "{name}"')
            tx.sql("DELETE FROM ms_blobs")
    assert find(catalog, schema)[0]["version"] == 2
    with pytest.raises(SchemaConflictError):
        catalog.install_schema(BlobSchema(schema.name, {}))
    with pytest.raises(SchemaConflictError):
        catalog.install_schema(BlobSchema(schema.name, {}, version=2))
    # Application tables and additive indexes are retained by inspection.
    catalog.sql(f'CREATE INDEX app_index ON "{name}"(number)')
    assert catalog.install_schema(schema) == name


def test_sql_scalar_constraints(catalog, schema):
    table = catalog.install_schema(schema)
    seed(catalog, schema, "one")
    for assignment in ["ok=2", "at='bad'", "label=NULL"]:
        with pytest.raises(ConstraintError):
            catalog.sql(f'UPDATE "{table}" SET {assignment},version=version+1')


def test_atomic_install(catalog, schema):
    with pytest.raises(RuntimeError):
        with catalog.transaction() as tx:
            catalog.install_schema(schema, tx=tx)
            tx.sql("CREATE TABLE app_rollback(id INTEGER)")
            raise RuntimeError("rollback")
    assert catalog.install_schema(schema) == schema.table_name_for(catalog.adapter)


def test_quoted_and_long_schema_names(catalog):
    field = 'select"%s; DROP TABLE ms_blobs;--'
    for name in ["表'\"%s;--", "x" * 48, "x" * 47 + "y"]:
        schema = BlobSchema(name, {field: Text()}, indexes=(Index(field),))
        table = catalog.install_schema(schema)
        assert len(table.encode()) <= (63 if catalog.adapter == "postgres" else 128)
        assert catalog.install_schema(schema) == table
        with catalog.transaction() as tx:
            d = for_transaction(tx)
            tx.sql(
                f"INSERT INTO ms_blobs(id,schema_name,schema_version,state) VALUES({d.bind},{d.bind},1,'ready')",
                (name, name),
            )
            tx.sql(
                f"INSERT INTO {d.quote(table)}(id,{d.quote(field)}) VALUES({d.bind},{d.bind})",
                (name, "question? percent%s quote'"),
            )
        assert find(catalog, schema, where={field: "question? percent%s quote'"})[0]["id"] == name


@pytest.mark.parametrize(
    "mutation",
    [
        "DROP INDEX {table}_i0",
        "ALTER TABLE {table} ALTER COLUMN label DROP NOT NULL",
        'ALTER TABLE {table} ALTER COLUMN label TYPE TEXT COLLATE "POSIX"',
        "ALTER TABLE {table} DISABLE TRIGGER {table}_update",
        "ALTER TABLE {table} DISABLE TRIGGER ALL",
        "ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE {table} DROP CONSTRAINT {table}_version_check",
        "ALTER TABLE {table} ADD COLUMN extra TEXT",
    ],
)
def test_postgres_detects_live_drift(pg, schema, mutation):
    with Catalog.connect_postgres(pg.dsn) as catalog:
        name = catalog.install_schema(schema)
        catalog.sql(mutation.format(table=name))
        with pytest.raises(SchemaConflictError):
            catalog.install_schema(schema)


def test_postgres_detects_function_drift(pg, schema):
    from meldstore.dialect import postgres_name

    with Catalog.connect_postgres(pg.dsn) as catalog:
        name = catalog.install_schema(schema)
        function = postgres_name(name + "_update_fn")
        catalog.sql(
            f"CREATE OR REPLACE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql "
            "AS 'BEGIN RETURN NEW; END'"
        )
        with pytest.raises(SchemaConflictError):
            catalog.install_schema(schema)


def test_postgres_identifier_preflight_and_collision(pg, monkeypatch):
    with Catalog.connect_postgres(pg.dsn) as catalog:
        for field in ["x" * 64, "é" * 32]:
            with pytest.raises(ValidationError, match="63 UTF-8"):
                catalog.install_schema(BlobSchema("long", {field: Text()}))
        with catalog.transaction(write=False) as tx:
            assert not namespace_objects(tx)
        monkeypatch.setattr("meldstore.dialect.postgres_name", lambda _: "ms_collision")
        catalog.install_schema(BlobSchema("one", {}))
        with pytest.raises(SchemaConflictError):
            catalog.install_schema(BlobSchema("two", {}))


def test_postgres_rejects_nonfinite_float(pg, schema):
    with Catalog.connect_postgres(pg.dsn) as catalog:
        name = catalog.install_schema(schema)
        seed(catalog, schema, "one")
        for value in [float("inf"), float("-inf"), float("nan")]:
            with pytest.raises(ConstraintError):
                catalog.sql(f'UPDATE "{name}" SET score=%s,version=version+1', (value,))
        with pytest.raises(ValidationError, match="NUL"):
            find(catalog, schema, where={"label": "bad\x00text"})


def test_postgres_competing_guarded_edits(pg, schema):
    with Catalog.connect_postgres(pg.dsn) as catalog:
        catalog.install_schema(schema)
        seed(catalog, schema, "one")
    barrier = Barrier(2)

    def writer(value):
        with Catalog.connect_postgres(pg.dsn) as catalog:
            barrier.wait(timeout=10)
            try:
                edit(catalog, schema, "one", number=value)
                return "updated"
            except ConflictError:
                return "conflict"

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(writer, [1, 2])) == ["conflict", "updated"]
    with Catalog.connect_postgres(pg.dsn) as catalog:
        assert find(catalog, schema)[0]["version"] == 2


def test_extension_ddl_and_publication_guards(catalog, schema):
    with catalog.transaction() as tx:
        catalog.install_schema(schema, tx=tx)
        payload_catalog.install(schema, tx)
        encodings.install(tx)
        lifecycle.install(tx)
        lifecycle.install_schema(schema, tx)
    with catalog.transaction() as tx:
        catalog.install_schema(schema, tx=tx)
        payload_catalog.install(schema, tx)
        encodings.install(tx)
        lifecycle.install(tx)
        lifecycle.install_schema(schema, tx)
    with pytest.raises(ConstraintError):
        seed(catalog, schema, "unprepared")
    with catalog.transaction() as tx:
        d = for_transaction(tx)
        tx.sql(
            f"INSERT INTO ms_blobs(id,schema_name,schema_version) VALUES('one',{d.bind},1)",
            (schema.name,),
        )
    with pytest.raises(ConstraintError):
        catalog.sql("UPDATE ms_blobs SET state='ready' WHERE id='one'")
    with pytest.raises(ConstraintError):
        catalog.sql("DELETE FROM ms_blobs WHERE id='one'")


def test_encoding_and_retirement_sql_guards(catalog, schema):
    with catalog.transaction() as tx:
        catalog.install_schema(schema, tx=tx)
        payload_catalog.install(schema, tx)
        encodings.install(tx)
        lifecycle.install(tx)
        lifecycle.install_schema(schema, tx)
        d = for_transaction(tx)
        b = d.bind
        tx.sql("INSERT INTO ms_encodings VALUES('token','example.format',1,'{}')")
        tx.sql(
            "INSERT INTO ms_prepared VALUES(" + ",".join(b for _ in range(10)) + ")",
            (
                "token",
                "synthetic-storage",
                "synthetic-key",
                0,
                "0" * 32,
                "xxh3_128",
                "file",
                1,
                schema.name,
                1,
            ),
        )
        tx.sql(
            f"INSERT INTO ms_blobs(id,schema_name,schema_version) VALUES('one',{b},1)",
            (schema.name,),
        )
        tx.sql(f"INSERT INTO {d.quote(d.table(schema))}(id,label) VALUES('one','a')")
        tx.sql("INSERT INTO ms_objects VALUES('one','token')")
        tx.sql("UPDATE ms_blobs SET state='ready' WHERE id='one'")
    name = schema.table_name_for(catalog.adapter)
    for statement in [
        "UPDATE ms_prepared SET object_key='other'",
        "UPDATE ms_objects SET token='other'",
        "UPDATE ms_blobs SET state='publishing'",
        "DELETE FROM ms_objects",
        "DELETE FROM ms_encodings",
        "UPDATE ms_encodings SET encoding_version=2",
        f'DELETE FROM "{name}"',
    ]:
        with pytest.raises(ConstraintError):
            catalog.sql(statement)
    with catalog.transaction() as tx:
        d = for_transaction(tx)
        tx.sql(f"INSERT INTO ms_retired VALUES('one','token',{d.bind},1,1)", (schema.name,))
        tx.sql(
            "INSERT INTO ms_gc(storage_id,object_key,token,blob_id,reason) "
            "VALUES('synthetic-storage','synthetic-key','token','one','retired')"
        )
        tx.sql("UPDATE ms_blobs SET state='pending_delete'")
        tx.sql(f'DELETE FROM "{name}"')
        tx.sql("DELETE FROM ms_objects")
        tx.sql("DELETE FROM ms_blobs")
    for statement in [
        "DELETE FROM ms_retired",
        "UPDATE ms_retired SET metadata_version=2",
        "DELETE FROM ms_gc",
        "UPDATE ms_gc SET object_key='other'",
    ]:
        with pytest.raises(ConstraintError):
            catalog.sql(statement)
    with pytest.raises(ConstraintError):
        with catalog.transaction() as tx:
            d = for_transaction(tx)
            tx.sql(
                f"INSERT INTO ms_blobs(id,schema_name,schema_version) VALUES('one',{d.bind},1)",
                (schema.name,),
            )


def test_shared_edit_rollback_and_readonly(catalog, schema):
    catalog.install_schema(schema)
    seed(catalog, schema, "one")
    with pytest.raises(RuntimeError):
        with catalog.transaction() as tx:
            metadata.update(tx, "one", schema, metadata.changes(schema, {"number": 7}, 1), 1)
            tx.sql("CREATE TABLE app_rollback(id INTEGER)")
            raise RuntimeError("abort")
    with pytest.raises(ValidationError):
        with catalog.transaction(write=False) as tx:
            metadata.update(tx, "one", schema, {"number": 9}, 1)
    assert find(catalog, schema)[0]["version"] == 1


def test_postgres_catalog_inspection_is_readonly(pg, schema):
    from meldstore.postgres_schema import CORE, schema_objects, verify

    with Catalog.connect_postgres(pg.dsn) as catalog:
        catalog.install_schema(schema)
        with catalog.transaction(write=False) as tx:
            verify(tx, CORE)
            verify(tx, schema_objects(schema))


def test_postgres_index_uniqueness_and_insert_version(pg):
    schema = BlobSchema(
        "unique", {"label": Text(required=True)}, indexes=(Index("label", unique=True),)
    )
    with Catalog.connect_postgres(pg.dsn) as catalog:
        name = catalog.install_schema(schema)
        seed(catalog, schema, "one")
        with pytest.raises(ConstraintError):
            seed(catalog, schema, "duplicate")
        with pytest.raises(ConstraintError):
            with catalog.transaction() as tx:
                tx.sql(
                    "INSERT INTO ms_blobs(id,schema_name,schema_version) VALUES('two','unique',1)"
                )
                tx.sql(f"INSERT INTO \"{name}\"(id,label,version) VALUES('two','b',2)")
        assert catalog.sql("SELECT id FROM ms_blobs") == [{"id": "one"}]


def test_postgres_maximum_field_name(pg):
    field = "é" * 31 + "x"
    schema = BlobSchema("maxfield", {field: Text()})
    with Catalog.connect_postgres(pg.dsn) as catalog:
        name = catalog.install_schema(schema)
        with catalog.transaction() as tx:
            tx.sql(
                "INSERT INTO ms_blobs(id,schema_name,schema_version,state) "
                "VALUES('one','maxfield',1,'ready')"
            )
            tx.sql(f"INSERT INTO \"{name}\"(id,\"{field}\") VALUES('one','valid')")
        assert find(catalog, schema, where={field: "valid"})[0]["metadata"] == {field: "valid"}


def test_postgres_layout_registry_is_required(pg, schema):
    with Catalog.connect_postgres(pg.dsn) as catalog:
        catalog.install_schema(schema)
        catalog.sql("DROP TABLE ms_pg_layout")
        with pytest.raises(SchemaConflictError):
            catalog.install_schema(schema)


def test_postgres_reopen_and_partial_namespace(pg, schema):
    with Catalog.connect_postgres(pg.dsn) as catalog:
        catalog.sql("CREATE TABLE ms_partial(id TEXT)")
        with pytest.raises(SchemaConflictError):
            catalog.install_schema(schema)
        catalog.sql("DROP TABLE ms_partial")
        catalog.install_schema(schema)
        seed(catalog, schema, "one")
    with Catalog.connect_postgres(pg.dsn) as catalog:
        assert catalog.install_schema(schema) == schema.table_name_for("postgres")
        assert find(catalog, schema)[0]["id"] == "one"


def test_store_metadata_methods_with_sql_only_storage_stub(pg, schema, monkeypatch):
    """Exercise real Store read/edit methods without claiming coordinator qualification."""
    from meldstore import Store

    class NoPayloadIO:
        root = None

        def __getattr__(self, name):
            pytest.fail("Metadata-only method attempted payload I/O: " + name)

    with Catalog.connect_postgres(pg.dsn) as catalog:
        # The production admission gate remains closed. This isolated harness
        # tests SQL methods only, using fabricated descriptors and no actual bytes.
        with pytest.raises(ValidationError, match="Bind PostgreSQL"):
            Store(catalog, NoPayloadIO())
        monkeypatch.setattr(catalog, "bind_storage", lambda root: None)
        store = Store(catalog, NoPayloadIO())
        store.install_schema(schema)
        with catalog.transaction() as tx:
            tx.sql("INSERT INTO ms_encodings VALUES('token','example.format',1,'{}')")
            tx.sql(
                "INSERT INTO ms_prepared VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("token", "synthetic", "key", 0, "0" * 32, "xxh3_128", "file", 1, schema.name, 1),
            )
            tx.sql(
                "INSERT INTO ms_blobs(id,schema_name,schema_version) VALUES('one',%s,1)",
                (schema.name,),
            )
            tx.sql(
                f"INSERT INTO \"{schema.table_name_for('postgres')}\"(id,label) VALUES('one','a')"
            )
            tx.sql("INSERT INTO ms_objects VALUES('one','token')")
            tx.sql("UPDATE ms_blobs SET state='ready'")
        original = store.stat("one")
        assert original["handler_id"] == "example.format"
        assert store.find(schema=schema, where={"label": "a"}) == [original]
        changed = store.update_metadata(
            "one", schema=schema, changes={"number": 2}, expected_version=1
        )
        assert changed["version"] == 2
        assert changed["metadata"]["number"] == 2
        assert changed["object_key"] == original["object_key"]
        with pytest.raises(ConflictError):
            store.update_metadata("one", schema=schema, changes={"number": 3}, expected_version=1)
        catalog.sql(
            f'ALTER TABLE "{schema.table_name_for("postgres")}" DISABLE TRIGGER '
            f'"{schema.table_name_for("postgres")}_update"'
        )
        with pytest.raises(SchemaConflictError):
            store.find(schema=schema)
