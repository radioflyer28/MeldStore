import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from meldstore import (
    BlobSchema,
    Boolean,
    BusyError,
    Catalog,
    CommitError,
    ConstraintError,
    Float,
    Index,
    Integer,
    SchemaConflictError,
    Text,
    Timestamp,
    TransactionError,
    ValidationError,
)
from meldstore import (
    quote_identifier as q,
)


@pytest.fixture(params=["sqlite", "melddb"])
def catalog(request, tmp_path):
    with Catalog(tmp_path / "catalog.db", adapter=request.param) as catalog:
        yield catalog


@pytest.fixture
def schema():
    return BlobSchema(
        "dataset",
        {"label": Text(required=True), "source": Text(immutable=True)},
        indexes=(Index("label"),),
    )


def seed(tx, schema, blob_id="one"):
    # S01 catalog fixture only: no bytes have been prepared/published.
    tx.sql(
        "INSERT INTO ms_blobs (id, schema_name, schema_version) VALUES (?, ?, 1)",
        (blob_id, schema.name),
    )
    tx.sql(
        f"INSERT INTO {q(schema.table_name)} (id, label, source) VALUES (?, ?, ?)",
        (blob_id, "first", "original"),
    )


def test_install_idempotent_and_no_managed_tables(catalog, schema):
    assert catalog.sql("SELECT name FROM sqlite_master WHERE type = 'table'") == []
    assert catalog.install_schema(schema) == schema.table_name
    assert catalog.install_schema(schema) == schema.table_name
    tables = {r["name"] for r in catalog.sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"ms_format", "ms_schemas", "ms_blobs", schema.table_name}


def test_conflict_and_no_implicit_evolution(catalog, schema):
    catalog.install_schema(schema)
    with pytest.raises(SchemaConflictError):
        catalog.install_schema(BlobSchema("dataset", {"different": Text()}))
    with pytest.raises(SchemaConflictError):
        catalog.install_schema(BlobSchema("dataset", {}, version=2))
    assert catalog.install_schema(schema) == schema.table_name


def test_quoted_identifiers_and_injection(catalog):
    name = "表'\";--"
    column = 'select"; DROP TABLE ms_blobs;--'
    schema = BlobSchema(name, {column: Text()}, indexes=(Index(column),))
    table = catalog.install_schema(schema)
    assert catalog.install_schema(schema) == table
    with catalog.transaction() as tx:
        tx.sql("INSERT INTO ms_blobs (id, schema_name, schema_version) VALUES ('a', ?, 1)", (name,))
        tx.sql(f"INSERT INTO {q(table)} (id, {q(column)}) VALUES ('a', ?)", ("bound'value",))
        assert tx.sql(f"SELECT {q(column)} FROM {q(table)}") == [{column: "bound'value"}]


def test_shared_application_fk_and_restrict(catalog, schema):
    with catalog.transaction() as tx:
        catalog.install_schema(schema, tx=tx)
        tx.sql(
            "CREATE TABLE app_reference (blob_id TEXT NOT NULL REFERENCES ms_blobs(id) "
            "ON DELETE RESTRICT)"
        )
        seed(tx, schema)
        tx.sql("INSERT INTO app_reference VALUES (?)", ("one",))
    assert catalog.sql("SELECT b.id FROM ms_blobs b JOIN app_reference a ON a.blob_id=b.id") == [
        {"id": "one"}
    ]
    with pytest.raises(ConstraintError):
        catalog.sql("INSERT INTO app_reference VALUES ('missing')")
    with pytest.raises(ConstraintError):
        with catalog.transaction() as tx:
            tx.sql(f"DELETE FROM {q(schema.table_name)}")
            tx.sql("DELETE FROM ms_blobs")
    assert len(catalog.sql(f"SELECT * FROM {q(schema.table_name)}")) == 1


def test_atomic_install_and_application_rollback(catalog, schema):
    with pytest.raises(RuntimeError):
        with catalog.transaction() as tx:
            catalog.install_schema(schema, tx=tx)
            tx.sql("CREATE TABLE app_notes (note TEXT)")
            seed(tx, schema)
            raise RuntimeError("application failed")
    assert catalog.sql("SELECT name FROM sqlite_master WHERE type='table'") == []


def test_caught_sql_failure_poisoned(catalog):
    with pytest.raises(TransactionError):
        with catalog.transaction() as tx:
            tx.sql("CREATE TABLE example (id INTEGER PRIMARY KEY)")
            tx.sql("INSERT INTO example VALUES (1)")
            with pytest.raises(ConstraintError):
                tx.sql("INSERT INTO example VALUES (1)")
    assert catalog.sql("SELECT name FROM sqlite_master WHERE name='example'") == []


@pytest.mark.parametrize("operation", ["nested", "catalog_sql", "close", "expired"])
def test_invalid_operations_poison_transaction(catalog, operation):
    with catalog.transaction() as old:
        old.sql("SELECT 1")
    with pytest.raises(TransactionError):
        with catalog.transaction() as tx:
            tx.sql("CREATE TABLE doomed (id INTEGER)")
            with pytest.raises(TransactionError):
                if operation == "nested":
                    with catalog.transaction():
                        pass
                elif operation == "catalog_sql":
                    catalog.sql("SELECT 1")
                elif operation == "close":
                    catalog.close()
                else:
                    old.sql("SELECT 1")
    assert catalog.sql("SELECT name FROM sqlite_master WHERE name='doomed'") == []


def test_foreign_transaction(catalog, schema, tmp_path):
    with Catalog(tmp_path / "foreign.db", adapter=catalog.adapter) as other:
        with catalog.transaction() as tx:
            with pytest.raises(TransactionError):
                other.install_schema(schema, tx=tx)
        assert other.sql("SELECT name FROM sqlite_master WHERE type='table'") == []


def test_thread_confinement(catalog):
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(TransactionError):
            pool.submit(catalog.sql, "SELECT 1").result()
    assert catalog.sql("SELECT 1 AS value") == [{"value": 1}]


@pytest.mark.parametrize(
    "sql",
    [
        "COMMIT",
        "-- comment\n ROLLBACK",
        "/* hi */ PRAGMA foreign_keys=OFF",
        ";COMMIT",
        "ATTACH ':memory:' AS other",
        "/* unclosed",
    ],
)
def test_control_sql_rejected(catalog, sql):
    with pytest.raises(ValidationError):
        catalog.sql(sql)


def test_bindings_and_returning(catalog):
    with catalog.transaction() as tx:
        assert tx.sql("CREATE TABLE example (name TEXT)") == []
        assert tx.sql("INSERT INTO example VALUES (:name) RETURNING name", {"name": "a'b"}) == [
            {"name": "a'b"}
        ]
        assert tx.sql("WITH n AS (SELECT ? AS value) SELECT * FROM n", [7]) == [{"value": 7}]
    with pytest.raises(ValidationError):
        catalog.sql("SELECT ?", "bad")


def test_sql_concurrency_and_immutability(catalog, schema):
    catalog.install_schema(schema)
    with catalog.transaction() as tx:
        seed(tx, schema)
    table = q(schema.table_name)
    with pytest.raises(ConstraintError):
        catalog.sql(f"UPDATE {table} SET label='bad'")
    with pytest.raises(ConstraintError):
        catalog.sql(f"UPDATE {table} SET source='changed', version=version+1")
    assert catalog.sql(
        f"UPDATE {table} SET label=?, version=version+1 WHERE id=? AND version=? RETURNING version",
        ("second", "one", 1),
    ) == [{"version": 2}]
    assert (
        catalog.sql(
            f"UPDATE {table} SET label=?, version=version+1 "
            "WHERE id=? AND version=? RETURNING version",
            ("stale", "one", 1),
        )
        == []
    )
    with pytest.raises(ConstraintError):
        catalog.sql("UPDATE ms_blobs SET id='different'")


def test_schema_guards_cannot_be_silently_repaired(catalog, schema):
    catalog.install_schema(schema)
    catalog.sql(f"DROP TRIGGER {q(schema.table_name + '_update')}")
    with pytest.raises(SchemaConflictError):
        catalog.install_schema(schema)


def test_namespace_collision(catalog, schema):
    catalog.sql("CREATE TABLE ms_blobs (wrong TEXT)")
    with pytest.raises(SchemaConflictError):
        catalog.install_schema(schema)
    assert catalog.sql("SELECT name FROM sqlite_master WHERE type='table'") == [
        {"name": "ms_blobs"}
    ]


def test_commit_constraint_failure_and_reuse(catalog):
    with catalog.transaction() as tx:
        tx.sql("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        tx.sql(
            "CREATE TABLE child (id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)"
        )
    with pytest.raises(CommitError):
        with catalog.transaction() as tx:
            tx.sql("INSERT INTO child VALUES (1)")
    assert catalog.sql("SELECT * FROM child") == []


@pytest.mark.parametrize("first", ["sqlite", "melddb"])
def test_reopen_with_other_adapter_and_plain_driver(tmp_path, schema, first):
    path = tmp_path / "shared.db"
    with Catalog(path, adapter=first) as catalog:
        catalog.install_schema(schema)
        with catalog.transaction() as tx:
            seed(tx, schema)
    with Catalog(path, adapter="melddb" if first == "sqlite" else "sqlite") as catalog:
        assert catalog.install_schema(schema) == schema.table_name
    with sqlite3.connect(path) as raw:
        raw.execute("PRAGMA foreign_keys=ON")
        assert raw.execute("SELECT id FROM ms_blobs").fetchall() == [("one",)]
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(f"UPDATE {q(schema.table_name)} SET label='bad'")


def test_second_connection_busy(catalog, tmp_path):
    with Catalog(tmp_path / "catalog.db", adapter=catalog.adapter, timeout=0.01) as other:
        with catalog.transaction() as tx:
            tx.sql("CREATE TABLE held (id INTEGER)")
            with pytest.raises(BusyError):
                other.sql("SELECT 1")
        assert other.sql("SELECT * FROM held") == []


def test_direct_adapter_needs_no_melddb_or_codec_import(tmp_path):
    script = """
import sys
class Block:
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'melddb', 'numpy', 'pandas', 'blosc2', 'pyarrow'}:
            raise AssertionError('unexpected import: ' + fullname)
sys.meta_path.insert(0, Block())
from meldstore import Catalog, BlobSchema
with Catalog(sys.argv[1], adapter='sqlite') as catalog:
    catalog.install_schema(BlobSchema('empty', {}))
"""
    subprocess.run([sys.executable, "-c", script, str(tmp_path / "plain.db")], check=True)


@pytest.mark.parametrize(
    "column,value",
    [
        ("count", 1.5),
        ("flag", 2),
        ("score", float("inf")),
        ("instant", "2026-01-01"),
        ("label", None),
    ],
)
def test_sql_field_constraints(catalog, column, value):
    schema = BlobSchema(
        "typed",
        {
            "count": Integer(),
            "flag": Boolean(),
            "score": Float(),
            "instant": Timestamp(),
            "label": Text(required=True),
        },
    )
    catalog.install_schema(schema)
    with catalog.transaction() as tx:
        tx.sql("INSERT INTO ms_blobs (id, schema_name, schema_version) VALUES ('a', 'typed', 1)")
        tx.sql(f"INSERT INTO {q(schema.table_name)} (id, label) VALUES ('a', 'label')")
    with pytest.raises(ConstraintError):
        catalog.sql(f"UPDATE {q(schema.table_name)} SET {q(column)}=?, version=version+1", (value,))


def test_unique_index_and_cross_schema_reference(catalog, schema):
    one = BlobSchema("one", {"label": Text()}, indexes=(Index("label", unique=True),))
    catalog.install_schema(one)
    catalog.install_schema(schema)
    with catalog.transaction() as tx:
        for blob_id in ("a", "b"):
            tx.sql(
                "INSERT INTO ms_blobs (id, schema_name, schema_version) VALUES (?, 'one', 1)",
                (blob_id,),
            )
        tx.sql(f"INSERT INTO {q(one.table_name)} (id, label) VALUES ('a', 'same')")
    with pytest.raises(ConstraintError):
        catalog.sql(f"INSERT INTO {q(one.table_name)} (id, label) VALUES ('b', 'same')")
    with pytest.raises(ConstraintError):
        catalog.sql(f"INSERT INTO {q(schema.table_name)} (id, label) VALUES ('b', 'wrong schema')")


def test_interrupt_rolls_back_and_expires_handle(catalog):
    with pytest.raises(KeyboardInterrupt):
        with catalog.transaction() as tx:
            tx.sql("CREATE TABLE interrupted (id INTEGER)")
            raise KeyboardInterrupt()
    with pytest.raises(TransactionError):
        tx.sql("SELECT 1")
    assert catalog.sql("SELECT name FROM sqlite_master WHERE name='interrupted'") == []


def test_version_rules_hold_for_separate_process(catalog, schema, tmp_path):
    catalog.install_schema(schema)
    with catalog.transaction() as tx:
        seed(tx, schema)
    table = q(schema.table_name)
    catalog.sql(f"UPDATE {table} SET label='new', version=version+1 WHERE id='one' AND version=1")
    script = """
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA foreign_keys=ON')
assert db.execute('UPDATE ' + sys.argv[2] +
    " SET label='stale', version=version+1 WHERE id='one' AND version=1 RETURNING version").fetchall() == []
db.commit()
db.close()
"""
    subprocess.run([sys.executable, "-c", script, str(tmp_path / "catalog.db"), table], check=True)
    assert catalog.sql(f"SELECT label, version FROM {table}") == [{"label": "new", "version": 2}]
