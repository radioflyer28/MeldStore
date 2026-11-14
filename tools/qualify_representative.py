"""Explicit synthetic qualification. Smoke is the default, never a capacity claim."""

import argparse
import json
import math
import multiprocessing
import shutil
import sys
import time
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.qualification.corpus import PROFILES, Profile, generate
from tools.qualification.resources import Budget, capacity, fresh_root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=(*PROFILES, 'custom'), default='smoke')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=20261112)
    parser.add_argument('--files', type=int)
    parser.add_argument('--min-bytes', type=int)
    parser.add_argument('--max-bytes', type=int)
    parser.add_argument('--relationships', type=int)
    parser.add_argument('--generate-only', action='store_true')
    parser.add_argument('--keep-awake', action='store_true', help='Windows: hold idle sleep only for this run')
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--cleanup', action='store_true', help='Remove only owned Docker resources')
    parser.add_argument('--memory-bytes', type=int, default=4_000_000_000)
    parser.add_argument('--seconds', type=float, default=14_400)
    parser.add_argument('--request-timeout-seconds', type=float, default=60)
    parser.add_argument('--reserve-bytes', type=int, default=2_000_000_000)
    args = parser.parse_args(argv)
    if not math.isfinite(args.request_timeout_seconds) or args.request_timeout_seconds <= 0:
        parser.error('--request-timeout-seconds must be finite and positive')
    custom = (args.files, args.min_bytes, args.max_bytes, args.relationships)
    if args.profile == 'custom':
        if any(x is None for x in custom):
            parser.error('custom requires --files, --min-bytes, --max-bytes and --relationships')
        profile = Profile('custom', *custom, (1, 2))
    else:
        if any(x is not None for x in custom):
            parser.error('profile overrides require --profile custom')
        profile = PROFILES[args.profile]
    limits = {'memory_bytes': args.memory_bytes, 'seconds': args.seconds,
              'reserve_bytes': args.reserve_bytes}
    if sum((args.generate_only, args.preflight_only, args.cleanup)) > 1:
        parser.error('Select at most one of generate-only, preflight-only or cleanup')
    if args.cleanup:
        from tools.qualification.services import cleanup
        root = args.output.resolve()
        record = json.loads((root / 'ownership.json').read_text(encoding='utf-8'))
        cleanup(root, record['run_id'])
        print('Removed only owned Docker resources; local evidence retained.')
        return 0
    # Validate budgets before claiming a fresh directory.
    Budget(args.output.parent, **limits)
    repository = Path(__file__).resolve().parents[1]
    root, token = fresh_root(args.output, protected=[repository / 'src', repository / '.git',
                                                    repository / 'tests', repository / 'openspec'])
    budget = Budget(root, **limits)
    if args.generate_only:
        if shutil.disk_usage(root).free < profile.files * profile.maximum + args.reserve_bytes:
            raise RuntimeError('Insufficient disk for generated corpus and reserve')
        manifest = generate(root / 'corpus', profile, args.seed, check=budget.check)
        print(json.dumps({'profile': profile.name, 'files': len(manifest['files']),
                          'status': 'corpus-only', 'representative_qualified': False}))
        return 0
    from tools.qualification.concurrency import current_rss
    from tools.qualification.evidence import complete_report, provenance, summary, write_report
    from tools.qualification.execution import ExecutionGuard
    from tools.qualification.runner import variant
    from tools.qualification.services import Services
    report = {'version': 1, 'status': 'incomplete', 'profile': profile.name,
              'representative_qualified': False, 'adapters': {}, 'limits': limits}
    report_path = root / 'report.json'
    write_report(report_path, report)
    try:
        report['provenance'] = provenance()
        with ExecutionGuard(keep_awake=args.keep_awake) as guard, Services(root, token,
                request_timeout_seconds=args.request_timeout_seconds, deadline=budget.deadline) as services:
            budget.invariant = guard.check
            report['services'] = services.provenance()
            report['preflight'] = capacity(profile, shutil.disk_usage(root).free,
                                           services.free_bytes(deadline=budget.deadline),
                                           args.reserve_bytes)
            budget.check()
            write_report(report_path, report)
            if args.preflight_only:
                report['status'] = 'preflight-only'
                write_report(report_path, report)
                print(json.dumps(report['preflight']))
                return 0
            budget.service_check = services.check_reserve
            print(f'Generating {profile.files} synthetic Parquet files ({profile.name}).', flush=True)
            manifest = generate(root / 'corpus', profile, args.seed, check=budget.check)
            report['corpus'] = {'files': len(manifest['files']),
                                'bytes': sum(f['bytes'] for f in manifest['files']),
                                'min_bytes': min(f['bytes'] for f in manifest['files']),
                                'max_bytes': max(f['bytes'] for f in manifest['files']),
                                'relationships': profile.relationships, 'seed': args.seed}
            context = multiprocessing.get_context('spawn')
            for adapter in ('sqlite', 'melddb', 'postgres'):
                budget.check()
                print(f'Qualifying {adapter}.', flush=True)
                process = context.Process(target=variant, args=(adapter, root, manifest, services, limits))
                process.start()
                try:
                    while process.is_alive():
                        budget.check()
                        try:
                            if current_rss(process.pid) > args.memory_bytes:
                                raise RuntimeError('Adapter memory budget exceeded')
                        except (FileNotFoundError, ProcessLookupError):
                            pass
                        process.join(timeout=.25)
                    if process.exitcode:
                        raise RuntimeError('Adapter qualification failed: ' + adapter)
                finally:
                    if process.is_alive():
                        process.terminate()
                    process.join(timeout=10)
                result = json.loads((root / adapter / 'report.json').read_text(encoding='utf-8'))
                report['adapters'][adapter] = result
                write_report(report_path, report)
                print(f'{adapter}: passed.', flush=True)
            guard.check()
            report['execution'] = guard.evidence()
        budget.service_check = None
        budget.check()
        complete_report(report, manifest)
        report['execution'] = guard.evidence()
        report['elapsed_seconds'] = time.monotonic() - budget.started
        write_report(report_path, report)
        (root / 'summary.txt').write_text(summary(report), encoding='utf-8')
        print(summary(report))
        return 0
    except BaseException as exc:
        report['status'] = 'failed'
        report['representative_qualified'] = False
        report['error_type'] = type(exc).__name__
        if 'guard' in locals():
            report['execution'] = guard.evidence()
        write_report(report_path, report)
        raise


if __name__ == '__main__':
    raise SystemExit(main())
