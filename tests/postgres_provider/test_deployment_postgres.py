"""One explicit workflow: fresh deployment, SQLite cutover, native recovery."""

from contextlib import ExitStack, contextmanager
from uuid import uuid4

import test_archive_postgres as archive_fixtures
import test_transfer_postgres as transfer_fixtures

from meldstore import (
    BlobSchema,
    Catalog,
    Integer,
    MetadataMigration,
    S3Storage,
    Store,
    preflight_sqlite_to_postgres,
    restore_postgres_backup,
    transfer_sqlite_to_postgres,
)

source = transfer_fixtures.source
manifest = transfer_fixtures.manifest
s3_options = transfer_fixtures.s3_options
tools_for = archive_fixtures.tools_for


def test_fresh_cutover_and_restore_as_one_workflow(
    source, manifest, s3_options, tools_for, provider_environment, tmp_path
):
    driver = provider_environment.driver

    @contextmanager
    def database():
        name = 'ms_integrated_' + uuid4().hex
        with driver.connect(provider_environment.dsn, autocommit=True) as admin:
            admin.execute(driver.sql.SQL('CREATE DATABASE {}').format(driver.sql.Identifier(name)))
            try:
                yield driver.conninfo.make_conninfo(provider_environment.dsn, dbname=name)
            finally:
                admin.execute(driver.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(driver.sql.Identifier(name)))

    def options():
        return dict(s3_options, prefix='integrated/' + uuid4().hex)

    source_store, schema = source
    with ExitStack() as stack:
        fresh_dsn, cutover_dsn, restored_dsn = (stack.enter_context(database()) for _ in range(3))
        fresh_storage = S3Storage(coordination_directory=tmp_path / 'fresh', **options())
        with Catalog.connect_postgres(fresh_dsn) as catalog:
            catalog.install_schema(schema)
            catalog.bind_postgres_storage(fresh_storage, initialize=True)
            fresh = Store(catalog, fresh_storage)
            fresh.install_schema(schema)
            for statement in manifest.ddl:
                catalog.sql(statement)
            prepared = fresh.prepare(b'synthetic source bytes', schema=schema, handler='bytes')
            with catalog.transaction() as tx:
                fresh.finalize(prepared, tx=tx, schema=schema, metadata={'label': 'one'}, id='one')
                tx.sql_many('INSERT INTO app_tags VALUES(%s,%s)', [(1, 'tag')])
                tx.sql_many('INSERT INTO app_links VALUES(%s,%s)', [('one', 1)])
                fresh.publish('one', tx=tx)
            assert fresh.get('one') == source_store.get('one')
            assert manifest.verify(catalog)

        evolved = BlobSchema(schema.name, {**schema.fields, 'revision': Integer(required=True)},
                             version=2, handlers=schema.handlers)
        source_store.migrate(MetadataMigration('revision', schema, evolved, 'add-revision',
                                               lambda row: dict(row, revision=1)))
        expected = source_store.stat('one')
        assert preflight_sqlite_to_postgres(source_store, cutover_dsn, manifest=manifest)['blobs'] == 1
        cutover_options = options()
        cutover_root = tmp_path / 'cutover'
        assert transfer_sqlite_to_postgres(source_store, cutover_dsn, cutover_root,
                                           manifest=manifest, **cutover_options)['destination_ready']
        cutover_storage = S3Storage(coordination_directory=cutover_root, **cutover_options)
        with Catalog.connect_postgres(cutover_dsn, storage=cutover_storage, maintenance=True) as catalog:
            cutover = Store(catalog, cutover_storage)
            assert cutover.stat('one') == expected
            assert cutover.get('one') == source_store.get('one')
            assert manifest.verify(catalog)
            assert cutover.migration_status('revision')['state'] == 'complete'
            report = cutover.reconcile(verify=True)
            assert not any(report[k] for k in ('missing', 'corrupt', 'catalog_errors', 'pending'))
            archive = tmp_path / 'native-archive'
            backup = cutover.backup(archive, application=manifest.application, tools=tools_for(cutover_dsn))
            assert backup['catalog_backend'] == 'postgresql'

        restored_options = options()
        restored_root = tmp_path / 'restored'
        assert restore_postgres_backup(archive, restored_dsn, restored_root,
                                        tools=tools_for(restored_dsn), verify_application=manifest.verify,
                                        **restored_options)['destination_ready']
        restored_storage = S3Storage(coordination_directory=restored_root, **restored_options)
        with Catalog.connect_postgres(restored_dsn, storage=restored_storage, maintenance=True) as catalog:
            restored = Store(catalog, restored_storage)
            restored.install_schema(evolved)
            assert restored.install_query_indexes() == []
            assert restored.stat('one') == expected
            assert restored.get('one') == source_store.get('one')
            assert restored.deletion_status('retired')['state'] == 'done'
            assert manifest.verify(catalog)
            assert restored.migration_status('revision') == source_store.migration_status('revision')
            with catalog.transaction() as tx:
                tx.sql("DELETE FROM app_links WHERE blob_id='one'")
                restored.delete('one', expected_version=expected['version'], tx=tx)
            assert restored.cleanup()[0]['state'] == 'done'
        # Operator cutover is deliberately not automatic; source remains usable.
        assert source_store.stat('one') == expected
        assert source_store.get('one') == b'synthetic source bytes'
