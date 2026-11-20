"""Strict metadata matrix completion and reproducible, sanitized evidence."""

import json
import math
import os
import platform
import random
import sqlite3
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path

import xxhash

from tools.qualification.evidence import sanitized
from tools.qualification.fixture import BATCH_SIZES, expected_query
from tools.qualification.interval_fixture import (
    LAYOUTS,
    boundary_cases,
    cases,
    ddl,
    manifest,
    matrix,
    profile_from,
)
from tools.qualification.measurements import latency

ROOT = Path(__file__).resolve().parents[2]
PIN = '01e27441d7512f85663c8a86986429d62a69bff9'


def provenance():
    import meldstore
    if Path(meldstore.__file__).resolve().parent != ROOT / 'src' / 'meldstore':
        raise ValueError('Imported MeldStore is not the attested source')
    for name, module in tuple(sys.modules.items()):
        if name.startswith('meldstore.') and getattr(module, '__file__', None):
            if not Path(module.__file__).resolve().is_relative_to(ROOT / 'src' / 'meldstore'):
                raise ValueError('Mixed MeldStore import roots')
    provider = json.loads(distribution('melddb').read_text('direct_url.json') or '{}')
    if provider.get('vcs_info', {}).get('commit_id') != PIN:
        raise ValueError('Installed MeldDB differs from exact qualified pin')
    files = sorted({*ROOT.glob('src/meldstore/**/*.py'), *ROOT.glob('tools/qualification/*.py'),
                    ROOT / 'tools/qualify_intervals.py', ROOT / 'tools/qualify_local.py',
                    ROOT / 'tools/postgres_lab.py', ROOT / 'tools/s3_lab.py', ROOT / 'uv.lock'})
    packages = {}
    for name in ('meldstore', 'melddb', 'obstore', 'xxhash', 'psycopg', 'psycopg-binary'):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {'revision': subprocess.check_output(
                ['git', '-c', f'safe.directory={ROOT.as_posix()}', '-c', 'core.bare=false',
                 '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True, timeout=10).strip(),
            'provider': PIN, 'python': platform.python_version(), 'os': platform.system(),
            'architecture': platform.machine(), 'import_root_verified': True,
            'os_release': platform.release(), 'processor': platform.processor(),
            'logical_cpus': os.cpu_count(), 'sqlite_version': sqlite3.sqlite_version,
            'packages': packages,
            'sources': {p.relative_to(ROOT).as_posix(): xxhash.xxh3_128(p.read_bytes()).hexdigest()
                        for p in files}}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def valid_samples(samples, count):
    return (isinstance(samples, list) and len(samples) == count
            and all(type(v) in (float, int) and math.isfinite(v) and v >= 0 for v in samples))


def validate(report, *, partial=False):
    p = profile_from(report['profile'])
    expected_matrix = matrix(p, report['seed'])
    require(report.get('schema_version') in (1, 2) and report.get('scope') == 'metadata-only', 'Scope')
    require(report.get('representative_qualified') is False, 'Payload claim is forbidden')
    if not partial:
        require(report.get('cleanup') is True and report.get('continuity') is True, 'Cleanup/continuity')
    prov = report['provenance']
    require(prov == report.get('provenance_after') and prov.get('provider') == PIN
            and prov.get('import_root_verified') is True, 'Provenance changed')
    required_sources = ('uv.lock', 'tools/qualify_intervals.py',
                        'tools/qualification/interval_worker.py',
                        'tools/qualification/interval_fixture.py',
                        'tools/qualification/interval_service.py',
                        'tools/qualification/interval_evidence.py', 'src/meldstore/catalog.py')
    require(all(len(prov.get('sources', {}).get(n, '')) == 32 for n in required_sources), 'Source attestation')
    rows = report['cells']
    require(report['schedule'] == expected_matrix and len(rows) <= len(expected_matrix)
            and (partial or len(rows) == len(expected_matrix)), 'Matrix size')
    expected_results = [expected_query(manifest(p), *bounds) for bounds in cases(p)]
    effective = {}
    for ordinal, (cell, expected_cell) in enumerate(zip(rows, expected_matrix[:len(rows)], strict=True)):
        require(all(cell[k] == v for k, v in expected_cell.items()), 'Matrix cell')
        require(cell.get('status') == 'passed' and cell.get('provenance') == prov, 'Cell provenance/status')
        require(cell.get('local_cleanup') is True and cell.get('payload_io_calls') == 0, 'Cell cleanup/I/O')
        require(cell.get('fixture_verified_before') is True
                and cell.get('fixture_verified_after') is True, 'Fixture verification')
        require(cell.get('boundary_cases') == len(boundary_cases(p)), 'Boundary cases')
        require(cell['ddl'] == ddl(cell['layout']) and cell['indexes'] ==
                {k: list(v) for k, v in LAYOUTS[cell['layout']].items()}, 'Index definitions')
        require(cell['statistics_before'] == cell['statistics_after_reads'] == cell['statistics_after']
                and bool(cell['statistics_before']) == (cell['treatment'] == 'analyzed'), 'Statistics')
        adapter = cell['adapter']
        require(cell['settings'] and cell['settings'] == cell['settings_after'], 'Settings changed')
        if adapter in effective:
            require(cell['settings'] == effective[adapter], 'Paired settings differ')
        effective[adapter] = cell['settings']
        require(valid_samples([cell['build_seconds']], 1) and cell['peak_rss_bytes'] > 0, 'Build/resources')
        require(cell['allocation']['unit'] == 'bytes' and cell['allocation']['database'] > 0
                and cell['allocation_before']['database'] > 0, 'Allocation')
        if cell['allocation']['indexes'] is None:
            require(bool(cell['allocation'].get('unavailable_reason')), 'Missing metric reason')
        order = list(range(len(cases(p))))
        random.Random(report['seed'] + ordinal).shuffle(order)
        require(cell['query_order'] == order and [q['case'] for q in cell['queries']] == order, 'Query order')
        for q in cell['queries']:
            number = q['case']
            require(q['bounds'] == list(cases(p)[number]) and q['expected'] == expected_results[number], 'Oracle')
            require(q['plan'] and q['warmup'] == p.warmup and q['successes'] == p.samples
                    and q['errors'] == 0, 'Query outcomes')
            require(valid_samples(q['samples_seconds'], p.samples), 'Read samples')
            require(q['latency'] == latency(q['samples_seconds']), 'Read summary')
        write_order = list(BATCH_SIZES)
        random.Random(report['seed'] + ordinal + 1).shuffle(write_order)
        require(cell['write_order'] == write_order
                and [w['batch'] for w in cell['writes']] == write_order, 'Write matrix')
        for w in cell['writes']:
            require(w['rows'] == min(p.relationships, 10000) and w['rollback_verified'] is True,
                    'Rollback verification')
            require(w['boundary'] == 'transaction entry through deliberate rollback', 'Write boundary')
            require(valid_samples(w['samples_seconds'], 3 if p.name == 'full' else 1), 'Write samples')
            require(w['latency'] == latency(w['samples_seconds']), 'Write summary')
    if 'postgres' in p.adapters:
        from tools.qualification.interval_service import IMAGE, MEMORY, STARTUP_SETTINGS
        service = report.get('service', {})
        require(service.get('image') == IMAGE and service.get('image_id')
                and service.get('memory_limit_bytes') == MEMORY
                and service.get('memory_swap_limit_bytes') == MEMORY
                and service.get('startup_settings') == STARTUP_SETTINGS, 'Service provenance')
        if 'postgres' in effective:
            require(all(effective['postgres'].get(k) == v for k, v in STARTUP_SETTINGS.items())
                    and effective['postgres'].get('server_version', '').startswith('17.'), 'Server settings')
    if report['schema_version'] == 2:
        from tools.qualification.interval_checkpoint import validate_sessions
        validate_sessions(report, closed=not partial)
    sanitized(report)
    return p


def complete(report):
    # Fail closed even when asked to revalidate an already completed artifact.
    report['status'], report['full_metadata_qualified'] = 'incomplete', False
    p = validate(report)
    report['status'] = 'passed'
    report['full_metadata_qualified'] = p.name == 'full'


def summary(report):
    require(report['status'] == 'passed', 'Only completed reports can be summarized')
    lines = ['# Interval metadata experiment', '',
             f"Profile: {report['profile']['name']}. Scope: metadata only. Warm queries; OS cache uncontrolled.",
             '', 'Ratios compare p50 to start-only within the same adapter, statistics treatment and round.',
             'Below 1 is faster. All rounds are shown; mixed/noisy results do not establish a winner.', '',
             '| Adapter | Statistics | Layout | Query | Round p50 ratios | p50 ms | p95 ms |',
             '| --- | --- | --- | --- | --- | ---: | ---: |']
    groups = {}
    baseline = {}
    for cell in report['cells']:
        for q in cell['queries']:
            key = cell['adapter'], cell['treatment'], cell['layout'], q['case']
            groups.setdefault(key, []).append((cell['round'], q))
            if cell['layout'] == 'start':
                baseline[cell['adapter'], cell['treatment'], cell['round'], q['case']] = q['latency']['p50']
    for (a, t, layout, case), entries in sorted(groups.items()):
        samples = [s for _, q in entries for s in q['samples_seconds']]
        stats = latency(samples)
        ratios = []
        for r, q in sorted(entries):
            base = baseline[a, t, r, case]
            ratios.append(f"{q['latency']['p50'] / base:.3f}" if base else 'unavailable (zero baseline)')
        lines.append(f"| {a} | {t} | {layout} | {case} | {', '.join(ratios)} | "
                     f"{stats['p50'] * 1000:.3f} | {stats['p95'] * 1000:.3f} |")
    lines += ['', '## Costs per independently prepared round', '',
              'Insertion cost includes deliberate rollback, not durable commit throughput.', '',
              '| Adapter | Statistics | Layout | Round | Build ms | DB bytes | Insert+rollback p50 ms (100 / 1000 / 10000) |',
              '| --- | --- | --- | ---: | ---: | ---: | --- |']
    for cell in sorted(report['cells'], key=lambda c: (c['adapter'], c['treatment'], c['layout'], c['round'])):
        costs = ' / '.join(f"{w['latency']['p50'] * 1000:.2f}"
                           for w in sorted(cell['writes'], key=lambda w: w['batch']))
        lines.append(f"| {cell['adapter']} | {cell['treatment']} | {cell['layout']} | {cell['round']} | "
                     f"{cell['build_seconds'] * 1000:.2f} | {cell['allocation']['database']} | {costs} |")
    if report.get('schema_version') == 2:
        from tools.qualification.interval_checkpoint import session_summary
        lines += session_summary(report)
    lines += ['', 'SQLite allocation is whole-database pages and excludes WAL; per-index bytes are unavailable.',
              'PostgreSQL per-index allocations and all plans, settings and raw samples are in report.json.',
              'No universal index or memory default is implied. This does not qualify stored payloads.', '']
    return '\n'.join(lines)
