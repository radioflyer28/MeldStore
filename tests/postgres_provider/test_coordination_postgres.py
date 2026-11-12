"""Real PostgreSQL/RustFS binding and cooperative lease tests; no lifecycle claim."""

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import obstore
import pytest

from meldstore import (
    BlobSchema,
    BusyError,
    Catalog,
    ConstraintError,
    LocalStorage,
    S3Storage,
    StorageError,
    Store,
    TransactionError,
    ValidationError,
)
from meldstore.postgres_coordination import MARKER
from meldstore.storage import hash_file


@pytest.fixture
def database(provider_environment):
    driver = provider_environment.driver
    name = 'ms_binding_' + uuid4().hex
    with driver.connect(provider_environment.dsn, autocommit=True) as admin:
        admin.execute(driver.sql.SQL('CREATE DATABASE {}').format(driver.sql.Identifier(name)))
        dsn = driver.conninfo.make_conninfo(provider_environment.dsn, dbname=name)
        try:
            yield dsn
        finally:
            admin.execute(driver.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(
                driver.sql.Identifier(name)))


@pytest.fixture
def remote(tmp_path):
    if not os.environ.get('MELDSTORE_S3_CONFIG'):
        pytest.skip('Run tools/postgres_lab.py --with-rustfs')
    options = dict(bucket=os.environ['MELDSTORE_S3_BUCKET'], prefix='binding/' + uuid4().hex,
                   config=json.loads(os.environ['MELDSTORE_S3_CONFIG']),
                   retry_config={'max_retries': 0}, client_options={'timeout': '3s'})
    return S3Storage(coordination_directory=tmp_path / 'coordinator', **options), options


@pytest.fixture
def bound(database, remote):
    storage, options = remote
    with Catalog.connect_postgres(database) as catalog:
        catalog.install_schema(BlobSchema('data', {}))
        identity = catalog.bind_postgres_storage(storage, initialize=True)
        Store(catalog, storage).install_schema(BlobSchema('data', {}))
    return database, storage, options, identity


def test_initialize_reopen_and_immutable_secret_free_binding(bound):
    dsn, storage, _, identity = bound
    local = json.loads((storage.root / MARKER).read_text())
    assert local['catalog_id'] == identity
    assert set(local) == {'version', 'catalog_id', 'storage_id', 'coordinator_id', 'location'}
    with Catalog.connect_postgres(dsn, storage=storage) as catalog:
        assert catalog.bind_postgres_storage(storage) == identity
        assert Store(catalog, storage).find(schema=BlobSchema('data', {})) == []
        record = catalog.sql('SELECT binding FROM ms_store_binding', write=False)[0]
        assert json.loads(record['binding']) == local
        for sql in ('DELETE FROM ms_store_binding', 'UPDATE ms_store_binding SET binding=binding'):
            with pytest.raises(ConstraintError):
                catalog.sql(sql)
    with Catalog.connect_postgres(dsn, storage=storage, maintenance=True) as catalog:
        catalog.require_maintenance(storage.root)


def test_wrong_database_prefix_and_local_storage_rejected(bound, provider_environment, tmp_path):
    dsn, storage, options, _ = bound
    with Catalog.connect_postgres(provider_environment.dsn) as other:
        with pytest.raises(ValidationError):
            other.bind_postgres_storage(storage)
    other = S3Storage(coordination_directory=tmp_path / 'other',
                      **dict(options, prefix='other/' + uuid4().hex))
    before = other.inventory(max_entries=10)
    with Catalog.connect_postgres(dsn) as catalog:
        with pytest.raises(ValidationError):
            catalog.bind_postgres_storage(other, initialize=True)
        with pytest.raises(ValidationError):
            catalog.bind_postgres_storage(LocalStorage(tmp_path / 'local'))
    assert other.inventory(max_entries=10) == before
    with Catalog.connect_postgres(dsn, storage=storage) as catalog:
        with pytest.raises(ValidationError):
            Store(catalog, other)
    shutil.copytree(storage.root, tmp_path / 'copied')
    with pytest.raises(StorageError):
        S3Storage(coordination_directory=tmp_path / 'copied', **options)


@pytest.mark.parametrize('artifact', [MARKER, 'meldstore-access.sqlite'])
def test_missing_artifacts_never_adopt_or_recreate(bound, artifact):
    dsn, storage, _, _ = bound
    path = storage.root / artifact
    path.unlink()
    with pytest.raises(ValidationError):
        Catalog.connect_postgres(dsn, storage=storage)
    assert not path.exists()
    with Catalog.connect_postgres(dsn) as catalog:
        with pytest.raises(ValidationError):
            catalog.bind_postgres_storage(storage, initialize=True)


def test_remote_identity_rechecked_before_sql(bound, monkeypatch):
    dsn, storage, _, _ = bound
    obstore.put(storage._store, 'meldstore-storage.json', b'{}')
    with Catalog.connect_postgres(dsn) as catalog:
        monkeypatch.setattr(catalog, 'transaction', lambda **kw: pytest.fail('SQL before identity'))
        with pytest.raises(ValidationError):
            catalog.bind_postgres_storage(storage)


def test_concurrent_clients_and_external_writer_exclude_maintenance(bound, provider_environment):
    dsn, storage, _, identity = bound
    barrier = Barrier(2)

    def participant(value):
        with Catalog.connect_postgres(dsn, storage=storage) as catalog:
            barrier.wait(timeout=10)
            assert catalog.bind_postgres_storage(storage) == identity
            with catalog.storage_access():
                with provider_environment.driver.connect(dsn) as raw:
                    assert raw.execute('SELECT %s', (value,)).fetchone() == (value,)
                with pytest.raises(TransactionError):
                    catalog.close()
                with pytest.raises(BusyError):
                    Catalog.connect_postgres(dsn, storage=storage, maintenance=True)
            barrier.wait(timeout=10)

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(participant, (1, 2)))
    with Catalog.connect_postgres(dsn, storage=storage, maintenance=True) as catalog:
        catalog.require_maintenance(storage.root)
        with pytest.raises(BusyError):
            Catalog.connect_postgres(dsn, storage=storage)
        with pytest.raises(BusyError):
            Catalog.connect_postgres(dsn, storage=storage, maintenance=True)


def test_verified_remote_reader_holds_gate_until_exit(bound, tmp_path):
    dsn, storage, _, _ = bound
    source = tmp_path / 'synthetic'
    source.write_bytes(b'synthetic reader fixture')
    size, digest = hash_file(source)
    key = 'objects/' + uuid4().hex
    storage.upload(source, key)
    with Catalog.connect_postgres(dsn, storage=storage, maintenance=True) as catalog:
        store = Store(catalog, storage)
        # Explicit fixture SQL, not a claim about the not-yet-ported publish API.
        with catalog.transaction() as tx:
            tx.sql("INSERT INTO ms_prepared VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   ('reader', storage.storage_id, key, size, digest, 'xxh3_128', 'file', 1, 'data', 1))
            tx.sql("INSERT INTO ms_blobs(id,schema_name,schema_version) VALUES('reader','data',1)")
            table = BlobSchema('data', {}).table_name_for('postgres')
            tx.sql(f"INSERT INTO {table}(id) VALUES('reader')")
            tx.sql("INSERT INTO ms_objects VALUES('reader','reader')")
            tx.sql("UPDATE ms_blobs SET state='ready' WHERE id='reader'")
        with store.materialize('reader') as path:
            assert path.read_bytes() == source.read_bytes()
            with pytest.raises(TransactionError):
                catalog.require_maintenance(storage.root)
            with pytest.raises(TransactionError):
                catalog.close()
        assert not path.exists()
        catalog.require_maintenance(storage.root)


def test_separate_process_lease_released_after_abrupt_exit(bound):
    dsn, storage, options, _ = bound
    script = '''
import json, os, sys
from meldstore import Catalog, S3Storage
storage = S3Storage(coordination_directory=sys.argv[1], **json.loads(os.environ['TEST_OPTIONS']))
with Catalog.connect_postgres(os.environ['TEST_DSN'], storage=storage) as catalog:
    with catalog.storage_access():
        print('leased', flush=True)
        sys.stdin.readline()
        os._exit(0)
'''
    env = dict(os.environ, TEST_DSN=dsn, TEST_OPTIONS=json.dumps(options))
    process = subprocess.Popen([sys.executable, '-c', script, str(storage.root)], env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'leased'
        with pytest.raises(BusyError):
            Catalog.connect_postgres(dsn, storage=storage, maintenance=True)
        process.communicate('\n', timeout=15)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    with Catalog.connect_postgres(dsn, storage=storage, maintenance=True) as catalog:
        catalog.require_maintenance(storage.root)


def test_initialization_failure_retains_evidence_and_rejects_adoption(database, remote, monkeypatch):
    storage, _ = remote
    with Catalog.connect_postgres(database) as catalog:
        catalog.install_schema(BlobSchema('data', {}))
        original = catalog._commit

        def uncertain(manager):
            original(manager)
            raise OSError('synthetic lost commit response')

        monkeypatch.setattr(catalog, '_commit', uncertain)
        with pytest.raises(Exception, match='Commit'):
            catalog.bind_postgres_storage(storage, initialize=True)
        with pytest.raises(TransactionError):
            catalog.sql('SELECT 1')
    assert (storage.root / MARKER).is_file()
    with Catalog.connect_postgres(database, storage=storage) as catalog:
        catalog.sql('SELECT singleton FROM ms_store_binding', write=False)


def test_nonpublic_schema_and_unbound_store_rejected(pg, remote):
    storage, _ = remote
    with Catalog.connect_postgres(pg.dsn) as catalog:
        with pytest.raises(ValidationError):
            Store(catalog, storage)
        with pytest.raises(ValidationError, match='dedicated public'):
            catalog.bind_postgres_storage(storage, initialize=True)


def test_rollback_during_initialization_leaves_unadoptable_evidence(database, remote, monkeypatch):
    storage, _ = remote
    with Catalog.connect_postgres(database) as catalog:
        catalog.install_schema(BlobSchema('data', {}))
        original = catalog._commit

        def rolled_back(manager):
            manager.__exit__(RuntimeError, RuntimeError('synthetic interruption'), None)
            raise OSError('synthetic lost response')

        monkeypatch.setattr(catalog, '_commit', rolled_back)
        with pytest.raises(Exception, match='Commit'):
            catalog.bind_postgres_storage(storage, initialize=True)
        monkeypatch.setattr(catalog, '_commit', original)
    assert (storage.root / MARKER).is_file()
    with pytest.raises(ValidationError):
        Catalog.connect_postgres(database, storage=storage)
    with Catalog.connect_postgres(database) as catalog:
        with pytest.raises(ValidationError):
            catalog.bind_postgres_storage(storage, initialize=True)


def test_initialization_rejects_occupied_prefix(database, remote):
    storage, _ = remote
    obstore.put(storage._store, 'unexpected', b'preserve me')
    with Catalog.connect_postgres(database) as catalog:
        catalog.install_schema(BlobSchema('data', {}))
        with pytest.raises(ValidationError, match='fresh S3'):
            catalog.bind_postgres_storage(storage, initialize=True)
    assert bytes(obstore.get(storage._store, 'unexpected').bytes()) == b'preserve me'
    assert not (storage.root / MARKER).exists()


def test_initialization_rejects_preexisting_blob_rows(database, remote):
    storage, _ = remote
    with Catalog.connect_postgres(database) as catalog:
        catalog.install_schema(BlobSchema('data', {}))
        catalog.sql("INSERT INTO ms_blobs(id,schema_name,schema_version) VALUES('old','data',1)")
        with pytest.raises(ValidationError, match='empty blob catalog'):
            catalog.bind_postgres_storage(storage, initialize=True)
    assert not (storage.root / MARKER).exists()


def test_binding_never_reads_remote_inside_sql_transaction(bound, monkeypatch):
    dsn, storage, _, _ = bound
    with Catalog.connect_postgres(dsn) as catalog:
        original = storage._remote_identity

        def checked():
            assert catalog._active is None
            return original()

        monkeypatch.setattr(storage, '_remote_identity', checked)
        catalog.bind_postgres_storage(storage)
