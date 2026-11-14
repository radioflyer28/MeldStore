"""Focused provenance and acceptance tests; no live services or large workloads."""

import copy
import json
import shutil
import subprocess
import sys

import pytest

from tools import qualify_find as runner

PACKAGE_FILES = {
    'meldstore/__init__.py': b'"""Synthetic package."""\n',
    'meldstore/store.py': b'VALUE = 1\n',
    'meldstore/encodings.py': b'VALUE = 2\n',
    'meldstore/nested/helper.py': b'VALUE = 3\n',
}


def write_package(root, files=PACKAGE_FILES):
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


@pytest.fixture
def baseline(tmp_path):
    repository = tmp_path / 'repository'
    repository.mkdir()
    subprocess.run(['git', 'init', '-q', str(repository)], check=True, capture_output=True)

    def git(*args):
        return subprocess.check_output([
            'git', '-c', 'core.bare=false', '-c', 'core.autocrlf=false',
            '-c', f'safe.directory={repository.as_posix()}', '-C', str(repository), *args,
        ])

    write_package(repository / 'src')
    git('add', 'src')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qm', 'synthetic baseline')
    revision = git('rev-parse', 'HEAD').decode().strip()
    extracted = tmp_path / 'extracted'
    shutil.copytree(repository / 'src', extracted)
    return repository, revision, extracted


def test_baseline_verifies_every_python_file_with_one_git_process(baseline, monkeypatch):
    repository, revision, extracted = baseline
    calls = []
    original = subprocess.check_output

    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, 'check_output', counted)
    result = runner.verify_baseline(extracted, revision=revision, repository=repository)
    assert result['verified'] and result['revision'] == revision
    assert result['sources'] == runner.fingerprints(PACKAGE_FILES)
    assert result['crlf_normalized_files'] == []
    assert set(result['git_blobs']) == PACKAGE_FILES.keys()
    assert len(calls) == 1 and 'ls-tree' in calls[0]


@pytest.mark.parametrize('damage', ['missing', 'extra', 'changed', 'whitespace', 'bare_cr'])
def test_baseline_rejects_unproven_sources(baseline, damage):
    repository, revision, extracted = baseline
    path = extracted / 'meldstore/nested/helper.py'
    if damage == 'missing':
        path.unlink()
    elif damage == 'extra':
        (extracted / 'meldstore/extra.py').write_text('VALUE = 9\n', encoding='utf-8')
    elif damage == 'changed':
        path.write_bytes(b'VALUE = 99\n')
    elif damage == 'whitespace':
        path.write_bytes(b'VALUE = 3  \n')
    else:
        path.write_bytes(b'VALUE = 3\r')
    with pytest.raises(ValueError, match='differ'):
        runner.verify_baseline(extracted, revision=revision, repository=repository)


def test_baseline_accepts_only_crlf_normalization_and_records_actual_bytes(baseline):
    repository, revision, extracted = baseline
    actual = {name: data.replace(b'\n', b'\r\n') for name, data in PACKAGE_FILES.items()}
    write_package(extracted, actual)
    result = runner.verify_baseline(extracted, revision=revision, repository=repository)
    assert set(result['crlf_normalized_files']) == PACKAGE_FILES.keys()
    assert result['sources'] == runner.fingerprints(actual)
    assert result['sources'] != runner.fingerprints(PACKAGE_FILES)


def source_process(source, expected=None, preload=None):
    script = (
        "import json, sys\n"
        "from tools.qualify_find import attest_source\n"
        "assert not any(n == 'meldstore' or n.startswith('meldstore.') for n in sys.modules)\n"
    )
    if preload is not None:
        script += f"sys.path.insert(0, {str(preload)!r})\nimport meldstore.store\n"
    script += (
        f"sys.path.insert(0, {str(source)!r})\n"
        f"print(json.dumps(attest_source({str(source)!r}, {expected!r})))\n"
    )
    return subprocess.run([sys.executable, '-B', '-c', script], cwd=runner.ROOT,
                          text=True, capture_output=True)


@pytest.mark.parametrize('crlf', [False, True])
def test_worker_attestation_uses_selected_actual_source_without_eager_import(tmp_path, crlf):
    files = {name: data.replace(b'\n', b'\r\n') if crlf else data
             for name, data in PACKAGE_FILES.items()}
    selected = write_package(tmp_path / 'selected', files)
    result = source_process(selected, runner.fingerprints(files))
    assert result.returncode == 0, result.stderr
    attestation = json.loads(result.stdout)
    assert attestation['sources'] == runner.fingerprints(files)
    assert attestation['imports_verified'] is True
    assert {'meldstore', 'meldstore.store', 'meldstore.encodings'} <= set(
        attestation['imported_modules'])
    assert str(tmp_path) not in result.stdout


def test_worker_rejects_preimported_other_package(tmp_path):
    selected = write_package(tmp_path / 'selected')
    other = write_package(tmp_path / 'other')
    result = source_process(selected, preload=other)
    assert result.returncode != 0 and 'outside the selected source' in result.stderr


def test_worker_rejects_source_changes_since_manifest(tmp_path):
    selected = write_package(tmp_path / 'selected')
    expected = runner.fingerprints(PACKAGE_FILES)
    (selected / 'meldstore/encodings.py').write_bytes(b'VALUE = 99\n')
    result = source_process(selected, expected)
    assert result.returncode != 0 and 'changed after' in result.stderr


def report_fixture():
    baseline = runner.fingerprints(PACKAGE_FILES)
    candidate = runner.fingerprints({k: v + b'# candidate\n' for k, v in PACKAGE_FILES.items()})
    report = dict(
        status='completed_measurements', baseline=runner.BASELINE_REVISION,
        baseline_sources=baseline, candidate_sources=candidate,
        baseline_verification=dict(verified=True, revision=runner.BASELINE_REVISION,
                                   sources=baseline),
        samples=100, warmups=5, relationships=300000, comparisons=[],
        owned_services_removed=True, latency_memory_review='pending',
        requested=dict(adapters=['sqlite', 'melddb', 'postgres'],
                       counts=[100, 1000], clients=[1, 2, 4]),
    )
    for adapter, count, clients in sorted(runner.REQUIRED_MATRIX):
        comparison = dict(adapter=adapter, records=count, clients=clients, sql_reduction=.98)
        for label, sources, sql_count in [('baseline', baseline, 1000), ('candidate', candidate, 20)]:
            result = dict(
                status='passed', returned_records=count, measured_reads=100, warmups=5,
                sql_per_find=sql_count, latency=dict(unit='seconds', samples=100, p50=.1, p95=.2),
                peak_rss_bytes=1000, storage_calls=0, errors=0, conflicts=0,
                source_attestation=dict(imports_verified=True, sources=sources,
                                        imported_modules=['meldstore', 'meldstore.store',
                                                          'meldstore.encodings']),
            )
            comparison[label] = dict(results=[copy.deepcopy(result) for _ in range(clients)],
                                     sampled_aggregate_rss_bytes=1000 * clients)
        report['comparisons'].append(comparison)
    return report


def test_full_measurements_are_not_accepted_before_explicit_review():
    report = report_fixture()
    original = copy.deepcopy(report)
    summary = runner.validate_report(report)
    assert report == original
    assert summary == dict(
        completed_measurements=True, acceptance_profile='full',
        worker_attestations_complete=True, source_provenance_verified=True,
        latency_memory_review='pending', full_acceptance=False,
    )
    report['latency_memory_review'] = 'approved'
    assert runner.validate_report(report)['full_acceptance']
    report['latency_memory_review'] = 'rejected'
    assert not runner.validate_report(report)['full_acceptance']


@pytest.mark.parametrize('dimension', ['samples', 'warmups', 'relationships', 'matrix'])
def test_diagnostic_runs_never_receive_full_acceptance(dimension):
    report = report_fixture()
    report['latency_memory_review'] = 'approved'
    if dimension == 'relationships':
        report['relationships'] = 0
    elif dimension == 'matrix':
        report['comparisons'] = report['comparisons'][:1]
        case = report['comparisons'][0]
        report['requested'] = dict(adapters=[case['adapter']], counts=[case['records']],
                                   clients=[case['clients']])
    else:
        report[dimension] = 1 if dimension == 'samples' else 0
        for case in report['comparisons']:
            for label in ('baseline', 'candidate'):
                for result in case[label]['results']:
                    if dimension == 'samples':
                        result['measured_reads'] = result['latency']['samples'] = 1
                    else:
                        result['warmups'] = 0
    summary = runner.validate_report(report)
    assert summary['completed_measurements']
    assert summary['acceptance_profile'] == 'diagnostic'
    assert not summary['full_acceptance']


def test_missing_requested_case_remains_incomplete():
    report = report_fixture()
    report['comparisons'].pop()
    assert not runner.validate_report(report)['completed_measurements']


@pytest.mark.parametrize('damage', [
    'duplicate', 'worker', 'cardinality', 'reads', 'errors', 'storage',
    'sql_count', 'reduction', 'gate', 'attestation', 'imports', 'memory', 'latency',
])
def test_report_rejects_invalid_or_inconsistent_evidence(damage):
    report = report_fixture()
    case = next(c for c in report['comparisons'] if c['adapter'] == 'postgres'
                and c['records'] == 100 and c['clients'] == 4)
    result = case['candidate']['results'][-1]
    if damage == 'duplicate':
        report['comparisons'].append(copy.deepcopy(case))
    elif damage == 'worker':
        case['candidate']['results'].pop()
    elif damage == 'cardinality':
        result['returned_records'] = 99
    elif damage == 'reads':
        result['measured_reads'] = 99
    elif damage == 'errors':
        result['errors'] = 1
    elif damage == 'storage':
        result['storage_calls'] = 1
    elif damage == 'sql_count':
        result['sql_per_find'] = 21
    elif damage == 'reduction':
        case['sql_reduction'] = .99
    elif damage == 'gate':
        for row in case['candidate']['results']:
            row['sql_per_find'] = 101
        case['sql_reduction'] = .899
    elif damage == 'attestation':
        result['source_attestation']['sources']['meldstore/store.py'] = 'wrong'
    elif damage == 'imports':
        result['source_attestation']['imports_verified'] = False
    elif damage == 'memory':
        result['peak_rss_bytes'] = 0
    else:
        result['latency']['p50'] = float('nan')
    with pytest.raises(ValueError):
        runner.validate_report(report)


def test_old_report_requires_explicit_external_source_audit_without_mutation():
    report = report_fixture()
    report['status'] = 'passed'
    del report['requested'], report['baseline_verification']
    candidate = report.pop('candidate_sources')
    report['provenance'] = {'source_xxh3_128': {'src/' + k: v for k, v in candidate.items()}}
    for case in report['comparisons']:
        for label in ('baseline', 'candidate'):
            for result in case[label]['results']:
                del result['source_attestation']
    report['latency_memory_review'] = 'approved'
    original = copy.deepcopy(report)
    summary = runner.validate_report(report)
    assert report == original
    assert summary['completed_measurements']
    assert not summary['worker_attestations_complete']
    assert not summary['source_provenance_verified']
    assert not summary['full_acceptance']
    report['source_audit'] = dict(
        status='verified', notes='Independent archive and recorded-source comparison.',
        baseline_revision=runner.BASELINE_REVISION, baseline_sources=report['baseline_sources'],
        candidate_sources=candidate,
    )
    assert runner.validate_report(report)['full_acceptance']
    report['source_audit']['candidate_sources'] = {'wrong': 'wrong'}
    with pytest.raises(ValueError, match='audit'):
        runner.validate_report(report)


def test_spawned_sqlite_pilot_attests_before_timing(tmp_path):
    # Two fresh workers ensure source selection is exercised through spawn.
    from meldstore import Catalog
    from tools.qualification.find_fixture import seed_find

    catalog_path = tmp_path / 'pilot.db'
    with Catalog(catalog_path, adapter='sqlite') as catalog:
        seed_find(catalog, count=1, encoding='empty')
    manifest = runner.fingerprints(runner.source_bytes(runner.ROOT / 'src'))
    result = runner.measure(dict(adapter='sqlite', catalog=str(catalog_path),
                                 expected_sources=manifest),
                            str(runner.ROOT / 'src'), 1, 2, 1, 0, 60)
    assert len(result['results']) == 2
    for worker in result['results']:
        assert worker['measured_reads'] == worker['returned_records'] == 1
        assert worker['storage_calls'] == 0
        assert worker['source_attestation']['sources'] == manifest


def test_worker_keeps_legacy_parent_signature_and_optional_manifest(tmp_path, monkeypatch):
    import inspect
    from contextlib import nullcontext
    from types import SimpleNamespace

    from meldstore import Catalog
    from tools.qualification.find_fixture import seed_find

    assert list(inspect.signature(runner.worker).parameters) == [
        'config', 'source', 'count', 'samples', 'warmups', 'barrier', 'lock', 'pipe',
    ]
    path = tmp_path / 'legacy-parent.db'
    with Catalog(path, adapter='sqlite') as catalog:
        seed_find(catalog, count=1)
    messages = []
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    runner.worker(
        dict(adapter='sqlite', catalog=str(path)), str(runner.ROOT / 'src'), 1, 1, 0,
        SimpleNamespace(wait=lambda **kwargs: None), nullcontext(),
        SimpleNamespace(send=messages.append, close=lambda: None),
    )
    assert len(messages) == 1 and messages[0]['status'] == 'passed'
    assert messages[0]['source_attestation']['imports_verified']


def reusable_fixture():
    report = report_fixture()
    runtime = dict(melddb_revision='pin', python='3.13', sqlite_version='3', os='Linux',
                   os_release='test', architecture='test', logical_cpus=4,
                   lock_xxh3_128='lock', packages={'melddb': 'test'},
                   source_xxh3_128={name: 'fixture' for name in (
                       'tools/qualification/find_fixture.py', 'tools/qualification/fixture.py',
                       'tools/qualification/measurements.py', 'tools/qualification/services.py',
                       'tools/postgres_lab.py', 'tools/s3_lab.py', 'tools/qualify_local.py')})
    report['provenance'] = copy.deepcopy(runtime)
    options = dict(samples=100, warmups=5, relationships=300000, matrix=runner.REQUIRED_MATRIX)
    return report, runtime, options


def test_reuse_preserves_complete_baseline_and_ignores_superseded_candidate():
    report, runtime, options = reusable_fixture()
    report['comparisons'][0]['candidate'] = {'superseded': True}
    before = copy.deepcopy(report)
    result = runner.reusable_baseline(report, report['baseline_sources'], runtime, **options)
    assert set(result) == runner.REQUIRED_MATRIX
    assert report == before
    assert result[('postgres', 1000, 4)]['results'][0]['measured_reads'] == 100


@pytest.mark.parametrize('damage', [
    'source', 'runtime', 'samples', 'warmups', 'relationships', 'matrix', 'completion',
    'errors', 'count', 'latency', 'attestation', 'duplicate',
])
def test_reuse_rejects_incompatible_or_incomplete_baseline(damage):
    report, runtime, options = reusable_fixture()
    sources = copy.deepcopy(report['baseline_sources'])
    row = report['comparisons'][0]['baseline']['results'][0]
    if damage == 'source':
        sources['meldstore/store.py'] = 'different'
    elif damage == 'runtime':
        runtime['lock_xxh3_128'] = 'different'
    elif damage in {'samples', 'warmups', 'relationships'}:
        options[damage] -= 1
    elif damage == 'matrix':
        report['comparisons'].pop()
    elif damage == 'completion':
        report['owned_services_removed'] = False
    elif damage == 'errors':
        row['errors'] = 1
    elif damage == 'count':
        row['returned_records'] = 0
    elif damage == 'latency':
        row['latency']['p95'] = float('nan')
    elif damage == 'attestation':
        row['source_attestation']['imports_verified'] = False
    elif damage == 'duplicate':
        report['comparisons'].append(copy.deepcopy(report['comparisons'][0]))
    with pytest.raises(ValueError):
        runner.reusable_baseline(report, sources, runtime, **options)


def test_baseline_fixture_change_requires_matching_explicit_equivalence_audit():
    report, runtime, options = reusable_fixture()
    name = 'tools/qualification/find_fixture.py'
    runtime['source_xxh3_128'][name] = 'reformatted'
    with pytest.raises(ValueError, match='fixture changed'):
        runner.reusable_baseline(report, report['baseline_sources'], runtime, **options)
    report['fixture_source_audit'] = {name: dict(recorded_xxh3_128='fixture',
        final_xxh3_128='reformatted', ast_identical=True, change='Reviewed import wrapping only')}
    assert len(runner.reusable_baseline(report, report['baseline_sources'], runtime, **options)) == 18
    report['fixture_source_audit'][name]['ast_identical'] = False
    with pytest.raises(ValueError, match='fixture changed'):
        runner.reusable_baseline(report, report['baseline_sources'], runtime, **options)
