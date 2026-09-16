import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace

import obstore
import pytest

from meldstore import (
    BlobSchema,
    BusyError,
    Catalog,
    CommitError,
    ConflictError,
    ConstraintError,
    LocalStorage,
    NotFoundError,
    Store,
    Text,
    TransactionError,
    ValidationError,
    catalog_access,
)
from meldstore import (
    quote_identifier as q,
)


@pytest.fixture(params=["sqlite", "melddb"])
def setup(request, tmp_path):
    schema = BlobSchema("dataset", {"label": Text(required=True)})
    source = tmp_path / "source"
    source.write_bytes(b"original immutable bytes")
    with Catalog(tmp_path / "catalog.db", adapter=request.param) as catalog:
        Store(catalog, LocalStorage(tmp_path / "objects")).install_schema(schema)
    return request.param, tmp_path, schema, source


@contextmanager
def opened(setup, *, maintenance=False):
    adapter, path, _, _ = setup
    with Catalog(path / "catalog.db", adapter=adapter, maintenance=maintenance) as catalog:
        yield Store(catalog, LocalStorage(path / "objects"))


def create(store, setup, id="one", *, publish=True):
    _, _, schema, source = setup
    token = store.prepare_file(source, schema=schema)
    with store.catalog.transaction() as tx:
        store.finalize(token, tx=tx, schema=schema, metadata={"label": "one"}, id=id)
        if publish:
            store.publish(id, tx=tx)
    return token


@pytest.mark.parametrize("publish", [True, False])
def test_retirement_restricts_refs_and_cleanup_is_separate(setup, publish):
    with opened(setup) as store:
        token = create(store, setup, publish=publish)
        store.catalog.sql(
            "CREATE TABLE app_ref(id TEXT REFERENCES ms_blobs(id) ON DELETE RESTRICT)"
        )
        store.catalog.sql("INSERT INTO app_ref VALUES('one')")
        with pytest.raises(ConstraintError):
            store.delete("one", expected_version=1)
        assert store.catalog.sql("SELECT * FROM ms_gc") == []
        assert store.catalog.sql("SELECT * FROM ms_retired") == []
        with pytest.raises(RuntimeError):
            with store.catalog.transaction() as tx:
                tx.sql("DELETE FROM app_ref")
                store.delete("one", expected_version=1, tx=tx)
                raise RuntimeError("abort")
        assert store.catalog.sql("SELECT * FROM app_ref") == [{"id": "one"}]
        with store.catalog.transaction() as tx:
            tx.sql("DELETE FROM app_ref")
            result = store.delete("one", expected_version=1, tx=tx)
        assert result["state"] == "pending"
        assert (store.storage.root / token.object_key).is_file()
        assert store.find(schema=setup[2]) == []
        with pytest.raises(NotFoundError):
            store.stat("one")
        assert store.delete("one", expected_version=1) == result
        with pytest.raises(ConflictError):
            store.delete("one", expected_version=2)
        with pytest.raises(TransactionError):
            store.cleanup()
    with opened(setup, maintenance=True) as store:
        assert store.cleanup()[0]["state"] == "done"
        assert store.deletion_status("one")["state"] == "done"
        assert not (store.storage.root / token.object_key).exists()
        assert store.cleanup() == []
        assert store.delete("one", expected_version=1)["state"] == "done"
        with pytest.raises(ConflictError):
            store.import_file(setup[3], schema=setup[2], metadata={"label": "new"}, id="one")


def test_stale_delete_and_bookkeeping_guards(setup):
    with opened(setup) as store:
        create(store, setup)
        store.update_metadata("one", schema=setup[2], changes={"label": "two"}, expected_version=1)
        with pytest.raises(ConflictError):
            store.delete("one", expected_version=1)
        for sql in [
            "UPDATE ms_blobs SET state='publishing' WHERE id='one'",
            "UPDATE ms_blobs SET state='pending_delete' WHERE id='one'",
            "DELETE FROM ms_blobs WHERE id='one'",
            "DELETE FROM ms_objects WHERE blob_id='one'",
            f"DELETE FROM {q(setup[2].table_name)} WHERE id='one'",
        ]:
            with pytest.raises(ConstraintError):
                store.catalog.sql(sql)
        assert store.stat("one")["version"] == 2


def test_resolve_and_explicit_discard_protect_unknown_outcomes(setup):
    with opened(setup) as store:
        token = store.prepare_file(setup[3], schema=setup[2])
        args = dict(prepared=token, schema=setup[2], metadata={"label": "one"})
        assert store.resolve("one", **args)["outcome"] == "prepared"
        with store.catalog.transaction() as tx:
            store.finalize(token, tx=tx, schema=setup[2], metadata={"label": "one"}, id="one")
        assert store.resolve("one", **args)["outcome"] == "publishing"
        with pytest.raises(ConflictError):
            store.discard_prepared(token)
        with store.catalog.transaction() as tx:
            store.publish("one", tx=tx)
        assert store.resolve("one", **args)["outcome"] == "ready"
        with pytest.raises(ConflictError):
            store.resolve("one", **dict(args, metadata={"label": "different"}))
        with pytest.raises(ConflictError):
            store.resolve("one", **dict(args, prepared=replace(token, digest="0" * 32)))
        store.delete("one", expected_version=1)
        assert store.resolve("one", **args)["outcome"] == "retired"
        unused = store.prepare_file(setup[3], schema=setup[2])
        store.discard_prepared(unused)
        assert (
            store.resolve("absent", prepared=unused, schema=setup[2], metadata={"label": "one"})[
                "outcome"
            ]
            == "discarded"
        )
        with pytest.raises(ConflictError):
            with store.catalog.transaction() as tx:
                store.finalize(
                    unused, tx=tx, schema=setup[2], metadata={"label": "one"}, id="absent"
                )


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("operation", ["publish", "delete"])
def test_lost_commit_result_requires_reopen_and_exact_resolution(
    setup, committed, operation, monkeypatch
):
    with opened(setup) as store:
        token = create(store, setup, publish=operation == "delete")
        original = store.catalog._commit

        def fail(manager):
            if committed:
                original(manager)
            raise RuntimeError("lost commit result")

        monkeypatch.setattr(store.catalog, "_commit", fail)
        with pytest.raises(CommitError):
            with store.catalog.transaction() as tx:
                tx.sql("CREATE TABLE app_atomic(note TEXT)")
                tx.sql("INSERT INTO app_atomic VALUES('committed together')")
                if operation == "publish":
                    store.publish("one", tx=tx)
                else:
                    store.delete("one", expected_version=1, tx=tx)
        with pytest.raises(TransactionError):
            store.stat("one")
    with opened(setup) as store:
        outcome = store.resolve("one", prepared=token, schema=setup[2], metadata={"label": "one"})[
            "outcome"
        ]
        assert (
            outcome == ("ready" if operation == "publish" else "retired")
            if committed
            else outcome == ("publishing" if operation == "publish" else "ready")
        )
        assert (
            bool(store.catalog.sql("SELECT name FROM sqlite_master WHERE name='app_atomic'"))
            == committed
        )


def test_materialized_reader_and_direct_sql_lease_exclude_maintenance(setup):
    _, path, _, _ = setup
    with opened(setup) as store:
        create(store, setup)
        with store.materialize("one") as file:
            store.delete("one", expected_version=1)
            assert file.read_bytes() == setup[3].read_bytes()
            with pytest.raises(TransactionError):
                store.catalog.close()
            with pytest.raises(BusyError):
                Catalog(path / "catalog.db", maintenance=True)
    with catalog_access(path / "catalog.db"):
        with pytest.raises(BusyError):
            Catalog(path / "catalog.db", maintenance=True)
    with opened(setup, maintenance=True) as store:
        with pytest.raises(BusyError):
            Catalog(path / "catalog.db")
        assert store.cleanup()[0]["state"] == "done"


def test_reconcile_reports_without_deleting_and_orphans_require_explicit_keys(setup):
    with opened(setup) as store:
        good = create(store, setup)
        missing = create(store, setup, "missing")
        corrupt = create(store, setup, "corrupt")
        held = store.prepare_file(setup[3], schema=setup[2])
        (store.storage.root / missing.object_key).unlink()
        (store.storage.root / corrupt.object_key).write_bytes(b"x" * corrupt.byte_size)
        orphan = "objects/" + "a" * 32
        stage = "staging/" + "b" * 32
        for key in (orphan, stage, "unrelated.txt"):
            obstore.put(store.storage._store, key, b"orphan")
    with opened(setup, maintenance=True) as store:
        report = store.reconcile(verify=True)
        assert report["missing"] == [missing.object_key]
        assert report["corrupt"] == [corrupt.object_key]
        assert report["orphans"] == [orphan]
        assert report["staging"] == [stage]
        assert report["unrecognized"] == ["unrelated.txt"]
        assert [p["token"] for p in report["prepared"]] == [held.token]
        with pytest.raises(ConflictError):
            store.queue_orphans([held.object_key])
        assert store.cleanup() == []
        store.queue_orphans([orphan, stage])
        assert len(store.cleanup()) == 2
        assert (store.storage.root / good.object_key).exists()
        assert (store.storage.root / held.object_key).exists()
        assert (store.storage.root / "unrelated.txt").exists()
        store.discard_prepared(held)
        assert store.cleanup()[0]["state"] == "done"


def test_storage_failure_stays_pending_and_missing_object_retry_succeeds(setup, monkeypatch):
    with opened(setup) as store:
        token = create(store, setup)
        store.delete("one", expected_version=1)
    with opened(setup, maintenance=True) as store:
        delete = obstore.delete

        def denied(*args, **kwargs):
            raise PermissionError("access denied")

        monkeypatch.setattr(obstore, "delete", denied)
        assert store.cleanup()[0]["state"] == "pending"
        assert store.deletion_status("one")["last_error"]
        monkeypatch.setattr(obstore, "delete", delete)
        (store.storage.root / token.object_key).unlink()
        assert store.cleanup()[0]["state"] == "done"


def test_root_cannot_be_shared_by_different_catalogs(setup):
    with Catalog(setup[1] / "other.db", adapter=setup[0]) as catalog:
        with pytest.raises(ValidationError, match="another catalog"):
            Store(catalog, LocalStorage(setup[1] / "objects"))


@pytest.mark.parametrize(
    "checkpoint",
    ["retired_uncommitted", "retired_committed", "object_deleted", "cleanup_committed"],
)
def test_process_termination_around_retirement_and_cleanup(setup, checkpoint):
    with opened(setup) as store:
        token = create(store, setup)
    code = """
import os,sys,obstore
from meldstore import Catalog,LocalStorage,Store
db,root,adapter,checkpoint=sys.argv[1:]
with Catalog(db,adapter=adapter,maintenance=True) as catalog:
    store=Store(catalog,LocalStorage(root))
    with catalog.transaction() as tx:
        store.delete('one',expected_version=1,tx=tx)
        if checkpoint=='retired_uncommitted': os._exit(71)
    if checkpoint=='retired_committed': os._exit(71)
    if checkpoint=='object_deleted':
        original=obstore.delete
        def stop(*args,**kwargs):
            original(*args,**kwargs)
            os._exit(71)
        obstore.delete=stop
    store.cleanup()
    os._exit(71)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(setup[1] / "catalog.db"),
            str(setup[1] / "objects"),
            setup[0],
            checkpoint,
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 71, result.stderr.decode()
    with opened(setup, maintenance=True) as store:
        if checkpoint == "retired_uncommitted":
            assert store.stat("one")["state"] == "ready"
            assert store.cleanup() == []
            assert (store.storage.root / token.object_key).exists()
        else:
            assert store.find(schema=setup[2]) == []
            if checkpoint == "object_deleted":
                assert store.deletion_status("one")["state"] == "pending"
                assert not (store.storage.root / token.object_key).exists()
            store.cleanup()
            assert store.deletion_status("one")["state"] == "done"


def test_access_exclusion_is_cross_process_and_covers_standalone_sql(setup):
    script = """
import sys,sqlite3
from meldstore import BusyError,Catalog,catalog_access
db,mode=sys.argv[1:]
try:
    if mode=='maintenance':
        with Catalog(db,maintenance=True): pass
    elif mode=='sql':
        with catalog_access(db):
            with sqlite3.connect(db) as raw: raw.execute('SELECT 1')
    else:
        with Catalog(db): pass
except BusyError:
    sys.exit(74)
sys.exit(1)
"""

    def blocked(mode):
        result = subprocess.run(
            [sys.executable, "-c", script, str(setup[1] / "catalog.db"), mode],
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 74, result.stderr.decode()

    with opened(setup) as store:
        create(store, setup)
        with store.materialize("one"):
            blocked("maintenance")
    with catalog_access(setup[1] / "catalog.db"):
        blocked("maintenance")
    with opened(setup, maintenance=True):
        blocked("normal")
        blocked("sql")


def test_maintenance_bounds_paths_and_protected_catalog_references(setup):
    from meldstore import IntegrityError

    with opened(setup) as store:
        token = create(store, setup)
    with opened(setup, maintenance=True) as store:
        for keys in [
            [],
            ["../source"],
            ["meldstore-storage.json"],
            ["objects/"],
            ["objects/" + "a" * 32] * 2,
        ]:
            with pytest.raises(ValidationError):
                store.queue_orphans(keys)
        for limit in (0, True, 1001):
            with pytest.raises(ValidationError):
                store.cleanup(limit=limit)
        with pytest.raises(ValidationError, match="max_entries"):
            store.reconcile(max_entries=1)
        # Deliberately corrupted cleanup intent must not delete a live reference.
        store.catalog.sql(
            "INSERT INTO ms_gc(storage_id,object_key,reason) VALUES(?,?,'orphan')",
            (token.storage_id, token.object_key),
        )
        with pytest.raises(ConflictError):
            store.cleanup()
        assert (store.storage.root / token.object_key).exists()
        store.catalog.sql(
            "CREATE TABLE bad_fk(id TEXT REFERENCES ms_blobs(id) DEFERRABLE INITIALLY DEFERRED)"
        )
        # Foreign-key corruption is simulated with a separate unauthorized driver.
        import sqlite3

        with sqlite3.connect(setup[1] / "catalog.db") as raw:
            raw.execute("INSERT INTO bad_fk VALUES('missing')")
        with pytest.raises(IntegrityError):
            store.cleanup()
        assert store.reconcile()["catalog_errors"]


def test_retirement_works_after_metadata_evolution(setup):
    from meldstore import Integer, MetadataMigration

    old = setup[2]
    new = BlobSchema(old.name, {**old.fields, "revision": Integer(required=True)}, version=2)
    with opened(setup) as store:
        create(store, setup)
        migration = MetadataMigration(
            "upgrade", old, new, "revision-1", lambda values: dict(values, revision=1)
        )
        store.migrate(migration, max_batches=0)
        with pytest.raises(ConflictError, match="migration"):
            store.delete("one", expected_version=1)
        store.migrate(migration)
        assert store.delete("one", expected_version=2)["schema_version"] == 2
    with opened(setup, maintenance=True) as store:
        assert store.cleanup()[0]["state"] == "done"


def test_sql_only_retirement_and_discard(setup, monkeypatch):
    with opened(setup) as store:
        token = create(store, setup)
        unused = store.prepare_file(setup[3], schema=setup[2])

        def forbidden(*args, **kwargs):
            pytest.fail("Lifecycle SQL operation touched object storage")

        for name in ("put", "get", "head", "list", "delete", "rename"):
            monkeypatch.setattr(obstore, name, forbidden)
        store.delete("one", expected_version=1)
        store.discard_prepared(unused)
        assert (
            store.resolve("one", prepared=token, schema=setup[2], metadata={"label": "one"})[
                "outcome"
            ]
            == "retired"
        )


def test_cleanup_commit_failure_is_resolved_by_reopening(setup, monkeypatch):
    with opened(setup) as store:
        token = create(store, setup)
        store.delete("one", expected_version=1)
    with opened(setup, maintenance=True) as store:
        original = store.catalog._commit
        calls = 0

        def fail(manager):
            nonlocal calls
            calls += 1
            if calls == 2:  # selection transaction committed, object deleted, job still pending
                raise RuntimeError("before cleanup result commit")
            original(manager)

        monkeypatch.setattr(store.catalog, "_commit", fail)
        with pytest.raises(CommitError):
            store.cleanup()
        assert not (store.storage.root / token.object_key).exists()
    with opened(setup, maintenance=True) as store:
        assert store.deletion_status("one")["state"] == "pending"
        assert store.cleanup()[0]["state"] == "done"


def test_missing_retirement_guard_is_not_repaired(setup):
    from meldstore import SchemaConflictError

    with opened(setup) as store:
        create(store, setup)
        store.catalog.sql(f"DROP TRIGGER {q(setup[2].table_name + '_retire')}")
        with pytest.raises(SchemaConflictError):
            store.install_schema(setup[2])
        with pytest.raises(SchemaConflictError):
            store.delete("one", expected_version=1)


def test_install_lifecycle_upgrades_s02_without_rebuilding_existing_tables(tmp_path):
    from meldstore import payload_catalog

    schema = BlobSchema("old", {})
    with Catalog(tmp_path / "old.db", adapter="sqlite") as catalog:
        with catalog.transaction() as tx:
            catalog.install_schema(schema, tx=tx)
            payload_catalog.install(schema, tx)
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        source = tmp_path / "source"
        source.write_bytes(b"old bytes")
        blob = store.import_file(source, schema=schema, metadata={}, id="old")
        original = catalog.sql(
            "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        store.install_lifecycle()
        current = {
            row["name"]: row["sql"]
            for row in catalog.sql("SELECT name,sql FROM sqlite_master WHERE type='table'")
        }
        assert all(current[row["name"]] == row["sql"] for row in original)
        assert store.stat("old") == blob
        store.delete("old", expected_version=1)


def test_cleanup_rejects_symlink_target(setup):
    with opened(setup, maintenance=True) as store:
        directory = store.storage.root / "objects"
        directory.mkdir(exist_ok=True)
        key = "objects/" + "c" * 32
        try:
            (store.storage.root / key).symlink_to(setup[3])
        except OSError:
            pytest.skip("Creating symlinks requires OS permission")
        with pytest.raises(ValidationError, match="symlinks"):
            store.queue_orphans([key])
        assert setup[3].read_bytes() == b"original immutable bytes"
