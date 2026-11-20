import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.qualification.interval_service import IntervalService
from tools.qualification.resources import ResourceLimit, fresh_root
from tools.qualify_intervals import main, run_worker


def test_service_refuses_collision_before_claiming_resource(tmp_path, monkeypatch):
    root, token = fresh_root(tmp_path / 'owned')
    service = IntervalService(root, token, time.monotonic() + 30)
    monkeypatch.setattr('tools.qualification.interval_service.inspect_resource', lambda *a, **k: {'Id': 'other'})
    monkeypatch.setattr(service, 'close', lambda: None)
    with pytest.raises(ValueError, match='already exists'):
        service.__enter__()
    assert json.loads((root / 'ownership.json').read_text())['resources'] == []


def test_service_checks_volume_ownership_before_mount(tmp_path, monkeypatch):
    root, token = fresh_root(tmp_path / 'owned')
    service = IntervalService(root, token, time.monotonic() + 30)
    results = iter([None, None, {'Labels': {'other': 'owner'}}])
    monkeypatch.setattr('tools.qualification.interval_service.inspect_resource', lambda *a, **k: next(results))
    monkeypatch.setattr('tools.qualification.interval_service.docker', lambda *a, **k: '')
    def forbidden(*a, **k):
        raise AssertionError('Container must not be started')
    monkeypatch.setattr('tools.qualification.interval_service.docker_run', forbidden)
    monkeypatch.setattr(service, 'close', lambda: None)
    with pytest.raises(ValueError, match='volume ownership'):
        service.__enter__()


def test_startup_timeout_attempts_cleanup(tmp_path, monkeypatch):
    root, token = fresh_root(tmp_path / 'owned')
    service = IntervalService(root, token, time.monotonic() + 30)
    monkeypatch.setattr('tools.qualification.interval_service.inspect_resource', lambda *a, **k: None)
    def timeout(*a, **k):
        raise ResourceLimit('startup deadline')
    monkeypatch.setattr('tools.qualification.interval_service.docker', timeout)
    cleaned = []
    monkeypatch.setattr(service, 'close', lambda: cleaned.append(True))
    with pytest.raises(ResourceLimit):
        service.__enter__()
    assert cleaned == [True]


class Running:
    def __init__(self):
        self.stopped = False
        self.killed = False
    def poll(self):
        return 1 if self.killed else None
    def terminate(self):
        self.stopped = True
    def wait(self, timeout):
        if not self.killed:
            raise subprocess.TimeoutExpired('synthetic', timeout)
        return 1
    def kill(self):
        self.killed = True


def test_parent_deadline_kills_unresponsive_worker(tmp_path, monkeypatch):
    process = Running()
    process.pid = 1234
    def kill_tree(command, **kwargs):
        assert command == ['taskkill.exe', '/PID', '1234', '/T', '/F']
        process.stopped = process.killed = True
    monkeypatch.setattr('tools.qualify_intervals.subprocess.run', kill_tree)
    monkeypatch.setattr('tools.qualify_intervals.subprocess.Popen', lambda *a, **k: process)
    def exhausted():
        raise ResourceLimit('deadline')
    budget = SimpleNamespace(check=exhausted, root=tmp_path)
    with pytest.raises(ResourceLimit):
        run_worker({'directory': str(tmp_path / 'cell')}, None, budget, 100)
    assert process.stopped and process.killed
    assert not (tmp_path / 'cell/result.json').exists()


def test_disk_exhaustion_stops_worker(tmp_path, monkeypatch):
    process = Running()
    process.pid = 1234
    def kill_tree(command, **kwargs):
        assert command == ['taskkill.exe', '/PID', '1234', '/T', '/F']
        process.stopped = process.killed = True
    monkeypatch.setattr('tools.qualify_intervals.subprocess.run', kill_tree)
    monkeypatch.setattr('tools.qualify_intervals.subprocess.Popen', lambda *a, **k: process)
    budget = SimpleNamespace(check=lambda: None, root=tmp_path, deadline=time.monotonic() + 10)
    with pytest.raises(ResourceLimit, match='disk budget'):
        run_worker({'directory': str(tmp_path / 'cell')}, None, budget, 1)
    assert process.stopped and process.killed


def test_cleanup_failure_prevents_completion(tmp_path, monkeypatch):
    monkeypatch.setattr('tools.qualify_intervals.preflight', lambda *a: {})
    monkeypatch.setattr('tools.qualify_intervals.provenance', lambda: {})
    monkeypatch.setattr('tools.qualify_intervals.matrix', lambda *a: [])
    class FailedCleanup:
        def __init__(self, *args):
            pass
        def __enter__(self):
            return self
        def provenance(self):
            return {}
        def __exit__(self, *args):
            raise RuntimeError('cleanup failed')
    monkeypatch.setattr('tools.qualify_intervals.IntervalService', FailedCleanup)
    output = tmp_path / 'new'
    assert main(['--output', str(output), '--reserve-bytes', '0']) == 1
    report = json.loads((output / 'report.json').read_text())
    assert report['status'] == 'failed' and not report['cleanup']
    assert not report['full_metadata_qualified']


def test_protected_destination_cannot_be_claimed():
    from tools.qualify_intervals import ROOT
    path = ROOT / 'src' / 'interval-test-must-not-be-created'
    assert not path.exists()
    with pytest.raises(ValueError, match='protected'):
        main(['--output', str(path)])
    assert not path.exists()


def test_payload_access_is_rejected():
    from tools.qualification.interval_worker import no_payload
    with pytest.raises(AssertionError, match='payload I/O'):
        no_payload()


def test_provenance_checks_imported_root(monkeypatch):
    import meldstore
    from tools.qualification.interval_evidence import provenance
    monkeypatch.setattr(meldstore, '__file__', str(Path('/unrelated') / 'meldstore/__init__.py'))
    with pytest.raises(ValueError, match='Imported MeldStore'):
        provenance()



def test_worker_scratch_cleanup_preserves_logs_and_outside_paths(tmp_path):
    from tools.qualify_intervals import cleanup_worker_scratch
    directory = tmp_path / 'cell'
    directory.mkdir()
    scratch = directory / 'interval-generated'
    scratch.mkdir()
    (scratch / 'catalog.sqlite').write_bytes(b'disposable')
    (directory / 'worker.log').write_text('retained')
    cleanup_worker_scratch(directory, tmp_path)
    assert not scratch.exists() and (directory / 'worker.log').read_text() == 'retained'
    with pytest.raises(ValueError, match='escaped'):
        cleanup_worker_scratch(tmp_path, directory)


def test_worker_scratch_refuses_linked_catalogs(tmp_path, monkeypatch):
    from tools.qualify_intervals import cleanup_worker_scratch
    directory = tmp_path / 'cell'
    scratch = directory / 'interval-generated'
    scratch.mkdir(parents=True)
    monkeypatch.setattr(Path, 'is_junction', lambda self: self == scratch)
    with pytest.raises(ValueError, match='Linked'):
        cleanup_worker_scratch(directory, tmp_path)
    assert scratch.exists()


def test_terminated_worker_leaves_no_running_child(tmp_path):
    import os
    import sys

    from tools.qualify_intervals import terminate_worker
    beat = tmp_path / 'beat.txt'
    script = """import pathlib, sys, time
p = pathlib.Path(sys.argv[1])
while True:
    p.write_text(str(time.time()))
    time.sleep(.02)
"""
    process = subprocess.Popen([sys.executable, '-c', script, str(beat)],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
    try:
        until = time.monotonic() + 10
        while not beat.exists() and time.monotonic() < until:
            time.sleep(.02)
        assert beat.exists()
        terminate_worker(process)
        assert process.poll() is not None
        content = beat.read_bytes()
        time.sleep(.15)
        assert beat.read_bytes() == content
    finally:
        if process.poll() is None:
            terminate_worker(process)


def test_service_waits_for_tcp_after_temporary_socket_server(tmp_path, monkeypatch):
    import sys

    from tools.qualification.interval_service import MEMORY
    from tools.qualification.services import OWNER_LABEL

    root, token = fresh_root(tmp_path / 'owned')
    service = IntervalService(root, token, time.monotonic() + 30)
    details = {'Config': {'Labels': {OWNER_LABEL: token}},
               'NetworkSettings': {'Ports': {'5432/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '54321'}]}},
               'HostConfig': {'Memory': MEMORY, 'MemorySwap': MEMORY}, 'Image': 'qualified-image'}
    resources = iter([None, None, {'Labels': {OWNER_LABEL: token}}, details])
    monkeypatch.setattr('tools.qualification.interval_service.inspect_resource', lambda *a, **k: next(resources))
    monkeypatch.setattr('tools.qualification.interval_service.docker', lambda *a, **k: '')
    monkeypatch.setitem(sys.modules, 'psycopg', SimpleNamespace(
        conninfo=SimpleNamespace(make_conninfo=lambda **kwargs: 'synthetic-dsn')))
    state = {'tcp_ready': False, 'probes': 0}
    def command(*args, **kwargs):
        if args[0] == 'run':
            return SimpleNamespace(returncode=0)
        assert 'pg_isready' in args
        state['probes'] += 1
        # The temporary initializer answers socket probes before TCP is available.
        ready = '-h' not in args or state['tcp_ready']
        if '-h' in args:
            assert args[args.index('-h') + 1] == '127.0.0.1'
        return SimpleNamespace(returncode=0 if ready else 1)
    monkeypatch.setattr('tools.qualification.interval_service.docker_run', command)
    monkeypatch.setattr('tools.qualification.interval_service.time.sleep',
                        lambda _: state.update(tcp_ready=True))
    monkeypatch.setattr(service, 'close', lambda: None)
    def reserve(*args):
        assert state['tcp_ready'], 'Service returned while only its temporary socket was ready'
    monkeypatch.setattr(service, 'check_reserve', reserve)
    with service:
        assert state['tcp_ready'] and state['probes'] == 2
