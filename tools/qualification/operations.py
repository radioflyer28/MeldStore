"""Whole-store operational phases and narrow existing fault-injection seams."""

from contextlib import nullcontext
from unittest.mock import patch

from meldstore import (
    CommitError,
    ConflictError,
    IntegrityError,
    TransactionError,
    preflight_sqlite_to_postgres,
    restore_backup_to_s3,
    restore_postgres_backup,
    transfer_sqlite_to_postgres,
)
from meldstore import backup as artifacts
from tools.qualification.fixture import (
    APP,
    SCHEMA,
    metadata,
    transfer_limits,
    transfer_manifest,
    verify_application,
)
from tools.qualification.workload import opened, require, verify_payloads


def uncertain_publication(config, manifest, corpus, metrics):
    entry = manifest['files'][0]
    with metrics.operation('uncertain_publication'):
        with opened(config) as store:
            prepared = store.prepare_file(corpus / entry['name'], schema=SCHEMA)
            original = store.catalog._commit  # Existing MeldStore test seam, not provider internals.

            def lose(manager):
                original(manager)
                raise OSError('synthetic lost commit response')

            with patch.object(store.catalog, '_commit', lose):
                try:
                    with store.catalog.transaction() as tx:
                        store.finalize(prepared, tx=tx, schema=SCHEMA,
                                       metadata=metadata(entry), id='uncertain-probe')
                        store.publish('uncertain-probe', tx=tx)
                except CommitError:
                    pass
                else:
                    raise AssertionError('Lost response was reported as success')
                try:
                    store.stat('uncertain-probe')
                except TransactionError:
                    pass
                else:
                    raise AssertionError('Uncertain handle was reusable')
        with opened(config, maintenance=True) as store:
            result = store.resolve('uncertain-probe', prepared=prepared, schema=SCHEMA,
                                   metadata=metadata(entry))
            require(result['outcome'] == 'ready', 'Durable publication could not be resolved')
            record = store.stat('uncertain-probe')
            require(record['digest'] == entry['xxh3_128'], 'Recovered digest differs')
            try:
                store.queue_orphans([prepared.object_key])
            except ConflictError:
                pass
            else:
                raise AssertionError('Referenced payload was eligible for cleanup')
            store.delete('uncertain-probe', expected_version=record['version'])
            store.cleanup()


def operational(config, manifest, work, services, metrics, budget):
    postgres = config['adapter'] == 'postgres'
    archive = work / 'archive'
    with opened(config, maintenance=True) as store:
        store.cleanup()
        before = [store.stat(e['id']) for e in manifest['files']]
        tools = services.tools(config['dsn']) if postgres else None
        stage = 'postgres_backup_catalog' if postgres else 'backup_catalog'

        def interrupt(at):
            budget.check()
            if at == stage:
                raise InterruptedError('synthetic backup interruption')

        with metrics.operation('interrupted_backup'):
            with patch.object(artifacts, '_checkpoint', interrupt):
                try:
                    store.backup(work / 'interrupted', application=APP, tools=tools)
                except InterruptedError:
                    pass
                else:
                    raise AssertionError('Interruption seam was not reached')
            require(not (work / 'interrupted/manifest.json').exists(), 'Partial backup has manifest')
        with metrics.operation('backup', bytes=sum(e['bytes'] for e in manifest['files'])):
            with patch.object(artifacts, '_checkpoint', lambda stage: budget.check()):
                backup = store.backup(archive, application=APP, tools=tools)
        require([store.stat(e['id']) for e in manifest['files']] == before, 'Backup changed source')

    with services.database() if postgres else nullcontext(None) as target_dsn:
        target = work / 'corrupt-restore'
        options = services.options()

        def restore():
            if postgres:
                result = restore_postgres_backup(
                    archive, target_dsn, target, tools=services.tools(target_dsn),
                    verify_application=lambda tx: verify_application(tx, manifest), **options)
                require(result['destination_ready'], 'Restore not ready')
                return {'adapter': 'postgres', 'dsn': target_dsn, 'coordinator': target,
                        'options': options}
            result = restore_backup_to_s3(archive, target, **options)
            return {'adapter': config['adapter'], 'catalog': result['catalog'],
                    'coordinator': result['storage'], 'options': options}

        # Corrupt one byte, prove rejection, then restore the original byte in this owned artifact.
        payload = archive / 'storage' / backup['objects'][0]['object_key']
        with metrics.operation('corrupt_restore_refused'):
            with payload.open('r+b') as stream:
                original = stream.read(1)
                stream.seek(0)
                stream.write(bytes([original[0] ^ 255]))
            try:
                try:
                    restore()
                except IntegrityError:
                    pass
                else:
                    raise AssertionError('Corrupted artifact accepted')
                require(not (target / 'catalog.sqlite').exists(), 'Corrupt restore published catalog')
                if postgres:
                    require(not target.exists(), 'Corrupt native archive mutated target')
            finally:
                with payload.open('r+b') as stream:
                    stream.write(original)
        target, options = work / 'restored', services.options()
        with metrics.operation('restore'):
            with patch.object(artifacts, '_checkpoint', lambda stage: budget.check()):
                restored = restore()
            with opened(restored) as store:
                require([store.stat(e['id']) for e in manifest['files']] == before,
                        'Restored records differ')
                verify_payloads(store, manifest, work / 'restored-exports', budget)
        with metrics.operation('occupied_restore_refused'):
            try:
                restore()
            except ConflictError:
                pass
            else:
                raise AssertionError('Occupied restore target accepted')

    if not postgres:
        with services.database() as destination:
            options, target = services.options(), work / 'cutover'
            declaration = transfer_manifest(manifest)
            limits = transfer_limits(manifest)
            with opened(config, maintenance=True) as source:
                with metrics.operation('cutover_preflight', **limits):
                    preflight = preflight_sqlite_to_postgres(source, destination, manifest=declaration, **limits)
                    require(preflight['blobs'] == len(manifest['files']), 'Cutover count differs')
                with metrics.operation('cutover', **limits):
                    result = transfer_sqlite_to_postgres(source, destination, target,
                                                        manifest=declaration, **limits, **options)
                    require(result['destination_ready'], 'Cutover not ready')
                require([source.stat(e['id']) for e in manifest['files']] == before,
                        'Cutover changed source')
            with opened({'adapter': 'postgres', 'dsn': destination, 'coordinator': target,
                         'options': options}) as store:
                require([store.stat(e['id']) for e in manifest['files']] == before,
                        'Cutover records differ')
                verify_payloads(store, manifest, work / 'cutover-exports', budget)
                require(store.migration_status('workload_revision')['state'] == 'complete',
                        'Cutover lost migration history')
    # Verify source bytes again after all operational mutations and copies.
    with opened(config) as store:
        verify_payloads(store, manifest, work / 'source-final-exports', budget)
