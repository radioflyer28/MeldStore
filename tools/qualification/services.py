"""Disposable loopback services using the existing lab's pinned images/helpers."""

import json
import math
import os
import secrets
import subprocess
import time
from contextlib import contextmanager

from tools.postgres_lab import IMAGE as POSTGRES_IMAGE
from tools.postgres_lab import docker
from tools.qualification.resources import ResourceLimit, owned_root, record_resource
from tools.s3_lab import IMAGE as RUSTFS_IMAGE
from tools.s3_lab import Lab


class Services:
    def __init__(self, root, token, *, request_timeout_seconds=60):
        if not math.isfinite(request_timeout_seconds) or request_timeout_seconds <= 0:
            raise ValueError('Request timeout must be finite and positive')
        self.request_timeout_seconds = request_timeout_seconds
        self.root, self.token = owned_root(root, token), token
        self.pg = 'meldstore-workload-' + token
        self.rust = Lab()
        self.rust.name = 'meldstore-s07-' + token
        self.pg_started = False
        self.max_volume_bytes = 0
        self.volume_samples_unavailable = 0
        self.password = secrets.token_hex(24)

    def __enter__(self):
        import psycopg
        try:
            for name in (self.pg, self.rust.name):
                record_resource(self.root, self.token, 'container', name)
            for name in (self.pg + '-data', self.rust.name + '-data', self.rust.name + '-logs'):
                record_resource(self.root, self.token, 'volume', name)
            subprocess.run([
                'docker', 'run', '--detach', '--name', self.pg,
                '--label', 'meldstore.qualifier=representative',
                '--publish', '127.0.0.1::5432', '--env', 'POSTGRES_PASSWORD',
                '--env', 'POSTGRES_USER=meldstore_lab', '--env', 'POSTGRES_DB=postgres',
                '--mount', f'type=volume,source={self.pg}-data,target=/var/lib/postgresql/data',
                POSTGRES_IMAGE,
            ], env={**os.environ, 'POSTGRES_PASSWORD': self.password},
                check=True, capture_output=True, text=True)
            self.pg_started = True
            ports = json.loads(docker('inspect', '--format', '{{json .NetworkSettings.Ports}}',
                                      self.pg))['5432/tcp']
            if len(ports) != 1 or ports[0]['HostIp'] != '127.0.0.1':
                raise RuntimeError('Docker did not bind PostgreSQL exclusively to loopback')
            self.dsn = psycopg.conninfo.make_conninfo(
                host='127.0.0.1', port=ports[0]['HostPort'], user='meldstore_lab',
                password=self.password, dbname='postgres', connect_timeout=2)
            deadline = time.monotonic() + 60
            while True:
                try:
                    with psycopg.connect(self.dsn, autocommit=True) as connection:
                        self.server_version = connection.execute('SHOW server_version').fetchone()[0]
                    break
                except psycopg.OperationalError:
                    if time.monotonic() > deadline:
                        raise RuntimeError('Disposable PostgreSQL startup timed out') from None
                    time.sleep(0.5)
            self.rust.__enter__()
            self.native_tools = {name: docker('exec', self.pg, name, '--version')
                                 for name in ('pg_dump', 'pg_restore', 'psql')}
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

    def free_bytes(self):
        # Query the mounted volumes, not overlay or host-drive free space.
        values = []
        for container, path in ((self.pg, '/var/lib/postgresql/data'), (self.rust.name, '/data')):
            lines = docker('exec', container, 'df', '-Pk', path).splitlines()
            values.append(int(lines[-1].split()[3]) * 1024)
        return min(values)

    def check_reserve(self, reserve):
        sample = self.volume_bytes()
        if sample is not None:
            self.max_volume_bytes = max(self.max_volume_bytes, sample)
        if self.free_bytes() < reserve:
            raise ResourceLimit('Docker volume disk reserve exhausted')

    def volume_bytes(self):
        total = 0
        for container, path in ((self.pg, '/var/lib/postgresql/data'), (self.rust.name, '/data')):
            for attempt in range(3):
                result = subprocess.run(['docker', 'exec', container, 'du', '-sk', path],
                                        capture_output=True, text=True)
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
        if labels.get('meldstore.qualifier') != 's07':
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


def cleanup(root, token):
    """Remove only names derived from this run's validated ownership token."""
    import re
    owned_root(root, token)
    if not re.fullmatch('[0-9a-f]{32}', token):
        raise ValueError('Invalid ownership token')
    containers = [('meldstore-workload-' + token, 'representative'),
                  ('meldstore-s07-' + token, 's07')]
    for name, label in containers:
        inspected = subprocess.run(['docker', 'container', 'inspect', name],
                                   capture_output=True, text=True)
        if inspected.returncode:
            # An unavailable daemon is not evidence of successful cleanup.
            docker('info', '--format', '{{.ServerVersion}}')
            if 'No such' not in inspected.stderr:
                raise RuntimeError('Cannot establish disposable container state')
            continue
        details = json.loads(inspected.stdout)[0]
        if details['Config']['Labels'].get('meldstore.qualifier') != label:
            raise ValueError('Container ownership label mismatch')
        docker('rm', '--force', name)
    for name in (containers[0][0] + '-data', containers[1][0] + '-data', containers[1][0] + '-logs'):
        inspected = subprocess.run(['docker', 'volume', 'inspect', name],
                                   capture_output=True, text=True)
        if inspected.returncode == 0:
            docker('volume', 'rm', name)
        elif 'No such' not in inspected.stderr:
            raise RuntimeError('Cannot establish disposable volume state')
