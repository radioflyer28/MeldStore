"""Native PostgreSQL 17 archive and operator-assisted restore on pinned RustFS."""

import json
import os
from uuid import uuid4

import pytest
from test_coordination_postgres import bound, database, remote  # noqa: F401
from test_lifecycle_postgres import deployment  # noqa: F401

from meldstore import (
    Catalog,
    ConflictError,
    ConstraintError,
    IntegrityError,
    PostgresArchiveTools,
    S3Storage,
    Store,
    ValidationError,
    restore_postgres_backup,
)


@pytest.fixture
def tools_for(provider_environment):
    container = os.environ.get('MELDSTORE_PG_CONTAINER')
    assert container, 'Native archive tests require the disposable PostgreSQL Docker lab'

    def tools(dsn):
        internal = provider_environment.driver.conninfo.make_conninfo(dsn, host='127.0.0.1', port=5432)
        env = ('PGHOST', 'PGPORT', 'PGUSER', 'PGPASSWORD', 'PGDATABASE', 'PGCONNECT_TIMEOUT')
        prefix = ('docker', 'exec', '-i', *(v for name in env for v in ('--env', name)), container)
        return PostgresArchiveTools(internal, prefix=prefix)

    return tools


@pytest.fixture
def restore_database(provider_environment):
    driver = provider_environment.driver
    name = 'ms_restore_' + uuid4().hex
    with driver.connect(provider_environment.dsn, autocommit=True) as admin:
        admin.execute(driver.sql.SQL('CREATE DATABASE {}').format(driver.sql.Identifier(name)))
        try:
            yield driver.conninfo.make_conninfo(provider_environment.dsn, dbname=name)
        finally:
            admin.execute(driver.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(driver.sql.Identifier(name)))


@pytest.fixture
def restore_options():
    if not os.environ.get('MELDSTORE_S3_CONFIG'):
        pytest.skip('Run tools/postgres_lab.py --with-rustfs')
    return dict(bucket=os.environ['MELDSTORE_S3_BUCKET'], prefix='restore/' + uuid4().hex,
                config=json.loads(os.environ['MELDSTORE_S3_CONFIG']),
                retry_config={'max_retries': 0}, client_options={'timeout': '3s'})


def verify_application(tx):
    return tx.sql('SELECT * FROM app_view ORDER BY id') == [{'id': 'one', 'label': 'kept'}] and tx.sql(
        'SELECT * FROM app_aux.settings') == [{'id': 1, 'value': 'external schema data'}]


@pytest.fixture
def archive_source(request):
    dsn, storage, options, schema = request.getfixturevalue('deployment')
    with Catalog.connect_postgres(dsn, storage=storage, maintenance=True) as catalog:
        store = Store(catalog, storage)
        store.put(b'archive bytes', schema=schema, metadata={}, handler='bytes', id='one')
        catalog.sql('CREATE TABLE app_links(id TEXT PRIMARY KEY REFERENCES ms_blobs(id),label TEXT NOT NULL)')
        catalog.sql("INSERT INTO app_links VALUES('one','kept')")
        catalog.sql('CREATE INDEX app_label_index ON app_links(label)')
        catalog.sql('CREATE VIEW app_view AS SELECT b.id,a.label FROM ms_blobs b JOIN app_links a ON b.id=a.id')
        catalog.sql("CREATE FUNCTION app_guard_fn() RETURNS trigger LANGUAGE plpgsql AS "
                    "$$BEGIN IF NEW.label='' THEN RAISE EXCEPTION USING ERRCODE='23514',"
                    "MESSAGE='label required'; END IF; RETURN NEW; END$$")
        catalog.sql('CREATE TRIGGER app_guard BEFORE UPDATE ON app_links FOR EACH ROW EXECUTE FUNCTION app_guard_fn()')
        catalog.sql('CREATE SCHEMA app_aux')
        catalog.sql('CREATE TABLE app_aux.settings(id BIGINT PRIMARY KEY,value TEXT NOT NULL)')
        catalog.sql("INSERT INTO app_aux.settings VALUES(1,'external schema data')")
        yield store, dsn, options, schema


@pytest.fixture
def artifact(archive_source, tools_for, tmp_path):
    store, dsn, _, _ = archive_source
    path = tmp_path / 'archive'
    manifest = store.backup(path, application={'id': 'native-test', 'schema_revision': '1'},
                            tools=tools_for(dsn))
    return path, manifest


def test_native_archive_restores_whole_application_sql_and_payloads(
    archive_source, artifact, restore_database, restore_options, tools_for, tmp_path
):
    store, _, _, _ = archive_source
    path, manifest = artifact
    assert manifest['catalog_backend'] == 'postgresql' and manifest['version'] == 2
    assert manifest['archive_format'] == 'custom'
    assert not (path / 'catalog.sqlite').exists()
    assert 'password' not in (path / 'manifest.json').read_text().lower()
    target_root = tmp_path / 'restored'
    result = restore_postgres_backup(path, restore_database, target_root,
                                     tools=tools_for(restore_database), verify_application=verify_application,
                                     **restore_options)
    assert result['destination_ready']
    storage = S3Storage(coordination_directory=target_root, **restore_options)
    with Catalog.connect_postgres(restore_database, storage=storage) as catalog:
        restored = Store(catalog, storage)
        assert restored.stat('one') == store.stat('one')
        assert restored.get('one') == b'archive bytes'
        assert verify_application(catalog)
        with pytest.raises(ConstraintError):
            restored.delete('one', expected_version=1)
        with pytest.raises(ConstraintError):
            catalog.sql("UPDATE app_links SET label='' WHERE id='one'")
        with pytest.raises(ConstraintError):
            catalog.sql("INSERT INTO app_links VALUES('missing','label')")
    assert store.get('one') == b'archive bytes'


@pytest.mark.parametrize('unfinished', ['prepared', 'publishing', 'pending-delete'])
def test_archive_rejects_unfinished_lifecycle_before_manifest(archive_source, tools_for, tmp_path, unfinished):
    store, dsn, _, schema = archive_source
    prepared = store.prepare(b'unfinished', schema=schema, handler='bytes')
    if unfinished != 'prepared':
        with store.catalog.transaction() as tx:
            store.finalize(prepared, tx=tx, schema=schema, metadata={}, id='unfinished')
            if unfinished == 'pending-delete':
                store.publish('unfinished', tx=tx)
        if unfinished == 'pending-delete':
            store.delete('unfinished', expected_version=1)
    destination = tmp_path / 'unfinished'
    with pytest.raises(ConflictError):
        store.backup(destination, application={'id': 'test', 'schema_revision': '1'}, tools=tools_for(dsn))
    assert not destination.exists()


@pytest.mark.parametrize('stage', ['postgres_backup_catalog', 'postgres_backup_object', 'postgres_backup_manifest'])
def test_archive_manifest_is_last(archive_source, tools_for, tmp_path, monkeypatch, stage):
    from meldstore import backup as artifacts

    store, dsn, _, _ = archive_source
    destination = tmp_path / 'incomplete'

    def interrupt(at):
        if at == stage:
            raise RuntimeError('synthetic archive interruption')

    monkeypatch.setattr(artifacts, '_checkpoint', interrupt)
    with pytest.raises(RuntimeError, match='interruption'):
        store.backup(destination, application={'id': 'test', 'schema_revision': '1'}, tools=tools_for(dsn))
    assert not (destination / 'manifest.json').exists()
    assert store.get('one') == b'archive bytes'


@pytest.mark.parametrize('tamper', ['archive', 'payload', 'manifest'])
def test_tampered_or_incomplete_artifact_never_mutates_target(
    artifact, restore_database, restore_options, tools_for, tmp_path, tamper
):
    path, manifest = artifact
    if tamper == 'archive':
        with (path / 'catalog.pgcustom').open('ab') as stream:
            stream.write(b'tampering')
    elif tamper == 'payload':
        (path / 'storage' / manifest['objects'][0]['object_key']).write_bytes(b'corrupt')
    else:
        (path / 'manifest.json').unlink()
    destination = tmp_path / 'rejected'
    with pytest.raises(IntegrityError):
        restore_postgres_backup(path, restore_database, destination, tools=tools_for(restore_database),
                                verify_application=verify_application, **restore_options)
    assert not destination.exists()
    with Catalog.connect_postgres(restore_database) as catalog:
        assert catalog.sql("SELECT relname FROM pg_class WHERE relnamespace='public'::regnamespace",
                           write=False) == []


@pytest.mark.parametrize('stage', ['postgres_restore_catalog', 'postgres_restore_object', 'postgres_restore_ready'])
def test_interrupted_restore_withholds_admission(
    artifact, restore_database, restore_options, tools_for, tmp_path, monkeypatch, stage
):
    from meldstore import backup as artifacts

    path, _ = artifact
    destination = tmp_path / 'partial-restore'

    def interrupt(at):
        if at == stage:
            raise RuntimeError('synthetic restore interruption')

    monkeypatch.setattr(artifacts, '_checkpoint', interrupt)
    with pytest.raises(RuntimeError, match='interruption'):
        restore_postgres_backup(path, restore_database, destination, tools=tools_for(restore_database),
                                verify_application=verify_application, **restore_options)
    storage = S3Storage(coordination_directory=destination, **restore_options)
    with pytest.raises(ValidationError):
        Catalog.connect_postgres(restore_database, storage=storage)


def test_wrong_dump_database_fails_snapshot_binding(archive_source, restore_database, tools_for, tmp_path):
    store, _, _, _ = archive_source
    path = tmp_path / 'wrong-database'
    with pytest.raises(ValidationError, match='dump tool failed'):
        store.backup(path, application={'id': 'test', 'schema_revision': '1'},
                     tools=tools_for(restore_database))
    assert not (path / 'manifest.json').exists()


def test_wrong_restore_tool_database_fails_before_archive_write(
    archive_source, artifact, restore_database, restore_options, tools_for, tmp_path
):
    store, source_dsn, _, _ = archive_source
    path, _ = artifact
    destination = tmp_path / 'wrong-tools'
    with pytest.raises(ValidationError):
        restore_postgres_backup(path, restore_database, destination, tools=tools_for(source_dsn),
                                verify_application=verify_application, **restore_options)
    assert not destination.exists()
    assert store.get('one') == b'archive bytes'


def test_occupied_restore_database_is_never_overwritten(artifact, restore_database, restore_options, tools_for, tmp_path):
    path, _ = artifact
    with Catalog.connect_postgres(restore_database) as catalog:
        catalog.sql('CREATE TABLE existing(id BIGINT)')
        catalog.sql('INSERT INTO existing VALUES(7)')
    with pytest.raises(ConflictError):
        restore_postgres_backup(path, restore_database, tmp_path / 'occupied',
                                tools=tools_for(restore_database), verify_application=verify_application,
                                **restore_options)
    with Catalog.connect_postgres(restore_database) as catalog:
        assert catalog.sql('SELECT id FROM existing', write=False) == [{'id': 7}]


def test_archive_rejects_running_metadata_migration(archive_source, tools_for, tmp_path):
    from meldstore import BlobSchema, Integer, MetadataMigration

    store, dsn, _, old = archive_source
    store.put(b'second', schema=old, metadata={}, handler='bytes', id='two')
    new = BlobSchema(old.name, {'count': Integer(required=True)}, version=2, handlers=old.handlers)
    plan = MetadataMigration('count', old, new, 'add-count', lambda row: {'count': 1})
    assert store.migrate(plan, batch_size=1, max_batches=1)['state'] == 'running'
    with pytest.raises(ConflictError, match='migrations'):
        store.backup(tmp_path / 'running-migration', application={'id': 'test', 'schema_revision': '1'},
                     tools=tools_for(dsn))


def test_failed_application_verification_withholds_restore_readiness(
    artifact, restore_database, restore_options, tools_for, tmp_path
):
    path, _ = artifact
    destination = tmp_path / 'unverified-app'
    with pytest.raises(IntegrityError, match='Application restore'):
        restore_postgres_backup(path, restore_database, destination, tools=tools_for(restore_database),
                                verify_application=lambda tx: False, **restore_options)
    storage = S3Storage(coordination_directory=destination, **restore_options)
    with pytest.raises(ValidationError):
        Catalog.connect_postgres(restore_database, storage=storage)


def test_lost_final_restore_response_can_be_inspected_after_reopen(
    artifact, restore_database, restore_options, tools_for, tmp_path, monkeypatch
):
    from meldstore import CommitError
    from meldstore import backup as artifacts

    path, _ = artifact
    destination = tmp_path / 'uncertain-restore'
    lose = False
    original = Catalog._commit

    def commit(catalog, manager):
        nonlocal lose
        original(catalog, manager)
        if lose:
            lose = False
            raise OSError('synthetic lost final response')

    def checkpoint(stage):
        nonlocal lose
        if stage == 'postgres_restore_ready':
            lose = True

    monkeypatch.setattr(Catalog, '_commit', commit)
    monkeypatch.setattr(artifacts, '_checkpoint', checkpoint)
    with pytest.raises(CommitError):
        restore_postgres_backup(path, restore_database, destination, tools=tools_for(restore_database),
                                verify_application=verify_application, **restore_options)
    storage = S3Storage(coordination_directory=destination, **restore_options)
    with Catalog.connect_postgres(restore_database, storage=storage) as catalog:
        assert Store(catalog, storage).get('one') == b'archive bytes'
        assert verify_application(catalog)


def test_occupied_restore_prefix_has_no_target_side_effects(
    artifact, restore_database, restore_options, tools_for, tmp_path
):
    import obstore
    from obstore.store import S3Store

    from meldstore.s3 import _settings

    path, _ = artifact
    backend = S3Store(restore_options['bucket'], prefix=restore_options['prefix'],
                      config=_settings(restore_options['bucket'], restore_options['prefix'], restore_options['config']))
    obstore.put(backend, 'occupied', b'preserve')
    destination = tmp_path / 'occupied-prefix'
    with pytest.raises(ConflictError):
        restore_postgres_backup(path, restore_database, destination, tools=tools_for(restore_database),
                                verify_application=verify_application, **restore_options)
    assert not destination.exists()
    with Catalog.connect_postgres(restore_database) as catalog:
        assert catalog.sql("SELECT relname FROM pg_class WHERE relnamespace='public'::regnamespace",
                           write=False) == []
    assert bytes(obstore.get(backend, 'occupied').bytes()) == b'preserve'
