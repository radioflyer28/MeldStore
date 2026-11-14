"""Bounded deterministic Parquet corpus; never reads private source data."""

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

import xxhash


@dataclass(frozen=True)
class Profile:
    name: str
    files: int
    minimum: int
    maximum: int
    relationships: int
    clients: tuple[int, ...]

    def __post_init__(self):
        if self.name not in ('smoke', 'representative', 'custom'):
            raise ValueError('Unknown profile')
        if not 1 <= self.files <= 1000 or not 250_000 <= self.minimum <= self.maximum:
            raise ValueError('Invalid file count or byte limits')
        if not 1 <= self.relationships <= 10_000_000:
            raise ValueError('Invalid relationship count')
        if (not self.clients or self.files < max(self.clients)
                or any(n not in (1, 2, 4) for n in self.clients)):
            raise ValueError('Invalid client counts')
        fixed = {'smoke': (4, 1_000_000, 4_000_000, 2000, (1, 2)),
                 'representative': (100, 100_000_000, 300_000_000, 300_000, (1, 2, 4))}
        if self.name in fixed and (self.files, self.minimum, self.maximum,
                                   self.relationships, self.clients) != fixed[self.name]:
            raise ValueError('Modified standard profiles must be labeled custom')


PROFILES = {
    'smoke': Profile('smoke', 4, 1_000_000, 4_000_000, 2000, (1, 2)),
    'representative': Profile('representative', 100, 100_000_000, 300_000_000,
                              300_000, (1, 2, 4)),
}
ROW_GROUP = 4096


def digest(path):
    value = xxhash.xxh3_128()
    size = 0
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
            size += len(block)
    return size, value.hexdigest()


def generate(directory, profile, seed=20261112, check=lambda: None):
    import pyarrow as pa
    import pyarrow.parquet as pq

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {'version': 1, 'generator': 'fixed-binary-v1', 'seed': seed,
                'profile': asdict(profile), 'pyarrow': pa.__version__,
                'encoding': {'compression': 'NONE', 'dictionary': False,
                             'row_group_rows': ROW_GROUP}, 'files': []}
    schema = pa.schema([('ordinal', pa.int64()), ('value', pa.binary(32))])
    for number in range(profile.files):
        check()
        rng = random.Random(seed + number)
        # Leave space for the final row group/footer at both size boundaries.
        margin = min(max(200_000, profile.maximum // 100),
                     (profile.maximum - profile.minimum) // 4)
        low, high = profile.minimum + margin, profile.maximum - margin
        target = low + (high - low) * number // max(1, profile.files - 1)
        path = directory / f'blob-{number:04d}.parquet'
        rows = 0
        with path.open('xb') as stream:
            with pq.ParquetWriter(stream, schema, compression='NONE',
                                  use_dictionary=False, write_statistics=True) as writer:
                while stream.tell() < target:
                    check()
                    data = rng.randbytes(32 * ROW_GROUP)
                    values = pa.Array.from_buffers(pa.binary(32), ROW_GROUP,
                                                   [None, pa.py_buffer(data)])
                    writer.write_table(pa.table({'ordinal': range(rows, rows + ROW_GROUP),
                                                 'value': values}, schema=schema))
                    rows += ROW_GROUP
        size, hash_value = digest(path)
        if not profile.minimum <= size <= profile.maximum:
            raise ValueError('Encoded file outside profile bounds; use a wider custom range')
        manifest['files'].append({'name': path.name, 'id': f'blob-{number:04d}',
                                  'bytes': size, 'rows': rows, 'xxh3_128': hash_value,
                                  'source_id': f'source-{number % 5}',
                                  'config_id': f'config-{number % 3}',
                                  'start_offset': number * 60, 'end_offset': number * 60 + 120})
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    validate(directory, manifest, check=check)
    return manifest


def validate(directory, manifest, check=lambda: None):
    import pyarrow.parquet as pq

    if manifest['version'] != 1 or manifest['generator'] != 'fixed-binary-v1':
        raise ValueError('Unsupported corpus version')
    config = dict(manifest['profile'])
    config['clients'] = tuple(config['clients'])
    profile = Profile(**config)
    files = manifest['files']
    if len(files) != profile.files or len({f['name'] for f in files}) != profile.files:
        raise ValueError('Corpus file count mismatch')
    if len({f['id'] for f in files}) != profile.files:
        raise ValueError('Duplicate blob identity')
    for number, entry in enumerate(files):
        check()
        if entry['name'] != f'blob-{number:04d}.parquet' or entry['id'] != f'blob-{number:04d}':
            raise ValueError('Unexpected corpus identity or path')
        path = Path(directory) / entry['name']
        if path.is_symlink() or not path.is_file():
            raise ValueError('Corpus entry must be an ordinary file')
        size, hash_value = digest(path)
        if not profile.minimum <= size <= profile.maximum or size != entry['bytes']:
            raise ValueError('Corpus encoded size mismatch')
        if hash_value != entry['xxh3_128']:
            raise ValueError('Corpus integrity mismatch')
        with pq.ParquetFile(path) as parquet:
            if parquet.metadata.num_rows != entry['rows']:
                raise ValueError('Corpus row count mismatch')
            offset = 0
            for batch in parquet.iter_batches(batch_size=ROW_GROUP):
                check()
                if batch.column('ordinal').to_pylist() != list(range(offset, offset + len(batch))):
                    raise ValueError('Parquet row sequence mismatch')
                offset += len(batch)
    return {'profile': profile.name, 'files': len(files),
            'bytes': sum(f['bytes'] for f in files), 'corpus_valid': True,
            'representative_qualified': False}
