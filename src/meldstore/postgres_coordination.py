"""Fail-closed, single-host PostgreSQL/S3 identity and cooperative admission."""

import json
import os
from uuid import uuid4

from .access import Lease, canonical
from .errors import ValidationError
from .postgres_schema import CORE, create, guard, namespace_objects, table, verify
from .s3 import S3Storage, _decode, _encode, _identity

MARKER = "meldstore-postgres.json"
OBJECTS = [
    table("ms_store_binding", "singleton BIGINT PRIMARY KEY CHECK(singleton=1), "
          "binding TEXT NOT NULL"),
    *guard("ms_store_binding_immutable", "ms_store_binding", "UPDATE OR DELETE",
           "TRUE", "Store binding is immutable"),
]


def _read_marker(path):
    try:
        with path.open('rb') as stream:
            data = _decode(stream.read(2049), (
                'version', 'catalog_id', 'coordinator_id', 'storage_id', 'location',
            ))
        if not _identity(data['catalog_id']) or not _identity(data['storage_id']):
            raise ValueError
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValidationError("Missing or invalid PostgreSQL coordinator identity") from exc


def _verify_storage(storage):
    # Re-read both markers; a previously constructed storage object is not proof
    # that its persistent binding is still intact. No payload bytes are read.
    with (storage.root / 'meldstore-s3.json').open('rb') as stream:
        local = _decode(stream.read(2049), (
            'version', 'location', 'storage_id', 'coordinator_id',
        ))
    if local != {
        'version': 1, 'location': storage.location,
        'storage_id': storage.storage_id, 'coordinator_id': storage.coordinator_id,
    } or storage._remote_identity() != {
        'version': 1, 'id': storage.storage_id, 'coordinator_id': storage.coordinator_id,
    }:
        raise ValidationError("S3 identity changed; PostgreSQL admission denied")


def bind(catalog, storage, *, initialize, maintenance):
    catalog.require_idle()
    if catalog.adapter != 'postgres' or not isinstance(storage, S3Storage):
        raise ValidationError("PostgreSQL binding requires explicit S3Storage")
    if type(initialize) is not bool or type(maintenance) is not bool:
        raise ValidationError("initialize and maintenance must be boolean")
    key = canonical(storage.root)
    marker = storage.root / MARKER
    gate = storage.root / 'meldstore-access.sqlite'
    bound = catalog._postgres_binding
    if bound is not None and (initialize or maintenance != catalog._exclusive):
        raise ValidationError("Close and reopen to change PostgreSQL admission mode")
    # Serialize initialization with S3 initialization and all subsequent admissions.
    # Live participants retain a separate shared/exclusive gate for their lifetime.
    init = Lease(storage.root / 'meldstore-init.sqlite', owner=key, exclusive=initialize)
    lease = None
    try:
        _verify_storage(storage)  # Network I/O before any SQL transaction.
        if initialize:
            if marker.exists() or gate.exists():
                raise ValidationError("Initialization requires an unused coordinator binding")
            if set(storage.inventory(max_entries=2)) != {"meldstore-storage.json"}:
                raise ValidationError("First binding requires a fresh S3 prefix")
            identity = {
                'version': 1, 'catalog_id': uuid4().hex,
                'coordinator_id': storage.coordinator_id,
                'storage_id': storage.storage_id, 'location': storage.location,
            }
        else:
            identity = _read_marker(marker)
            if not gate.is_file():
                raise ValidationError("Access gate missing; automatic recreation is forbidden")
        if any(identity[k] != v for k, v in {
            'coordinator_id': storage.coordinator_id,
            'storage_id': storage.storage_id, 'location': storage.location,
        }.items()) or (bound is not None and bound != identity):
            raise ValidationError("PostgreSQL catalog/coordinator/storage binding mismatch")
        encoded = _encode(identity).decode('utf-8')
        if bound is None:
            lease = Lease(gate, owner=identity['catalog_id'], exclusive=maintenance)
        with catalog.transaction(write=initialize) as tx:
            if tx.sql("SELECT current_schema() AS name") != [{'name': 'public'}]:
                raise ValidationError("Bound PostgreSQL stores require a dedicated public schema")
            if initialize:
                # First-time bootstrap must be explicitly quiesced, including
                # standalone SQL users that do not yet have a bound access gate.
                tx.sql("SELECT singleton FROM ms_format FOR UPDATE")
                verify(tx, CORE)
                if 'ms_store_binding' in namespace_objects(tx):
                    raise ValidationError("Database already has a storage binding")
                if tx.sql("SELECT id FROM ms_blobs LIMIT 1") or (
                    'ms_prepared' in namespace_objects(tx)
                    and tx.sql("SELECT token FROM ms_prepared LIMIT 1")
                ):
                    raise ValidationError("First binding requires an empty blob catalog")
                create(tx, OBJECTS)
                # Small local identity write, never a payload/network operation.
                # Retain it after any uncertain commit; never repair by guessing.
                with marker.open('xb') as stream:
                    stream.write(encoded.encode('utf-8'))
                    stream.flush()
                    os.fsync(stream.fileno())
                tx.sql("INSERT INTO ms_store_binding VALUES(1,%s)", (encoded,))
            else:
                verify(tx, OBJECTS)
                if tx.sql("SELECT singleton,binding FROM ms_store_binding") != [
                    {'singleton': 1, 'binding': encoded}
                ]:
                    raise ValidationError("Database does not match the persistent coordinator")
        if lease is not None:
            catalog._leases[key] = lease
            lease = None
        catalog._postgres_binding = json.loads(encoded)
        catalog._exclusive = maintenance
        return identity['catalog_id']
    finally:
        if lease is not None:
            lease.close()
        init.close()


def publish_destination(tx, storage, catalog_id, *, restored=False):
    """Internal final publication after fresh-target transfer/restore verification.

    The owning workflow holds an exclusive gate and has verified every payload.
    Ordinary bind/initialize never invokes this to adopt an occupied destination.
    """
    identity = {'version': 1, 'catalog_id': catalog_id, 'storage_id': storage.storage_id,
                'coordinator_id': storage.coordinator_id, 'location': storage.location}
    encoded = _encode(identity).decode('utf-8')
    if restored:
        verify(tx, OBJECTS)
        # Only the isolated native restore workflow may replace the archived
        # physical binding; logical storage identity and public blob IDs persist.
        tx.sql('DROP TRIGGER ms_store_binding_immutable ON ms_store_binding')
        tx.sql('UPDATE ms_store_binding SET binding=%s WHERE singleton=1', (encoded,))
        trigger = next(obj for obj in OBJECTS if obj.kind == 'trigger')
        tx.sql(trigger.sql)
        verify(tx, OBJECTS)
    else:
        create(tx, OBJECTS)
        tx.sql('INSERT INTO ms_store_binding VALUES(1,%s)', (encoded,))
    with (storage.root / MARKER).open('xb') as stream:
        stream.write(encoded.encode('utf-8'))
        stream.flush()
        os.fsync(stream.fileno())
