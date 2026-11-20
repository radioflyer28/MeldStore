import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from meldstore import Catalog
from tools.qualification.fixture import expected_query, instant, query, verify_application
from tools.qualification.interval_evidence import complete, summary
from tools.qualification.interval_fixture import (
    ADAPTERS,
    LAYOUTS,
    PROFILES,
    Profile,
    boundary_cases,
    cases,
    ddl,
    indexes,
    manifest,
    matrix,
    seed,
    settings,
    statistics,
    verify_treatment,
)
from tools.qualification.interval_service import STARTUP_SETTINGS
from tools.qualification.interval_worker import measure
from tools.qualification.resources import ResourceLimit
from tools.qualify_intervals import main, preflight


@pytest.mark.parametrize('name,count', [('smoke', 24), ('full', 72)])
def test_profile_matrix_is_exact_and_reproducible(name, count):
    profile = PROFILES[name]
    cells = matrix(profile, 7)
    assert len(cells) == count
    assert cells == matrix(profile, 7)
    assert cells != matrix(profile, 8)
    assert len({tuple(c.values()) for c in cells}) == count
    assert {c['adapter'] for c in cells} == set(ADAPTERS)


@pytest.mark.parametrize('field,value', [('relationships', 12), ('files', 1), ('rounds', 1),
                                        ('adapters', ('sqlite',)), ('samples', 2), ('warmup', 1)])
def test_full_profile_cannot_silently_shrink(field, value):
    with pytest.raises(ValueError):
        replace(PROFILES['full'], **{field: value})


@pytest.mark.parametrize('adapter', ['sqlite', 'melddb'])
def test_fixture_oracle_boundaries_and_integrity(tmp_path, adapter):
    profile = Profile('custom', 4, 4, 1, 1, 3)
    with Catalog(tmp_path / 'catalog.sqlite', adapter=adapter, maintenance=True) as catalog:
        fixture = seed(catalog, profile)
        for start, end in boundary_cases(profile):
            assert catalog.sql(query('?'), (end, start), write=False) == expected_query(fixture, start, end)
        # The first row occupies [0,5); merely touching either end is not overlap.
        assert expected_query(fixture, instant(-1), instant(0)) == []
        assert expected_query(fixture, instant(5), instant(5.000001)) == []
        assert expected_query(fixture, instant(4.999999), instant(5)) == [
            {'blob_id': 'find-0000', 'matches': 1}]
        catalog.sql("UPDATE app_intervals SET end_time=? WHERE id=0", (instant(99),))
        assert not verify_application(catalog, fixture)


@pytest.mark.parametrize('adapter', ['sqlite', 'melddb'])
@pytest.mark.parametrize('layout', LAYOUTS)
def test_layout_statistics_and_effective_settings(tmp_path, adapter, layout):
    with Catalog(tmp_path / 'catalog.sqlite', adapter=adapter, maintenance=True) as catalog:
        seed(catalog, Profile('custom', 4, 100, 1, 1, 3))
        for sql in ddl(layout):
            catalog.sql(sql)
        assert indexes(catalog) == LAYOUTS[layout]
        assert statistics(catalog) == []
        catalog.maintain_sqlite(analyze=True)
        stats, effective = statistics(catalog), settings(catalog)
        assert stats
        verify_treatment(catalog, layout, stats, effective)
        with pytest.raises(AssertionError, match='settings'):
            verify_treatment(catalog, layout, stats, {})
        catalog.sql('CREATE INDEX app_extra ON app_intervals(blob_id)')
        with pytest.raises(AssertionError, match='indexes'):
            verify_treatment(catalog, layout, stats, effective)


def test_unplanned_statistics_change_is_rejected(tmp_path):
    with Catalog(tmp_path / 'catalog.sqlite', adapter='sqlite', maintenance=True) as catalog:
        seed(catalog, Profile('custom', 4, 100, 1, 1, 3))
        catalog.sql(ddl('start')[0])
        before, effective = statistics(catalog), settings(catalog)
        catalog.maintain_sqlite(analyze=True)
        with pytest.raises(AssertionError, match='statistics'):
            verify_treatment(catalog, 'start', before, effective)


@pytest.mark.parametrize('adapter', ['sqlite', 'melddb'])
def test_measurement_excludes_warmup_and_rolls_back(tmp_path, adapter):
    profile = Profile('custom', 4, 100, 1, 2, 3)
    with Catalog(tmp_path / 'catalog.sqlite', adapter=adapter, maintenance=True) as catalog:
        result = measure(catalog, profile, dict(adapter=adapter, layout='start', treatment='analyzed', round=0),
                         SimpleNamespace(check=lambda: None), 3)
        assert all(len(q['samples_seconds']) == 3 and q['warmup'] == 2 for q in result['queries'])
        assert {w['batch'] for w in result['writes']} == {100, 1000, 10000}
        assert all(w['rollback_verified'] for w in result['writes'])
        assert verify_application(catalog, manifest(profile))
        assert result['statistics_before'] == result['statistics_after']


def test_bad_query_result_fails_even_when_fast(tmp_path, monkeypatch):
    from tools.qualification import interval_worker
    monkeypatch.setattr(interval_worker, 'expected_query', lambda *args: [{'blob_id': 'wrong', 'matches': 1}])
    with Catalog(tmp_path / 'catalog.sqlite', adapter='sqlite', maintenance=True) as catalog:
        with pytest.raises(AssertionError, match='oracle'):
            measure(catalog, Profile('custom', 4, 100, 1, 1, 3),
                    dict(adapter='sqlite', layout='start', treatment='fresh', round=0),
                    SimpleNamespace(check=lambda: None), 1)


def evidence_fixture():
    import random

    from tools.qualification.interval_evidence import PIN
    from tools.qualification.interval_service import IMAGE, MEMORY
    from tools.qualification.measurements import latency
    profile = PROFILES['smoke']
    prov = dict(provider=PIN, import_root_verified=True, sources={n: 'a' * 32 for n in (
        'uv.lock', 'tools/qualify_intervals.py', 'tools/qualification/interval_worker.py',
        'tools/qualification/interval_fixture.py', 'tools/qualification/interval_service.py',
        'tools/qualification/interval_evidence.py', 'src/meldstore/catalog.py')})
    report = dict(schema_version=1, scope='metadata-only', profile=profile.document(), seed=1,
                  status='incomplete', full_metadata_qualified=False, representative_qualified=False,
                  cleanup=True, continuity=True, provenance=prov, provenance_after=copy.deepcopy(prov),
                  schedule=matrix(profile, 1), cells=[], service=dict(image=IMAGE, image_id='sha256:abc',
                  memory_limit_bytes=MEMORY, memory_swap_limit_bytes=MEMORY, startup_settings=STARTUP_SETTINGS))
    for ordinal, cell in enumerate(report['schedule']):
        order = list(range(4))
        random.Random(1 + ordinal).shuffle(order)
        writes = [100, 1000, 10000]
        random.Random(2 + ordinal).shuffle(writes)
        stats = [{'synthetic': 1}] if cell['treatment'] == 'analyzed' else []
        effective = ({**STARTUP_SETTINGS, 'server_version': '17.7'} if cell['adapter'] == 'postgres'
                     else {'cache_size': -2000})
        report['cells'].append(dict(**cell, status='passed', provenance=copy.deepcopy(prov),
            local_cleanup=True, payload_io_calls=0, fixture_verified_before=True,
            fixture_verified_after=True, boundary_cases=len(boundary_cases(profile)),
            ddl=ddl(cell['layout']), indexes={k: list(v) for k, v in LAYOUTS[cell['layout']].items()},
            settings=effective, settings_after=copy.deepcopy(effective), statistics_before=stats,
            statistics_after_reads=copy.deepcopy(stats), statistics_after=copy.deepcopy(stats),
            build_seconds=.01, peak_rss_bytes=10, allocation=dict(unit='bytes', database=100,
            indexes=None, unavailable_reason='unavailable in fixture'), allocation_before=dict(database=90),
            query_order=order, queries=[dict(case=n, bounds=list(cases(profile)[n]),
                expected=expected_query(manifest(profile), *cases(profile)[n]), plan=[{'detail': 'synthetic'}],
                warmup=1, successes=3, errors=0, samples_seconds=[.1, .2, .3], latency=latency([.1,.2,.3]))
                for n in order], write_order=writes, writes=[dict(batch=n, rows=2000,
                rollback_verified=True, boundary='transaction entry through deliberate rollback',
                samples_seconds=[.1], latency=latency([.1])) for n in writes]))
    return report


def test_complete_smoke_does_not_qualify_full_or_payloads():
    report = evidence_fixture()
    complete(report)
    assert report['status'] == 'passed'
    assert not report['full_metadata_qualified'] and not report['representative_qualified']
    assert 'metadata only' in summary(report)


@pytest.mark.parametrize('damage', ['cell', 'provenance', 'sources', 'samples', 'settings', 'stats',
                                    'query', 'oracle', 'ddl', 'writes', 'rollback', 'cleanup',
                                    'service', 'io', 'warmup', 'scale', 'private'])
def test_completion_rejects_damaged_evidence(damage):
    report = evidence_fixture()
    cell = report['cells'][0]
    if damage == 'cell':
        report['cells'].pop()
    elif damage == 'provenance':
        cell['provenance']['provider'] = 'wrong'
    elif damage == 'sources':
        report['provenance']['sources'].clear()
    elif damage == 'samples':
        cell['queries'][0]['samples_seconds'].pop()
    elif damage == 'settings':
        cell['settings_after'] = {}
    elif damage == 'stats':
        cell['statistics_after'] = [{'changed': True}]
    elif damage == 'query':
        cell['queries'].pop()
    elif damage == 'oracle':
        cell['queries'][0]['expected'] = []
    elif damage == 'ddl':
        cell['ddl'] = []
    elif damage == 'writes':
        cell['writes'].pop()
    elif damage == 'rollback':
        cell['writes'][0]['rollback_verified'] = False
    elif damage == 'cleanup':
        report['cleanup'] = False
    elif damage == 'service':
        report['service']['memory_limit_bytes'] = 0
    elif damage == 'io':
        cell['payload_io_calls'] = 1
    elif damage == 'warmup':
        cell['queries'][0]['warmup'] = 0
    elif damage == 'scale':
        report['profile']['name'] = 'full'
    else:
        report['private'] = 'postgresql://user:secret@host/db'
    with pytest.raises(ValueError):
        complete(report)
    assert report['status'] == 'incomplete' and not report['full_metadata_qualified']


def test_summary_includes_regression_and_unavailable_space():
    from tools.qualification.measurements import latency
    report = evidence_fixture()
    for cell in report['cells']:
        if cell['layout'] == 'covering-end':
            for q in cell['queries']:
                q['samples_seconds'] = [1, 2, 3]
                q['latency'] = latency([1, 2, 3])
    complete(report)
    text = summary(report)
    assert '10.000' in text and 'unavailable' in text and 'not durable commit throughput' in text


def test_cli_refuses_overrides_and_occupied_destinations(tmp_path):
    with pytest.raises(SystemExit):
        main(['--output', str(tmp_path / 'new'), '--files', '1'])
    with pytest.raises(ValueError, match='fresh'):
        main(['--output', str(tmp_path)])
    assert not (tmp_path / 'report.json').exists()


def test_memory_preflight_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr('tools.qualify_intervals.available_memory', lambda: 1)
    with pytest.raises(ResourceLimit, match='memory'):
        preflight(tmp_path, 100, 100, 100, True)


def test_postgres_timeout_uses_server_canonical_unit():
    assert STARTUP_SETTINGS['statement_timeout'] == '1min'


# Checkpoint fixtures keep the original oracle and all treatment evidence intact.
def checkpoint_fixture(count=3):
    from tools.qualification.interval_checkpoint import digest
    report = evidence_fixture()
    report.update(schema_version=2, status='paused', resume_eligible=True,
                  continuity_scope='within-sessions',
                  limits=dict(memory_bytes=4_000_000_000, disk_bytes=4_000_000_000, reserve_bytes=0))
    report['cells'] = report['cells'][:count]
    identifier = '1' * 32
    for cell in report['cells']:
        cell['session_id'] = identifier
    report['checkpoints'] = [dict(ordinal=i, session_id=identifier, xxh3_128=digest(c))
                             for i, c in enumerate(report['cells'])]
    report['sessions'] = [dict(id=identifier, start_cell=0, end_cell=count,
        started_at='2026-11-18T00:00:00+00:00', checkpoint_at='2026-11-18T00:01:00+00:00',
        ended_at='2026-11-18T00:02:00+00:00', cleanup=True, continuity=True,
        provenance=copy.deepcopy(report['provenance']), limits=copy.deepcopy(report['limits']),
        seconds=900, service=copy.deepcopy(report['service']), execution=dict(
            continuity_lost=False, gap_limit_seconds=30, maximum_scheduling_gap_seconds=1))]
    return report


def test_checkpoint_prefix_validates_without_qualifying(tmp_path):
    import json

    from tools.qualification.interval_checkpoint import load_checkpoint
    report = checkpoint_fixture()
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report))
    assert load_checkpoint(path, report['provenance']) == report
    with pytest.raises(ValueError, match='Matrix size'):
        complete(report)
    assert report['status'] == 'incomplete' and not report['full_metadata_qualified']


@pytest.mark.parametrize('damage', ['checksum', 'samples', 'provenance', 'profile', 'seed', 'schedule',
    'partition', 'chronology', 'session_service', 'session_provenance', 'session_limits',
    'session_cleanup', 'session_continuity', 'execution', 'cleanup', 'active', 'legacy', 'eligibility'])
def test_resume_rejects_corrupt_or_incompatible_checkpoints(tmp_path, damage):
    import json

    from tools.qualification.interval_checkpoint import load_checkpoint
    report = checkpoint_fixture()
    expected_provenance = copy.deepcopy(report['provenance'])
    if damage == 'checksum':
        report['checkpoints'][0]['xxh3_128'] = '0' * 32
    elif damage == 'samples':
        report['cells'][0]['queries'][0]['samples_seconds'].pop()
    elif damage == 'provenance':
        expected_provenance['python'] = 'different'
    elif damage == 'profile':
        report['profile']['samples'] = 1
    elif damage == 'seed':
        report['seed'] += 1
    elif damage == 'schedule':
        report['schedule'].reverse()
    elif damage == 'partition':
        report['sessions'][0]['end_cell'] -= 1
    elif damage == 'chronology':
        report['sessions'][0]['ended_at'] = '2026-11-17T00:00:00+00:00'
    elif damage.startswith('session_'):
        key = damage.removeprefix('session_')
        report['sessions'][0][key] = False if key in ('cleanup', 'continuity') else {}
    elif damage == 'execution':
        report['sessions'][0]['execution']['maximum_scheduling_gap_seconds'] = 31
    elif damage == 'cleanup':
        report['cleanup'] = False
    elif damage == 'active':
        report['status'] = 'incomplete'
    elif damage == 'legacy':
        report['schema_version'] = 1
    else:
        report['resume_eligible'] = False
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report))
    original = path.read_bytes()
    with pytest.raises(ValueError):
        load_checkpoint(path, expected_provenance)
    assert path.read_bytes() == original


@pytest.fixture
def simulated_interval_run(monkeypatch):
    from contextlib import nullcontext

    from tools import qualify_intervals as runner
    evidence = evidence_fixture()
    calls = []
    class Service:
        def __init__(self, *args):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def provenance(self):
            return copy.deepcopy(evidence['service'])
        def check_reserve(self, *args):
            pass
        def database(self):
            return nullcontext(None)
    def worker(config, *args):
        ordinal = config['order_seed'] - 1
        calls.append(ordinal)
        return copy.deepcopy(evidence['cells'][ordinal])
    monkeypatch.setattr(runner, 'provenance', lambda: copy.deepcopy(evidence['provenance']))
    monkeypatch.setattr(runner, 'preflight', lambda *a: {})
    monkeypatch.setattr(runner, 'IntervalService', Service)
    monkeypatch.setattr(runner, 'run_worker', worker)
    return SimpleNamespace(evidence=evidence, calls=calls, worker=worker, service=Service)


def test_pause_resume_reuses_prefix_and_preserves_source(tmp_path, simulated_interval_run):
    import json

    from tools.qualification.interval_checkpoint import load_checkpoint
    first, second = tmp_path / 'first', tmp_path / 'second'
    assert main(['--output', str(first), '--seed', '1', '--reserve-bytes', '0', '--pause-after', '3']) == 0
    source = first / 'report.json'
    saved = source.read_bytes()
    report = load_checkpoint(source, simulated_interval_run.evidence['provenance'])
    assert len(report['cells']) == 3
    assert main(['--output', str(second), '--resume-from', str(source), '--seconds', '1800']) == 0
    final = json.loads((second / 'report.json').read_text())
    assert final['status'] == 'passed' and len(final['sessions']) == 2
    assert simulated_interval_run.calls == list(range(24))
    assert final['cells'][:3] == report['cells'] and source.read_bytes() == saved
    assert final['sessions'][1]['seconds'] == 1800
    assert 'another session' in (second / 'summary.md').read_text()
    assert not final['full_metadata_qualified'] and not final['resume_eligible']


@pytest.mark.parametrize('stop', ['interrupt', 'failure', 'marker'])
def test_interrupted_treatment_reruns_or_marker_pauses_at_boundary(
        tmp_path, monkeypatch, simulated_interval_run, stop):
    import json
    from pathlib import Path

    from tools import qualify_intervals as runner
    first, second = tmp_path / 'first', tmp_path / 'second'
    def worker(config, *args):
        ordinal = config['order_seed'] - 1
        if stop == 'marker' and ordinal == 0:
            (Path(config['directory']).parent / 'PAUSE').touch()
        if stop != 'marker' and ordinal == 1:
            raise KeyboardInterrupt() if stop == 'interrupt' else ResourceLimit('deadline')
        return simulated_interval_run.worker(config, *args)
    monkeypatch.setattr(runner, 'run_worker', worker)
    expected = {'interrupt': 130, 'failure': 1, 'marker': 0}[stop]
    assert main(['--output', str(first), '--seed', '1', '--reserve-bytes', '0']) == expected
    report = json.loads((first / 'report.json').read_text())
    assert report['resume_eligible'] and report['cleanup'] and len(report['cells']) == 1
    monkeypatch.setattr(runner, 'run_worker', simulated_interval_run.worker)
    assert main(['--output', str(second), '--resume-from', str(first / 'report.json')]) == 0
    assert simulated_interval_run.calls == list(range(24))


@pytest.mark.parametrize('override', [['--seed', '2'], ['--profile', 'full'], ['--files', '5'],
    ['--relationships', '10'], ['--adapters', 'sqlite'], ['--memory-bytes', '1'],
    ['--disk-bytes', '1'], ['--reserve-bytes', '1']])
def test_resume_rejects_overrides_before_creating_output(tmp_path, simulated_interval_run, override):
    first, second = tmp_path / 'first', tmp_path / 'second'
    assert main(['--output', str(first), '--seed', '1', '--reserve-bytes', '0', '--pause-after', '1']) == 0
    with pytest.raises(SystemExit):
        main(['--output', str(second), '--resume-from', str(first / 'report.json'), *override])
    assert not second.exists()


def test_resume_rejects_service_drift_before_worker(tmp_path, monkeypatch, simulated_interval_run):
    import json
    first, second = tmp_path / 'first', tmp_path / 'second'
    assert main(['--output', str(first), '--seed', '1', '--reserve-bytes', '0', '--pause-after', '1']) == 0
    monkeypatch.setattr(simulated_interval_run.service, 'provenance', lambda self: {})
    assert main(['--output', str(second), '--resume-from', str(first / 'report.json')]) == 1
    report = json.loads((second / 'report.json').read_text())
    assert not report['resume_eligible'] and simulated_interval_run.calls == [0]


def test_cleanup_failure_after_checkpoint_forbids_resume(tmp_path, monkeypatch, simulated_interval_run):
    import json
    def failed_exit(self, *args):
        raise RuntimeError('cleanup failed')
    monkeypatch.setattr(simulated_interval_run.service, '__exit__', failed_exit)
    output = tmp_path / 'failed'
    assert main(['--output', str(output), '--seed', '1', '--reserve-bytes', '0', '--pause-after', '1']) == 1
    report = json.loads((output / 'report.json').read_text())
    assert len(report['cells']) == 1 and not report['cleanup'] and not report['resume_eligible']


def test_resume_cannot_write_inside_source_directory(tmp_path, simulated_interval_run):
    first = tmp_path / 'first'
    assert main(['--output', str(first), '--seed', '1', '--reserve-bytes', '0', '--pause-after', '1']) == 0
    source = first / 'report.json'
    original = source.read_bytes()
    with pytest.raises(ValueError, match='protected'):
        main(['--output', str(first / 'nested'), '--resume-from', str(source)])
    assert source.read_bytes() == original


def test_changed_effective_settings_rejected_before_query_timing(tmp_path, monkeypatch):
    from tools.qualification import interval_worker
    def forbidden(*args):
        raise AssertionError('Must not compute query results or measure')
    monkeypatch.setattr(interval_worker, 'expected_query', forbidden)
    with Catalog(tmp_path / 'catalog.sqlite', adapter='sqlite', maintenance=True) as catalog:
        with pytest.raises(AssertionError, match='Checkpoint effective settings'):
            measure(catalog, Profile('custom', 4, 100, 1, 1, 3),
                    dict(adapter='sqlite', layout='start', treatment='fresh', round=0),
                    SimpleNamespace(check=lambda: None), 1, expected_settings={'different': True})



def test_unverified_worker_teardown_forbids_resume(tmp_path, monkeypatch, simulated_interval_run):
    import json

    from tools import qualify_intervals as runner
    def worker(config, *args):
        if config['order_seed'] == 2:
            raise runner.WorkerCleanupError('unknown worker state')
        return simulated_interval_run.worker(config, *args)
    monkeypatch.setattr(runner, 'run_worker', worker)
    output = tmp_path / 'failed'
    assert main(['--output', str(output), '--seed', '1', '--reserve-bytes', '0']) == 1
    report = json.loads((output / 'report.json').read_text())
    assert len(report['cells']) == 1 and not report['cleanup'] and not report['resume_eligible']


def test_execution_gap_forbids_resume(tmp_path, monkeypatch, simulated_interval_run):
    import json

    from tools import qualify_intervals as runner
    original = runner.ExecutionGuard.evidence
    def evidence(guard):
        return {**original(guard), 'continuity_lost': True}
    monkeypatch.setattr(runner.ExecutionGuard, 'evidence', evidence)
    output = tmp_path / 'failed'
    assert main(['--output', str(output), '--seed', '1', '--reserve-bytes', '0', '--pause-after', '1']) == 1
    report = json.loads((output / 'report.json').read_text())
    assert not report['resume_eligible'] and not report['continuity']
