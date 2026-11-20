"""Controlled metadata-only interval experiments. Run with uv; full scale is opt-in."""

import argparse
import copy
import json
import math
import os
import shutil
import subprocess
import sys
import time
from contextlib import nullcontext
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.qualification.evidence import write_report
from tools.qualification.execution import ExecutionGuard
from tools.qualification.interval_checkpoint import (
    checkpoint,
    load_checkpoint,
    utc_now,
    validate_sessions,
)
from tools.qualification.interval_evidence import (
    ROOT,
    complete,
    provenance,
    require,
    summary,
    validate,
)
from tools.qualification.interval_fixture import ADAPTERS, PROFILES, Profile, matrix, profile_from
from tools.qualification.interval_service import MEMORY, IntervalService
from tools.qualification.resources import Budget, ResourceLimit, fresh_root


def available_memory():
    if os.name == 'nt':
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                        *[(name, ctypes.c_ulonglong) for name in
                          ('total', 'available', 'page_total', 'page_available',
                           'virtual_total', 'virtual_available', 'extended')]]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise ResourceLimit('Cannot establish available physical memory')
        return status.available
    meminfo = Path('/proc/meminfo')
    if meminfo.exists():
        for line in meminfo.read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) * 1024
    raise ResourceLimit('Available-memory preflight is unsupported on this platform')


def preflight(root, memory_bytes, disk_bytes, reserve_bytes, postgres):
    available = available_memory()
    required_memory = memory_bytes + (MEMORY if postgres else 0)
    if available < required_memory:
        raise ResourceLimit('Insufficient available memory for declared client and service budgets')
    free = shutil.disk_usage(root).free
    if free < disk_bytes + reserve_bytes:
        raise ResourceLimit('Insufficient host disk for declared experiment budget and reserve')
    return {'available_memory_bytes': available, 'required_memory_bytes': required_memory,
            'free_disk_bytes': free, 'disk_budget_bytes': disk_bytes, 'reserve_bytes': reserve_bytes}


def directory_bytes(root):
    total = 0
    for path in Path(root).rglob('*'):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            pass  # The worker may be removing its own temporary catalog.
    return total


class WorkerCleanupError(RuntimeError):
    """Worker liveness or scratch cleanup could not be established."""


def terminate_worker(proc):
    if os.name == 'nt':
        # The Windows venv launcher can have a Python child. Stop its whole tree.
        subprocess.run(['taskkill.exe', '/PID', str(proc.pid), '/T', '/F'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        proc.wait(timeout=5)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def cleanup_worker_scratch(directory, root):
    """Only remove this stopped worker's generated temporary catalogs, never logs."""
    directory, root = Path(directory), Path(root).resolve()
    if directory.resolve().parent != root or directory.resolve() != directory.absolute():
        raise ValueError('Worker directory escaped owned root')
    targets = list(directory.glob('interval-*'))
    for target in targets:
        if target.resolve().parent != directory.resolve() or not target.is_dir():
            raise ValueError('Unexpected worker scratch path')
        for item in [target, *target.rglob('*')]:
            if item.is_symlink() or item.is_junction() or not item.resolve().is_relative_to(directory):
                raise ValueError('Linked worker scratch is not eligible for cleanup')
    for target in targets:
        shutil.rmtree(target)


def run_worker(config, dsn, budget, disk_bytes, service=None):
    directory = Path(config['directory'])
    directory.mkdir(exist_ok=False)
    # Internal control file is excluded from shareable evidence. No credentials.
    path = directory / 'control.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k != 'MELDSTORE_INTERVAL_DSN'}
    if dsn is not None:
        env['MELDSTORE_INTERVAL_DSN'] = dsn
    last_service, service_bytes = 0.0, 0
    with (directory / 'worker.log').open('w', encoding='utf-8') as log:
        proc = subprocess.Popen([sys.executable, '-m', 'tools.qualification.interval_worker', str(path)],
                                cwd=ROOT, env=env, stdout=log, stderr=log,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        try:
            while proc.poll() is None:
                budget.check()
                used = directory_bytes(budget.root)
                if service and time.monotonic() - last_service >= 10:
                    service.check_reserve(budget.reserve_bytes, budget.deadline)
                    service_bytes = service.volume_size(budget.deadline)
                    last_service = time.monotonic()
                if used + service_bytes > disk_bytes:
                    raise ResourceLimit('Experiment disk budget exceeded')
                time.sleep(min(.5, max(.001, budget.deadline - time.monotonic())))
            if proc.returncode:
                raise RuntimeError('Interval worker failed; see its sanitized result and log')
        finally:
            try:
                if proc.poll() is None:
                    terminate_worker(proc)
                cleanup_worker_scratch(directory, budget.root)
            except BaseException:
                raise WorkerCleanupError('Worker teardown could not be verified') from None
    budget.check()
    used = directory_bytes(budget.root) + (service.volume_size(budget.deadline) if service else 0)
    if used > disk_bytes:
        raise ResourceLimit('Experiment disk budget exceeded')
    return json.loads((directory / 'result.json').read_text(encoding='utf-8'))


def options(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('smoke', 'full', 'custom'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume-from', type=Path, help='Stopped, cleaned v2 report; output must be fresh')
    parser.add_argument('--pause-after', type=int, help='Pause after N new completed treatments')
    parser.add_argument('--seed', type=int)
    parser.add_argument('--files', type=int)
    parser.add_argument('--relationships', type=int)
    parser.add_argument('--adapters', nargs='+', choices=ADAPTERS)
    parser.add_argument('--seconds', type=float, default=14400, help='Wall budget for this session')
    parser.add_argument('--memory-bytes', type=int)
    parser.add_argument('--disk-bytes', type=int)
    parser.add_argument('--reserve-bytes', type=int)
    parser.add_argument('--keep-awake', action='store_true')
    args = parser.parse_args(argv)
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error('Time budget must be finite and positive')
    if args.pause_after is not None and args.pause_after < 1:
        parser.error('--pause-after must be positive')
    source = load_checkpoint(args.resume_from, provenance()) if args.resume_from else None
    if source:
        p = profile_from(source['profile'])
        for key, expected in (('profile', p.name), ('files', p.files), ('relationships', p.relationships),
                              ('adapters', list(p.adapters)), ('seed', source['seed'])):
            if getattr(args, key) is not None and getattr(args, key) != expected:
                parser.error('Resume cannot change ' + key)
        args.seed = source['seed']
    else:
        args.profile = args.profile or 'smoke'
        args.seed = 20261116 if args.seed is None else args.seed
        if args.profile != 'custom':
            if any(v is not None for v in (args.files, args.relationships, args.adapters)):
                parser.error('Overrides require --profile custom')
            p = PROFILES[args.profile]
        else:
            if args.files is None or args.relationships is None:
                parser.error('custom requires --files and --relationships')
            p = Profile('custom', args.files, args.relationships, 1, 1, 3,
                        tuple(args.adapters) if args.adapters else ADAPTERS)
    defaults = dict(memory_bytes=4_000_000_000, disk_bytes=4_000_000_000, reserve_bytes=2_000_000_000)
    for key, default in defaults.items():
        expected = source['limits'][key] if source else default
        value = getattr(args, key)
        if source and value is not None and value != expected:
            parser.error('Resume cannot change ' + key)
        setattr(args, key, expected if value is None else value)
    if args.memory_bytes <= 0 or args.disk_bytes <= 0 or args.reserve_bytes < 0:
        parser.error('Resource budgets must be positive')
    return args, p, source


def new_report(args, p, source, token):
    report = copy.deepcopy(source) if source else {
        'schema_version': 2, 'scope': 'metadata-only', 'profile': p.document(),
        'seed': args.seed, 'representative_qualified': False,
        'cells': [], 'schedule': matrix(p, args.seed), 'checkpoints': [], 'sessions': [],
        'limits': {k: getattr(args, k) for k in ('memory_bytes', 'disk_bytes', 'reserve_bytes')},
        'continuity_scope': 'within-sessions'}
    report.update(status='incomplete', full_metadata_qualified=False, cleanup=False,
                  continuity=False, resume_eligible=False)
    for key in ('error_type', 'stop_reason', 'execution', 'parent_peak_rss_bytes'):
        report.pop(key, None)
    now = utc_now()
    report['sessions'].append({'id': token, 'start_cell': len(report['cells']),
        'end_cell': len(report['cells']), 'started_at': now, 'checkpoint_at': now, 'ended_at': None,
        'cleanup': False, 'continuity': False, 'limits': report['limits'], 'seconds': args.seconds})
    return report


def finish_session(report, guard):
    session = report['sessions'][-1]
    report['provenance_after'] = provenance()
    execution = guard.evidence() if guard is not None else {}
    report['execution'] = session['execution'] = execution
    session['ended_at'] = utc_now()
    session['cleanup'] = report['cleanup']
    continuous = execution.get('continuity_lost') is False
    report['continuity'] = session['continuity'] = continuous
    if (report['cleanup'] and continuous and report['provenance_after'] == report.get('provenance')
            and 0 < len(report['cells']) < len(report['schedule'])):
        validate(report, partial=True)
        validate_sessions(report, closed=True)
        report['resume_eligible'] = True


def main(argv=None):
    args, p, source = options(argv)
    protected = [ROOT / name for name in
                 ('src', 'tests', 'tools', 'docs', 'openspec', '.git', '.venv', '.agents')]
    if args.resume_from:
        protected.append(args.resume_from.resolve().parent)
    root, token = fresh_root(args.output, protected=protected)
    report = new_report(args, p, source, token)
    write_report(root / 'report.json', report)
    guard, failure = None, None
    try:
        report['preflight'] = preflight(root, args.memory_bytes, args.disk_bytes,
                                        args.reserve_bytes, 'postgres' in p.adapters)
        current = provenance()
        if source:
            require(current == report['provenance'], 'Resume provenance changed after validation')
        report['provenance'] = current
        report['sessions'][-1]['provenance'] = current
        with ExecutionGuard(keep_awake=args.keep_awake) as guard:
            budget = Budget(root, memory_bytes=args.memory_bytes, seconds=args.seconds,
                            reserve_bytes=args.reserve_bytes, invariant=guard.check)
            service_context = (IntervalService(root, token, budget.deadline)
                               if 'postgres' in p.adapters else nullcontext(None))
            with service_context as service:
                # Catch inside the context so cleanup still runs normally on interruption.
                try:
                    service_prov = service.provenance() if service else None
                    if source:
                        require(service_prov == report.get('service'), 'Resume service differs')
                    report['service'] = service_prov
                    report['sessions'][-1]['service'] = service_prov
                    first = len(report['cells'])
                    for ordinal in range(first, len(report['schedule'])):
                        if ((root / 'PAUSE').exists() or
                                (args.pause_after is not None and ordinal - first >= args.pause_after)):
                            report['stop_reason'] = 'pause_requested'
                            break
                        budget.check()
                        if service:
                            service.check_reserve(args.reserve_bytes, budget.deadline)
                        cell = report['schedule'][ordinal]
                        print(f"Cell {ordinal + 1}/{len(report['schedule'])}: "
                              f"{cell['adapter']} {cell['layout']} {cell['treatment']} round {cell['round']}",
                              flush=True)
                        database = service.database() if cell['adapter'] == 'postgres' else nullcontext(None)
                        with database as dsn:
                            config = {'directory': str(root / f'cell-{ordinal:03d}'),
                                      'profile': p.document(), 'cell': cell, 'order_seed': args.seed + ordinal,
                                      'provenance': current,
                                      'expected_settings': next((c['settings'] for c in report['cells']
                                          if c['adapter'] == cell['adapter']), None),
                                      'memory_bytes': args.memory_bytes,
                                      'reserve_bytes': args.reserve_bytes,
                                      'seconds': max(.001, budget.deadline - time.monotonic())}
                            result = run_worker(config, dsn, budget, args.disk_bytes, service)
                        budget.check()
                        report = checkpoint(report, result, guard, provenance())
                        write_report(root / 'report.json', report)
                        print('Cell passed and checkpointed', flush=True)
                    budget.check()
                except BaseException as exc:
                    failure = exc
                report['parent_peak_rss_bytes'] = budget.max_rss
                report['sessions'][-1]['parent_peak_rss_bytes'] = budget.max_rss
            # A failed scratch deletion must also prevent completion or resume.
            require(not isinstance(failure, WorkerCleanupError)
                    and not any(root.glob('cell-*/interval-*')), 'Worker scratch cleanup incomplete')
            report['cleanup'] = True
        finish_session(report, guard)
        if failure is not None:
            raise failure
        if len(report['cells']) == len(report['schedule']):
            complete(report)
            report['resume_eligible'] = False
            (root / 'summary.md').write_text(summary(report), encoding='utf-8')
        else:
            report['status'] = 'paused'
        write_report(root / 'report.json', report)
    except BaseException as exc:
        report['status'], report['full_metadata_qualified'] = 'failed', False
        report['resume_eligible'] = False
        report['error_type'] = type(exc).__name__
        try:
            finish_session(report, guard)
        except BaseException:
            report['resume_eligible'] = False
        if isinstance(exc, KeyboardInterrupt):
            report['status'], report['stop_reason'] = 'paused', 'keyboard_interrupt'
        write_report(root / 'report.json', report)
        print(f"Interval experiment {report['status']}: {type(exc).__name__}; "
              f"resume eligible: {report['resume_eligible']}", flush=True)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
    if report['status'] == 'paused':
        print(f"Paused after {len(report['cells'])}/{len(report['schedule'])} treatments; "
              f"resume eligible: {report['resume_eligible']}", flush=True)
    else:
        print('Metadata experiment passed; payload qualification unchanged.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
