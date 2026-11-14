"""One isolated process per adapter, with durable partial evidence."""

import threading
import time
from contextlib import nullcontext
from pathlib import Path

from tools.qualification.concurrency import concurrent
from tools.qualification.evidence import write_report
from tools.qualification.measurements import Measurements
from tools.qualification.operations import operational, uncertain_publication
from tools.qualification.resources import Budget
from tools.qualification.workload import (
    finish_migration,
    ingest,
    metadata_workload,
    migrations,
    opened,
    rollback_publication,
    verify_payloads,
)
from tools.qualify_local import peak_rss


def footprint(root):
    total = 0
    for path in Path(root).rglob('*'):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except FileNotFoundError:
            pass  # A staging file was removed between directory scan and stat.
    return total


def variant(adapter, root, manifest, services, limits):
    work = root / adapter
    work.mkdir(exist_ok=False)
    report = {'adapter': adapter, 'status': 'incomplete', 'operations': [],
              'sampling_interval_seconds': .25, 'sampled_disk_bytes': 0,
              'resource_scope': 'client RSS; variant staging/catalog/archive disk; corpus excluded'}
    metrics = Measurements(lambda: write_report(work / 'report.json', report))
    report['operations'] = metrics.operations
    write_report(work / 'report.json', report)
    stop = threading.Event()
    budget = Budget(root, **limits, service_check=services.check_reserve)
    samples, failures = [], []

    def sample():
        while not stop.is_set():
            try:
                samples.append(footprint(work))
                budget.check()
            except BaseException as exc:
                failures.append(type(exc).__name__)
                # Main workload checks Budget at every boundary; supervisor enforces hard timeout.
                stop.set()
            stop.wait(.25)

    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    try:
        with services.database() if adapter == 'postgres' else nullcontext(None) as dsn:
            config = {'adapter': adapter, 'dsn': dsn, 'catalog': work / 'catalog.sqlite',
                      'coordinator': work / 'coordinator', 'options': services.options()}
            with opened(config, initialize=True) as store, metrics.storage(), metrics.catalog(store.catalog):
                ingest(store, manifest, root / 'corpus', metrics, budget)
                metadata_workload(store, manifest, metrics, budget)
                rollback_publication(store, manifest, root / 'corpus', metrics)
                with metrics.operation('export_verify'):
                    verify_payloads(store, manifest, work / 'exports', budget)
            concurrent(config, manifest, metrics, budget)
            uncertain_publication(config, manifest, root / 'corpus', metrics)
            with opened(config, maintenance=True) as store, metrics.storage(), metrics.catalog(store.catalog):
                plan, before = migrations(store, manifest, metrics, budget)
            with opened(config, maintenance=True) as store, metrics.storage(), metrics.catalog(store.catalog):
                finish_migration(store, manifest, metrics, plan, before)
            operational(config, manifest, work, services, metrics, budget)
        if failures:
            raise RuntimeError('Resource sampler failed: ' + failures[0])
        budget.check()
        report['status'] = 'passed'
    except BaseException as exc:
        report['status'] = 'failed'
        report['error_type'] = type(exc).__name__
        raise
    finally:
        stop.set()
        monitor.join(timeout=10)
        report['peak_rss_bytes'] = peak_rss()
        report['sampled_service_volume_bytes'] = services.max_volume_bytes
        report['service_disk_unavailable_samples'] = services.volume_samples_unavailable
        report['service_disk_unavailable_reason'] = (
            'Files disappeared during live volume traversal' if services.volume_samples_unavailable else None)
        report['call_count_scope'] = (
            'Wrapped public APIs only; missing counts are unmeasured, not zero or wire round trips')
        report['service_resource_snapshot'] = services.resource_snapshot()
        report['sampled_disk_bytes'] = max([footprint(work), *samples])
        report['elapsed_seconds'] = time.monotonic() - budget.started
        report['pid_recorded'] = False  # Machine/process identifiers are not publishable evidence.
        write_report(work / 'report.json', report)
        # sys.exit remains the supervisor's responsibility; no process-global exit here.
