"""Explicit offline recovery of interrupted v2 interval checkpoints (Windows only)."""

import argparse
import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import xxhash

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.qualification.evidence import sanitized, write_report
from tools.qualification.interval_checkpoint import (
    load_checkpoint,
    timestamp,
    utc_now,
    validate_sessions,
)
from tools.qualification.interval_evidence import ROOT, provenance, require, validate
from tools.qualification.resources import fresh_root, owned_root
from tools.qualification.services import cleanup, inspect_resource
from tools.qualify_intervals import cleanup_worker_scratch


def byte_digest(value):
    return xxhash.xxh3_128(value).hexdigest()


def process_inventory():
    # Fail closed on unsupported platforms, inaccessible command lines or CIM failure.
    # No credentials or process command lines are copied into shareable evidence.
    require(os.name == 'nt', 'Interrupted recovery currently requires Windows process inspection')
    script = """$ErrorActionPreference = 'Stop'
$items = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^(python.*|uv|uvx)\\.exe$'
} | Select-Object ProcessId, Name, CommandLine)
ConvertTo-Json -InputObject $items -Compress
"""
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, check=True, timeout=30)
    rows = json.loads(result.stdout)
    require(isinstance(rows, list), 'Invalid process inventory')
    return rows


def require_stopped(root):
    rows = process_inventory()
    require(any(row.get('ProcessId') == os.getpid() for row in rows),
            'Process inventory does not include this recovery process')
    needle = str(root).replace('\\', '/').casefold()
    for row in rows:
        require(type(row.get('ProcessId')) is int and isinstance(row.get('Name'), str),
                'Invalid process identity')
        command = row.get('CommandLine')
        require(isinstance(command, str) and bool(command), 'Cannot inspect a Python/uv process')
        if row['ProcessId'] == os.getpid():
            continue
        normalized = command.replace('\\', '/').casefold()
        # Block all interval runners/workers, including a worker missing its root argument.
        active = 'qualify_intervals' in normalized or 'qualification.interval_worker' in normalized
        # uv and the Windows venv launcher may be ancestors of this recovery command.
        own_launcher = ('recover_intervals.py' in normalized
                        and re.fullmatch(r'(python.*|uv|uvx)\.exe', row['Name'], re.I))
        require(not active and (needle not in normalized or own_launcher),
                'An interval runner or source-root process is still active')
    return {'method': 'windows-cim-python-uv-inventory', 'checked_at': utc_now(),
            'runner_and_workers_absent': True}


def canonical(path):
    path = Path(path).absolute()
    require(path.resolve() == path and not path.is_symlink() and not path.is_junction(),
            'Recovery paths must be canonical, without links or junctions')
    return path


def interrupted_report(raw, current):
    report = json.loads(raw)
    require(report.get('schema_version') == 2, 'Recovery requires v2 evidence')
    require(report.get('status') == 'incomplete' and report.get('resume_eligible') is False
            and report.get('cleanup') is False, 'Recovery requires an interrupted open report')
    require(report.get('full_metadata_qualified') is False
            and 0 < len(report['cells']) < len(report['schedule']), 'No unfinished prefix')
    require(report['provenance'] == current, 'Recovery provenance differs')
    validate(report, partial=True)
    session = report['sessions'][-1]
    require(session['ended_at'] is None and session['cleanup'] is False,
            'Recovery cannot override a finalized session')
    require(session['start_cell'] < session['end_cell'], 'Open session has no accepted treatments')
    require(timestamp(utc_now()) >= timestamp(session['checkpoint_at']), 'Recovery clock predates checkpoint')
    return report


def worker_directories(root, report):
    directories = []
    session = report['sessions'][-1]
    for directory in root.glob('cell-*'):
        canonical(directory)
        require(directory.is_dir() and re.fullmatch(r'cell-\d{3}', directory.name),
                'Unexpected worker directory')
        ordinal = int(directory.name[5:])
        require(session['start_cell'] <= ordinal <= len(report['cells']), 'Worker outside source session')
        control_path = canonical(directory / 'control.json')
        control = json.loads(control_path.read_bytes())
        require(Path(control['directory']) == directory
                and control['profile'] == report['profile']
                and control['cell'] == report['schedule'][ordinal]
                and control['provenance'] == report['provenance'], 'Worker ownership/control mismatch')
        # Preflight every deletion before any cleanup, including nested reparse points.
        for target in directory.glob('interval-*'):
            require(target.is_dir(), 'Unexpected scratch file')
            for item in [target, *target.rglob('*')]:
                canonical(item)
                require(item.resolve().is_relative_to(directory), 'Scratch escaped owned worker')
        directories.append(directory)
    return directories


def unchanged(source, raw, manifest, manifest_raw, current, tool_hash):
    require(source.read_bytes() == raw and manifest.read_bytes() == manifest_raw,
            'Source evidence or ownership changed during recovery')
    require(provenance() == current, 'Recovery provenance changed')
    require(byte_digest(Path(__file__).read_bytes()) == tool_hash, 'Recovery tool changed')


def recover(source, output):
    source = canonical(source)
    require(source.name == 'report.json', 'Source must be an owned report.json')
    root = canonical(source.parent)
    manifest = canonical(root / 'ownership.json')
    raw, manifest_raw = source.read_bytes(), manifest.read_bytes()
    current = provenance()
    report = interrupted_report(raw, current)
    ownership = json.loads(manifest_raw)
    token = report['sessions'][-1]['id']
    owned_root(root, token)
    # Interval runs own PostgreSQL resources only. Refuse extra/missing registrations.
    pg = 'meldstore-workload-' + token
    expected = ([{'kind': 'volume', 'name': pg + '-data'}, {'kind': 'container', 'name': pg}]
                if 'postgres' in report['profile']['adapters'] else [])
    require(ownership['resources'] == expected, 'Unexpected interval resource manifest')
    directories = worker_directories(root, report)
    output = canonical(output)
    require(not output.exists(), 'Output must be a fresh directory')
    protected = [root, *(ROOT / name for name in ('src', 'tools', 'tests', '.git', '.agents'))]
    require(not any(output == p or output in p.parents or p in output.parents for p in protected),
            'Output overlaps a protected location')
    tool_hash = byte_digest(Path(__file__).read_bytes())
    lock = root / 'recovery.lock'
    # Exclusive operator operation; a stale lock fails closed and requires inspection.
    with lock.open('x', encoding='utf-8') as handle:
        handle.write(str(os.getpid()))
    try:
        checks = [require_stopped(root)]
        unchanged(source, raw, manifest, manifest_raw, current, tool_hash)
        cleanup(root, token)  # Validates all Docker ownership labels before deleting anything.
        for directory in directories:
            cleanup_worker_scratch(directory, root)
        for entry in expected:
            require(inspect_resource(entry['kind'], entry['name']) is None,
                    'Owned resource remains after cleanup')
        require(not list(root.glob('cell-*/interval-*')), 'Worker scratch remains')
        checks.append(require_stopped(root))
        unchanged(source, raw, manifest, manifest_raw, current, tool_hash)
        recovered_at = utc_now()
        result = copy.deepcopy(report)
        session = result['sessions'][-1]
        audit = {'version': 1, 'kind': 'verified-interrupted-prefix',
                 'source_report_xxh3_128': byte_digest(raw),
                 'source_ownership_xxh3_128': byte_digest(manifest_raw),
                 'tool': 'tools/recover_intervals.py', 'tool_xxh3_128': tool_hash,
                 'session_id': token, 'accepted_treatments': len(report['cells']),
                 'measurement_ended_at': session['checkpoint_at'], 'recovered_at': recovered_at,
                 'original_status': report['status'], 'original_ended_at': session['ended_at'],
                 'original_cleanup': report['cleanup'], 'original_resume_eligible': False,
                 'original_report_continuity': report['continuity'],
                 'process_checks': checks, 'owned_cleanup_verified': True,
                 'continuity_after_checkpoint': 'unknown; excluded from measurement',
                 'incomplete_treatment_samples_retained': False}
        session.update(ended_at=session['checkpoint_at'], cleanup=True,
                       end_reason='recovered-at-last-verified-checkpoint', cleanup_verified_at=recovered_at)
        result.setdefault('recoveries', []).append(audit)
        result.update(status='paused', resume_eligible=True, cleanup=True, continuity=True,
                      stop_reason='verified-interrupted-prefix-recovery')
        # The saved execution snapshot already covers the accepted prefix. Never extend it.
        validate(result, partial=True)
        validate_sessions(result, closed=True)
        sanitized(result)
        destination, _ = fresh_root(output, protected)
        candidate = destination / 'recovered.json'
        write_report(candidate, result)
        try:
            load_checkpoint(candidate, current)
            unchanged(source, raw, manifest, manifest_raw, current, tool_hash)
            (destination / 'recovery.md').write_text(
                '# Verified interrupted interval prefix\n\n'
                f'Retained {len(result["cells"])} complete treatments. Original report unchanged.\n\n'
                'The recovered session ends at its last verified checkpoint, not at process exit. '
                'Continuity after that boundary is unknown. Cleanup was verified later; see '
                'recoveries in report.json for timestamps, source/tool digests and process checks. '
                'No incomplete samples were retained. Resume into fresh resources.\n', encoding='utf-8')
            candidate.rename(destination / 'report.json')
        except BaseException:
            candidate.unlink(missing_ok=True)
            raise
        return result
    finally:
        lock.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    report = recover(args.source, args.output)
    print(f'Recovered {len(report["cells"])} complete treatments; no full qualification claim.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
