from datetime import datetime, timezone

import pytest

from meldstore import (
    BlobSchema,
    Boolean,
    Catalog,
    ConstraintError,
    Index,
    Integer,
    LocalStorage,
    MetadataMigration,
    NotFoundError,
    SchemaConflictError,
    Store,
    Text,
    Timestamp,
)
from meldstore import (
    quote_identifier as q,
)


@pytest.fixture(params=["sqlite", "melddb"])
def setup(request, tmp_path):
    old = BlobSchema(
        "measurements",
        {"label": Text(required=True), "ok": Boolean(), "at": Timestamp()},
        indexes=(Index("label", "at"),),
    )
    new = BlobSchema(
        old.name, {**old.fields, "count": Integer(required=True)}, version=2, indexes=old.indexes
    )
    source = tmp_path / "source"
    source.write_bytes(b"unchanged payload")
    with Catalog(tmp_path / "catalog.db", adapter=request.param) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        store.install_schema(old)
        for id in "abc":
            store.import_file(
                source,
                schema=old,
                metadata={"label": id, "ok": True, "at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
                id=id,
            )
        yield store, old, new, source


def plan(old, new, transform=None, **kwargs):
    def convert(values):
        assert values["ok"] is True
        assert values["at"].tzinfo is not None
        return dict(values, count=1)

    return MetadataMigration("add_count", old, new, "count-v1", transform or convert, **kwargs)


def test_coexistence_resume_and_unchanged_payload(setup):
    store, old, new, source = setup
    original = store.stat("a")
    migration = plan(old, new)
    progress = store.migrate(migration, batch_size=1, max_batches=1)
    assert (progress["state"], progress["migrated_rows"], progress["last_id"]) == (
        "running",
        1,
        "a",
    )
    assert [r["id"] for r in store.find(schema=old)] == ["b", "c"]
    assert [r["id"] for r in store.find(schema=new)] == ["a"]
    assert store.stat("b")["metadata"].keys() == old.fields.keys()
    store.install_schema(old)
    store.install_schema(new)
    with pytest.raises(SchemaConflictError):
        store.import_file(source, schema=old, metadata={"label": "d"})
    result = store.migrate(migration, batch_size=1)
    assert result["state"] == "complete" and result["migrated_rows"] == 3
    assert store.migrate(migration) == result
    assert store.migration_status(migration.name) == result
    current = store.stat("a")
    assert current["version"] == 2 and current["schema_version"] == 2
    assert (current["object_key"], current["digest"]) == (
        original["object_key"],
        original["digest"],
    )
    with store.materialize("a") as path:
        assert path.read_bytes() == source.read_bytes()
    store.import_file(source, schema=new, metadata={"label": "d", "count": 9}, id="d")
    assert store.stat("d")["schema_version"] == 2


def test_dry_run_rolls_back_and_batch_failure_resumes(setup):
    store, old, new, _ = setup
    before = store.catalog.sql("SELECT name,sql FROM sqlite_master ORDER BY name")
    result = store.migrate(plan(old, new), dry_run=True, batch_size=1)
    assert result["dry_run"] and result["migrated_rows"] == 3
    assert store.catalog.sql("SELECT name,sql FROM sqlite_master ORDER BY name") == before
    assert store.stat("a")["schema_version"] == 1
    with pytest.raises(NotFoundError):
        store.migration_status("add_count")

    def interrupted(values):
        if values["label"] == "c":
            raise RuntimeError("interrupted")
        return dict(values, count=1)

    store.migrate(plan(old, new), max_batches=0)
    with pytest.raises(RuntimeError, match="interrupted"):
        store.migrate(plan(old, new, interrupted), batch_size=2)
    assert store.migration_status("add_count")["migrated_rows"] == 2
    assert store.stat("c")["schema_version"] == 1
    assert store.catalog.sql("SELECT * FROM ms_migration_steps") == []
    assert store.migrate(plan(old, new))["migrated_rows"] == 3


def test_application_indexes_triggers_and_blob_foreign_keys_survive(setup):
    store, old, new, _ = setup
    table = q(old.table_name)
    with store.catalog.transaction() as tx:
        tx.sql("CREATE TABLE app_link (blob_id TEXT REFERENCES ms_blobs(id))")
        tx.sql("INSERT INTO app_link VALUES ('a')")
        tx.sql(f"CREATE INDEX app_label ON {table}(label)")
        tx.sql("CREATE TABLE app_audit (blob_id TEXT)")
        tx.sql(
            f"CREATE TRIGGER app_edit AFTER UPDATE ON {table} BEGIN INSERT INTO app_audit VALUES (NEW.id); END"
        )
        tx.sql(f"CREATE VIEW app_view AS SELECT id,label FROM {table}")
    before = store.catalog.sql(
        "SELECT name,sql FROM sqlite_master WHERE name LIKE 'app_%' ORDER BY name"
    )
    store.migrate(plan(old, new))
    assert (
        store.catalog.sql(
            "SELECT name,sql FROM sqlite_master WHERE name LIKE 'app_%' ORDER BY name"
        )
        == before
    )
    assert store.catalog.sql("SELECT * FROM app_link") == [{"blob_id": "a"}]
    assert len(store.catalog.sql("SELECT * FROM app_audit")) == 3
    assert len(store.catalog.sql("SELECT * FROM app_view")) == 3
    assert store.catalog.sql("SELECT * FROM pragma_foreign_key_check") == []


def test_incoming_metadata_foreign_keys_refuse_or_coordinate(setup):
    store, old, new, _ = setup
    ddl = f"CREATE TABLE child (blob_id TEXT REFERENCES {q(old.table_name)}(id))"
    store.catalog.sql(ddl)
    store.catalog.sql("INSERT INTO child VALUES ('a')")
    with pytest.raises(SchemaConflictError, match="coordination"):
        store.migrate(plan(old, new))
    assert store.stat("a")["schema_version"] == 1
    assert store.catalog.sql("SELECT * FROM child") == [{"blob_id": "a"}]

    def before(tx):
        tx.sql("CREATE TABLE saved_child AS SELECT * FROM child")
        tx.sql("DROP TABLE child")

    def after(tx):
        tx.sql(ddl)
        tx.sql("INSERT INTO child SELECT * FROM saved_child")
        tx.sql("DROP TABLE saved_child")

    store.migrate(plan(old, new, before_ddl=before, after_ddl=after))
    assert store.catalog.sql("SELECT * FROM child") == [{"blob_id": "a"}]
    assert store.catalog.sql("SELECT * FROM pragma_foreign_key_check") == []


def test_direct_sql_version_and_required_guards(setup):
    store, old, new, _ = setup
    store.migrate(plan(old, new), batch_size=1, max_batches=1)
    table = q(old.table_name)
    for statement in [
        f"UPDATE {table} SET count=NULL,version=version+1 WHERE id='a'",
        f"UPDATE {table} SET label='x',version=version+1 WHERE id='b'",
        f"UPDATE {table} SET schema_version=2,count=1,version=version+1 WHERE id='b'",
        f"UPDATE {table} SET count=2 WHERE id='a'",
        f"DELETE FROM {table} WHERE id='b'",
    ]:
        with pytest.raises(ConstraintError):
            store.catalog.sql(statement)
    assert (
        store.update_metadata("a", schema=new, changes={"count": 2}, expected_version=2)["version"]
        == 3
    )


def test_unique_target_failure_is_atomic_and_identity_is_checked(setup):
    store, old, new, _ = setup
    new = BlobSchema(new.name, new.fields, version=2, indexes=(Index("count", unique=True),))
    with pytest.raises(ConstraintError):
        store.migrate(plan(old, new), batch_size=3)
    assert store.migration_status("add_count")["migrated_rows"] == 0
    assert len(store.find(schema=old)) == 3
    with pytest.raises(SchemaConflictError):
        store.migrate(MetadataMigration("add_count", old, new, "different", lambda x: x))
    result = store.migrate(plan(old, new, lambda v: dict(v, count=ord(v["label"]))))
    assert result["state"] == "complete"


def test_process_exit_rolls_back_partial_batch_then_other_adapter_resumes(setup, tmp_path):
    import subprocess
    import sys

    store, old, new, _ = setup
    store.migrate(plan(old, new), batch_size=1, max_batches=1)
    code = """
import os, sys
from meldstore import *
from meldstore.migrations import decode
old, new = decode(sys.argv[3]), decode(sys.argv[4])
def convert(row):
    if row['label'] == 'c':
        os._exit(73)
    return dict(row, count=1)
with Catalog(sys.argv[1], adapter=sys.argv[5]) as catalog:
    store = Store(catalog, LocalStorage(sys.argv[2]))
    store.migrate(MetadataMigration('add_count', old, new, 'count-v1', convert), batch_size=2)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(tmp_path / "catalog.db"),
            str(tmp_path / "objects"),
            old.definition,
            new.definition,
            store.catalog.adapter,
        ],
        timeout=30,
        capture_output=True,
    )
    assert result.returncode == 73, result.stderr.decode()
    assert store.migration_status("add_count")["migrated_rows"] == 1
    assert store.stat("b")["schema_version"] == 1
    assert store.catalog.sql("SELECT * FROM ms_migration_steps") == []
    other = "melddb" if store.catalog.adapter == "sqlite" else "sqlite"
    with Catalog(tmp_path / "catalog.db", adapter=other) as catalog:
        resumed = Store(catalog, LocalStorage(tmp_path / "objects"))
        assert resumed.migrate(plan(old, new))["migrated_rows"] == 3


def test_third_version_removes_field_and_preserves_immutable_rule(setup):
    store, old, new, _ = setup
    store.migrate(plan(old, new))
    third = BlobSchema(old.name, {"label": Text(required=True, immutable=True)}, version=3)
    migration = MetadataMigration(
        "v3", new, third, "only-label", lambda row: {"label": row["label"]}
    )
    assert store.migrate(migration)["migrated_rows"] == 3
    assert store.stat("a")["metadata"] == {"label": "a"}
    assert store.stat("a")["version"] == 3
    store.install_schema(old)
    store.install_schema(new)
    store.install_schema(third)
    with pytest.raises(ConstraintError):
        store.catalog.sql(
            f"UPDATE {q(old.table_name)} SET label='new',version=version+1 WHERE id='a'"
        )


def test_failed_coordination_rolls_back_ddl_and_data(setup):
    store, old, new, _ = setup
    # SQLite resolves foreign-key identifiers case-insensitively.
    store.catalog.sql(
        f"CREATE TABLE child (blob_id TEXT REFERENCES {q(old.table_name.upper())}(id))"
    )
    store.catalog.sql("INSERT INTO child VALUES ('a')")
    with pytest.raises(SchemaConflictError, match="coordination"):
        store.migrate(plan(old, new))

    def before(tx):
        tx.sql("DROP TABLE child")

    with pytest.raises(SchemaConflictError, match="restore"):
        store.migrate(plan(old, new, before_ddl=before, after_ddl=lambda tx: None))
    assert store.catalog.sql("SELECT * FROM child") == [{"blob_id": "a"}]
    assert store.stat("a")["schema_version"] == 1


def test_dry_run_checks_deferred_application_constraints(setup):
    store, old, new, _ = setup
    store.catalog.sql("CREATE TABLE app_parent (id TEXT PRIMARY KEY)")
    store.catalog.sql(
        "CREATE TABLE app_child (id TEXT REFERENCES app_parent(id) DEFERRABLE INITIALLY DEFERRED)"
    )
    store.catalog.sql(
        f"CREATE TRIGGER broken AFTER UPDATE ON {q(old.table_name)} BEGIN INSERT INTO app_child VALUES ('missing'); END"
    )
    with pytest.raises(SchemaConflictError, match="foreign key"):
        store.migrate(plan(old, new), dry_run=True)
    assert store.stat("a")["schema_version"] == 1
    assert store.catalog.sql("SELECT * FROM app_child") == []


def test_migration_has_no_storage_io(setup, monkeypatch):
    import obstore

    store, old, new, _ = setup

    def forbidden(*args, **kwargs):
        pytest.fail("Migration touched payload storage")

    for name in ("get", "put", "delete", "list", "head"):
        monkeypatch.setattr(obstore, name, forbidden)
    assert store.migrate(plan(old, new))["state"] == "complete"


def test_incompatible_type_and_changed_payload_contract_refuse(setup):
    from meldstore import ValidationError

    store, old, _, _ = setup
    target = BlobSchema(old.name, {"label": Integer()}, version=2)
    with pytest.raises(SchemaConflictError, match="field type"):
        store.migrate(plan(old, target))
    assert store.stat("a")["schema_version"] == 1
    with pytest.raises(ValidationError, match="payload contract"):
        plan(old, BlobSchema(old.name, old.fields, version=2, handlers=("other",)))


def test_empty_schema_and_empty_catalog_migration(tmp_path):
    with Catalog(tmp_path / "empty.db", adapter="sqlite") as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        old, new = BlobSchema("empty", {}), BlobSchema("empty", {}, version=2)
        store.install_schema(old)
        result = store.migrate(MetadataMigration("empty", old, new, "identity", lambda v: v))
        assert result["state"] == "complete" and result["total_rows"] == 0
        source = tmp_path / "source"
        source.write_bytes(b"x")
        store.import_file(source, schema=new, metadata={}, id="a")
        third = BlobSchema("empty", {}, version=3)
        assert (
            store.migrate(MetadataMigration("empty-v3", new, third, "identity", lambda v: v))[
                "migrated_rows"
            ]
            == 1
        )


def test_temp_trigger_refused_without_silent_loss(setup):
    store, old, new, _ = setup
    store.catalog.sql(
        f"CREATE TEMP TRIGGER app_temp AFTER UPDATE ON {q(old.table_name)} BEGIN SELECT 1; END"
    )
    with pytest.raises(SchemaConflictError, match="TEMP triggers"):
        store.migrate(plan(old, new))
    assert store.catalog.sql("SELECT name FROM sqlite_temp_master WHERE name='app_temp'") == [
        {"name": "app_temp"}
    ]
    assert store.stat("a")["schema_version"] == 1
