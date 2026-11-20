"""Deterministic metadata-only interval fixtures and experimental treatments."""

import random
import re
from dataclasses import asdict, dataclass
from itertools import batched, product

from tools.qualification.find_fixture import record_id, seed_find
from tools.qualification.fixture import (
    DDL,
    instant,
    interval_cases,
    relationships,
    verify_application,
)

ADAPTERS = ('sqlite', 'melddb', 'postgres')
LAYOUTS = {
    'start': {'app_interval_start': ('start_time', 'end_time')},
    'start-plus-end': {'app_interval_start': ('start_time', 'end_time'),
                       'app_interval_end': ('end_time', 'start_time')},
    'covering-start': {'app_interval_cover_start': ('start_time', 'end_time', 'config_id', 'blob_id')},
    'covering-end': {'app_interval_cover_end': ('end_time', 'start_time', 'config_id', 'blob_id')},
}
TREATMENTS = ('fresh', 'analyzed')


@dataclass(frozen=True)
class Profile:
    name: str
    files: int
    relationships: int
    rounds: int
    warmup: int
    samples: int
    adapters: tuple = ADAPTERS

    def __post_init__(self):
        if (self.name not in ('smoke', 'full', 'custom')
                or any(type(v) is not int or v < 1 for v in
                       (self.files, self.relationships, self.rounds, self.warmup, self.samples))
                or self.relationships < self.files or self.files > 10000
                or not self.adapters or len(set(self.adapters)) != len(self.adapters)
                or not set(self.adapters) <= set(ADAPTERS)):
            raise ValueError('Invalid interval profile')
        fixed = {'smoke': (4, 2000, 1, 1, 3), 'full': (100, 300000, 3, 5, 40)}
        if self.name in fixed and (self.values() != fixed[self.name] or self.adapters != ADAPTERS):
            raise ValueError('Profile overrides require custom scope')

    def values(self):
        return self.files, self.relationships, self.rounds, self.warmup, self.samples

    def document(self):
        return {**asdict(self), 'adapters': list(self.adapters)}


def profile_from(document):
    return Profile(**{**document, 'adapters': tuple(document['adapters'])})


PROFILES = {'smoke': Profile('smoke', 4, 2000, 1, 1, 3),
            'full': Profile('full', 100, 300000, 3, 5, 40)}


def matrix(profile, seed):
    cells = [dict(adapter=a, layout=layout, treatment=t, round=r)
             for r, a, layout, t in product(range(profile.rounds), profile.adapters, LAYOUTS, TREATMENTS)]
    random.Random(seed).shuffle(cells)
    return cells


def manifest(profile):
    return {'profile': {'relationships': profile.relationships},
            'files': [{'id': record_id(i), 'config_id': f'config-{i % 3}',
                       'start_offset': i * 60} for i in range(profile.files)]}


def cases(profile):
    return [(instant(a), instant(b)) for a, b in interval_cases(profile.files)]


def boundary_cases(profile):
    return [(instant(-2), instant(-1)), (instant(-1), instant(0)),
            (instant(5), instant(5.000001)), (instant(4.999999), instant(5)),
            (instant(0), instant(.000001)),
            (instant(profile.files * 60 + 120), instant(profile.files * 60 + 121))]


def seed(catalog, profile, check=lambda: None):
    seed_find(catalog, count=profile.files)
    b = '%s' if catalog.adapter == 'postgres' else '?'
    with catalog.transaction() as tx:
        for sql in DDL[:2]:
            tx.sql(sql)
        tx.sql_many(f'INSERT INTO app_configs VALUES({b},{b})',
                    [(f'config-{i}', f'configuration {i}') for i in range(3)])
    fixture = manifest(profile)
    for rows in batched(relationships(fixture), 1000):
        check()
        with catalog.transaction() as tx:
            tx.sql_many(f'INSERT INTO app_intervals VALUES({",".join([b] * 5)})', rows)
    if not verify_application(catalog, fixture):
        raise AssertionError('Initial interval fixture differs')
    return fixture


def ddl(layout):
    return [f'CREATE INDEX {name} ON app_intervals({",".join(columns)})'
            for name, columns in LAYOUTS[layout].items()]


def indexes(catalog):
    if catalog.adapter == 'postgres':
        rows = catalog.sql("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' "
                           "AND tablename='app_intervals' ORDER BY indexname", write=False)
        return {r['indexname']: tuple(re.sub(r'[\s"]', '', r['indexdef']).split('(')[-1][:-1].split(','))
                for r in rows if r['indexname'] != 'app_intervals_pkey'}
    rows = catalog.sql("SELECT name FROM sqlite_master WHERE type='index' "
                       "AND tbl_name='app_intervals' AND sql IS NOT NULL ORDER BY name", write=False)
    return {r['name']: tuple(v['name'] for v in catalog.sql(
        'SELECT name FROM pragma_index_info(?) ORDER BY seqno', (r['name'],), write=False)) for r in rows}


def statistics(catalog):
    if catalog.adapter == 'postgres':
        return catalog.sql("SELECT tablename,attname,null_frac,n_distinct,"
                           "most_common_vals::text AS mcv,most_common_freqs::text AS mcf,"
                           "histogram_bounds::text AS histogram,correlation FROM pg_stats "
                           "WHERE schemaname='public' ORDER BY tablename,attname", write=False)
    if not catalog.sql("SELECT name FROM sqlite_master WHERE name='sqlite_stat1'", write=False):
        return []
    return catalog.sql('SELECT tbl,idx,stat FROM sqlite_stat1 ORDER BY tbl,idx', write=False)


def settings(catalog):
    if catalog.adapter == 'postgres':
        names = ('shared_buffers', 'work_mem', 'effective_cache_size', 'max_parallel_workers',
                 'max_parallel_workers_per_gather', 'autovacuum', 'statement_timeout', 'server_version')
        return {name: catalog.sql(f"SELECT current_setting('{name}') AS value", write=False)[0]['value']
                for name in names}
    result = {name: catalog.sql(f'SELECT * FROM pragma_{name}', write=False)
              for name in ('page_size', 'cache_size', 'journal_mode', 'synchronous')}
    from meldstore import ValidationError
    try:
        result['mmap_size'] = catalog.sql('SELECT * FROM pragma_mmap_size', write=False)
    except ValidationError:
        result['mmap_size'] = {'unavailable': 'Runtime does not expose pragma_mmap_size'}
    result['version'] = catalog.sql('SELECT sqlite_version() AS value', write=False)[0]['value']
    return result


def allocation(catalog):
    if catalog.adapter == 'postgres':
        return {'unit': 'bytes', 'database': catalog.sql(
            'SELECT pg_database_size(current_database()) AS n', write=False)[0]['n'],
            'indexes': catalog.sql("SELECT indexrelname AS name,pg_relation_size(indexrelid) AS bytes "
                                   "FROM pg_stat_user_indexes WHERE relname='app_intervals' "
                                   "ORDER BY indexrelname", write=False)}
    pages = catalog.sql('SELECT * FROM pragma_page_count', write=False)[0]['page_count']
    size = catalog.sql('SELECT * FROM pragma_page_size', write=False)[0]['page_size']
    return {'unit': 'bytes', 'database': pages * size, 'indexes': None,
            'unavailable_reason': 'Portable SQLite measurement uses database allocated pages; excludes WAL'}


def verify_treatment(catalog, layout, expected_stats, expected_settings):
    if indexes(catalog) != LAYOUTS[layout]:
        raise AssertionError('Unexpected application indexes')
    if statistics(catalog) != expected_stats:
        raise AssertionError('Unplanned statistics change')
    if settings(catalog) != expected_settings:
        raise AssertionError('Effective settings changed')
