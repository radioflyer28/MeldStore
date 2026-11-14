import json

import pytest

from tools.qualification.corpus import PROFILES
from tools.qualification.resources import (
    Budget,
    ResourceLimit,
    capacity,
    fresh_root,
    owned_root,
    record_resource,
)


def test_capacity_checks_host_and_docker_separately():
    p = PROFILES['smoke']
    assert capacity(p, 10**10, 10**10, 100)['corpus_upper_bytes'] == 16_000_000
    for host, docker in [(0, 10**10), (10**10, 0), (100_000_000, 100_000_000)]:
        with pytest.raises(ResourceLimit):
            capacity(p, host, docker, 100)


def test_output_ownership_and_no_overwrite(tmp_path):
    path, token = fresh_root(tmp_path / 'run')
    record_resource(path, token, 'container', 'example')
    assert owned_root(path, token) == path
    assert json.loads((path / 'ownership.json').read_text())['resources'][0]['name'] == 'example'
    with pytest.raises(ValueError):
        fresh_root(path)
    with pytest.raises(ValueError):
        owned_root(path, 'wrong')


def test_overlap_rejected_before_creation(tmp_path):
    with pytest.raises(ValueError, match='protected'):
        fresh_root(tmp_path / 'private/sub', [tmp_path / 'private'])
    assert not (tmp_path / 'private').exists()


def test_resource_limits(tmp_path, monkeypatch):
    import tools.qualification.resources as resources
    monkeypatch.setattr(resources, 'peak_rss', lambda: 1000)
    with pytest.raises(ResourceLimit, match='memory'):
        Budget(tmp_path, memory_bytes=999, reserve_bytes=0).check()
    budget = Budget(tmp_path, memory_bytes=2000, reserve_bytes=0)
    budget.started -= 20_000
    with pytest.raises(ResourceLimit, match='Runtime'):
        budget.check()
    with pytest.raises(ResourceLimit, match='reserve'):
        Budget(tmp_path, memory_bytes=2000, reserve_bytes=10**20).check()

def test_interrupted_service_startup_cleans_only_owned_names(tmp_path, monkeypatch):
    pytest.importorskip("psycopg")
    from types import SimpleNamespace

    import tools.qualification.services as services

    root, token = fresh_root(tmp_path / 'run')
    calls = []

    volumes = set()

    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ['docker', 'run']:
            raise RuntimeError('startup failed')
        if args[1:3] == ['volume', 'create']:
            volumes.add(args[-1])
        elif args[1:3] == ['volume', 'rm']:
            volumes.remove(args[-1])
        elif args[1:3] == ['volume', 'inspect'] and args[-1] in volumes:
            return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(
                [{'Labels': {services.OWNER_LABEL: token}}]))
        else:
            return SimpleNamespace(returncode=1, stderr='No such resource', stdout='')
        return SimpleNamespace(returncode=0, stderr='', stdout='')

    monkeypatch.setattr(services.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='startup failed'):
        services.Services(root, token).__enter__()
    assert not volumes
    assert sum(call[1:3] == ['volume', 'rm'] for call in calls) == 3
    assert all(token in call[-1] for call in calls if 'inspect' in call)
    assert not any('prune' in call or 'unrelated' in call for call in calls)


def test_cleanup_refuses_foreign_container_label(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import tools.qualification.services as services

    root, token = fresh_root(tmp_path / 'run')
    record_resource(root, token, 'container', 'meldstore-workload-' + token)
    monkeypatch.setattr(services.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout=json.dumps([{'Config': {'Labels': {'meldstore.qualifier': 'foreign'}}}]),
        stderr=''))
    with pytest.raises(ValueError, match='label'):
        services.cleanup(root, token)


@pytest.mark.parametrize('exit_code,stderr,expected', [
    (1, "du: cannot access '/data/tmp/gone': No such file or directory", None),
    (1, "du: cannot read directory '/data': Permission denied", 'error'),
])
def test_volume_sampler_distinguishes_live_churn_from_failure(tmp_path, monkeypatch,
                                                             exit_code, stderr, expected):
    from types import SimpleNamespace

    import tools.qualification.services as services

    root, token = fresh_root(tmp_path / 'run')
    lab = services.Services(root, token)

    def run(args, **kwargs):
        if args[-1] != '/data':
            return SimpleNamespace(returncode=0, stdout='100 /var/lib/postgresql/data', stderr='')
        return SimpleNamespace(returncode=exit_code, stdout='50 /data', stderr=stderr)

    monkeypatch.setattr(services.subprocess, 'run', run)
    if expected == 'error':
        with pytest.raises(RuntimeError, match='sampling failed'):
            lab.volume_bytes()
    else:
        assert lab.volume_bytes() is None
        assert lab.volume_samples_unavailable == 1

def test_repeated_memory_sampling_is_bounded():
    import os
    import sys

    if sys.platform != 'win32':
        pytest.skip('Windows ctypes regression')
    import gc

    from tools.qualification.concurrency import current_rss
    from tools.qualify_local import peak_rss

    # Warm up before measuring long-lived type/handle accumulation.
    peak_rss()
    current_rss(os.getpid())
    gc.collect()
    initial = current_rss(os.getpid())
    for _ in range(10_000):
        assert peak_rss() > 0
        assert current_rss(os.getpid()) > 0
    gc.collect()
    assert current_rss(os.getpid()) - initial < 8_000_000


@pytest.mark.parametrize('failures', [1, 10])
def test_ownership_update_handles_bounded_windows_sharing_errors(tmp_path, monkeypatch, failures):
    import os

    if os.name != 'nt':
        pytest.skip('Windows file replacement semantics')
    import tools.qualification.evidence as evidence

    root, token = fresh_root(tmp_path / 'run')
    replace = os.replace
    calls = []

    def sharing_error(source, target):
        calls.append((source, target))
        if len(calls) <= failures:
            error = PermissionError('temporary sharing denial')
            error.winerror = 5
            raise error
        replace(source, target)

    monkeypatch.setattr(evidence.os, 'replace', sharing_error)
    if failures == 10:
        with pytest.raises(PermissionError):
            record_resource(root, token, 'container', 'example')
        assert len(calls) == 10
        assert json.loads((root / 'ownership.json').read_text())['resources'] == []
    else:
        record_resource(root, token, 'container', 'example')
        assert len(calls) == 2
        assert json.loads((root / 'ownership.json').read_text())['resources'] == [
            {'kind': 'container', 'name': 'example'}]



@pytest.mark.parametrize('seconds', [0, -1, float('inf'), float('nan')])
def test_service_request_timeout_requires_finite_positive_budget(tmp_path, seconds):
    from tools.qualification.services import Services

    root, token = fresh_root(tmp_path / 'run')
    with pytest.raises(ValueError, match='finite and positive'):
        Services(root, token, request_timeout_seconds=seconds)


def test_service_request_timeout_is_recorded_without_enabling_retries(tmp_path):
    from tools.qualification.services import Services

    root, token = fresh_root(tmp_path / 'run')
    lab = Services(root, token, request_timeout_seconds=300)
    lab.server_version, lab.native_tools = 'test', {}
    assert lab.options()['client_options'] == {'timeout': '300s'}
    assert lab.options()['retry_config'] == {'max_retries': 0}
    assert lab.provenance()['client_policy'] == {'request_timeout_seconds': 300, 'max_retries': 0}


@pytest.mark.parametrize('seconds', ['0', '-1', 'inf', 'nan'])
def test_invalid_cli_request_timeout_does_not_create_output(tmp_path, seconds):
    from tools.qualify_representative import main

    target = tmp_path / 'invalid'
    with pytest.raises(SystemExit):
        main(['--output', str(target), '--request-timeout-seconds', seconds])
    assert not target.exists()


def test_cleanup_uses_manifest_and_refuses_foreign_volume(tmp_path, monkeypatch):
    import tools.qualification.services as services

    root, token = fresh_root(tmp_path / 'run')
    calls = []

    def inspect(kind, name):
        calls.append((kind, name))
        return {'Labels': {services.OWNER_LABEL: 'foreign'}}

    def forbidden(*args, **kwargs):
        pytest.fail('Cleanup tried to remove an unowned resource')

    monkeypatch.setattr(services, 'inspect_resource', inspect)
    monkeypatch.setattr(services, 'docker', forbidden)
    services.cleanup(root, token)
    assert not calls
    volume = 'meldstore-workload-' + token + '-data'
    record_resource(root, token, 'volume', volume)
    with pytest.raises(ValueError, match='volume ownership label'):
        services.cleanup(root, token)
    assert calls == [('volume', volume)]


def test_probe_timeout_obeys_remaining_budget(tmp_path, monkeypatch):
    import subprocess

    import tools.qualification.services as services

    root, token = fresh_root(tmp_path / 'run')
    lab = services.Services(root, token)
    budget = Budget(root, seconds=0.5, service_check=lab.check_reserve)

    def stall(args, *, timeout, **kwargs):
        assert args[3] == 'du'
        assert 0 < timeout <= 0.5
        raise subprocess.TimeoutExpired(args, timeout)

    monkeypatch.setattr(services.subprocess, 'run', stall)
    with pytest.raises(ResourceLimit, match='command deadline'):
        budget.check()
