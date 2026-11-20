"""Recovery uses real sanitized checkpoint evidence and the production validators."""

import copy
import json
import os
from pathlib import Path

import pytest

from tools import recover_intervals as recovery
from tools.qualification.evidence import write_report
from tools.qualification.interval_checkpoint import load_checkpoint


@pytest.fixture
def interrupted(tmp_path, monkeypatch):
    evidence = Path(__file__).resolve().parents[2] / 'docs/evidence/interval-checkpoint-smoke-2026-11-18.json'
    report = json.loads(evidence.read_bytes())
    report['sessions'] = report['sessions'][:1]
    count = report['sessions'][0]['end_cell']
    report['cells'] = report['cells'][:count]
    report['checkpoints'] = report['checkpoints'][:count]
    report.update(status='incomplete', cleanup=False, continuity=False, resume_eligible=False,
                  full_metadata_qualified=False)
    report['sessions'][-1].update(ended_at=None, cleanup=False)
    root = tmp_path / 'source'
    root.mkdir()
    token = report['sessions'][-1]['id']
    pg = 'meldstore-workload-' + token
    (root / 'ownership.json').write_text(json.dumps({'version': 1, 'run_id': token, 'resources': [
        {'kind': 'volume', 'name': pg + '-data'}, {'kind': 'container', 'name': pg}]}))
    write_report(root / 'report.json', report)
    monkeypatch.setattr(recovery, 'provenance', lambda: copy.deepcopy(report['provenance']))
    monkeypatch.setattr(recovery, 'process_inventory', lambda: [
        {'ProcessId': os.getpid(), 'Name': 'python.exe', 'CommandLine': 'recovery test'}])
    monkeypatch.setattr(recovery, 'cleanup', lambda *args: None)
    monkeypatch.setattr(recovery, 'inspect_resource', lambda *args: None)
    return root / 'report.json', tmp_path / 'recovered', report


def test_recovery_preserves_prefix_guard_and_source_and_can_resume(interrupted, monkeypatch):
    source, output, original = interrupted
    before = source.read_bytes()
    directory = source.parent / f'cell-{len(original["cells"]):03}'
    directory.mkdir()
    (directory / 'control.json').write_text(json.dumps({
        'directory': str(directory), 'profile': original['profile'],
        'cell': original['schedule'][len(original['cells'])], 'provenance': original['provenance']}))
    scratch = directory / 'interval-aborted'
    scratch.mkdir()
    (scratch / 'catalog.sqlite').write_bytes(b'incomplete database')
    result = recovery.recover(source, output)
    assert source.read_bytes() == before
    assert not scratch.exists() and (directory / 'control.json').exists()
    assert result['cells'] == original['cells']
    assert result['checkpoints'] == original['checkpoints']
    session = result['sessions'][-1]
    assert session['execution'] == original['sessions'][-1]['execution']
    assert session['ended_at'] == session['checkpoint_at']
    assert session['cleanup_verified_at'] >= session['ended_at']
    audit = result['recoveries'][-1]
    assert audit['source_report_xxh3_128'] == recovery.byte_digest(before)
    assert audit['original_ended_at'] is None and audit['original_cleanup'] is False
    assert audit['continuity_after_checkpoint'].startswith('unknown')
    assert not result['full_metadata_qualified']
    loaded = load_checkpoint(output / 'report.json', original['provenance'])
    from tools.qualify_intervals import new_report, options
    monkeypatch.setattr('tools.qualify_intervals.provenance', lambda: original['provenance'])
    args, profile, resumed = options(['--resume-from', str(output / 'report.json'),
                                     '--output', str(output.parent / 'next')])
    assert loaded == resumed
    next_report = new_report(args, profile, resumed, 'a' * 32)
    assert next_report['recoveries'] == result['recoveries']
    assert next_report['sessions'][-1]['start_cell'] == len(original['cells'])
    assert not next_report['resume_eligible']


@pytest.mark.parametrize('damage', ['legacy', 'checksum', 'samples', 'provenance', 'closed', 'guard'])
def test_damaged_or_incompatible_evidence_cannot_be_recovered(interrupted, damage):
    source, output, report = interrupted
    if damage == 'legacy':
        report['schema_version'] = 1
    elif damage == 'checksum':
        report['checkpoints'][0]['xxh3_128'] = '0' * 32
    elif damage == 'samples':
        report['cells'].pop()
    elif damage == 'provenance':
        report['provenance_after'] = {}
    elif damage == 'closed':
        report['sessions'][-1]['ended_at'] = report['sessions'][-1]['checkpoint_at']
    else:
        report['sessions'][-1]['execution']['continuity_lost'] = True
    write_report(source, report)
    before = source.read_bytes()
    with pytest.raises((ValueError, KeyError)):
        recovery.recover(source, output)
    assert source.read_bytes() == before and not output.exists()


@pytest.mark.parametrize('command', ['python -m tools.qualification.interval_worker elsewhere',
                                     'uv run python tools/qualify_intervals.py', None])
def test_active_or_uninspectable_process_refuses_before_cleanup(interrupted, monkeypatch, command):
    source, output, _ = interrupted
    called = []
    monkeypatch.setattr(recovery, 'cleanup', lambda *a: called.append(True))
    monkeypatch.setattr(recovery, 'process_inventory', lambda: [
        {'ProcessId': os.getpid(), 'Name': 'python.exe', 'CommandLine': 'recover'},
        {'ProcessId': os.getpid() + 1, 'Name': 'python.exe', 'CommandLine': command}])
    with pytest.raises(ValueError):
        recovery.recover(source, output)
    assert not called and not output.exists()


def test_cleanup_failure_cannot_publish_eligible_report(interrupted, monkeypatch):
    source, output, _ = interrupted
    before = source.read_bytes()
    def fail(*args):
        raise RuntimeError('Docker unavailable or ownership mismatch')
    monkeypatch.setattr(recovery, 'cleanup', fail)
    with pytest.raises(RuntimeError):
        recovery.recover(source, output)
    assert source.read_bytes() == before and not output.exists()
    assert not (source.parent / 'recovery.lock').exists()


def test_resource_still_present_refuses(interrupted, monkeypatch):
    source, output, _ = interrupted
    monkeypatch.setattr(recovery, 'inspect_resource', lambda *args: {'Id': 'remaining'})
    with pytest.raises(ValueError, match='remains'):
        recovery.recover(source, output)
    assert not output.exists()


@pytest.mark.parametrize('problem', ['session', 'extra-resource', 'occupied', 'overlap', 'lock'])
def test_unsafe_ownership_and_destinations_refuse(interrupted, problem):
    source, output, _ = interrupted
    manifest = source.parent / 'ownership.json'
    ownership = json.loads(manifest.read_bytes())
    if problem == 'session':
        ownership['run_id'] = 'b' * 32
    elif problem == 'extra-resource':
        ownership['resources'].append({'kind': 'volume', 'name': 'unrelated'})
    elif problem == 'occupied':
        output.mkdir()
    elif problem == 'overlap':
        output = source.parent / 'nested'
    else:
        (source.parent / 'recovery.lock').write_text('stale')
    manifest.write_text(json.dumps(ownership))
    before = source.read_bytes()
    with pytest.raises((ValueError, FileExistsError)):
        recovery.recover(source, output)
    assert source.read_bytes() == before and not (output / 'report.json').exists()


def test_changed_source_after_cleanup_refuses(interrupted, monkeypatch):
    source, output, _ = interrupted
    monkeypatch.setattr(recovery, 'cleanup', lambda *args: source.write_bytes(source.read_bytes() + b' '))
    with pytest.raises(ValueError, match='changed during recovery'):
        recovery.recover(source, output)
    assert not output.exists()


def test_incomplete_inventory_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(recovery, 'process_inventory', lambda: [])
    with pytest.raises(ValueError, match='inventory'):
        recovery.require_stopped(tmp_path)


def test_process_inventory_failure_propagates(interrupted, monkeypatch):
    source, output, _ = interrupted
    def fail():
        raise RuntimeError('CIM unavailable')
    monkeypatch.setattr(recovery, 'process_inventory', fail)
    with pytest.raises(RuntimeError):
        recovery.recover(source, output)
    assert not output.exists()


def test_worker_ownership_refuses_before_cleanup(interrupted, monkeypatch):
    source, output, original = interrupted
    directory = source.parent / f'cell-{len(original["cells"]):03}'
    directory.mkdir()
    (directory / 'control.json').write_text(json.dumps({
        'directory': str(source.parent.parent), 'profile': original['profile'],
        'cell': original['schedule'][len(original['cells'])], 'provenance': original['provenance']}))
    called = []
    monkeypatch.setattr(recovery, 'cleanup', lambda *args: called.append(True))
    with pytest.raises(ValueError, match='ownership/control'):
        recovery.recover(source, output)
    assert not called and not output.exists()


def test_process_appearing_after_cleanup_prevents_publication(interrupted, monkeypatch):
    source, output, _ = interrupted
    before = source.read_bytes()
    own = {'ProcessId': os.getpid(), 'Name': 'python.exe', 'CommandLine': 'recover'}
    snapshots = iter([[own], [own, {'ProcessId': os.getpid() + 1, 'Name': 'python.exe',
                                  'CommandLine': 'python tools/qualify_intervals.py'}]])
    monkeypatch.setattr(recovery, 'process_inventory', lambda: next(snapshots))
    with pytest.raises(ValueError, match='active'):
        recovery.recover(source, output)
    assert source.read_bytes() == before and not output.exists()


def test_linked_source_refuses(interrupted):
    source, output, _ = interrupted
    link = source.parent.parent / 'linked'
    try:
        link.symlink_to(source.parent, target_is_directory=True)
    except OSError:
        pytest.skip('Host does not permit symbolic link creation')
    before = source.read_bytes()
    with pytest.raises(ValueError, match='canonical'):
        recovery.recover(link / 'report.json', output)
    assert source.read_bytes() == before and not output.exists()


def test_junction_scratch_refuses_before_any_cleanup(interrupted, monkeypatch):
    source, output, original = interrupted
    directory = source.parent / f'cell-{len(original["cells"]):03}'
    directory.mkdir()
    (directory / 'control.json').write_text(json.dumps({
        'directory': str(directory), 'profile': original['profile'],
        'cell': original['schedule'][len(original['cells'])], 'provenance': original['provenance']}))
    scratch = directory / 'interval-junction'
    scratch.mkdir()
    monkeypatch.setattr(Path, 'is_junction', lambda self: self == scratch)
    called = []
    monkeypatch.setattr(recovery, 'cleanup', lambda *args: called.append(True))
    with pytest.raises(ValueError, match='canonical'):
        recovery.recover(source, output)
    assert scratch.exists() and not called and not output.exists()


def test_final_validation_failure_removes_eligible_staging_file(interrupted, monkeypatch):
    source, output, _ = interrupted
    before = source.read_bytes()
    def fail(*args):
        raise ValueError('Final verification failed')
    monkeypatch.setattr(recovery, 'load_checkpoint', fail)
    with pytest.raises(ValueError, match='Final verification'):
        recovery.recover(source, output)
    assert source.read_bytes() == before
    assert not (output / 'report.json').exists() and not (output / 'recovered.json').exists()
