"""PostgreSQL SQL-only migration qualification; production storage gate stays closed."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest

from meldstore import (
    BlobSchema,
    Boolean,
    Catalog,
    ConflictError,
    ConstraintError,
    Index,
    Integer,
    MetadataMigration,
    NotFoundError,
    SchemaConflictError,
    Store,
    Text,
    Timestamp,
    TransactionError,
    ValidationError,
)


class NoPayloadIO:
    root = None

    def __getattr__(self, name):
        raise AssertionError("Migration attempted payload access: " + name)


def sql_store(catalog):
    # This SQL-only harness does not qualify or enable production storage binding.
    catalog.bind_storage = lambda root: None
    return Store(catalog, NoPayloadIO())


@pytest.fixture
def setup(pg):
    old = BlobSchema(
        "migration_data",
        {"label": Text(required=True), "ok": Boolean(), "at": Timestamp()},
        indexes=(Index("label", "at"),),
    )
    new = BlobSchema(
        old.name, {**old.fields, "count": Integer(required=True)}, version=2, indexes=old.indexes
    )
    with Catalog.connect_postgres(pg.dsn) as catalog:
        store = sql_store(catalog)
        store.install_schema(old)
        for id in "abc":
            with catalog.transaction() as tx:
                tx.sql(
                    "INSERT INTO ms_prepared VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (id, "synthetic", id, 0, "0" * 32, "xxh3_128", "file", 1, old.name, 1),
                )
                tx.sql(
                    "INSERT INTO ms_blobs(id,schema_name,schema_version) VALUES(%s,%s,1)",
                    (id, old.name),
                )
                tx.sql(
                    f'INSERT INTO "{old.table_name_for("postgres")}"(id,label,ok,at) VALUES(%s,%s,1,%s)',
                    (id, id, "2026-01-01T00:00:00.000000Z"),
                )
                tx.sql("INSERT INTO ms_objects VALUES(%s,%s)", (id, id))
                tx.sql("UPDATE ms_blobs SET state='ready' WHERE id=%s", (id,))
        yield store, old, new


def plan(old, new, transform=None, **kwargs):
    def convert(values):
        assert values["ok"] is True
        assert values["at"] == datetime(2026, 1, 1, tzinfo=timezone.utc)
        return dict(values, count=ord(values["label"]))

    return MetadataMigration("add_count", old, new, "count-v1", transform or convert, **kwargs)


def test_coexistence_resume_reopen_and_unchanged_descriptors(setup, pg):
    store, old, new = setup
    before = store.stat("a")
    progress = store.migrate(plan(old, new), batch_size=1, max_batches=1)
    assert (progress["migrated_rows"], progress["last_id"], progress["state"]) == (
        1,
        "a",
        "running",
    )
    assert [r["id"] for r in store.find(schema=old)] == ["b", "c"]
    assert [r["id"] for r in store.find(schema=new)] == ["a"]
    store.install_schema(old)
    store.install_schema(new)
    with Catalog.connect_postgres(pg.dsn) as catalog:
        resumed = sql_store(catalog)
        result = resumed.migrate(plan(old, new), batch_size=1)
        assert (result["migrated_rows"], result["state"]) == (3, "complete")
        assert resumed.migrate(plan(old, new)) == result
    current = store.stat("a")
    assert current["schema_version"] == current["version"] == 2
    assert (current["object_key"], current["digest"]) == (before["object_key"], before["digest"])


def test_dry_run_restores_schema_registry_and_rows(setup):
    store, old, new = setup
    before = store.catalog.sql("SELECT * FROM ms_pg_layout ORDER BY name")
    result = store.migrate(plan(old, new), dry_run=True, batch_size=1)
    assert result["dry_run"] and result["migrated_rows"] == 3
    assert store.catalog.sql("SELECT * FROM ms_pg_layout ORDER BY name") == before
    assert store.stat("a")["schema_version"] == 1
    with pytest.raises(NotFoundError):
        store.migration_status("add_count")


def test_failed_batch_leaves_durable_checkpoint(setup):
    store, old, new = setup

    def failing(row):
        if row["label"] == "c":
            raise RuntimeError("interrupted")
        return dict(row, count=1)

    with pytest.raises(RuntimeError, match="interrupted"):
        store.migrate(plan(old, new, failing), batch_size=2)
    assert store.migration_status("add_count")["migrated_rows"] == 2
    assert store.stat("c")["schema_version"] == 1
    assert store.catalog.sql("SELECT * FROM ms_migration_steps") == []
    assert store.migrate(plan(old, new))["migrated_rows"] == 3


def test_application_fk_indexes_views_triggers_keep_table_identity(setup):
    store, old, new = setup
    name = old.table_name_for("postgres")
    oid = store.catalog.sql("SELECT %s::regclass::oid AS oid", (name,))
    store.catalog.sql("CREATE TABLE app_blob(id TEXT REFERENCES ms_blobs(id))")
    store.catalog.sql(f'CREATE TABLE app_meta(id TEXT REFERENCES "{name}"(id))')
    store.catalog.sql("INSERT INTO app_blob VALUES('a')")
    store.catalog.sql("INSERT INTO app_meta VALUES('a')")
    store.catalog.sql(f'CREATE INDEX app_label ON "{name}"(label)')
    store.catalog.sql(f'CREATE VIEW app_view AS SELECT id,label FROM "{name}"')
    store.catalog.sql("CREATE TABLE app_audit(id TEXT)")
    store.catalog.sql(
        "CREATE FUNCTION app_audit_fn() RETURNS trigger LANGUAGE plpgsql AS "
        "'BEGIN INSERT INTO app_audit VALUES(NEW.id); RETURN NEW; END'"
    )
    store.catalog.sql(
        f'CREATE TRIGGER app_edit AFTER UPDATE ON "{name}" FOR EACH ROW EXECUTE FUNCTION app_audit_fn()'
    )
    store.migrate(plan(old, new))
    assert store.catalog.sql("SELECT %s::regclass::oid AS oid", (name,)) == oid
    assert store.catalog.sql("SELECT * FROM app_meta") == [{"id": "a"}]
    assert len(store.catalog.sql("SELECT * FROM app_audit")) == 3
    assert len(store.catalog.sql("SELECT * FROM app_view")) == 3
    with pytest.raises(ConstraintError):
        store.catalog.sql("INSERT INTO app_meta VALUES('missing')")
    store.install_schema(new)


def test_version_and_immutable_guards(setup):
    store, old, new = setup
    store.migrate(plan(old, new), batch_size=1, max_batches=1)
    table = old.table_name_for("postgres")
    for assignment, id in [
        ("label='x',version=version+1", "b"),
        ("schema_version=2,count=1,version=version+1", "b"),
        ("count=NULL,version=version+1", "a"),
        ("count=2", "a"),
    ]:
        with pytest.raises(ConstraintError):
            store.catalog.sql(f'UPDATE "{table}" SET {assignment} WHERE id=%s', (id,))
    assert (
        store.update_metadata("a", schema=new, changes={"count": 7}, expected_version=2)["version"]
        == 3
    )
    with pytest.raises(ConflictError):
        store.update_metadata("a", schema=new, changes={"count": 8}, expected_version=2)


@pytest.mark.parametrize("kind", ["foreign_key", "unique", "trigger"])
def test_dry_run_checks_deferred_application_rules(setup, kind):
    store, old, new = setup
    if kind == "foreign_key":
        store.catalog.sql("CREATE TABLE app_parent(id TEXT PRIMARY KEY)")
        store.catalog.sql(
            "CREATE TABLE app_effect(id TEXT REFERENCES app_parent(id) DEFERRABLE INITIALLY DEFERRED)"
        )
        effect = "INSERT INTO app_effect VALUES('missing');"
    elif kind == "unique":
        store.catalog.sql("CREATE TABLE app_effect(id TEXT UNIQUE DEFERRABLE INITIALLY DEFERRED)")
        effect = "INSERT INTO app_effect VALUES('duplicate');"
    else:
        store.catalog.sql("CREATE TABLE app_effect(id TEXT)")
        store.catalog.sql(
            "CREATE FUNCTION app_reject() RETURNS trigger LANGUAGE plpgsql AS "
            "'BEGIN RAISE EXCEPTION USING ERRCODE=''23514'', MESSAGE=''synthetic-secret''; END'"
        )
        store.catalog.sql(
            "CREATE CONSTRAINT TRIGGER app_reject AFTER INSERT ON app_effect "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app_reject()"
        )
        effect = "INSERT INTO app_effect VALUES('value');"
    body = "BEGIN " + effect + " RETURN NEW; END"
    store.catalog.sql(
        "CREATE FUNCTION app_effect_fn() RETURNS trigger LANGUAGE plpgsql AS '"
        + body.replace("'", "''")
        + "'"
    )
    store.catalog.sql(
        f'CREATE TRIGGER app_effect AFTER UPDATE ON "{old.table_name_for("postgres")}" '
        "FOR EACH ROW EXECUTE FUNCTION app_effect_fn()"
    )
    with pytest.raises(ConstraintError) as error:
        store.migrate(plan(old, new), dry_run=True)
    assert "synthetic-secret" not in str(error.value)
    assert store.stat("a")["schema_version"] == 1
    assert store.catalog.sql("SELECT * FROM app_effect") == []


def test_third_version_field_removal_and_schema_drift(setup):
    store, old, new = setup
    store.migrate(plan(old, new))
    third = BlobSchema(old.name, {"label": Text(required=True, immutable=True)}, version=3)
    assert (
        store.migrate(
            MetadataMigration("v3", new, third, "only-label", lambda row: {"label": row["label"]})
        )["state"]
        == "complete"
    )
    for schema in [old, new, third]:
        store.install_schema(schema)
    assert store.stat("a")["metadata"] == {"label": "a"}
    assert store.stat("a")["version"] == 3
    store.catalog.sql(
        f'ALTER TABLE "{old.table_name_for("postgres")}" DISABLE TRIGGER "{old.table_name_for("postgres")}_update"'
    )
    with pytest.raises(SchemaConflictError):
        store.find(schema=third)


def test_unique_failure_and_transform_identity(setup):
    store, old, new = setup
    unique = BlobSchema(new.name, new.fields, version=2, indexes=(Index("count", unique=True),))
    with pytest.raises(ConstraintError):
        store.migrate(plan(old, unique, lambda row: dict(row, count=1)))
    assert store.migration_status("add_count")["migrated_rows"] == 0
    with pytest.raises(SchemaConflictError):
        store.migrate(MetadataMigration("add_count", old, unique, "different", lambda row: row))
    assert store.migrate(plan(old, unique))["state"] == "complete"


def test_concurrent_resumers_and_guarded_edit(setup, pg):
    store, old, new = setup
    store.migrate(plan(old, new), max_batches=1, batch_size=1)
    barrier = Barrier(3)

    def worker(operation):
        with Catalog.connect_postgres(pg.dsn, timeout=10) as catalog:
            other = sql_store(catalog)
            barrier.wait(timeout=10)
            if operation == "edit":
                return other.update_metadata(
                    "a", schema=new, changes={"count": 999}, expected_version=2
                )["version"]
            return other.migrate(plan(old, new), batch_size=1)["migrated_rows"]

    with ThreadPoolExecutor(3) as pool:
        assert list(pool.map(worker, ["resume", "resume", "edit"])) == [3, 3, 3]
    assert store.stat("a")["metadata"]["count"] == 999
    assert store.stat("a")["version"] == 3
    assert store.stat("b")["version"] == 2
    assert store.catalog.sql("SELECT * FROM ms_migration_steps") == []


def test_process_exit_preserves_completed_batch(setup, pg):
    store, old, new = setup
    store.migrate(plan(old, new), max_batches=1, batch_size=1)
    code = """
import os
from meldstore import Catalog, MetadataMigration, Store
from meldstore.migrations import decode
old,new=decode(os.environ['TEST_OLD']),decode(os.environ['TEST_NEW'])
def convert(row):
    if row['label']=='c': os._exit(73)
    return dict(row,count=1)
class Storage: root=None
with Catalog.connect_postgres(os.environ['TEST_DSN']) as catalog:
    catalog.bind_storage=lambda root: None
    store=Store(catalog,Storage())
    store.migrate(MetadataMigration('add_count',old,new,'count-v1',convert),batch_size=2)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={
            **os.environ,
            "TEST_DSN": pg.dsn,
            "TEST_OLD": old.definition,
            "TEST_NEW": new.definition,
        },
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 73, result.stderr.decode()
    assert store.migration_status("add_count")["migrated_rows"] == 1
    assert store.stat("b")["schema_version"] == 1
    assert store.catalog.sql("SELECT * FROM ms_migration_steps") == []
    assert store.migrate(plan(old, new))["migrated_rows"] == 3


def test_validation_operation_poisoning_and_readonly(pg):
    with Catalog.connect_postgres(pg.dsn) as catalog:
        catalog.sql("CREATE TABLE app_parent(id INT PRIMARY KEY)")
        catalog.sql(
            "CREATE TABLE app_child(id INT REFERENCES app_parent(id) DEFERRABLE INITIALLY DEFERRED)"
        )
        with pytest.raises(TransactionError):
            with catalog.transaction() as tx:
                tx.sql("INSERT INTO app_child VALUES(1)")
                with pytest.raises(ConstraintError):
                    tx.validate_constraints()
        with pytest.raises(ValidationError):
            with catalog.transaction(write=False) as tx:
                tx.validate_constraints()
        assert catalog.sql("SELECT * FROM app_child") == []


def test_preflight_incompatible_type_and_pending_blob(setup):
    store, old, new = setup
    bad = BlobSchema(old.name, {"label": Integer()}, version=2)
    with pytest.raises(SchemaConflictError, match="field type"):
        store.migrate(plan(old, bad))
    assert store.stat("a")["schema_version"] == 1
    store.catalog.sql(
        "INSERT INTO ms_blobs(id,schema_name,schema_version) VALUES(%s,%s,1)", ("pending", old.name)
    )
    with pytest.raises(ConflictError, match="unpublished"):
        store.migrate(plan(old, new))


def test_empty_catalog_and_long_names(pg):
    old = BlobSchema("x" * 48, {})
    new = BlobSchema(old.name, {}, version=2)
    with Catalog.connect_postgres(pg.dsn) as catalog:
        store = sql_store(catalog)
        store.install_schema(old)
        result = store.migrate(
            MetadataMigration("empty", old, new, "identity", lambda row: row), dry_run=True
        )
        assert result["state"] == "complete" and result["migrated_rows"] == 0
        assert (
            store.migrate(MetadataMigration("empty", old, new, "identity", lambda row: row))[
                "state"
            ]
            == "complete"
        )
        store.install_schema(old)
        store.install_schema(new)


def test_failed_coordination_preserves_application_reference(setup):
    store, old, new = setup
    table = old.table_name_for("postgres")
    store.catalog.sql(f'CREATE TABLE app_child(id TEXT REFERENCES "{table}"(id))')
    store.catalog.sql("INSERT INTO app_child VALUES('a')")
    with pytest.raises(SchemaConflictError, match="restore"):
        store.migrate(
            plan(
                old,
                new,
                before_ddl=lambda tx: tx.sql("DROP TABLE app_child"),
                after_ddl=lambda tx: None,
            )
        )
    assert store.catalog.sql("SELECT * FROM app_child") == [{"id": "a"}]
    assert store.stat("a")["schema_version"] == 1


def test_after_hook_cannot_adopt_owned_schema_drift(setup):
    store, old, new = setup
    table = old.table_name_for("postgres")
    with pytest.raises(SchemaConflictError):
        store.migrate(
            plan(
                old,
                new,
                before_ddl=lambda tx: None,
                after_ddl=lambda tx: tx.sql(f'ALTER TABLE "{table}" ADD COLUMN sneaky TEXT'),
            )
        )
    assert store.stat("a")["schema_version"] == 1


def test_validation_disconnect_quarantines(pg):
    from meldstore import RollbackError

    with Catalog.connect_postgres(pg.dsn) as catalog:
        with pytest.raises(RollbackError) as caught:
            with catalog.transaction() as tx:
                pid = tx.sql("SELECT pg_backend_pid() AS pid")[0]["pid"]
                pg.admin.execute("SELECT pg_terminate_backend(%s,5000)", (pid,))
                tx.validate_constraints()
        assert caught.value.outcome == "unknown"
        with pytest.raises(TransactionError, match="closing"):
            catalog.sql("SELECT 1")
