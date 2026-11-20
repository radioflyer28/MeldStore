"""One isolated interval treatment. The parent enforces the hard process deadline."""

import json
import os
import random
import sys
import time
from contextlib import ExitStack
from itertools import batched
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from meldstore import Catalog
from tools.qualification.evidence import write_report
from tools.qualification.fixture import (
    BATCH_SIZES,
    expected_query,
    instant,
    query,
    verify_application,
)
from tools.qualification.interval_fixture import (
    allocation,
    boundary_cases,
    cases,
    ddl,
    indexes,
    profile_from,
    seed,
    settings,
    statistics,
    verify_treatment,
)
from tools.qualification.measurements import latency
from tools.qualification.resources import Budget


class RollbackProbe(Exception):
    pass


def no_payload(*args, **kwargs):
    raise AssertionError('Metadata experiment attempted payload I/O')


def measure(catalog, profile, cell, budget, order_seed, expected_settings=None):
    fixture = seed(catalog, profile, budget.check)
    b = '%s' if catalog.adapter == 'postgres' else '?'
    allocation_before = allocation(catalog)
    before = time.perf_counter()
    for statement in ddl(cell['layout']):
        catalog.sql(statement)
    build_seconds = time.perf_counter() - before
    if cell['treatment'] == 'analyzed':
        if catalog.adapter == 'postgres':
            catalog.sql('ANALYZE')
        else:
            catalog.maintain_sqlite(analyze=True)
    stats, effective = statistics(catalog), settings(catalog)
    if expected_settings is not None and effective != expected_settings:
        raise AssertionError('Checkpoint effective settings differ')
    if bool(stats) != (cell['treatment'] == 'analyzed'):
        raise AssertionError('Statistics treatment differs')
    verify_treatment(catalog, cell['layout'], stats, effective)
    if catalog.adapter == 'postgres':
        from tools.qualification.interval_service import STARTUP_SETTINGS
        for name, value in STARTUP_SETTINGS.items():
            if effective[name] != value:
                raise AssertionError('PostgreSQL effective setting differs: ' + name)
        if not effective['server_version'].startswith('17.'):
            raise AssertionError('PostgreSQL 17 required')
    selections = cases(profile)
    expected = {bounds: expected_query(fixture, *bounds)
                for bounds in {*selections, *boundary_cases(profile)}}
    statement = query(b)
    for start, end in boundary_cases(profile):
        budget.check()
        if catalog.sql(statement, (end, start), write=False) != expected[(start, end)]:
            raise AssertionError('Boundary oracle mismatch')
    order = list(range(len(selections)))
    random.Random(order_seed).shuffle(order)
    result = {**cell, 'status': 'incomplete', 'ddl': ddl(cell['layout']),
              'indexes': {k: list(v) for k, v in indexes(catalog).items()},
              'settings': effective, 'statistics_before': stats,
              'build_seconds': build_seconds, 'allocation_before': allocation_before,
              'allocation': allocation(catalog), 'queries': [], 'writes': [],
              'boundary_cases': len(boundary_cases(profile)), 'query_order': order,
              'fixture_verified_before': True, 'payload_io_calls': 0}
    explain = 'EXPLAIN ' if catalog.adapter == 'postgres' else 'EXPLAIN QUERY PLAN '
    for number in order:
        start, end = selections[number]
        plan = catalog.sql(explain + statement, (end, start), write=False)
        samples = []
        for call in range(profile.warmup + profile.samples):
            budget.check()
            before = time.perf_counter()
            rows = catalog.sql(statement, (end, start), write=False)
            elapsed = time.perf_counter() - before
            if rows != expected[(start, end)]:
                raise AssertionError('Interval oracle mismatch')
            if call >= profile.warmup:
                samples.append(elapsed)
        result['queries'].append({'case': number, 'bounds': [start, end], 'plan': plan,
                                  'warmup': profile.warmup, 'samples_seconds': samples,
                                  'latency': latency(samples), 'successes': len(samples), 'errors': 0,
                                  'expected': expected[(start, end)]})
    result['statistics_after_reads'] = statistics(catalog)
    verify_treatment(catalog, cell['layout'], stats, effective)
    count = min(10000, profile.relationships)
    extra = [(profile.relationships + i, fixture['files'][0]['id'], 'config-0', instant(0), instant(5))
             for i in range(count)]
    write_order = list(BATCH_SIZES)
    random.Random(order_seed + 1).shuffle(write_order)
    result['write_order'] = write_order
    for batch_size in write_order:
        batches = list(batched(extra, batch_size))
        samples = []
        for _ in range(3 if profile.name == 'full' else 1):
            budget.check()
            before = time.perf_counter()
            try:
                with catalog.transaction() as tx:
                    for batch in batches:
                        tx.sql_many(f'INSERT INTO app_intervals VALUES({",".join([b] * 5)})', batch)
                    raise RollbackProbe()
            except RollbackProbe:
                pass
            samples.append(time.perf_counter() - before)
            if not verify_application(catalog, fixture):
                raise AssertionError('Write probe changed persisted rows')
        result['writes'].append({'batch': batch_size, 'rows': count,
                                 'samples_seconds': samples, 'latency': latency(samples),
                                 'boundary': 'transaction entry through deliberate rollback',
                                 'rollback_verified': True})
    result['statistics_after'] = statistics(catalog)
    result['settings_after'] = settings(catalog)
    verify_treatment(catalog, cell['layout'], stats, effective)
    result['fixture_verified_after'] = verify_application(catalog, fixture)
    if not result['fixture_verified_after']:
        raise AssertionError('Final fixture differs')
    result['status'] = 'passed'
    return result


def run(config):
    from tools.qualification.interval_evidence import provenance
    root = Path(config['directory']).resolve()
    profile = profile_from(config['profile'])
    initial = provenance()
    if initial != config['provenance']:
        raise AssertionError('Worker provenance differs from parent')
    budget = Budget(root, memory_bytes=config['memory_bytes'], seconds=config['seconds'],
                    reserve_bytes=config['reserve_bytes'])
    import obstore
    with ExitStack() as stack:
        for name in ('get', 'get_range', 'head', 'put', 'copy', 'delete', 'list'):
            if hasattr(obstore, name):
                stack.enter_context(patch.object(obstore, name, no_payload))
        # TemporaryDirectory's generated path is checked before enabling recursive cleanup.
        temporary = TemporaryDirectory(prefix='interval-', dir=root)
        if Path(temporary.name).resolve().parent != root:
            raise ValueError('Temporary catalog escaped owned root')
        stack.enter_context(temporary)
        if config['cell']['adapter'] == 'postgres':
            catalog = Catalog.connect_postgres(os.environ['MELDSTORE_INTERVAL_DSN'])
        else:
            catalog = Catalog(Path(temporary.name) / 'catalog.sqlite',
                              adapter=config['cell']['adapter'], maintenance=True)
        with catalog:
            result = measure(catalog, profile, config['cell'], budget, config['order_seed'],
                             config.get('expected_settings'))
        budget.check()
    result['provenance'] = initial
    if provenance() != initial:
        raise AssertionError('Sources changed during measurement')
    result['peak_rss_bytes'] = budget.max_rss
    result['local_cleanup'] = True
    return result


def main():
    config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    target = Path(config['directory']) / 'result.json'
    try:
        result = run(config)
    except BaseException as exc:
        write_report(target, {'status': 'failed', 'error_type': type(exc).__name__})
        # Avoid connection strings and private paths from arbitrary exception messages.
        print(type(exc).__name__, flush=True)
        return 1
    write_report(target, result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
