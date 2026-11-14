"""Required by the live harness invocation; opt-in during ordinary unit runs."""

import os
from contextlib import nullcontext

import pytest

from tools.qualification.concurrency import concurrent
from tools.qualification.corpus import PROFILES, generate
from tools.qualification.measurements import Measurements
from tools.qualification.operations import operational, uncertain_publication
from tools.qualification.resources import Budget, fresh_root
from tools.qualification.workload import (
    finish_migration,
    ingest,
    metadata_workload,
    migrations,
    opened,
    rollback_publication,
    verify_payloads,
)


@pytest.fixture(scope='module')
def live(tmp_path_factory):
    if os.environ.get('MELDSTORE_WORKLOAD_LIVE') != '1':
        pytest.skip('Requires explicit disposable workload lab')
    from tools.qualification.services import Services
    root, token = fresh_root(tmp_path_factory.mktemp('workload') / 'run')
    manifest = generate(root / 'corpus', PROFILES['smoke'])
    with Services(root, token) as services:
        yield root, services, manifest


@pytest.mark.parametrize('adapter', ['sqlite', 'melddb', 'postgres'])
def test_live_workload_phases(live, adapter):
    root, services, manifest = live
    work = root / adapter
    work.mkdir()
    metrics, budget = Measurements(), Budget(work)
    with services.database() if adapter == 'postgres' else nullcontext(None) as dsn:
        config = {'adapter': adapter, 'dsn': dsn, 'catalog': work / 'catalog.sqlite',
                  'coordinator': work / 'coordinator', 'options': services.options()}
        with opened(config, initialize=True) as store, metrics.storage(), metrics.catalog(store.catalog):
            ingest(store, manifest, root / 'corpus', metrics, budget)
            metadata_workload(store, manifest, metrics, budget)
            rollback_publication(store, manifest, root / 'corpus', metrics)
            verify_payloads(store, manifest, work / 'exports', budget)
        concurrent(config, manifest, metrics, budget)
        uncertain_publication(config, manifest, root / "corpus", metrics)
        with opened(config, maintenance=True) as store, metrics.storage(), metrics.catalog(store.catalog):
            plan, before = migrations(store, manifest, metrics, budget)
        with opened(config, maintenance=True) as store, metrics.storage(), metrics.catalog(store.catalog):
            finish_migration(store, manifest, metrics, plan, before)

        operational(config, manifest, work, services, metrics, budget)





@pytest.mark.parametrize('adapter', ['sqlite', 'melddb'])
def test_live_metadata_scale_cutover_exceeds_default_row_limit(live, adapter):
    """Exercise the full SQL row count with small payloads before expensive full runs."""
    import copy

    from tools.qualification.fixture import transfer_limits

    root, services, original = live
    manifest = copy.deepcopy(original)
    manifest['profile']['name'] = 'custom'
    manifest['profile']['relationships'] = 300_000
    work = root / ('metadata-scale-' + adapter)
    work.mkdir()
    metrics, budget = Measurements(), Budget(work)
    config = {'adapter': adapter, 'dsn': None, 'catalog': work / 'catalog.sqlite',
              'coordinator': work / 'coordinator', 'options': services.options()}
    with opened(config, initialize=True) as store:
        ingest(store, manifest, root / 'corpus', metrics, budget)
    with opened(config, maintenance=True) as store:
        plan, before = migrations(store, manifest, metrics, budget)
    with opened(config, maintenance=True) as store:
        finish_migration(store, manifest, metrics, plan, before)
    operational(config, manifest, work, services, metrics, budget)
    limits = transfer_limits(manifest)
    assert limits == {'max_entries': 4, 'max_rows': 301_040}
    phases = {op['name']: op for op in metrics.operations}
    for name in ('cutover_preflight', 'cutover'):
        assert phases[name]['status'] == 'passed'
        assert phases[name]['max_rows'] == limits['max_rows']
