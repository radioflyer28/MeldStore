"""Whole-database native PostgreSQL archives with verified payloads and admission."""

import json
import math
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import backup as artifacts
from . import encodings, lifecycle, migrations
from .access import Lease
from .catalog import Catalog
from .dialect import POSTGRES
from .errors import ConflictError, IntegrityError, ValidationError
from .maintenance import _constraint_errors
from .postgres_coordination import OBJECTS as BINDING
from .postgres_coordination import publish_destination
from .postgres_extensions import PAYLOAD, publication_guard
from .postgres_migrations import verify_schema
from .postgres_schema import CORE, verify
from .postgres_transfer import _fresh, _fresh_s3
from .s3 import S3Storage
from .storage import KEY_PATTERN, LocalStorage


class PostgresArchiveTools:
    """Explicit trusted PostgreSQL 17 client tools; secrets stay in child env.

    prefix can select an operator-controlled process wrapper (for example the
    Docker qualification runner). It is an argv sequence, never a shell command.
    Its connection must address the same database as the Catalog; exported
    snapshots and a restore nonce verify that before archive/restore writes.
    """

    def __init__(self, dsn, *, prefix=(), pg_dump='pg_dump', pg_restore='pg_restore',
                 psql='psql', timeout=300):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValidationError('Archive timeout must be positive and finite')
        if not isinstance(prefix, (tuple, list)) or any(not isinstance(s, str) or not s for s in prefix):
            raise ValidationError('Tool prefix must be an explicit argv sequence')
        if any(not isinstance(s, str) or not s for s in (pg_dump, pg_restore, psql)):
            raise ValidationError('Supply explicit PostgreSQL client executable names')
        try:
            from psycopg.conninfo import conninfo_to_dict
        except ImportError:
            raise ValidationError("Install meldstore[postgres] to use PostgreSQL archive tools") from None
        try:
            fields = conninfo_to_dict(dsn)
        except Exception:
            raise ValidationError('Invalid archive-tool PostgreSQL configuration') from None
        if not (fields.get('host') or fields.get('hostaddr')) or not all(
            fields.get(k) for k in ('dbname', 'user')
        ) or 'service' in fields:
            raise ValidationError('Archive tools require explicit host, database and user')
        environment_names = {
            'host': 'PGHOST', 'hostaddr': 'PGHOSTADDR', 'port': 'PGPORT', 'dbname': 'PGDATABASE',
            'user': 'PGUSER', 'password': 'PGPASSWORD', 'passfile': 'PGPASSFILE',
            'connect_timeout': 'PGCONNECT_TIMEOUT', 'options': 'PGOPTIONS',
            'application_name': 'PGAPPNAME', 'sslmode': 'PGSSLMODE', 'sslcert': 'PGSSLCERT',
            'sslkey': 'PGSSLKEY', 'sslrootcert': 'PGSSLROOTCERT', 'sslcrl': 'PGSSLCRL',
            'sslcrldir': 'PGSSLCRLDIR', 'sslpassword': 'PGSSLPASSWORD',
            'target_session_attrs': 'PGTARGETSESSIONATTRS', 'channel_binding': 'PGCHANNELBINDING',
            'gssencmode': 'PGGSSENCMODE', 'krbsrvname': 'PGKRBSRVNAME',
        }
        if set(fields) - environment_names.keys():
            raise ValidationError('Archive tools received an unsupported explicit connection option')
        self._env = {environment_names[k]: v for k, v in fields.items()}
        self._prefix = tuple(prefix)
        self._commands = {'dump': pg_dump, 'restore': pg_restore, 'sql': psql}
        self._timeout = timeout

    def __repr__(self):
        return '<PostgresArchiveTools; connection details redacted>'

    def _run(self, kind, args, *, stdin=None, stdout=subprocess.PIPE):
        environment = {k: v for k, v in os.environ.items() if not k.startswith('PG')}
        environment.update(self._env)
        try:
            result = subprocess.run([*self._prefix, self._commands[kind], *args],
                                    env=environment, stdin=stdin, stdout=stdout,
                                    stderr=subprocess.PIPE, timeout=self._timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            raise ValidationError(f'PostgreSQL {kind} tool failed (detail redacted)') from None
        if result.returncode:
            raise ValidationError(f'PostgreSQL {kind} tool failed (detail redacted)')
        return result.stdout

    def versions(self):
        result = {}
        for kind in self._commands:
            value = self._run(kind, ['--version']).decode('utf-8').strip()
            if not re.fullmatch(r'(?:pg_dump|pg_restore|psql) \(PostgreSQL\) 17\.[^\r\n]+', value):
                raise ValidationError('PostgreSQL 17 client tools are required')
            result[kind] = value
        return result

    def dump(self, destination, snapshot):
        if not isinstance(snapshot, str) or not re.fullmatch(r'[0-9A-Fa-f-]+', snapshot):
            raise ValidationError('Invalid exported PostgreSQL snapshot')
        with Path(destination).open('xb') as output:
            self._run('dump', ['--format=custom', '--no-owner', '--no-privileges',
                               '--snapshot=' + snapshot], stdout=output)
            output.flush()
            os.fsync(output.fileno())

    def verify_target(self, catalog):
        nonce = uuid4().hex
        catalog.sql('CREATE TABLE ms_restore_probe(token TEXT PRIMARY KEY)')
        catalog.sql('INSERT INTO ms_restore_probe VALUES(%s)', (nonce,))
        observed = self._run('sql', ['--no-psqlrc', '--tuples-only', '--no-align',
                                     '--set=ON_ERROR_STOP=1', '--command',
                                     'SELECT token FROM public.ms_restore_probe'])
        if observed.decode('utf-8').strip() != nonce:
            raise ValidationError('Archive tools do not address the fresh target catalog')
        catalog.sql('DROP TABLE ms_restore_probe')

    def restore(self, source):
        with Path(source).open('rb') as incoming:
            self._run('restore', ['--single-transaction', '--exit-on-error', '--no-owner',
                                  '--no-privileges', '--dbname', self._env['PGDATABASE']],
                      stdin=incoming, stdout=subprocess.DEVNULL)


def _entries(tx, storage_id, max_entries):
    verify(tx, CORE)
    verify(tx, PAYLOAD)
    if 'ms_gc_pending' in lifecycle._objects(tx):
        from .postgres_extensions import GC_INDEX

        verify(tx, [GC_INDEX])
    if tx.sql('SELECT * FROM ms_format') != [{'singleton': 1, 'version': 1}] or tx.sql(
        'SELECT version FROM ms_payload_format'
    ) != [{'version': 1}]:
        raise IntegrityError('Unsupported PostgreSQL catalog format')
    lifecycle.verify(tx)
    encodings.verify(tx)
    if _constraint_errors(tx, validate=False):
        raise IntegrityError('Catalog has unvalidated constraints or disabled triggers')
    if (tx.sql("SELECT id FROM ms_blobs WHERE state!='ready' LIMIT 1")
        or tx.sql("SELECT object_key FROM ms_gc WHERE state!='done' LIMIT 1")
        or tx.sql("""SELECT token FROM ms_prepared p WHERE
            NOT EXISTS(SELECT 1 FROM ms_objects o WHERE o.token=p.token)
            AND NOT EXISTS(SELECT 1 FROM ms_gc g WHERE g.token=p.token AND g.state='done') LIMIT 1""")):
        raise ConflictError('Resolve unfinished payload/publication/cleanup work before archive')
    if 'ms_migrations' in lifecycle._objects(tx) and tx.sql(
        "SELECT name FROM ms_migrations WHERE state!='complete' LIMIT 1"
    ):
        raise ConflictError('Finish metadata migrations before archive')
    if tx.sql('SELECT token FROM ms_prepared WHERE storage_id!=%s LIMIT 1', (storage_id,)) or tx.sql(
        'SELECT object_key FROM ms_gc WHERE storage_id!=%s LIMIT 1', (storage_id,)
    ):
        raise IntegrityError('Archive requires one bound logical storage root')
    if tx.sql('SELECT o.token FROM ms_objects o JOIN ms_gc g ON g.token=o.token LIMIT 1'):
        raise IntegrityError('Live payload is queued for cleanup')
    if tx.sql("""SELECT b.id FROM ms_blobs b JOIN ms_objects o ON o.blob_id=b.id
        JOIN ms_prepared p ON p.token=o.token WHERE p.schema_name!=b.schema_name
        OR p.schema_version>b.schema_version LIMIT 1"""):
        raise IntegrityError('Payload schema association differs')
    declarations = tx.sql('SELECT * FROM ms_schemas ORDER BY name,version LIMIT %s', (max_entries + 1,))
    if len(declarations) > max_entries:
        raise ValidationError('Schema count exceeds max_entries')
    for row in declarations:
        schema = migrations.decode(row['definition'])
        if (schema.name, schema.version, POSTGRES.table(schema), schema.definition) != (
            row['name'], row['version'], row['table_name'], row['definition']
        ):
            raise IntegrityError('Invalid PostgreSQL schema declaration')
        if not tx.sql('SELECT 1 FROM ms_payload_schemas WHERE schema_name=%s AND schema_version=%s',
                      (schema.name, schema.version)) or not tx.sql(
            'SELECT 1 FROM ms_lifecycle_schemas WHERE schema_name=%s', (schema.name,)
        ):
            raise IntegrityError('Schema is not fully enrolled for payload/lifecycle operations')
        verify_schema(tx, schema.name)
        verify(tx, publication_guard(schema, evolved=migrations.evolved(tx, schema.name)))
        name = POSTGRES.quote(POSTGRES.table(schema))
        values = tx.sql(f'SELECT * FROM {name} WHERE schema_version=%s LIMIT %s',
                        (schema.version, max_entries + 1))
        if len(values) > max_entries:
            raise ValidationError('Metadata row count exceeds max_entries')
        for values_row in values:
            for field_name, field in schema.fields.items():
                field.from_sql(values_row[field_name])
        if tx.sql(f"""SELECT b.id FROM ms_blobs b LEFT JOIN {name} m ON m.id=b.id
                WHERE b.schema_name=%s AND (m.id IS NULL OR m.schema_version!=b.schema_version)
                LIMIT 1""", (schema.name,)):
            raise IntegrityError('Missing or inconsistent blob metadata')
    entries = tx.sql('''SELECT b.id,p.token,p.storage_id,p.object_key,p.byte_size,p.digest
        FROM ms_blobs b LEFT JOIN ms_objects o ON o.blob_id=b.id
        LEFT JOIN ms_prepared p ON p.token=o.token ORDER BY b.id LIMIT %s''', (max_entries + 1,))
    if len(entries) > max_entries:
        raise ValidationError('Object count exceeds max_entries')
    for entry in entries:
        if entry['storage_id'] != storage_id or not isinstance(entry['object_key'], str) or not KEY_PATTERN.fullmatch(entry['object_key']):
            raise IntegrityError('Missing or inconsistent payload descriptor')
    return entries


def _inventory(tx):
    # Whole-database coverage, including application schemas outside public.
    objects = tx.sql("""SELECT n.nspname AS schema,c.relname AS name,c.relkind::text AS kind
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname NOT LIKE 'pg_%%' AND n.nspname!='information_schema'
        UNION ALL SELECT n.nspname,p.proname||'('||pg_get_function_identity_arguments(p.oid)||')','function'
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname NOT LIKE 'pg_%%' AND n.nspname!='information_schema'
        UNION ALL SELECT n.nspname,c.relname||'.'||t.tgname,'trigger'
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE NOT t.tgisinternal AND n.nspname NOT LIKE 'pg_%%'
        ORDER BY 1,2,3""")
    counts = []
    for obj in objects:
        if obj['kind'] == 'r':
            name = POSTGRES.quote(obj['schema']) + '.' + POSTGRES.quote(obj['name'])
            counts.append({**obj, 'rows': tx.sql(f'SELECT COUNT(*) AS n FROM {name}')[0]['n']})
    return {'objects': objects, 'tables': counts}


def backup(store, destination, *, application, tools, max_entries=10000):
    store.catalog.require_maintenance(store.storage.root)
    artifacts._bound(max_entries)
    application = artifacts._application(application)
    if not isinstance(tools, PostgresArchiveTools):
        raise ValidationError('Supply explicit PostgreSQL native archive tools')
    versions = tools.versions()
    destination = artifacts._destination(destination, store.storage.root)
    with store.catalog.transaction(write=False) as tx:
        verify(tx, BINDING)
        entries = _entries(tx, store.storage.storage_id, max_entries)
        inventory = _inventory(tx)
        version = int(tx.sql("SELECT current_setting('server_version_num') AS v")[0]['v'])
        if version // 10000 != 17:
            raise ValidationError('This archive deployment requires PostgreSQL 17')
        snapshot = tx.sql('SELECT pg_export_snapshot() AS snapshot')[0]['snapshot']
        destination.mkdir()
        tools.dump(destination / 'catalog.pgcustom', snapshot)
    artifacts._checkpoint('postgres_backup_catalog')
    payloads = LocalStorage._replica(destination / 'storage', store.storage.storage_id)
    artifacts._copy_payloads(store.storage, payloads, entries, 'postgres_backup_object')
    manifest = {
        'format': 'meldstore-backup', 'version': 2, 'catalog_backend': 'postgresql',
        'archive_format': 'custom', 'hash_algorithm': 'xxh3_128',
        'created_at': datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z'),
        'application': application, 'storage_id': store.storage.storage_id,
        'catalog_id': store.catalog._postgres_binding['catalog_id'],
        'catalog': artifacts._descriptor(destination / 'catalog.pgcustom'),
        'identity': artifacts._descriptor(payloads.root / 'meldstore-storage.json'),
        'objects': entries, 'sql_inventory': inventory,
        'server_version': version, 'tools': versions,
    }
    artifacts._checkpoint('postgres_backup_manifest')
    artifacts._write_json(destination / 'manifest.json', manifest)
    return manifest


def _manifest(source, max_entries):
    path = artifacts._path(source / 'manifest.json')
    if not path.is_file() or path.stat().st_size > 1048576 + max_entries * 4096:
        raise IntegrityError('Missing, incomplete or oversized PostgreSQL manifest')

    def distinct(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate manifest key')
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=distinct)
        if set(value) != {'format', 'version', 'catalog_backend', 'archive_format', 'hash_algorithm',
                          'created_at', 'application', 'storage_id', 'catalog_id', 'catalog',
                          'identity', 'objects', 'sql_inventory', 'server_version', 'tools'}:
            raise ValueError
        if (value['format'], value['version'], value['catalog_backend'], value['archive_format'],
            value['hash_algorithm']) != ('meldstore-backup', 2, 'postgresql', 'custom', 'xxh3_128'):
            raise ValueError
        if type(value['version']) is not int or type(value['server_version']) is not int or value['server_version'] // 10000 != 17:
            raise ValueError
        for key in ('storage_id', 'catalog_id'):
            if not isinstance(value[key], str) or not re.fullmatch('[0-9a-f]{32}', value[key]):
                raise ValueError
        artifacts._application(value['application'])
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z', value['created_at']):
            raise ValueError
        instant = datetime.fromisoformat(value['created_at'])
        if instant.isoformat(timespec='microseconds').replace('+00:00', 'Z') != value['created_at']:
            raise ValueError
        for descriptor in (value['catalog'], value['identity']):
            if set(descriptor) != {'byte_size', 'digest'} or type(descriptor['byte_size']) is not int or descriptor['byte_size'] < 0 or not re.fullmatch('[0-9a-f]{32}', descriptor['digest']):
                raise ValueError
        if not isinstance(value['objects'], list) or len(value['objects']) > max_entries:
            raise ValueError
        ids, keys = set(), set()
        for entry in value['objects']:
            if set(entry) != {'id', 'token', 'storage_id', 'object_key', 'byte_size', 'digest'}:
                raise ValueError
            if any(type(entry[k]) is not str for k in ('id', 'token', 'storage_id', 'object_key', 'digest')):
                raise ValueError
            if entry['storage_id'] != value['storage_id'] or not KEY_PATTERN.fullmatch(entry['object_key']):
                raise ValueError
            if type(entry['byte_size']) is not int or entry['byte_size'] < 0 or not re.fullmatch('[0-9a-f]{32}', entry['digest']):
                raise ValueError
            if entry['id'] in ids or entry['object_key'] in keys:
                raise ValueError
            ids.add(entry['id'])
            keys.add(entry['object_key'])
        if set(value['sql_inventory']) != {'objects', 'tables'} or set(value['tools']) != {'dump', 'restore', 'sql'}:
            raise ValueError
        for key in ('objects', 'tables'):
            items = value['sql_inventory'][key]
            if not isinstance(items, list):
                raise ValueError
            for item in items:
                expected = {'schema', 'name', 'kind'} | ({'rows'} if key == 'tables' else set())
                if set(item) != expected or any(type(item[k]) is not str or not item[k]
                                                for k in ('schema', 'name', 'kind')):
                    raise ValueError
                if key == 'tables' and (type(item['rows']) is not int or item['rows'] < 0):
                    raise ValueError
        for tool in value['tools'].values():
            if not isinstance(tool, str) or not re.fullmatch(r'(?:pg_dump|pg_restore|psql) \(PostgreSQL\) 17\.[^\r\n]+', tool):
                raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, ValidationError):
        raise IntegrityError('Invalid PostgreSQL backup manifest') from None
    return value


def restore_postgres_backup(source, dsn, destination, *, tools, verify_application,
                            max_entries=10000, **s3_options):
    """Operator-assisted trusted native restore; verify before final admission."""
    artifacts._bound(max_entries)
    source = artifacts._path(Path(source))
    destination = artifacts._destination(destination, source)
    if not isinstance(tools, PostgresArchiveTools) or not callable(verify_application):
        raise ValidationError('Restore requires explicit native tools and application verification')
    tools.versions()
    manifest = _manifest(source, max_entries)
    artifacts._verify_file(source / 'catalog.pgcustom', manifest['catalog'])
    artifacts._verify_file(source / 'storage/meldstore-storage.json', manifest['identity'])
    payloads = LocalStorage(source / 'storage')
    if payloads.storage_id != manifest['storage_id']:
        raise IntegrityError('Backup payload identity differs')
    # Verify the entire artifact before any target mutation, including native restore.
    for entry in manifest['objects']:
        payloads.cleanup_key(entry['object_key'])
        with payloads.materialize(entry['object_key'], byte_size=entry['byte_size'], digest=entry['digest']):
            pass
    with Catalog.connect_postgres(dsn) as target:
        with target.transaction(write=False) as tx:
            _fresh(tx)
        _fresh_s3(s3_options)
        tools.verify_target(target)
        storage = S3Storage(coordination_directory=destination, _fresh=True,
                            _storage_id=manifest['storage_id'], **s3_options)
        catalog_id = uuid4().hex
        lease = Lease(storage.root / 'meldstore-access.sqlite', owner=catalog_id, exclusive=True)
        try:
            tools.restore(source / 'catalog.pgcustom')
            artifacts._checkpoint('postgres_restore_catalog')
            with target.transaction(write=False) as tx:
                verify(tx, BINDING)
                archived = json.loads(tx.sql('SELECT binding FROM ms_store_binding')[0]['binding'])
                if archived['catalog_id'] != manifest['catalog_id'] or archived['storage_id'] != manifest['storage_id']:
                    raise IntegrityError('Archive catalog identity differs from manifest')
                if _entries(tx, storage.storage_id, max_entries) != manifest['objects'] or _inventory(tx) != manifest['sql_inventory']:
                    raise IntegrityError('Restored SQL differs from archive manifest')
                if verify_application(tx) is not True:
                    raise IntegrityError('Application restore verification failed')
            artifacts._copy_payloads(payloads, storage, manifest['objects'], 'postgres_restore_object')
            with target.transaction() as tx:
                publish_destination(tx, storage, catalog_id, restored=True)
                tx.validate_constraints()
                artifacts._checkpoint('postgres_restore_ready')
        finally:
            lease.close()
    with Catalog.connect_postgres(dsn, storage=storage):
        pass
    return {'catalog_id': catalog_id, 'storage_id': storage.storage_id,
            'coordination_directory': str(storage.root), 'application': manifest['application'],
            'destination_ready': True}
