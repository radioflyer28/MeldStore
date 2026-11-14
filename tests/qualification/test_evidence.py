import copy
import json

import pytest

from tools.qualification.evidence import complete_report, sanitized, write_report
from tools.qualification.measurements import latency


@pytest.mark.parametrize('value', [r'C:\Users\someone\data', '/home/someone/data',
                                  'password=secret', 'postgresql://user:secret@host/db',
                                  {'secret_access_key': 'secret'}])
def test_publishable_evidence_rejects_private_values(value):
    with pytest.raises(ValueError):
        sanitized({'value': value})


def test_atomic_partial_report_cannot_be_completion(tmp_path):
    path = tmp_path / 'report.json'
    partial = {'status': 'incomplete', 'adapters': {}, 'representative_qualified': False}
    write_report(path, partial)
    assert json.loads(path.read_text()) == partial
    with pytest.raises(ValueError, match='adapters'):
        complete_report(partial, {'profile': {'name': 'smoke'}})
    assert json.loads(path.read_text())['status'] == 'incomplete'


def test_percentiles_have_units_and_sample_count():
    assert latency(list(range(100))) == {'unit': 'seconds', 'samples': 100, 'p50': 49, 'p95': 94}
    with pytest.raises(ValueError):
        latency([])


def report_fixture():
    from tools.qualification.fixture import interval_cases

    names = ['import', 'relationship_load', 'shared_publication_rollback', 'uncertain_publication',
             'migration_dry_run', 'migration_first_batch', 'migration_resume', 'interrupted_backup',
             'backup', 'corrupt_restore_refused', 'restore', 'occupied_restore_refused',
             'cutover', 'cutover_preflight', 'export_verify', 'alternative_index_build']
    operations = [{'name': name, 'status': 'passed'} for name in names]
    measured = {'latency': latency([1] * 100), 'warmup': 5}
    for index in ('start', 'end'):
        operations.extend(
            dict(name='interval_query', status='passed', index=index, selectivity=list(case),
                 plan=[{'plan': 'sample plan'}], successes=100, errors=0, **measured)
            for case in interval_cases(4))
        operations.extend(dict(name='bulk_sample', status='passed', index=index, batch=batch,
                               rows=2000) for batch in (100, 1000, 10000))
        operations.append(dict(name='repeated_sql_sample', status='passed', index=index, rows=2000))
    operations.append(dict(name='concurrent_clients', status='passed', clients=1, attempts=2,
                           successes=2, conflicts=0, errors=0, sampled_aggregate_rss_bytes=20,
                           results=[dict(client=0, status='passed', peak_rss_bytes=10,
                                         disjoint_success=1, shared_success=1, shared_conflict=0,
                                         **measured)]))
    report = {'status': 'incomplete', 'profile': 'smoke', 'representative_qualified': False,
              'adapters': {name: {'status': 'passed', 'operations': copy.deepcopy(operations),
                                  'peak_rss_bytes': 10, 'sampled_disk_bytes': 20}
                           for name in ('sqlite', 'melddb', 'postgres')}}
    manifest = {'profile': {'name': 'smoke', 'clients': [1], 'relationships': 2000},
                'files': [{} for _ in range(4)]}
    return report, manifest


def test_smoke_success_does_not_become_representative():
    report, manifest = report_fixture()
    complete_report(report, manifest)
    assert report['status'] == 'passed'
    assert report['representative_qualified'] is False


@pytest.mark.parametrize('damage', ['phase', 'failed', 'memory', 'disk', 'clients', 'scale'])
def test_completion_requires_all_evidence(damage):
    report, manifest = report_fixture()
    value = report['adapters']['postgres']
    if damage == 'phase':
        value['operations'].pop(0)
    elif damage == 'failed':
        value['operations'][0]['status'] = 'incomplete'
    elif damage == 'memory':
        value['peak_rss_bytes'] = None
    elif damage == 'disk':
        value['sampled_disk_bytes'] = None
    elif damage == 'clients':
        manifest['profile']['clients'] = [1, 2]
    else:
        manifest['profile']['name'] = 'representative'
        report['profile'] = 'representative'
    with pytest.raises(ValueError):
        complete_report(report, manifest)
    assert report['status'] == 'incomplete'

def test_missing_dependency_fails_instead_of_skipping(tmp_path, monkeypatch):
    import tools.qualification.evidence as evidence
    from tools.qualify_representative import main

    def missing():
        raise ImportError('required dependency absent')

    monkeypatch.setattr(evidence, 'provenance', missing)
    target = tmp_path / 'missing'
    with pytest.raises(ImportError):
        main(['--output', str(target)])
    report = json.loads((target / 'report.json').read_text())
    assert report['status'] == 'failed'
    assert report['representative_qualified'] is False


@pytest.mark.parametrize('missing', ['interval', 'bulk', 'export', 'worker_latency', 'worker_rss'])
def test_completion_rejects_missing_measurement_cases(missing):
    report, manifest = report_fixture()
    ops = report['adapters']['postgres']['operations']
    if missing in ('interval', 'bulk', 'export'):
        name = {'interval': 'interval_query', 'bulk': 'bulk_sample', 'export': 'export_verify'}[missing]
        ops.remove(next(op for op in ops if op['name'] == name))
    else:
        worker = next(op for op in ops if op['name'] == 'concurrent_clients')['results'][0]
        del worker['latency' if missing == 'worker_latency' else 'peak_rss_bytes']
    with pytest.raises(ValueError):
        complete_report(report, manifest)
    assert report['representative_qualified'] is False

def test_teardown_failure_cannot_publish_qualification(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import tools.qualification.evidence as evidence
    import tools.qualification.services as services
    from tools import qualify_representative as cli

    manifest = {'profile': {'name': 'representative', 'relationships': 300_000,
                            'clients': [1, 2, 4]},
                'files': [{'bytes': 100_000_000}] * 100}

    class FailedTeardown:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            raise RuntimeError('Teardown failed')

        def provenance(self):
            return {}

        def free_bytes(self, **kwargs):
            return 10**15

        def check_reserve(self, *_):
            pass

    target = tmp_path / 'run'

    def configure_process(*, target, args):
        def start():
            adapter, output = args[:2]
            folder = output / adapter
            folder.mkdir()
            evidence.write_report(folder / 'report.json', {'status': 'passed', 'operations': [],
                                                            'peak_rss_bytes': 1})

        return SimpleNamespace(start=start, is_alive=lambda: False, join=lambda **kwargs: None,
                               exitcode=0)

    def complete(*args):
        pytest.fail('Completion was attempted before service teardown')

    monkeypatch.setattr(evidence, 'complete_report', complete)
    monkeypatch.setattr(evidence, 'provenance', dict)
    monkeypatch.setattr(services, 'Services', FailedTeardown)
    monkeypatch.setattr(cli, 'capacity', lambda *args: {})
    monkeypatch.setattr(cli, 'generate', lambda *args, **kwargs: manifest)
    monkeypatch.setattr(cli.multiprocessing, 'get_context', lambda _: SimpleNamespace(
        Process=configure_process))
    with pytest.raises(RuntimeError, match='Teardown failed'):
        cli.main(['--profile', 'representative', '--output', str(target)])
    report = json.loads((target / 'report.json').read_text())
    assert report['status'] == 'failed'
    assert report['representative_qualified'] is False
    assert set(report['adapters']) == {'sqlite', 'melddb', 'postgres'}
