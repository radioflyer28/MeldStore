"""Real PostgreSQL 17/RustFS publication and recovery qualification."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from test_coordination_postgres import bound, database, remote  # noqa: F401

from meldstore import (
    BlobSchema,
    Catalog,
    CommitError,
    ConflictError,
    ConstraintError,
    IntegrityError,
    NotFoundError,
    PreparedFile,
    SchemaConflictError,
    StorageError,
    Store,
    TransactionError,
    TransactionOutcomeError,
)


@pytest.fixture
def deployment(request):
    dsn, storage, options, identity = request.getfixturevalue('bound')
    schema = BlobSchema('values', {}, handlers=('bytes', 'file'))
    with Catalog.connect_postgres(dsn, storage=storage) as catalog:
        Store(catalog, storage).install_schema(schema)
    return dsn, storage, options, schema


def test_remote_bytes_files_export_references_retirement(deployment, tmp_path):
    dsn, storage, _, schema = deployment
    source = tmp_path / 'source'
    source.write_bytes(b'synthetic immutable bytes')
    with Catalog.connect_postgres(dsn, storage=storage) as catalog:
        store = Store(catalog, storage)
        assert store.install_query_indexes() == ['ms_gc_pending']
        assert store.install_query_indexes() == []
        record = store.put(b'bytes value', schema=schema, metadata={}, handler='bytes', id='bytes')
        assert store.get('bytes') == b'bytes value'
        assert store.put(b'', schema=schema, metadata={}, handler='bytes', id='empty')['byte_size'] == 0
        assert store.get('empty') == b''
        store.import_file(source, schema=schema, metadata={}, id='file')
        output = store.export_file('file', tmp_path / 'out')
        assert output.read_bytes() == source.read_bytes()
        with pytest.raises(ConflictError):
            store.export_file('file', output)
        catalog.sql('CREATE TABLE app_ref(id TEXT PRIMARY KEY REFERENCES ms_blobs(id))')
        catalog.sql("INSERT INTO app_ref VALUES('bytes')")
        with pytest.raises(ConstraintError):
            store.delete('bytes', expected_version=1)
        assert store.stat('bytes') == record
        with catalog.transaction() as tx:
            tx.sql("DELETE FROM app_ref WHERE id='bytes'")
            store.delete('bytes', expected_version=1, tx=tx)
        with pytest.raises(NotFoundError):
            store.stat('bytes')
        with pytest.raises(ConflictError):
            store.put(b'bytes value', schema=schema, metadata={}, handler='bytes', id='bytes')
    with Catalog.connect_postgres(dsn, storage=storage, maintenance=True) as catalog:
        store = Store(catalog, storage)
        report = store.reconcile(verify=True)
        assert len(report['ready']) == 2 and len(report['pending']) == 1
        assert not report['catalog_errors'] and not report['corrupt'] and not report['missing']
        assert store.cleanup()[0]['state'] == 'done'
        assert store.cleanup() == []
        assert store.deletion_status('bytes')['state'] == 'done'


def test_bulk_failure_rolls_back_publication_and_application_rows(deployment):
    dsn, storage, _, schema = deployment
    with Catalog.connect_postgres(dsn, storage=storage) as catalog:
        store = Store(catalog, storage)
        prepared = store.prepare(b'bulk', schema=schema, handler='bytes')
        catalog.sql('CREATE TABLE app_ref(id TEXT PRIMARY KEY REFERENCES ms_blobs(id))')
        with pytest.raises(ConstraintError):
            with catalog.transaction() as tx:
                store.finalize(prepared, tx=tx, schema=schema, metadata={}, id='bulk')
                store.publish('bulk', tx=tx)
                tx.sql_many('INSERT INTO app_ref VALUES(%s)', [('bulk',), ('missing',)])
        assert catalog.sql('SELECT * FROM app_ref', write=False) == []
        assert store.resolve('bulk', prepared=prepared, schema=schema, metadata={})['outcome'] == 'prepared'
        with catalog.transaction() as tx:
            store.finalize(prepared, tx=tx, schema=schema, metadata={}, id='bulk')
            store.publish('bulk', tx=tx)
            tx.sql_many('INSERT INTO app_ref VALUES(%s)', [('bulk',)])
        assert store.get('bulk') == b'bulk'


def test_concurrent_same_id_retries_and_explicit_unused_token_cleanup(deployment):
    dsn, storage, _, schema = deployment
    barrier = Barrier(2)

    def publish(_):
        with Catalog.connect_postgres(dsn, storage=storage) as catalog:
            store = Store(catalog, storage)
            original = storage.upload
            # Synchronize before the operation, without changing the shared backend.
            assert callable(original)
            barrier.wait(timeout=10)
            return store.put(b'same', schema=schema, metadata={}, handler='bytes', id='same')

    with ThreadPoolExecutor(2) as pool:
        records = list(pool.map(publish, range(2)))
    assert records[0] == records[1]
    with Catalog.connect_postgres(dsn, storage=storage, maintenance=True) as catalog:
        store = Store(catalog, storage)
        with pytest.raises(ConflictError):
            store.put(b'different', schema=schema, metadata={}, handler='bytes', id='same')
        report = store.reconcile(verify=True)
        assert len(report['ready']) == 1 and not report['corrupt']
        with pytest.raises(ConflictError):
            store.queue_orphans([records[0]['object_key']])


def test_discard_and_publication_compete_atomically(deployment):
    dsn, storage, _, schema = deployment
    with Catalog.connect_postgres(dsn, storage=storage) as catalog:
        prepared = Store(catalog, storage).prepare(b'race', schema=schema, handler='bytes')
    barrier = Barrier(2)

    def race(action):
        with Catalog.connect_postgres(dsn, storage=storage) as catalog:
            store = Store(catalog, storage)
            barrier.wait(timeout=10)
            try:
                if action == 'discard':
                    store.discard_prepared(prepared)
                else:
                    with catalog.transaction() as tx:
                        store.finalize(prepared, tx=tx, schema=schema, metadata={}, id='race')
                        store.publish('race', tx=tx)
                return action
            except ConflictError:
                return 'conflict'

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(race, ('discard', 'publish')))
    assert results.count('conflict') == 1
    with Catalog.connect_postgres(dsn, storage=storage, maintenance=True) as catalog:
        store = Store(catalog, storage)
        outcome = store.resolve('race', prepared=prepared, schema=schema, metadata={})['outcome']
        assert outcome == ('discarded' if 'discard' in results else 'ready')
        cleaned = store.cleanup()
        assert bool(cleaned) == (outcome == 'discarded')
        if outcome == 'ready':
            assert store.get('race') == b'race'


@pytest.mark.parametrize('operation', ['publish', 'retire'])
def test_lost_commit_response_quarantines_and_reopens(deployment, monkeypatch, operation):
    dsn, storage, _, schema = deployment
    with Catalog.connect_postgres(dsn, storage=storage) as catalog:
        store = Store(catalog, storage)
        prepared = store.prepare(b'unknown', schema=schema, handler='bytes')
        if operation == 'retire':
            with catalog.transaction() as tx:
                store.finalize(prepared, tx=tx, schema=schema, metadata={}, id='uncertain')
                store.publish('uncertain', tx=tx)
        original = catalog._commit

        def lose(manager):
            original(manager)
            raise OSError('synthetic lost response after durable commit')

        monkeypatch.setattr(catalog, '_commit', lose)
        with pytest.raises(CommitError):
            with catalog.transaction() as tx:
                if operation == 'publish':
                    store.finalize(prepared, tx=tx, schema=schema, metadata={}, id='uncertain')
                    store.publish('uncertain', tx=tx)
                else:
                    store.delete('uncertain', expected_version=1, tx=tx)
        with pytest.raises(TransactionError):
            store.reconcile()
    with Catalog.connect_postgres(dsn, storage=storage, maintenance=True) as catalog:
        store = Store(catalog, storage)
        resolved = store.resolve('uncertain', prepared=prepared, schema=schema, metadata={})
        assert resolved['outcome'] == ('ready' if operation == 'publish' else 'retired')
        if operation == 'publish':
            assert store.get('uncertain') == b'unknown'
            with pytest.raises(ConflictError):
                store.queue_orphans([prepared.object_key])
        else:
            assert store.cleanup()[0]['state'] == 'done'


def test_real_disconnect_before_commit_retains_prepared_object(deployment, provider_environment):
    dsn, storage, _, schema = deployment
    with Catalog.connect_postgres(dsn, storage=storage) as catalog:
        store = Store(catalog, storage)
        prepared = store.prepare(b'connection loss', schema=schema, handler='bytes')
        with pytest.raises(TransactionOutcomeError):
            with catalog.transaction() as tx:
                store.finalize(prepared, tx=tx, schema=schema, metadata={}, id='lost')
                store.publish('lost', tx=tx)
                pid = tx.sql('SELECT pg_backend_pid() AS pid')[0]['pid']
                with provider_environment.driver.connect(dsn, autocommit=True) as admin:
                    assert admin.execute('SELECT pg_terminate_backend(%s)', (pid,)).fetchone()[0]
        with pytest.raises(TransactionError):
            store.stat('lost')
    with Catalog.connect_postgres(dsn, storage=storage, maintenance=True) as catalog:
        store = Store(catalog, storage)
        assert store.resolve('lost', prepared=prepared, schema=schema, metadata={})['outcome'] == 'prepared'
        with pytest.raises(ConflictError):
            store.queue_orphans([prepared.object_key])
        store.discard_prepared(prepared)
        assert store.cleanup()[0]['state'] == 'done'


@pytest.mark.parametrize('stage', ['prepared', 'finalized', 'published', 'committed'])
def test_abrupt_process_exit_preserves_inspectable_state(deployment, tmp_path, stage):
    dsn, storage, options, schema = deployment
    source = tmp_path / 'synthetic'
    source.write_bytes(b'process interruption')
    script = '''
import json, os, sys
from dataclasses import asdict
from meldstore import BlobSchema, Catalog, S3Storage, Store
storage = S3Storage(coordination_directory=sys.argv[1], **json.loads(os.environ['TEST_OPTIONS']))
schema = BlobSchema('values', {}, handlers=('bytes', 'file'))
with Catalog.connect_postgres(os.environ['TEST_DSN'], storage=storage) as catalog:
    store = Store(catalog, storage)
    prepared = store.prepare_file(sys.argv[2], schema=schema)
    print(json.dumps(asdict(prepared)), flush=True)
    if sys.argv[3] == 'prepared': os._exit(0)
    with catalog.transaction() as tx:
        store.finalize(prepared, tx=tx, schema=schema, metadata={}, id='crash')
        if sys.argv[3] == 'finalized': os._exit(0)
        store.publish('crash', tx=tx)
        if sys.argv[3] == 'published': os._exit(0)
    os._exit(0)
'''
    result = subprocess.run([sys.executable, '-c', script, str(storage.root), str(source), stage],
                            env=dict(os.environ, TEST_DSN=dsn, TEST_OPTIONS=json.dumps(options)),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, 'Child process failed'
    prepared = PreparedFile(**json.loads(result.stdout))
    with Catalog.connect_postgres(dsn, storage=storage, maintenance=True) as catalog:
        store = Store(catalog, storage)
        outcome = store.resolve('crash', prepared=prepared, schema=schema, metadata={})['outcome']
        assert outcome == ('ready' if stage == 'committed' else 'prepared')
        with pytest.raises(ConflictError):
            store.queue_orphans([prepared.object_key])
        if stage == 'committed':
            with store.materialize('crash') as path:
                assert path.read_bytes() == source.read_bytes()


def test_no_overwrite_corruption_and_guard_drift_block_unsafe_cleanup(deployment, tmp_path):
    import obstore

    dsn, storage, _, schema = deployment
    source = tmp_path / 'replacement'
    source.write_bytes(b'wrong')
    with Catalog.connect_postgres(dsn, storage=storage, maintenance=True) as catalog:
        store = Store(catalog, storage)
        record = store.put(b'original', schema=schema, metadata={}, handler='bytes', id='safe')
        with pytest.raises(StorageError):
            storage.upload(source, record['object_key'])
        assert store.get('safe') == b'original'
        obstore.put(storage._store, record['object_key'], b'corrupt!')
        with pytest.raises(IntegrityError):
            store.get('safe')
        assert store.reconcile(verify=True)['corrupt'] == [record['object_key']]
        store.delete('safe', expected_version=1)
        catalog.sql('ALTER TABLE ms_gc DISABLE TRIGGER ms_gc_identity')
        with pytest.raises(SchemaConflictError):
            store.cleanup()
        assert record['object_key'] in storage.inventory(max_entries=30)


def test_cleanup_failure_is_resumable_and_no_payload_io_in_sql(deployment, monkeypatch):
    dsn, storage, _, schema = deployment
    with Catalog.connect_postgres(dsn, storage=storage, maintenance=True) as catalog:
        store = Store(catalog, storage)
        upload = storage.upload
        delete = storage.delete_object

        def checked_upload(*args):
            assert catalog._active is None
            return upload(*args)

        monkeypatch.setattr(storage, 'upload', checked_upload)
        store.put(b'delete later', schema=schema, metadata={}, handler='bytes', id='later')
        store.delete('later', expected_version=1)

        def unavailable(*args):
            assert catalog._active is None
            raise StorageError('synthetic transport outage')

        monkeypatch.setattr(storage, 'delete_object', unavailable)
        assert store.cleanup()[0]['state'] == 'pending'
        monkeypatch.setattr(storage, 'delete_object', delete)
        assert store.cleanup()[0]['state'] == 'done'
        assert store.cleanup() == []
