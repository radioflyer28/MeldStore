import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import obstore
import pytest

from meldstore import BlobSchema, Catalog, LocalStorage, Store, Text


@pytest.fixture(params=["sqlite", "melddb"])
def setup_store(tmp_path, request):
    schema = BlobSchema("dataset", {"label": Text(required=True)})
    source = tmp_path / "source"
    source.write_bytes(b"abc")
    with Catalog(tmp_path / "catalog.db", adapter=request.param) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        store.install_schema(schema)
        yield store, schema, source


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_import_reopen_and_materialize_exact_file(tmp_path, adapter):
    source = tmp_path / "source.parquet"
    source.write_bytes(b"abc")
    schema = BlobSchema("dataset", {"label": Text(required=True)})
    with Catalog(tmp_path / "catalog.db", adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        store.install_schema(schema)
        blob = store.import_file(source, schema=schema, metadata={"label": "example"}, id="sample")
        assert blob["digest"] == "06b05ab6733a618578af5f94892f3950"
        assert blob["byte_size"] == 3
        assert blob["metadata"] == {"label": "example"}
    with Catalog(tmp_path / "catalog.db", adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        assert store.stat("sample") == blob
        with store.materialize("sample") as result:
            assert isinstance(result, Path)
            assert result != source
            assert result.read_bytes() == b"abc"
        assert not result.exists()
    assert source.read_bytes() == b"abc"


def test_caller_id_retry_is_idempotent_and_changed_content_conflicts(setup_store):
    from meldstore.errors import ConflictError

    store, schema, source = setup_store
    first = store.import_file(source, schema=schema, metadata={"label": "one"}, id="retry")
    assert store.import_file(source, schema=schema, metadata={"label": "one"}, id="retry") == first
    assert len(list((store.storage.root / "objects").iterdir())) == 1
    source.write_bytes(b"different")
    with pytest.raises(ConflictError):
        store.import_file(source, schema=schema, metadata={"label": "one"}, id="retry")
    assert len(list((store.storage.root / "objects").iterdir())) == 1


def test_prepared_and_publishing_are_hidden_until_shared_commit(setup_store):
    from meldstore.errors import NotFoundError

    store, schema, source = setup_store
    prepared = store.prepare_file(source, schema=schema)
    assert store.find(schema=schema) == []
    with store.catalog.transaction() as tx:
        store.finalize(prepared, tx=tx, schema=schema, metadata={"label": "one"}, id="shared")
    assert store.find(schema=schema) == []
    with pytest.raises(NotFoundError):
        store.stat("shared")
    with store.catalog.transaction() as tx:
        tx.sql("CREATE TABLE app_notes (blob_id TEXT REFERENCES ms_blobs(id), note TEXT)")
        tx.sql("INSERT INTO app_notes VALUES ('shared', 'application')")
        store.publish("shared", tx=tx)
    assert [r["id"] for r in store.find(schema=schema, where={"label": "one"})] == ["shared"]
    assert store.find(schema=schema, where={"label": "other"}) == []
    assert store.catalog.sql(
        "SELECT note FROM app_notes JOIN ms_blobs ON blob_id=id WHERE state='ready'"
    ) == [{"note": "application"}]


def test_ready_catalog_damage_is_not_reported_as_absence(setup_store):
    from meldstore.errors import IntegrityError

    store, schema, source = setup_store
    store.import_file(source, schema=schema, metadata={"label": "one"}, id="damaged")
    # Direct unauthorized bookkeeping edit simulates catalog damage.
    store.catalog.sql("DELETE FROM ms_objects WHERE blob_id='damaged'")
    with pytest.raises(IntegrityError):
        store.stat("damaged")


@pytest.mark.parametrize("damage", ["same_size", "truncated", "missing"])
def test_corrupt_or_missing_payload_never_exposes_path(setup_store, damage):
    from meldstore.errors import IntegrityError

    store, schema, source = setup_store
    blob = store.import_file(source, schema=schema, metadata={"label": "one"})
    object_path = store.storage.root / blob["object_key"]
    if damage == "missing":
        object_path.unlink()
    else:
        object_path.write_bytes(b"bad" if damage == "same_size" else b"a")
    with pytest.raises(IntegrityError):
        with store.materialize(blob["id"]):
            pytest.fail("A corrupt result must never be exposed")
    assert store.stat(blob["id"])["id"] == blob["id"]


def test_metadata_queries_do_not_call_object_storage(setup_store, monkeypatch):
    store, schema, source = setup_store
    blob = store.import_file(source, schema=schema, metadata={"label": "one"})

    def forbidden(*args, **kwargs):
        pytest.fail("Metadata-only query performed object I/O")

    for name in ("get", "head", "put", "rename", "list"):
        monkeypatch.setattr(obstore, name, forbidden)
    assert store.stat(blob["id"]) == blob
    assert store.find(schema=schema, where={"label": "one"}) == [blob]


def test_validation_precedes_upload(setup_store, monkeypatch):
    from meldstore.errors import ValidationError

    store, schema, source = setup_store

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid input attempted an upload")

    monkeypatch.setattr(obstore, "put", forbidden)
    with pytest.raises(ValidationError):
        store.import_file(source, schema=schema, metadata={})
    with pytest.raises(ValidationError):
        store.import_file(
            source, schema=schema, metadata={"label": "one"}, handler="pandas.parquet"
        )
    assert store.find(schema=schema) == []


def test_validator_receives_private_snapshot_and_cannot_change_payload(setup_store):
    from meldstore.errors import IntegrityError

    store, _, source = setup_store
    seen = []

    def validate(path):
        seen.append(path)
        assert path != source
        assert path.read_bytes() == b"abc"
        path.write_bytes(b"altered")

    schema = BlobSchema("validated", {}, validator_id="app.rule.v1", payload_validator=validate)
    store.install_schema(schema)
    with pytest.raises(IntegrityError):
        store.import_file(source, schema=schema, metadata={})
    assert len(seen) == 1
    assert not seen[0].exists()
    assert source.read_bytes() == b"abc"
    assert not (store.storage.root / "objects").exists()


def test_shared_transaction_rollback_keeps_only_prepared_object(setup_store):
    store, schema, source = setup_store
    prepared = store.prepare_file(source, schema=schema)
    with pytest.raises(RuntimeError):
        with store.catalog.transaction() as tx:
            store.finalize(prepared, tx=tx, schema=schema, metadata={"label": "one"}, id="atomic")
            tx.sql("CREATE TABLE rolled_back (blob_id TEXT REFERENCES ms_blobs(id))")
            tx.sql("INSERT INTO rolled_back VALUES ('atomic')")
            store.publish("atomic", tx=tx)
            raise RuntimeError("Application aborted")
    assert store.find(schema=schema) == []
    assert store.catalog.sql("SELECT name FROM sqlite_master WHERE name='rolled_back'") == []
    with store.catalog.transaction() as tx:
        store.finalize(prepared, tx=tx, schema=schema, metadata={"label": "one"}, id="atomic")
        store.publish("atomic", tx=tx)
    assert store.stat("atomic")["token"] == prepared.token


def test_tampered_token_poisoned_transaction_and_cannot_be_reused(setup_store):
    from meldstore.errors import ConflictError, TransactionError, ValidationError

    store, schema, source = setup_store
    prepared = store.prepare_file(source, schema=schema)
    with pytest.raises(TransactionError):
        with store.catalog.transaction() as tx:
            with pytest.raises(ValidationError):
                store.finalize(
                    replace(prepared, digest="0" * 32),
                    tx=tx,
                    schema=schema,
                    metadata={"label": "one"},
                    id="a",
                )
    with store.catalog.transaction() as tx:
        store.finalize(prepared, tx=tx, schema=schema, metadata={"label": "one"}, id="a")
        store.publish("a", tx=tx)
    with store.catalog.transaction() as tx:
        assert (
            store.finalize(prepared, tx=tx, schema=schema, metadata={"label": "one"}, id="a")["id"]
            == "a"
        )
    with pytest.raises(ConflictError):
        with store.catalog.transaction() as tx:
            store.finalize(prepared, tx=tx, schema=schema, metadata={"label": "one"}, id="b")


def test_source_and_materialization_work_are_rejected_inside_sql_transaction(setup_store):
    from meldstore.errors import TransactionError

    store, schema, source = setup_store
    with pytest.raises(TransactionError):
        with store.catalog.transaction():
            store.prepare_file(source, schema=schema)
    with pytest.raises(TransactionError):
        with store.catalog.transaction():
            with store.materialize("missing"):
                pytest.fail("Must not perform materialization inside SQL transaction")


def test_no_overwrite_promotion_retains_original_object(setup_store):
    from meldstore.errors import StorageError

    store, schema, source = setup_store
    blob = store.import_file(source, schema=schema, metadata={"label": "one"})
    source.write_bytes(b"replacement")
    with pytest.raises(StorageError):
        store.storage.upload(source, blob["object_key"])
    with store.materialize(blob["id"]) as materialized:
        assert materialized.read_bytes() == b"abc"


def test_local_root_identity_prevents_materializing_from_wrong_root(setup_store, tmp_path):
    from meldstore.errors import ValidationError

    store, schema, source = setup_store
    blob = store.import_file(source, schema=schema, metadata={"label": "one"})
    wrong = Store(store.catalog, LocalStorage(tmp_path / "wrong"))
    assert wrong.stat(blob["id"]) == blob  # Metadata is independent of object I/O.
    with pytest.raises(ValidationError):
        with wrong.materialize(blob["id"]):
            pytest.fail("Wrong storage root must be rejected")


def test_bounded_equality_query_and_keyset_pagination(setup_store):
    from meldstore.errors import ValidationError

    store, schema, source = setup_store
    for id in ("a", "b", "c"):
        store.import_file(source, schema=schema, metadata={"label": "one"}, id=id)
    assert [row["id"] for row in store.find(schema=schema, limit=2)] == ["a", "b"]
    assert [row["id"] for row in store.find(schema=schema, limit=2, after="b")] == ["c"]
    with pytest.raises(ValidationError):
        store.find(schema=schema, limit=1001)
    with pytest.raises(ValidationError):
        store.find(schema=schema, where={"missing": 1})


@pytest.mark.parametrize(
    "checkpoint", ["uploaded", "prepared", "finalized", "published", "committed"]
)
def test_process_exit_never_exposes_uncommitted_blob(setup_store, checkpoint):
    store, schema, source = setup_store
    store.catalog.sql("CREATE TABLE application_refs (blob_id TEXT REFERENCES ms_blobs(id))")
    script = """
import os, sys, obstore
from meldstore import BlobSchema, Catalog, LocalStorage, Store, Text
source, root, database, adapter, checkpoint = sys.argv[1:]
schema = BlobSchema('dataset', {'label': Text(required=True)})
with Catalog(database, adapter=adapter) as catalog:
    store = Store(catalog, LocalStorage(root))
    if checkpoint == 'uploaded':
        rename = obstore.rename
        def interrupt(*args, **kwargs):
            rename(*args, **kwargs)
            os._exit(47)
        obstore.rename = interrupt
    token = store.prepare_file(source, schema=schema)
    if checkpoint == 'prepared': os._exit(47)
    with catalog.transaction() as tx:
        store.finalize(token, tx=tx, schema=schema, metadata={'label': 'one'}, id='crash')
        tx.sql("INSERT INTO application_refs VALUES ('crash')")
        if checkpoint == 'finalized': os._exit(47)
        store.publish('crash', tx=tx)
        if checkpoint == 'published': os._exit(47)
    os._exit(47)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(source),
            str(store.storage.root),
            str(source.parent / "catalog.db"),
            store.catalog.adapter,
            checkpoint,
        ]
    )
    assert result.returncode == 47
    expected = ["crash"] if checkpoint == "committed" else []
    assert [r["id"] for r in store.find(schema=schema)] == expected
    assert [
        r["blob_id"] for r in store.catalog.sql("SELECT blob_id FROM application_refs")
    ] == expected
    assert len(list((store.storage.root / "objects").iterdir())) == 1
    assert source.read_bytes() == b"abc"
    if checkpoint == "committed":
        with store.materialize("crash") as file:
            assert file.read_bytes() == b"abc"


def test_empty_and_multipart_files_are_exact(setup_store):
    store, schema, source = setup_store
    source.write_bytes(b"")
    empty = store.import_file(source, schema=schema, metadata={"label": "empty"})
    assert empty["digest"] == "99aa06d3014798d86001c324468d497f"
    with store.materialize(empty["id"]) as path:
        assert path.read_bytes() == b""
    content = b"payload\x00" * 1024 * 1024
    source.write_bytes(content)
    large = store.import_file(source, schema=schema, metadata={"label": "multipart"})
    with store.materialize(large["id"]) as path:
        assert path.read_bytes() == content


def test_finalize_and_publish_do_not_perform_storage_io(setup_store, monkeypatch):
    store, schema, source = setup_store
    token = store.prepare_file(source, schema=schema)

    def forbidden(*args, **kwargs):
        pytest.fail("SQL publication performed storage I/O")

    for name in ("get", "head", "put", "rename", "list"):
        monkeypatch.setattr(obstore, name, forbidden)
    with store.catalog.transaction() as tx:
        store.finalize(token, tx=tx, schema=schema, metadata={"label": "one"}, id="sql-only")
        store.publish("sql-only", tx=tx)
    assert store.stat("sql-only")["state"] == "ready"


def test_sql_readiness_and_application_guards(setup_store):
    from meldstore import ConstraintError

    store, schema, source = setup_store
    store.catalog.sql(
        "INSERT INTO ms_blobs (id, schema_name, schema_version) VALUES ('bare','dataset',1)"
    )
    with pytest.raises(ConstraintError):
        store.catalog.sql("UPDATE ms_blobs SET state='ready' WHERE id='bare'")
    store.catalog.sql("CREATE TABLE approved (blob_id TEXT REFERENCES ms_blobs(id))")
    store.catalog.sql(
        "CREATE TRIGGER app_ready BEFORE UPDATE OF state ON ms_blobs "
        "WHEN NEW.state='ready' AND NOT EXISTS (SELECT 1 FROM approved WHERE blob_id=NEW.id) "
        "BEGIN SELECT RAISE(ABORT, 'application approval required'); END"
    )
    token = store.prepare_file(source, schema=schema)
    with pytest.raises(ConstraintError):
        with store.catalog.transaction() as tx:
            store.finalize(token, tx=tx, schema=schema, metadata={"label": "one"}, id="approved")
            store.publish("approved", tx=tx)
    assert store.find(schema=schema) == []
    with store.catalog.transaction() as tx:
        store.finalize(token, tx=tx, schema=schema, metadata={"label": "one"}, id="approved")
        tx.sql("INSERT INTO approved VALUES ('approved')")
        store.publish("approved", tx=tx)
    assert store.stat("approved")["state"] == "ready"


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_prepared_token_survives_reopen_with_other_adapter(tmp_path, adapter):
    source = tmp_path / "source"
    source.write_bytes(b"abc")
    schema = BlobSchema("dataset", {})
    with Catalog(tmp_path / "catalog.db", adapter=adapter) as catalog:
        catalog.install_schema(schema)  # Add payload tables without rebuilding the S01 catalog.
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        store.install_schema(schema)
        store.install_schema(schema)
        token = store.prepare_file(source, schema=schema)
    other = "sqlite" if adapter == "melddb" else "melddb"
    with Catalog(tmp_path / "catalog.db", adapter=other) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        store.install_schema(schema)
        with catalog.transaction() as tx:
            store.finalize(token, tx=tx, schema=schema, metadata={}, id="portable")
            store.publish("portable", tx=tx)
        with store.materialize("portable") as file:
            assert file.read_bytes() == b"abc"


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_128_mib_file_does_not_require_a_whole_file_memory_buffer(adapter):
    script = Path(__file__).resolve().parents[1] / "tools" / "qualify_local.py"
    result = subprocess.run(
        [sys.executable, str(script), "--mib", "128", "--adapter", adapter],
        capture_output=True,
        text=True,
        check=True,
    )
    evidence = json.loads(result.stdout)
    assert evidence["size_bytes"] == 134217728
    assert evidence["digest"] == "d138824514899b7b317d2512f67edd3f"
    # Generous allocator/OS overhead allowance, still below a single 128 MiB buffer.
    assert evidence["additional_peak_rss_bytes"] < 96 * 1024 * 1024


def test_missing_publication_guard_is_not_silently_reinstalled(setup_store):
    from meldstore import SchemaConflictError, quote_identifier

    store, schema, _ = setup_store
    store.catalog.sql("DROP TRIGGER " + quote_identifier(schema.table_name + "_ready"))
    with pytest.raises(SchemaConflictError):
        store.install_schema(schema)
