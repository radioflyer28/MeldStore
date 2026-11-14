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
    names = ['import', 'relationship_load', 'interval_query', 'bulk_sample', 'repeated_sql_sample',
             'shared_publication_rollback', 'concurrent_clients', 'uncertain_publication',
             'migration_dry_run', 'migration_first_batch', 'migration_resume', 'interrupted_backup',
             'backup', 'corrupt_restore_refused', 'restore', 'occupied_restore_refused',
             'cutover', 'cutover_preflight']
    operations = [{'name': name, 'status': 'passed', 'clients': 1} for name in names]
    report = {'status': 'incomplete', 'profile': 'smoke', 'representative_qualified': False,
              'adapters': {name: {'status': 'passed', 'operations': copy.deepcopy(operations),
                                  'peak_rss_bytes': 10, 'sampled_disk_bytes': 20}
                           for name in ('sqlite', 'melddb', 'postgres')}}
    manifest = {'profile': {'name': 'smoke', 'clients': [1]}, 'files': []}
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
