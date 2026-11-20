"""Owned PostgreSQL-only service for metadata experiments; never accepts an ambient DSN."""

import json
import os
import secrets
import time

from tools.postgres_lab import IMAGE
from tools.qualification.resources import ResourceLimit, owned_root, record_resource
from tools.qualification.services import (
    OWNER_LABEL,
    Services,
    docker,
    docker_run,
    inspect_resource,
)

MEMORY = 2 * 1024**3
STARTUP_SETTINGS = {'shared_buffers': '128MB', 'work_mem': '4MB',
                    'effective_cache_size': '512MB', 'max_parallel_workers': '2',
                    'max_parallel_workers_per_gather': '2', 'autovacuum': 'off',
                    'statement_timeout': '1min'}


class IntervalService(Services):
    def __init__(self, root, token, deadline):
        self.root, self.token = owned_root(root, token), token
        self.deadline = deadline
        self.pg = 'meldstore-workload-' + token
        self.password = secrets.token_hex(24)

    def __enter__(self):
        try:
            for kind, name in (('volume', self.pg + '-data'), ('container', self.pg)):
                if inspect_resource(kind, name, timeout=self.startup_timeout()) is not None:
                    raise ValueError('Disposable interval resource already exists')
            record_resource(self.root, self.token, 'volume', self.pg + '-data')
            docker('volume', 'create', '--label', f'{OWNER_LABEL}={self.token}', self.pg + '-data',
                   timeout=self.startup_timeout())
            volume = inspect_resource('volume', self.pg + '-data', timeout=self.startup_timeout())
            if not volume or (volume.get('Labels') or {}).get(OWNER_LABEL) != self.token:
                raise ValueError('Service volume ownership mismatch')
            record_resource(self.root, self.token, 'container', self.pg)
            docker_run('run', '--detach', '--name', self.pg,
                       '--label', f'{OWNER_LABEL}={self.token}',
                       '--publish', '127.0.0.1::5432', '--env', 'POSTGRES_PASSWORD',
                       '--env', 'POSTGRES_USER=meldstore_lab', '--env', 'POSTGRES_DB=postgres',
                       '--memory', str(MEMORY), '--memory-swap', str(MEMORY),
                       '--mount', f'type=volume,source={self.pg}-data,target=/var/lib/postgresql/data',
                       IMAGE, *(v for k, value in STARTUP_SETTINGS.items()
                                for v in ('-c', f'{k}={value}')),
                       env={**os.environ, 'POSTGRES_PASSWORD': self.password},
                       timeout=self.startup_timeout())
            details = inspect_resource('container', self.pg, timeout=self.startup_timeout())
            if details['Config']['Labels'].get(OWNER_LABEL) != self.token:
                raise ValueError('Service ownership mismatch')
            ports = details['NetworkSettings']['Ports']['5432/tcp']
            if len(ports) != 1 or ports[0]['HostIp'] != '127.0.0.1':
                raise ValueError('Service must bind only to loopback')
            if details['HostConfig']['Memory'] != MEMORY or details['HostConfig']['MemorySwap'] != MEMORY:
                raise ValueError('Service memory limits differ')
            import psycopg
            self.dsn = psycopg.conninfo.make_conninfo(host='127.0.0.1', port=ports[0]['HostPort'],
                user='meldstore_lab', password=self.password, dbname='postgres', connect_timeout=2)
            until = min(time.monotonic() + 60, self.deadline)
            while time.monotonic() < until:
                result = docker_run('exec', self.pg, 'pg_isready', '-h', '127.0.0.1',
                                    '-U', 'meldstore_lab', '-t', '1',
                                    check=False, timeout=self.startup_timeout())
                if result.returncode == 0:
                    break
                time.sleep(.25)
            else:
                raise ResourceLimit('PostgreSQL startup deadline exceeded')
            self.evidence = {'image': IMAGE, 'image_id': details['Image'],
                             'memory_limit_bytes': MEMORY, 'memory_swap_limit_bytes': MEMORY,
                             'startup_settings': STARTUP_SETTINGS,
                             'topology': 'Disposable loopback PostgreSQL 17, one host; metadata only'}
            self.check_reserve(2_000_000_000, self.deadline)
            return self
        except BaseException:
            self.close()
            raise

    def check_reserve(self, reserve, deadline):
        from tools.qualification.services import probe_timeout
        lines = docker('exec', self.pg, 'df', '-Pk', '/var/lib/postgresql/data',
                       timeout=probe_timeout(deadline)).splitlines()
        if int(lines[-1].split()[3]) * 1024 < reserve:
            raise ResourceLimit('PostgreSQL volume reserve exhausted')

    def provenance(self):
        return json.loads(json.dumps(self.evidence))

    def volume_size(self, deadline):
        from tools.qualification.services import probe_timeout
        result = docker('exec', self.pg, 'du', '-sk', '/var/lib/postgresql/data',
                        timeout=probe_timeout(deadline))
        return int(result.split()[0]) * 1024
