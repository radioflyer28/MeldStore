"""Offline SQLite-to-PostgreSQL preflight and explicit application SQL ownership."""

import json
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from test_coordination_postgres import database  # noqa: F401

from meldstore import (
    BlobSchema,
    Catalog,
    ConflictError,
    ConstraintError,
    Integer,
    LocalStorage,
    MetadataMigration,
    SQLTransferManifest,
    Store,
    TableTransfer,
    Text,
    ValidationError,
    preflight_sqlite_to_postgres,
)


@pytest.fixture(params=['sqlite', 'melddb'])
def source(tmp_path, request, provider_environment):
    schema = BlobSchema('dataset', {'label': Text(required=True)}, handlers=('bytes', 'file'))
    storage = LocalStorage(tmp_path / 'source-objects')
    with Catalog(tmp_path / 'source.sqlite', adapter=request.param, maintenance=True) as catalog:
        store = Store(catalog, storage)
        store.install_schema(schema)
        store.install_query_indexes()
        store.put(b'synthetic source bytes', schema=schema, metadata={'label': 'one'},
                  handler='bytes', id='one')
        store.put(b'retired bytes', schema=schema, metadata={'label': 'retired'},
                  handler='bytes', id='retired')
        store.delete('retired', expected_version=1)
        store.cleanup()
        catalog.sql('CREATE TABLE app_tags(id INTEGER PRIMARY KEY,label TEXT NOT NULL)')
        catalog.sql('CREATE TABLE app_links(blob_id TEXT REFERENCES ms_blobs(id), '
                    'tag INTEGER REFERENCES app_tags(id), PRIMARY KEY(blob_id,tag))')
        catalog.sql('CREATE INDEX app_links_by_tag ON app_links(tag)')
        catalog.sql('CREATE VIEW app_lookup AS SELECT b.id,t.label FROM ms_blobs b '
                    'JOIN app_links l ON l.blob_id=b.id JOIN app_tags t ON t.id=l.tag')
        catalog.sql("CREATE TRIGGER app_tag_guard BEFORE INSERT ON app_tags WHEN NEW.label='' "
                    "BEGIN SELECT RAISE(ABORT,'empty label'); END")
        catalog.sql("INSERT INTO app_tags VALUES(1,'tag')")
        catalog.sql("INSERT INTO app_links VALUES('one',1)")
        yield store, schema


@pytest.fixture
def manifest():
    def verify(tx):
        return tx.sql('SELECT * FROM app_lookup ORDER BY id') == [{'id': 'one', 'label': 'tag'}]

    return SQLTransferManifest(
        application={'id': 'synthetic', 'schema_revision': '1'},
        source_objects={'app_tags': 'table', 'app_links': 'table', 'app_lookup': 'view',
                        'app_links_by_tag': 'index', 'app_tag_guard': 'trigger'},
        target_objects={'app_tags': 'table', 'app_links': 'table', 'app_lookup': 'view',
                        'app_links_by_tag': 'index', 'app_tag_guard': 'trigger',
                        'app_tag_guard_fn': 'function', 'app_tags_pkey': 'index',
                        'app_links_pkey': 'index'},
        ddl=(
            'CREATE TABLE app_tags(id BIGINT PRIMARY KEY,label TEXT NOT NULL)',
            'CREATE TABLE app_links(blob_id TEXT REFERENCES ms_blobs(id), '
            'tag BIGINT REFERENCES app_tags(id), PRIMARY KEY(blob_id,tag))',
            'CREATE INDEX app_links_by_tag ON app_links(tag)',
            'CREATE VIEW app_lookup AS SELECT b.id,t.label FROM ms_blobs b '
            'JOIN app_links l ON l.blob_id=b.id JOIN app_tags t ON t.id=l.tag',
            "CREATE FUNCTION app_tag_guard_fn() RETURNS trigger LANGUAGE plpgsql AS "
            "$$BEGIN IF NEW.label='' THEN RAISE EXCEPTION 'empty label'; END IF; RETURN NEW; END$$",
            'CREATE TRIGGER app_tag_guard BEFORE INSERT ON app_tags '
            'FOR EACH ROW EXECUTE FUNCTION app_tag_guard_fn()',
        ),
        tables=(TableTransfer('app_tags', 'app_tags', {'id': Integer(), 'label': Text()}),
                TableTransfer('app_links', 'app_links', {'blob_id': Text(), 'tag': Integer()})),
        verify=verify,
    )


def empty_database(dsn):
    with Catalog.connect_postgres(dsn) as catalog:
        assert catalog.sql("SELECT relname FROM pg_class WHERE relnamespace='public'::regnamespace",
                           write=False) == []


def test_preflight_complete_application_schema_rolls_back(source, manifest, request):
    dsn = request.getfixturevalue('database')
    store, schema = source
    before = store.stat('one')
    result = preflight_sqlite_to_postgres(store, dsn, manifest=manifest)
    assert result == {'application': manifest.application, 'blobs': 1,
                      'application_rows': {'app_tags': 1, 'app_links': 1}, 'destination_ready': False}
    assert store.stat('one') == before
    assert store.get('one') == b'synthetic source bytes'
    empty_database(dsn)


def test_preflight_preserves_evolved_metadata_and_history(source, manifest, request):
    dsn = request.getfixturevalue('database')
    store, old = source
    new = BlobSchema(old.name, {'label': Text(required=True), 'count': Integer(required=True)},
                     version=2, handlers=old.handlers)
    store.migrate(MetadataMigration('upgrade', old, new, 'add-count', lambda row: dict(row, count=7)))
    before = store.stat('one')
    assert before['version'] == 2
    assert preflight_sqlite_to_postgres(store, dsn, manifest=manifest)['blobs'] == 1
    assert store.stat('one') == before
    empty_database(dsn)


@pytest.mark.parametrize('failure', ['inventory', 'table', 'value', 'foreign-key', 'target-inventory'])
def test_preflight_rejects_missing_mapping_or_incompatible_data(source, manifest, request, failure):
    dsn = request.getfixturevalue('database')
    store, _ = source
    if failure == 'inventory':
        manifest = replace(manifest, source_objects={})
    elif failure == 'table':
        manifest = replace(manifest, tables=manifest.tables[:1])
    elif failure == 'value':
        store.catalog.sql("UPDATE app_tags SET label=?", ('nul\x00text',))
    elif failure == 'foreign-key':
        mapping = replace(manifest.tables[1], transform=lambda row: dict(row, blob_id='missing'))
        manifest = replace(manifest, tables=(manifest.tables[0], mapping))
    else:
        manifest = replace(manifest, target_objects={})
    with pytest.raises((ValidationError, ConstraintError)):
        preflight_sqlite_to_postgres(store, dsn, manifest=manifest)
    empty_database(dsn)
    assert store.get('one') == b'synthetic source bytes'


def test_preflight_occupied_target_and_explicit_row_bound(source, manifest, request):
    dsn = request.getfixturevalue('database')
    store, _ = source
    with pytest.raises(ValidationError, match='max_rows'):
        preflight_sqlite_to_postgres(store, dsn, manifest=manifest, max_rows=1)
    empty_database(dsn)
    with Catalog.connect_postgres(dsn) as target:
        target.sql('CREATE TABLE occupied(id BIGINT)')
    with pytest.raises(ConflictError):
        preflight_sqlite_to_postgres(store, dsn, manifest=manifest)
    with Catalog.connect_postgres(dsn) as target:
        assert target.sql("SELECT relname FROM pg_class WHERE relnamespace='public'::regnamespace",
                          write=False) == [{'relname': 'occupied'}]


@pytest.fixture
def s3_options():
    if not os.environ.get('MELDSTORE_S3_CONFIG'):
        pytest.skip('Run tools/postgres_lab.py --with-rustfs')
    return dict(bucket=os.environ['MELDSTORE_S3_BUCKET'], prefix='cutover/' + uuid4().hex,
                config=json.loads(os.environ['MELDSTORE_S3_CONFIG']),
                retry_config={'max_retries': 0}, client_options={'timeout': '3s'})


@pytest.mark.parametrize('evolve', [False, True])
def test_transfer_preserves_ids_descriptors_application_sql_and_source(
    source, manifest, request, s3_options, tmp_path, evolve
):
    from meldstore import S3Storage, transfer_sqlite_to_postgres

    dsn = request.getfixturevalue('database')
    store, schema = source
    if evolve:
        new = BlobSchema(schema.name, {'label': Text(required=True), 'count': Integer(required=True)},
                         version=2, handlers=schema.handlers)
        store.migrate(MetadataMigration('upgrade', schema, new, 'add-count',
                                        lambda row: dict(row, count=9)))
        schema = new
    before = store.stat('one')
    inventory = store.storage.inventory(max_entries=30)
    target_root = tmp_path / 'target'
    result = transfer_sqlite_to_postgres(store, dsn, target_root, manifest=manifest, **s3_options)
    assert result['destination_ready'] and result['storage_id'] == store.storage.storage_id
    target_storage = S3Storage(coordination_directory=target_root, **s3_options)
    with Catalog.connect_postgres(dsn, storage=target_storage) as target:
        target_store = Store(target, target_storage)
        assert target_store.stat('one') == before
        assert target_store.get('one') == b'synthetic source bytes'
        assert target_store.deletion_status('retired')['state'] == 'done'
        assert manifest.verify(target) is True
        with pytest.raises(ConstraintError):
            target_store.delete('one', expected_version=before['version'])
        with pytest.raises(ConstraintError):
            target.sql("INSERT INTO app_links VALUES('missing',1)")
        with pytest.raises(ValidationError):
            target.sql("INSERT INTO app_tags VALUES(2,'')")
        target_store.install_schema(schema)
    assert store.stat('one') == before
    assert store.storage.inventory(max_entries=30) == inventory
    assert store.get('one') == b'synthetic source bytes'
    with pytest.raises(ConflictError):
        transfer_sqlite_to_postgres(store, dsn, target_root, manifest=manifest, **s3_options)


@pytest.mark.parametrize('stage', ['transfer_object', 'transfer_catalog', 'transfer_ready'])
def test_interrupted_transfer_never_adopts_partial_destination(
    source, manifest, request, s3_options, tmp_path, monkeypatch, stage
):
    from meldstore import S3Storage, transfer_sqlite_to_postgres
    from meldstore import backup as artifacts

    dsn = request.getfixturevalue('database')
    store, _ = source
    target_root = tmp_path / 'partial'

    def interrupt(at):
        if at == stage:
            raise RuntimeError('synthetic transfer interruption')

    monkeypatch.setattr(artifacts, '_checkpoint', interrupt)
    with pytest.raises(RuntimeError, match='interruption'):
        transfer_sqlite_to_postgres(store, dsn, target_root, manifest=manifest, **s3_options)
    assert store.get('one') == b'synthetic source bytes'
    empty_database(dsn)
    storage = S3Storage(coordination_directory=target_root, **s3_options)
    with pytest.raises(ValidationError):
        Catalog.connect_postgres(dsn, storage=storage)
    with pytest.raises(ConflictError):
        transfer_sqlite_to_postgres(store, dsn, target_root, manifest=manifest, **s3_options)


def test_transfer_detects_destination_tampering_before_sql_publication(
    source, manifest, request, s3_options, tmp_path, monkeypatch
):
    import obstore

    from meldstore import IntegrityError, S3Storage, transfer_sqlite_to_postgres

    dsn = request.getfixturevalue('database')
    store, _ = source
    original = S3Storage.upload

    def corrupt(storage, path, key):
        original(storage, path, key)
        obstore.put(storage._store, key, b'corrupt copied bytes')

    monkeypatch.setattr(S3Storage, 'upload', corrupt)
    with pytest.raises(IntegrityError):
        transfer_sqlite_to_postgres(store, dsn, tmp_path / 'tampered', manifest=manifest, **s3_options)
    empty_database(dsn)
    assert store.get('one') == b'synthetic source bytes'


def test_occupied_prefix_rejected_without_local_or_catalog_mutation(
    source, manifest, request, s3_options, tmp_path
):
    import obstore
    from obstore.store import S3Store

    from meldstore import transfer_sqlite_to_postgres
    from meldstore.s3 import _settings

    dsn = request.getfixturevalue('database')
    store, _ = source
    backend = S3Store(s3_options['bucket'], prefix=s3_options['prefix'],
                      config=_settings(s3_options['bucket'], s3_options['prefix'], s3_options['config']))
    obstore.put(backend, 'occupied', b'preserve')
    target_root = tmp_path / 'occupied'
    with pytest.raises(ConflictError):
        transfer_sqlite_to_postgres(store, dsn, target_root, manifest=manifest, **s3_options)
    assert not target_root.exists()
    assert bytes(obstore.get(backend, 'occupied').bytes()) == b'preserve'
    empty_database(dsn)
