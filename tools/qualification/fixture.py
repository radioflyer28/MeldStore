"""Generic application-owned SQL fixture and independent expected results."""

from collections import Counter
from datetime import datetime, timedelta, timezone

from meldstore import BlobSchema, Integer, SQLTransferManifest, TableTransfer, Text, Timestamp

SCHEMA = BlobSchema('workload', {'source_id': Text(required=True), 'config_id': Text(required=True),
                                'start_time': Timestamp(required=True), 'end_time': Timestamp(required=True)},
                    handlers=('file',))
EVOLVED = BlobSchema(SCHEMA.name, {**SCHEMA.fields, 'revision': Integer(required=True)},
                     version=2, handlers=SCHEMA.handlers)
APP = {'id': 'synthetic-workload', 'schema_revision': '1'}
DDL = (
    'CREATE TABLE app_configs(id TEXT PRIMARY KEY,label TEXT NOT NULL)',
    'CREATE TABLE app_intervals(id BIGINT PRIMARY KEY,blob_id TEXT NOT NULL REFERENCES ms_blobs(id),'
    'config_id TEXT NOT NULL REFERENCES app_configs(id),start_time TEXT NOT NULL,end_time TEXT NOT NULL)',
    'CREATE INDEX app_interval_start ON app_intervals(start_time,end_time)',
)


def instant(seconds):
    return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat(
        timespec='microseconds').replace('+00:00', 'Z')


def metadata(entry):
    return {'source_id': entry['source_id'], 'config_id': entry['config_id'],
            'start_time': datetime.fromisoformat(instant(entry['start_offset'])),
            'end_time': datetime.fromisoformat(instant(entry['end_offset']))}


def relationships(manifest):
    files = manifest['files']
    for number in range(manifest['profile']['relationships']):
        entry = files[number % len(files)]
        start = entry['start_offset'] + (number // len(files)) % 120
        yield (number, entry['id'], entry['config_id'], instant(start), instant(start + 5))


def expected_query(manifest, start, end):
    counts = Counter(row[1] for row in relationships(manifest) if row[3] < end and row[4] > start)
    return [{'blob_id': key, 'matches': counts[key]} for key in sorted(counts)]


def query(bind):
    return ('SELECT a.blob_id,COUNT(*) AS matches FROM app_intervals a '
            'JOIN app_configs c ON a.config_id=c.id JOIN ms_blobs b ON b.id=a.blob_id '
            f'WHERE a.start_time<{bind} AND a.end_time>{bind} '
            'GROUP BY a.blob_id ORDER BY a.blob_id')


def verify_application(catalog, manifest):
    configs = catalog.sql('SELECT * FROM app_configs ORDER BY id')
    if configs != [{'id': f'config-{n}', 'label': f'configuration {n}'} for n in range(3)]:
        return False
    # Bounded ID-seek verification of every application row, not just counts.
    expected = iter(relationships(manifest))
    last = -1
    columns = ('id', 'blob_id', 'config_id', 'start_time', 'end_time')
    while True:
        rows = catalog.sql(f'SELECT * FROM app_intervals WHERE id>{last} ORDER BY id LIMIT 1000')
        if not rows:
            return next(expected, None) is None
        for row in rows:
            if tuple(row[key] for key in columns) != next(expected, None):
                return False
        last = rows[-1]['id']


def transfer_manifest(manifest):
    objects = {'app_configs': 'table', 'app_intervals': 'table', 'app_interval_start': 'index'}
    return SQLTransferManifest(
        application=APP, source_objects=objects,
        target_objects={**objects, 'app_configs_pkey': 'index', 'app_intervals_pkey': 'index'},
        ddl=DDL,
        tables=(TableTransfer('app_configs', 'app_configs', {'id': Text(), 'label': Text()}),
                TableTransfer('app_intervals', 'app_intervals',
                              {'id': Integer(), 'blob_id': Text(), 'config_id': Text(),
                               'start_time': Text(), 'end_time': Text()})),
        verify=lambda catalog: verify_application(catalog, manifest),
    )



def transfer_limits(manifest):
    # Explicit workload-sized ceiling; bounded headroom for catalog/migration rows.
    count = len(manifest['files'])
    return {'max_entries': count,
            'max_rows': manifest['profile']['relationships'] + 10 * count + 1000}
