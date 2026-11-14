"""Resource budgets and fresh output ownership for development-only runs."""

import json
import math
import secrets
import shutil
import time
from pathlib import Path

from tools.qualification.evidence import write_report
from tools.qualify_local import peak_rss


class ResourceLimit(RuntimeError):
    pass


def fresh_root(path, protected=()):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise ValueError('Output must be a fresh directory')
    resolved = path.resolve()
    for item in protected:
        item = Path(item).resolve()
        if resolved == item or resolved in item.parents or item in resolved.parents:
            raise ValueError('Output overlaps a protected location')
    resolved.mkdir(parents=True)
    token = secrets.token_hex(16)
    (resolved / 'ownership.json').write_text(json.dumps({'version': 1, 'run_id': token,
                                                       'resources': []}), encoding='utf-8')
    return resolved, token


def owned_root(path, token):
    path = Path(path)
    if path.is_symlink() or path.resolve() != path.absolute():
        raise ValueError('Cleanup root is not canonical')
    record = json.loads((path / 'ownership.json').read_text(encoding='utf-8'))
    if record['version'] != 1 or record['run_id'] != token:
        raise ValueError('Run ownership mismatch')
    return path


def record_resource(root, token, kind, name):
    root = owned_root(root, token)
    record = json.loads((root / 'ownership.json').read_text(encoding='utf-8'))
    record['resources'].append({'kind': kind, 'name': name})
    write_report(root / 'ownership.json', record)


def capacity(profile, host_free, docker_free, reserve):
    corpus = profile.files * profile.maximum
    # Source + SQLite backup + restored copy + native archive/cutover staging.
    host_required = 5 * corpus + reserve
    # Active source, restored/cutover destination and multipart headroom.
    docker_required = 4 * corpus + reserve
    if host_free < host_required:
        raise ResourceLimit('Insufficient host disk for declared copy graph')
    if docker_free < docker_required:
        raise ResourceLimit('Insufficient Docker volume disk for declared copy graph')
    # Docker Desktop may share the physical host disk: conservatively reserve both.
    if host_free < host_required + docker_required:
        raise ResourceLimit('Insufficient host disk including Docker backing storage')
    return {'corpus_upper_bytes': corpus, 'host_required_bytes': host_required,
            'docker_required_bytes': docker_required, 'host_free_bytes': host_free,
            'docker_free_bytes': docker_free, 'reserve_bytes': reserve}


class Budget:
    def __init__(self, root, *, memory_bytes=4_000_000_000, seconds=14_400,
                 reserve_bytes=2_000_000_000, service_check=None, invariant=None):
        if memory_bytes <= 0 or not math.isfinite(seconds) or seconds <= 0 or reserve_bytes < 0:
            raise ValueError('Invalid resource limits')
        self.root = Path(root)
        self.memory_bytes, self.seconds, self.reserve_bytes = memory_bytes, seconds, reserve_bytes
        self.started = time.monotonic()
        self.service_check = service_check
        self.invariant = invariant
        self.last_service = 0
        self.max_rss = 0

    def check(self):
        if self.invariant is not None:
            self.invariant()
        if time.monotonic() - self.started > self.seconds:
            raise ResourceLimit('Runtime budget exceeded')
        self.max_rss = max(self.max_rss, peak_rss())
        if self.max_rss > self.memory_bytes:
            raise ResourceLimit('Client memory budget exceeded')
        if shutil.disk_usage(self.root).free < self.reserve_bytes:
            raise ResourceLimit('Host disk reserve exhausted')
        if self.service_check and time.monotonic() - self.last_service > 2:
            self.service_check(self.reserve_bytes)
            self.last_service = time.monotonic()
