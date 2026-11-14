"""Public-API workload phases shared by the three catalog adapters."""

import time
from contextlib import contextmanager
from itertools import batched

from meldstore import (
    Catalog,
    ConstraintError,
    MetadataMigration,
    S3Storage,
    Store,
)
from tools.qualification.corpus import digest
from tools.qualification.fixture import (
    DDL,
    EVOLVED,
    SCHEMA,
    expected_query,
    instant,
    metadata,
    query,
    relationships,
    verify_application,
)
from tools.qualification.measurements import latency


@contextmanager
def opened(config, *, maintenance=False, initialize=False):
    storage = S3Storage(coordination_directory=config['coordinator'], **config['options'])
    if config['adapter'] == 'postgres':
        catalog = Catalog.connect_postgres(config['dsn'], **({} if initialize else
                                            {'storage': storage, 'maintenance': maintenance}))
    else:
        catalog = Catalog(config['catalog'], adapter=config['adapter'], maintenance=maintenance)
    with catalog:
        if config['adapter'] == 'postgres' and initialize:
            catalog.install_schema(SCHEMA)
            catalog.bind_postgres_storage(storage, initialize=True)
        store = Store(catalog, storage)
        yield store


def bind(store):
    return '%s' if store.catalog.adapter == 'postgres' else '?'


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def verify_payloads(store, manifest, output, budget):
    output.mkdir(exist_ok=False)
    for entry in manifest['files']:
        budget.check()
        record = store.stat(entry['id'])
        require((record['byte_size'], record['digest']) == (entry['bytes'], entry['xxh3_128']),
                'Stored descriptor differs from independent corpus')
        target = output / entry['name']
        store.export_file(entry['id'], target)
        require(digest(target) == (entry['bytes'], entry['xxh3_128']), 'Export integrity mismatch')
        # Only this loop's verified copy, contained by a newly created output directory.
        target.unlink()
    require(verify_application(store.catalog, manifest), 'Application rows differ')


def ingest(store, manifest, corpus, metrics, budget):
    catalog = store.catalog
    store.install_schema(SCHEMA)
    b = bind(store)
    with catalog.transaction() as tx:
        for statement in DDL:
            tx.sql(statement)
        tx.sql_many(f'INSERT INTO app_configs VALUES({b},{b})',
                    [(f'config-{n}', f'configuration {n}') for n in range(3)])
    with metrics.operation('import', bytes=sum(e['bytes'] for e in manifest['files'])):
        for entry in manifest['files']:
            budget.check()
            store.import_file(corpus / entry['name'], schema=SCHEMA,
                              metadata=metadata(entry), id=entry['id'])
    with metrics.operation('relationship_load', rows=manifest['profile']['relationships'], batch=1000):
        for batch in batched(relationships(manifest), 1000):
            budget.check()
            with catalog.transaction() as tx:
                tx.sql_many(f'INSERT INTO app_intervals VALUES({",".join([b] * 5)})', batch)
    require(verify_application(catalog, manifest), 'Initial application rows mismatch')
    for entry in manifest['files']:
        row = store.stat(entry['id'])
        expected = {**metadata(entry), 'start_time': instant(entry['start_offset']),
                    'end_time': instant(entry['end_offset'])}
        require(row['metadata'] == expected, 'Metadata differs from generation manifest')
        require(row['version'] == 1, 'Unexpected initial version')
    ids, after = [], None
    while page := store.find(schema=SCHEMA, limit=3, after=after):
        ids.extend(row['id'] for row in page)
        after = store.cursor(page[-1], schema=SCHEMA)
    require(ids == [e['id'] for e in manifest['files']], 'Pagination mismatch')


def metadata_workload(store, manifest, metrics, budget):
    catalog, b = store.catalog, bind(store)
    storage_before = {k: v for k, v in metrics.calls.items() if k.startswith('storage.')}
    cases = [(0, 1), (120, 121), (30, len(manifest['files']) * 30),
             (0, len(manifest['files']) * 60 + 120)]
    for orientation in ('start', 'end'):
        if orientation == 'end':
            with metrics.operation('alternative_index_build'):
                catalog.sql('CREATE INDEX app_interval_end ON app_intervals(end_time,start_time)')
        for start, end in cases:
            budget.check()
            expected = expected_query(manifest, instant(start), instant(end))
            params = (instant(end), instant(start))
            statement = query(b)
            explain = 'EXPLAIN ' if catalog.adapter == 'postgres' else 'EXPLAIN QUERY PLAN '
            with metrics.operation('interval_query', index=orientation, selectivity=[start, end],
                                   warmup=5) as result:
                result['plan'] = catalog.sql(explain + statement, params, write=False)
                samples = []
                for number in range(105):
                    budget.check()
                    before = time.perf_counter()
                    rows = catalog.sql(statement, params, write=False)
                    elapsed = time.perf_counter() - before
                    require(rows == expected, 'Interval query oracle mismatch')
                    if number >= 5:
                        samples.append(elapsed)
                result['latency'] = latency(samples)
                result['successes'], result['errors'] = 100, 0
        # Compare bounded insertion cost with each index arrangement, then rollback.
        count = min(10_000, manifest['profile']['relationships'])
        for batch_size in (100, 1000, 10000):
            with metrics.operation('bulk_sample', batch=batch_size, rows=count, index=orientation):
                class Rollback(Exception):
                    pass
                try:
                    with catalog.transaction() as tx:
                        for batch in batched(range(count), batch_size):
                            budget.check()
                            tx.sql_many(f'INSERT INTO app_intervals VALUES({",".join([b] * 5)})',
                                        ((manifest['profile']['relationships'] + i, manifest['files'][0]['id'],
                                          'config-0', instant(0), instant(5)) for i in batch))
                        raise Rollback()
                except Rollback:
                    pass
        with metrics.operation('repeated_sql_sample', rows=count, index=orientation):
            try:
                with catalog.transaction() as tx:
                    for i in range(count):
                        tx.sql(f'INSERT INTO app_intervals VALUES({",".join([b] * 5)})',
                               (manifest['profile']['relationships'] + i, manifest['files'][0]['id'],
                                'config-0', instant(0), instant(5)))
                    raise Rollback()
            except Rollback:
                pass
    catalog.sql('DROP INDEX app_interval_end')
    require(storage_before == {k: v for k, v in metrics.calls.items() if k.startswith('storage.')},
            'Metadata-only phase performed storage I/O')
    require(verify_application(catalog, manifest), 'Bulk sample rollback failed')


def rollback_publication(store, manifest, corpus, metrics):
    entry = manifest['files'][0]
    prepared = store.prepare_file(corpus / entry['name'], schema=SCHEMA)
    with metrics.operation('shared_publication_rollback'):
        try:
            with store.catalog.transaction() as tx:
                store.finalize(prepared, tx=tx, schema=SCHEMA, metadata=metadata(entry), id='rollback-probe')
                store.publish('rollback-probe', tx=tx)
                b = bind(store)
                tx.sql_many(f'INSERT INTO app_intervals VALUES({",".join([b] * 5)})',
                            [(manifest['profile']['relationships'] + 1, 'rollback-probe', 'missing',
                              instant(0), instant(1))])
        except ConstraintError:
            pass
        else:
            raise AssertionError('Invalid relationship committed')
        require(not store.catalog.sql("SELECT id FROM ms_blobs WHERE id='rollback-probe'", write=False),
                'Failed transaction published blob')
    store.discard_prepared(prepared)


def migrations(store, manifest, metrics, budget):
    plan = MetadataMigration('workload_revision', SCHEMA, EVOLVED, 'revision-v1',
                             lambda row: dict(row, revision=1))
    before = [store.stat(e['id']) for e in manifest['files']]
    with metrics.operation('migration_dry_run'):
        store.migrate(plan, dry_run=True, batch_size=2)
        require([store.stat(e['id']) for e in manifest['files']] == before, 'Dry run changed metadata')
    with metrics.operation('migration_first_batch'):
        result = store.migrate(plan, batch_size=1, max_batches=1)
        require(result['state'] == 'running' and result['migrated_rows'] == 1,
                'Migration did not stop at durable batch')
    # Caller closes/reopens before finish_migration to exercise durable state.
    budget.check()
    return plan, before


def finish_migration(store, manifest, metrics, plan, before):
    with metrics.operation('migration_resume'):
        progress = store.migrate(plan, batch_size=10)
        require(progress['state'] == 'complete', 'Migration incomplete')
        for entry, old in zip(manifest['files'], before, strict=True):
            new = store.stat(entry['id'])
            require(new['version'] == old['version'] + 1 and new['schema_version'] == 2,
                    'Migration applied an incorrect number of times')
            require(new['metadata'] == dict(old['metadata'], revision=1), 'Migration metadata mismatch')
            require(new['digest'] == old['digest'] and new['object_key'] == old['object_key'],
                    'Migration changed payload')
        require(verify_application(store.catalog, manifest), 'Migration damaged relationships')
