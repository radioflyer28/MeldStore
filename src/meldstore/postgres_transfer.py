"""Explicit offline SQLite cutover with application-owned SQL mappings."""

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

import obstore
from obstore.store import S3Store

from . import backup as artifacts
from . import encodings, installation, lifecycle, migrations, payload_catalog
from .catalog import Catalog
from .dialect import POSTGRES
from .errors import ConflictError, IntegrityError, ValidationError
from .postgres_extensions import ENCODING, LIFECYCLE, PAYLOAD, publication_guard, retirement_guard
from .postgres_migrations import DDL as MIGRATIONS
from .postgres_migrations import layout
from .postgres_schema import CORE, inspect, namespace_objects, schema_objects, verify
from .schema import Field, identifier


@dataclass(frozen=True)
class TableTransfer:
    """One ordinary source table, explicit target fields and optional row mapping."""

    source: str
    target: str
    fields: dict[str, Field]
    transform: Callable | None = None


@dataclass(frozen=True)
class SQLTransferManifest:
    """Trusted application SQL, complete source inventory and required verification.

    source_objects/target_objects map exact SQL object names to kinds (table,
    index, view, trigger, function, sequence). SQLite source kinds exclude implicit
    autoindexes. tables are inserted in supplied order. verify(tx) must return True.
    """

    application: dict
    source_objects: dict[str, str]
    target_objects: dict[str, str]
    ddl: tuple[str, ...]
    tables: tuple[TableTransfer, ...]
    verify: Callable


class _DryRun(Exception):
    pass


def _portable(value):
    if value is None or type(value) in (str, int, float, bytes):
        POSTGRES.value(value)
        if type(value) is int and not -(2**63) <= value < 2**63:
            raise ValidationError('Transfer integer is outside int64')
        if type(value) is float and not math.isfinite(value):
            raise ValidationError('Transfer float must be finite')
        return value
    raise ValidationError('Transfer requires explicit portable scalar values')


def _fresh(tx):
    if tx.sql('SELECT current_schema() AS name') != [{'name': 'public'}]:
        raise ValidationError('Fresh PostgreSQL destination must use public schema')
    if namespace_objects(tx) or tx.sql("""SELECT nspname FROM pg_namespace
            WHERE nspname NOT LIKE 'pg_%%' AND nspname NOT IN ('public','information_schema')"""):
        raise ConflictError('Destination database must be fresh and dedicated')


def _application_objects(tx):
    rows = tx.sql("""SELECT c.relname AS name, CASE c.relkind
        WHEN 'r' THEN 'table' WHEN 'p' THEN 'table' WHEN 'v' THEN 'view'
        WHEN 'm' THEN 'view' WHEN 'i' THEN 'index' WHEN 'S' THEN 'sequence'
        ELSE 'unsupported' END AS kind FROM pg_class c
        WHERE c.relnamespace=current_schema()::regnamespace
        UNION ALL SELECT p.proname,'function' FROM pg_proc p
        WHERE p.pronamespace=current_schema()::regnamespace
        UNION ALL SELECT t.tgname,'trigger' FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        WHERE c.relnamespace=current_schema()::regnamespace AND NOT t.tgisinternal""")
    return {r['name']: r['kind'] for r in rows if not r['name'].startswith('ms_')}


def _plan(snapshot, storage_id, manifest, max_entries, max_rows):
    if not isinstance(manifest, SQLTransferManifest) or not callable(manifest.verify):
        raise ValidationError('Supply an explicit SQLTransferManifest and verification callback')
    application = artifacts._application(manifest.application)
    if not isinstance(manifest.ddl, (tuple, list)) or any(
        not isinstance(sql, str) or not sql.strip() for sql in manifest.ddl
    ):
        raise ValidationError('Application DDL must be an ordered sequence of SQL statements')
    for inventory in (manifest.source_objects, manifest.target_objects):
        if not isinstance(inventory, dict):
            raise ValidationError('Application SQL object inventory must be a mapping')
        for name, kind in inventory.items():
            identifier(name)
            POSTGRES.quote(name)
            if name.lower().startswith(('ms_', 'sqlite_')) or kind not in {
                'table', 'index', 'view', 'trigger', 'function', 'sequence'
            }:
                raise ValidationError('Invalid application-owned SQL object mapping')
    with artifacts._database(snapshot) as source:
        entries = artifacts._entries(source, storage_id, max_entries)
        tx = artifacts._Query(source)
        inventory = source.execute(
            "SELECT name,type FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        external = {n: k for n, k in inventory if not n.startswith('ms_')}
        if external != manifest.source_objects:
            raise ValidationError('Application manifest omits or misidentifies source SQL objects')
        declarations = [migrations.decode(r['definition']) for r in tx.sql(
            'SELECT definition FROM ms_schemas ORDER BY name,version')]
        by_name = defaultdict(list)
        for schema in declarations:
            by_name[schema.name].append(schema)
            for name in schema.fields:
                POSTGRES.quote(name)
        owned = set(installation.CORE) | set(payload_catalog.DDL) | set(encodings.DDL) | set(lifecycle.DDL)
        owned |= set(migrations.DDL) | {'ms_gc_pending'}
        objects = list(CORE) + list(PAYLOAD) + list(ENCODING) + list(LIFECYCLE)
        if 'ms_migrations' in dict(inventory):
            objects += MIGRATIONS
        if 'ms_gc_pending' in dict(inventory):
            from .postgres_extensions import GC_INDEX

            objects.append(GC_INDEX)
        for schemas in by_name.values():
            schema = schemas[-1]
            evolved = migrations.evolved(tx, schema.name)
            owned |= set(migrations.layout(schemas) if evolved else installation._schema_objects(schema))
            owned.add(payload_catalog.guard(schema, evolved=evolved)[0])
            owned.add(lifecycle.schema_guard(schema)[0])
            objects += layout(schemas) if evolved else schema_objects(schema)
            objects += publication_guard(schema, evolved=evolved) + retirement_guard(schema)
        if {n for n, _ in inventory if n.startswith('ms_')} - owned:
            raise ValidationError('Unmapped reserved SQL objects in source catalog')
        names = [obj.name for obj in objects]
        if len(set(names)) != len(names):
            raise ValidationError('Mapped PostgreSQL object names collide')
        rows = {}
        budget = max_rows
        for name, kind in inventory:
            if kind != 'table' or not name.startswith('ms_'):
                continue
            table_rows = tx.sql(f'SELECT * FROM {artifacts._sql_name(name)} LIMIT ?', (budget + 1,))
            budget -= len(table_rows)
            if budget < 0:
                raise ValidationError('Transfer metadata exceeds explicit max_rows')
            for row in table_rows:
                for value in row.values():
                    _portable(value)
            rows[name] = table_rows
        for schemas in by_name.values():
            for row in rows[schemas[-1].table_name]:
                schema = next(s for s in schemas if s.version == row['schema_version'])
                for name, field in schema.fields.items():
                    field.from_sql(row[name])
            mapped = POSTGRES.table(schemas[-1])
            rows[mapped] = rows.pop(schemas[-1].table_name)
        for row in rows['ms_schemas']:
            row['table_name'] = POSTGRES.table(migrations.decode(row['definition']))
        rows.setdefault('ms_encoding_format', [{'version': 1}])
        rows.setdefault('ms_encodings', [])
        mappings = list(manifest.tables)
        if {m.source for m in mappings} != {n for n, k in external.items() if k == 'table'} or (
            len({m.source for m in mappings}) != len(mappings)
        ):
            raise ValidationError('Every application source table needs exactly one mapping')
        if len({m.target for m in mappings}) != len(mappings):
            raise ValidationError('Application table targets must be distinct')
        app_rows = []
        for mapping in mappings:
            if not isinstance(mapping, TableTransfer) or manifest.target_objects.get(mapping.target) != 'table':
                raise ValidationError('Missing declared target table')
            if not mapping.fields or any(not isinstance(f, Field) for f in mapping.fields.values()):
                raise ValidationError('Table mapping requires explicit target scalar fields')
            for name in mapping.fields:
                POSTGRES.quote(identifier(name))
            data = tx.sql(f'SELECT * FROM {artifacts._sql_name(mapping.source)} LIMIT ?', (budget + 1,))
            budget -= len(data)
            if budget < 0:
                raise ValidationError('Transfer metadata exceeds explicit max_rows')
            converted = []
            for row in data:
                value = mapping.transform(dict(row)) if mapping.transform else row
                if not isinstance(value, dict) or set(value) != set(mapping.fields):
                    raise ValidationError('Application row mapping must produce exact target fields')
                converted.append({name: _portable(field.normalize(field.from_sql(value[name])))
                                  for name, field in mapping.fields.items()})
            app_rows.append((mapping.target, converted))
    return dict(objects=objects, rows=rows, app_rows=app_rows, entries=entries, application=application)


def _insert(tx, name, rows):
    if not rows:
        return
    columns = list(rows[0])
    sql = (f'INSERT INTO {POSTGRES.quote(name)} (' + ','.join(POSTGRES.quote(c) for c in columns)
           + ') VALUES (' + ','.join('%s' for _ in columns) + ')')
    tx.sql_many(sql, (tuple(row[c] for c in columns) for row in rows))


def _load(tx, plan, manifest):
    _fresh(tx)
    objects = plan['objects']
    for obj in objects:
        if obj.kind == 'table':
            tx.sql(obj.sql.replace('%', '%%'))
    # Tables are listed in dependency order. Guards are installed after restoring
    # original versions/states, never disabled on an admitted existing store.
    for obj in objects:
        if obj.kind == 'table' and obj.name != 'ms_pg_layout':
            _insert(tx, obj.name, plan['rows'].get(obj.name, []))
    for obj in objects:
        if obj.kind != 'table':
            tx.sql(obj.sql.replace('%', '%%'))
    for obj in objects:
        tx.sql('INSERT INTO ms_pg_layout VALUES(%s,%s,%s)',
               (obj.name, obj.sql, json.dumps(inspect(tx, obj), sort_keys=True)))
    for sql in manifest.ddl:
        tx.sql(sql)
    for name, rows in plan['app_rows']:
        _insert(tx, name, rows)
        if tx.sql(f'SELECT COUNT(*) AS n FROM {POSTGRES.quote(name)}') != [{'n': len(rows)}]:
            raise IntegrityError('Application transfer row count differs')
    actual = _application_objects(tx)
    # Implicit PostgreSQL PK/unique indexes must also be explicitly inventoried.
    if actual != manifest.target_objects:
        raise ValidationError('Target SQL object inventory differs from application declaration')
    verify(tx, objects)
    if manifest.verify(tx) is not True:
        raise IntegrityError('Application verification must explicitly return True')
    tx.validate_constraints()


def preflight_sqlite_to_postgres(store, dsn, *, manifest, max_entries=10000, max_rows=100000):
    """Check source mappings/values and rollback-only target import; no payload I/O."""
    store.catalog.require_maintenance(store.storage.root)
    if store.catalog.adapter == 'postgres':
        raise ValidationError('Cutover source must be SQLite')
    artifacts._bound(max_entries)
    artifacts._bound(max_rows)
    with Catalog.connect_postgres(dsn) as target, TemporaryDirectory(prefix='meldstore-preflight-') as temp:
        with target.transaction(write=False) as tx:
            _fresh(tx)
        snapshot = Path(temp) / 'catalog.sqlite'
        store.catalog.snapshot(snapshot)
        plan = _plan(snapshot, store.storage.storage_id, manifest, max_entries, max_rows)
        try:
            with target.transaction() as tx:
                _load(tx, plan, manifest)
                raise _DryRun
        except _DryRun:
            pass
        return {'application': plan['application'], 'blobs': len(plan['entries']),
                'application_rows': {name: len(rows) for name, rows in plan['app_rows']},
                'destination_ready': False}


def _fresh_s3(options):
    from .s3 import _settings

    allowed = {'bucket', 'prefix', 'config', 'client_options', 'retry_config',
               'credential_provider', 'staging_directory'}
    if set(options) - allowed or not {'bucket', 'prefix', 'config'} <= set(options):
        raise ValidationError('Explicit fresh S3 options required; transfer owns identity/directory')
    settings = _settings(options['bucket'], options['prefix'], options['config'])
    backend = S3Store(options['bucket'], prefix=options['prefix'], config=settings,
                      **{k: v for k, v in options.items() if k in {
                          'client_options', 'retry_config', 'credential_provider'}})
    if any(batch for batch in obstore.list(backend, chunk_size=1)):
        raise ConflictError('Destination S3 prefix must be empty')


def transfer_sqlite_to_postgres(store, dsn, destination, *, manifest,
                               max_entries=10000, max_rows=100000, **s3_options):
    """Copy to fresh PostgreSQL/S3 targets; explicitly publish after verification.

    Source remains quiesced and unchanged. Failed targets are retained; retry at
    fresh targets rather than adopting partial state. This does not switch clients.
    """
    from uuid import uuid4

    from .access import Lease
    from .postgres_coordination import publish_destination
    from .s3 import S3Storage

    store.catalog.require_maintenance(store.storage.root)
    if store.catalog.adapter == 'postgres':
        raise ValidationError('Cutover source must be SQLite')
    artifacts._bound(max_entries)
    artifacts._bound(max_rows)
    destination = artifacts._destination(destination, store.storage.root, store.catalog.path)
    with Catalog.connect_postgres(dsn) as target, TemporaryDirectory(prefix='meldstore-cutover-') as temp:
        with target.transaction(write=False) as tx:
            _fresh(tx)
        snapshot = Path(temp) / 'catalog.sqlite'
        store.catalog.snapshot(snapshot)
        plan = _plan(snapshot, store.storage.storage_id, manifest, max_entries, max_rows)
        _fresh_s3(s3_options)
        try:
            with target.transaction() as tx:
                _load(tx, plan, manifest)
                raise _DryRun
        except _DryRun:
            pass
        storage = S3Storage(coordination_directory=destination, _fresh=True,
                            _storage_id=store.storage.storage_id, **s3_options)
        catalog_id = uuid4().hex
        lease = Lease(storage.root / 'meldstore-access.sqlite', owner=catalog_id, exclusive=True)
        try:
            artifacts._copy_payloads(store.storage, storage, plan['entries'], 'transfer_object')
            artifacts._checkpoint('transfer_catalog')
            with target.transaction() as tx:
                _load(tx, plan, manifest)
                publish_destination(tx, storage, catalog_id)
                artifacts._checkpoint('transfer_ready')
        finally:
            lease.close()
    # An uncertain commit never reaches this point; caller must reopen/inspect.
    with Catalog.connect_postgres(dsn, storage=storage):
        pass
    return {'catalog_id': catalog_id, 'storage_id': storage.storage_id,
            'coordination_directory': str(storage.root), 'application': plan['application'],
            'blobs': len(plan['entries']), 'destination_ready': True}
