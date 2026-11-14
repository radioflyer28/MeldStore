"""Atomic sanitized evidence, explicit incomplete state and provenance."""

import json
import os
import platform
import re
import sqlite3
import subprocess
import sys
import time
from importlib.metadata import distribution, version
from pathlib import Path

import xxhash


def provenance():
    root = Path(__file__).resolve().parents[2]
    source = json.loads(distribution('melddb').read_text('direct_url.json') or '{}')
    pin = source.get('vcs_info', {}).get('commit_id')
    expected = '01e27441d7512f85663c8a86986429d62a69bff9'
    if pin != expected:
        raise RuntimeError('Installed MeldDB does not match the qualified exact revision')
    git = ['git', '-c', f'safe.directory={root.as_posix()}', '-c', 'core.bare=false', '-C', str(root)]
    revision = subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip()
    dirty = bool(subprocess.check_output([*git, 'status', '--porcelain'], text=True))
    return {'meldstore_revision': revision, 'working_tree_dirty': dirty, 'melddb_revision': pin,
            'python': platform.python_version(), 'sqlite_version': sqlite3.sqlite_version,
            'os': platform.system(), 'os_release': platform.release(),
            'architecture': platform.machine(), 'logical_cpus': os.cpu_count(),
            'lock_xxh3_128': xxhash.xxh3_128((root / 'uv.lock').read_bytes()).hexdigest(),
            'packages': {name: version(name) for name in ('meldstore', 'pyarrow', 'obstore', 'xxhash',
                                                         'psycopg', 'psycopg-binary')},
            'interpreter_implementation': sys.implementation.name,
            'source_xxh3_128': {p.relative_to(root).as_posix(): xxhash.xxh3_128(p.read_bytes()).hexdigest()
                               for p in sorted([*root.glob('src/meldstore/**/*.py'),
                                                *root.glob('tools/qualification/*.py'),
                                                root/'tools/qualify_representative.py',
                                                root/'tools/qualify_local.py', root/'tools/s3_lab.py',
                                                root/'tools/postgres_lab.py'])}}



def sanitized(value):
    # Evidence is allowlisted by construction. Also reject accidental path/credential leaks.
    encoded = json.dumps(value, indent=2, allow_nan=False)
    if re.search(r'(?i)([a-z]:[\\/]|/(?:home|Users|tmp)/|postgres(?:ql)?://|password\s*=|secret_access_key)',
                 encoded):
        raise ValueError('Evidence contains a private path or connection secret')
    return encoded + '\n'


def write_report(path, report):
    path = Path(path)
    encoded = sanitized(report)
    temp = path.with_suffix('.tmp')
    temp.write_text(encoded, encoding='utf-8')
    # Windows scanners/directory sampling can briefly deny replacement. Retry only
    # sharing/access errors for this already-written evidence file, never SQL/I/O work.
    for attempt in range(10):
        try:
            os.replace(temp, path)
            break
        except PermissionError as exc:
            if os.name != 'nt' or getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 9:
                raise
            time.sleep(.02 * (attempt + 1))


def complete_report(report, manifest):
    required = {'import', 'relationship_load', 'interval_query', 'bulk_sample', 'repeated_sql_sample',
                'shared_publication_rollback', 'concurrent_clients', 'uncertain_publication',
                'migration_dry_run', 'migration_first_batch', 'migration_resume', 'interrupted_backup',
                'backup', 'corrupt_restore_refused', 'restore', 'occupied_restore_refused'}
    if set(report['adapters']) != {'sqlite', 'melddb', 'postgres'}:
        raise ValueError('All adapters are required')
    for adapter, result in report['adapters'].items():
        names = {op['name'] for op in result['operations'] if op['status'] == 'passed'}
        needs = required | ({'cutover', 'cutover_preflight'} if adapter != 'postgres' else set())
        if result['status'] != 'passed' or not needs <= names:
            raise ValueError('Required workload phase missing or failed')
        if any(op['status'] != 'passed' for op in result['operations']):
            raise ValueError('Incomplete workload operation')
        if not result.get('peak_rss_bytes') or not result.get('sampled_disk_bytes'):
            raise ValueError('Required resource evidence missing')
        clients = {op['clients'] for op in result['operations'] if op['name'] == 'concurrent_clients'}
        if clients != set(manifest['profile']['clients']):
            raise ValueError('Required client configuration missing')
    profile = manifest['profile']['name']
    if report['profile'] != profile:
        raise ValueError('Report profile differs from validated corpus')
    if profile == 'representative':
        if len(manifest['files']) != 100 or any(not 100_000_000 <= f['bytes'] <= 300_000_000
                                              for f in manifest['files']):
            raise ValueError('Representative scale not achieved')
    report['status'] = 'passed'
    report['representative_qualified'] = profile == 'representative'


def summary(report):
    lines = [f"Profile: {report['profile']}", f"Status: {report['status']}",
             f"Representative qualified: {report['representative_qualified']}",
             'Topology: one host; pinned PostgreSQL 17 and RustFS; no AWS claim.']
    for name, result in report['adapters'].items():
        lines.append(f"{name}: {result['status']}; {len(result['operations'])} recorded operations; "
                     f"peak client RSS {result.get('peak_rss_bytes', 'unavailable')} bytes")
    return '\n'.join(lines) + '\n'
