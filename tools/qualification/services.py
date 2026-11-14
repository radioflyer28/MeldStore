"""Disposable loopback services using the existing lab's pinned images/helpers."""

import json
import math
import os
import secrets
import subprocess
import time
from contextlib import contextmanager

from tools.postgres_lab import IMAGE as POSTGRES_IMAGE
from tools.qualification.resources import ResourceLimit, owned_root, record_resource
from tools.s3_lab import IMAGE as RUSTFS_IMAGE
from tools.s3_lab import Lab

OWNER_LABEL = 'meldstore.workload-run'
DOCKER_TIMEOUT = 30


def docker_run(*args, timeout=DOCKER_TIMEOUT, check=True, env=None):
    try:
        result = subprocess.run(['docker', *args], timeout=timeout, env=env,
                                capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        raise ResourceLimit(f'Docker {args[0]} exceeded its command deadline') from None
    if check and result.returncode:
        raise RuntimeError(f'Docker {args[0]} failed with exit code {result.returncode}')
    return result


def docker(*args, timeout=DOCKER_TIMEOUT):
    return docker_run(*args, timeout=timeout).stdout.strip()


def probe_timeout(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ResourceLimit('Runtime budget exceeded during Docker probe')
    return min(DOCKER_TIMEOUT, remaining)


class Services:
    def __init__(self, root, token, *, request_timeout_seconds=60, deadline=None):
        if not math.isfinite(request_timeout_seconds) or request_timeout_seconds <= 0:
            raise ValueError('Request timeout must be finite and positive')
        self.deadline = deadline
        self.request_timeout_seconds = request_timeout_seconds
        self.root, self.token = owned_root(root, token), token
        self.pg = 'meldstore-workload-' + token
        self.rust = Lab(owner_label=f'{OWNER_LABEL}={token}', command_timeout=DOCKER_TIMEOUT)
        self.rust.name = 'meldstore-s07-' + token
        self.max_volume_bytes = 0
        self.volume_samples_unavailable = 0
        self.password = secrets.token_hex(24)

    def startup_timeout(self):
        return DOCKER_TIMEOUT if self.deadline is None else probe_timeout(self.deadline)

    def __enter__(self):
        import psycopg
        try:
            for name in (self.pg + '-data', self.rust.name + '-data', self.rust.name + '-logs'):
                if inspect_resource('volume', name, timeout=self.startup_timeout()) is not None:
                    raise ValueError('Disposable Docker volume already exists')
                record_resource(self.root, self.token, 'volume', name)
                docker('volume', 'create', '--label', f'{OWNER_LABEL}={self.token}', name,
                       timeout=self.startup_timeout())
                details = inspect_resource('volume', name, timeout=self.startup_timeout()) or {}
                if (details.get('Labels') or {}).get(OWNER_LABEL) != self.token:
                    raise ValueError('Disposable Docker volume ownership mismatch')
            record_resource(self.root, self.token, 'container', self.pg)
            docker_run(
                'run', '--detach', '--name', self.pg,
                '--label', 'meldstore.qualifier=representative',
                '--label', f'{OWNER_LABEL}={self.token}',
                '--publish', '127.0.0.1::5432', '--env', 'POSTGRES_PASSWORD',
                '--env', 'POSTGRES_USER=meldstore_lab', '--env', 'POSTGRES_DB=postgres',
                '--mount', f'type=volume,source={self.pg}-data,target=/var/lib/postgresql/data',
                POSTGRES_IMAGE, timeout=self.startup_timeout(), env={**os.environ, 'POSTGRES_PASSWORD': self.password},
            )
            ports = json.loads(docker('inspect', '--format', '{{json .NetworkSettings.Ports}}',
                                      self.pg, timeout=self.startup_timeout()))['5432/tcp']
            if len(ports) != 1 or ports[0]['HostIp'] != '127.0.0.1':
                raise RuntimeError('Docker did not bind PostgreSQL exclusively to loopback')
            self.dsn = psycopg.conninfo.make_conninfo(
                host='127.0.0.1', port=ports[0]['HostPort'], user='meldstore_lab',
                password=self.password, dbname='postgres', connect_timeout=2)
            startup_deadline = time.monotonic() + 60
            if self.deadline is not None:
                startup_deadline = min(startup_deadline, self.deadline)
            while True:
                result = docker_run('exec', self.pg, 'pg_isready', '-h', '127.0.0.1',
                                    '-U', 'meldstore_lab', '-t', '1', check=False,
                                    timeout=probe_timeout(startup_deadline))
                if result.returncode == 0:
                    break
                time.sleep(min(0.5, probe_timeout(startup_deadline)))
            self.server_version = docker('exec', self.pg, 'postgres', '--version',
                                         timeout=self.startup_timeout()).removeprefix('postgres (PostgreSQL) ')
            record_resource(self.root, self.token, 'container', self.rust.name)
            self.rust.startup_deadline = self.deadline
            self.rust.__enter__()
            self.native_tools = {
                name: docker('exec', self.pg, name, '--version', timeout=self.startup_timeout())
                for name in ('pg_dump', 'pg_restore', 'psql')
            }
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        cleanup(self.root, self.token)

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def database(self):
        import psycopg
        name = 'workload_' + secrets.token_hex(12)
        with psycopg.connect(self.dsn, autocommit=True) as admin:
            admin.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(name)))
        try:
            yield psycopg.conninfo.make_conninfo(self.dsn, dbname=name)
        finally:
            with psycopg.connect(self.dsn, autocommit=True) as admin:
                admin.execute(psycopg.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(
                    psycopg.sql.Identifier(name)))

    def options(self):
        return {'bucket': self.rust.bucket, 'prefix': 'workload/' + secrets.token_hex(12),
                'config': self.rust.config, 'retry_config': {'max_retries': 0},
                'client_options': {'timeout': f'{self.request_timeout_seconds:g}s'}}

    def tools(self, dsn):
        import psycopg

        from meldstore import PostgresArchiveTools
        internal = psycopg.conninfo.make_conninfo(dsn, host='127.0.0.1', port=5432)
        names = ('PGHOST', 'PGPORT', 'PGUSER', 'PGPASSWORD', 'PGDATABASE', 'PGCONNECT_TIMEOUT')
        prefix = ('docker', 'exec', '-i', *(v for name in names for v in ('--env', name)), self.pg)
        return PostgresArchiveTools(internal, prefix=prefix)

    def free_bytes(self, *, deadline=None):
        if deadline is None:
            deadline = time.monotonic() + DOCKER_TIMEOUT
        # Query the mounted volumes, not overlay or host-drive free space.
        values = []
        for container, path in ((self.pg, '/var/lib/postgresql/data'), (self.rust.name, '/data')):
            lines = docker('exec', container, 'df', '-Pk', path,
                           timeout=probe_timeout(deadline)).splitlines()
            values.append(int(lines[-1].split()[3]) * 1024)
        return min(values)

    def check_reserve(self, reserve, deadline):
        sample = self.volume_bytes(deadline=deadline)
        if sample is not None:
            self.max_volume_bytes = max(self.max_volume_bytes, sample)
        if self.free_bytes(deadline=deadline) < reserve:
            raise ResourceLimit('Docker volume disk reserve exhausted')

    def volume_bytes(self, *, deadline=None):
        if deadline is None:
            deadline = time.monotonic() + DOCKER_TIMEOUT
        total = 0
        for container, path in ((self.pg, '/var/lib/postgresql/data'), (self.rust.name, '/data')):
            for attempt in range(3):
                result = docker_run('exec', container, 'du', '-sk', path, check=False,
                                    timeout=probe_timeout(deadline))
                if result.returncode == 0:
                    total += int(result.stdout.split()[0]) * 1024
                    break
                lines = result.stderr.strip().splitlines()
                transient = result.returncode == 1 and lines and all(
                    line.startswith("du: cannot access '" + path + '/')
                    and line.endswith('No such file or directory') for line in lines)
                if not transient:
                    raise RuntimeError('Service volume sampling failed')
                if attempt == 2:
                    self.volume_samples_unavailable += 1
                    return None  # Never report a partial traversal as zero or a complete sample.
                time.sleep(.02)
        return total

    def resource_snapshot(self):
        stats = {}
        for role, container in (('postgres', self.pg), ('rustfs', self.rust.name)):
            row = json.loads(docker('stats', '--no-stream', '--format', '{{json .}}', container))
            stats[role] = {'memory_usage': row['MemUsage'], 'cpu_percent': row['CPUPerc']}
        return stats

    def clear_payloads(self):
        # The entire bucket is inside this run's dedicated RustFS container.
        # Never accept an external endpoint or an arbitrary cleanup prefix.
        import obstore
        from obstore.store import S3Store
        owned_root(self.root, self.token)
        expected = 'meldstore-s07-' + self.token
        if self.rust.name != expected:
            raise ValueError('RustFS ownership mismatch')
        labels = json.loads(docker('inspect', '--format', '{{json .Config.Labels}}', expected))
        if labels.get(OWNER_LABEL) != self.token or labels.get('meldstore.qualifier') != 's07':
            raise ValueError('RustFS ownership label mismatch')
        store = S3Store(self.rust.bucket, config=self.rust.config)
        for batch in obstore.list(store):
            for entry in batch:
                obstore.delete(store, entry['path'])

    def provenance(self):
        return {'postgres_image': POSTGRES_IMAGE, 'rustfs_image': RUSTFS_IMAGE,
                'postgres_version': self.server_version, 'native_tools': self.native_tools,
                'client_policy': {'request_timeout_seconds': self.request_timeout_seconds,
                                  'max_retries': 0},
                'topology': 'loopback PostgreSQL/RustFS; one persistent single-host coordinator'}


def inspect_resource(kind, name, *, timeout=DOCKER_TIMEOUT):
    result = docker_run(kind, 'inspect', name, check=False, timeout=timeout)
    if result.returncode == 0:
        return json.loads(result.stdout)[0]
    if 'no such' in result.stderr.lower():
        return None
    raise RuntimeError(f'Cannot establish disposable Docker {kind} state')


def cleanup(root, token):
    """Remove only manifest resources bearing this run's Docker ownership label."""
    import re

    root = owned_root(root, token)
    if not re.fullmatch('[0-9a-f]{32}', token):
        raise ValueError('Invalid ownership token')
    pg, rust = 'meldstore-workload-' + token, 'meldstore-s07-' + token
    allowed = {'container': {pg, rust}, 'volume': {pg + '-data', rust + '-data', rust + '-logs'}}
    record = json.loads((root / 'ownership.json').read_text(encoding='utf-8'))
    resources = {(entry['kind'], entry['name']) for entry in record['resources']}
    if any(name not in allowed.get(kind, set()) for kind, name in resources):
        raise ValueError('Unexpected resource in workload ownership record')
    removable = []
    for kind, name in sorted(resources):
        details = inspect_resource(kind, name)
        if details is None:
            continue
        labels = (details['Config'] if kind == 'container' else details).get('Labels') or {}
        if labels.get(OWNER_LABEL) != token:
            raise ValueError(f'Docker {kind} ownership label mismatch')
        # Container IDs are immutable. A replacement with the same name is not ours.
        removable.append((kind, details['Id'] if kind == 'container' else name))
    for kind, identifier in removable:
        if kind == 'container':
            docker('rm', '--force', identifier)
        else:
            docker('volume', 'rm', identifier)
