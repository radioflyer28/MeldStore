"""Explicit file preparation, SQL publication and verified temporary materialization."""

import json
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import wraps
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from . import encodings, lifecycle, migrations, payload_catalog, query
from .errors import (
    ConflictError,
    IntegrityError,
    NotFoundError,
    SchemaConflictError,
    ValidationError,
)
from .handlers import HandlerRegistry, UnsupportedHandlerError, descriptor_json
from .schema import BlobSchema, identifier
from .schema import quote_identifier as q
from .storage import LocalStorage, hash_file


@dataclass(frozen=True)
class PreparedFile:
    token: str
    storage_id: str
    object_key: str
    byte_size: int
    digest: str
    hash_algorithm: str
    handler_id: str
    handler_version: int
    schema_name: str
    schema_version: int


@dataclass(frozen=True)
class PreparedValue(PreparedFile):
    """Physical-file token plus an immutable logical encoding declaration."""

    encoding_id: str
    encoding_version: int
    descriptor: str


def _payload_operation(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        self.catalog.require_idle()
        self.catalog._readers += 1
        try:
            return method(self, *args, **kwargs)
        finally:
            self.catalog._readers -= 1

    return wrapped


class Store:
    """Borrows a caller-owned Catalog with local or S3 payload storage."""

    def __init__(self, catalog, storage: LocalStorage, *, handlers=None):
        self.catalog = catalog
        self.storage = storage
        self.handlers = HandlerRegistry() if handlers is None else handlers
        if not isinstance(self.handlers, HandlerRegistry):
            raise ValidationError("handlers must be a HandlerRegistry")
        catalog.bind_storage(storage.root)

    def install_schema(self, schema):
        with self.catalog.transaction() as tx:
            table = self.catalog.install_schema(schema, tx=tx)
            payload_catalog.install(schema, tx)
            encodings.install(tx)
            lifecycle.install(tx)
            lifecycle.install_schema(schema, tx)
            return table

    def install_lifecycle(self):
        """Explicit additive S04 upgrade for an existing S02/S03 catalog."""
        with self.catalog.transaction() as tx:
            lifecycle.install(tx)
            for row in tx.sql("SELECT definition FROM ms_schemas ORDER BY name,version"):
                lifecycle.install_schema(migrations.decode(row["definition"]), tx)

    def install_query_indexes(self):
        """Explicit additive S06a upgrade; no table rebuild or automatic ANALYZE."""
        from .installation import _objects, _verify

        ddl = {
            "ms_gc_pending": "CREATE INDEX ms_gc_pending ON ms_gc(storage_id,object_key) WHERE state='pending'"
        }
        with self.catalog.transaction() as tx:
            lifecycle.verify(tx)
            objects = _objects(tx)
            added = []
            for name, sql in ddl.items():
                if name in objects:
                    _verify(objects, {name: sql})
                else:
                    tx.sql(sql)
                    added.append(name)
            return added

    def _schema(self, schema, tx, *, write=False):
        if not isinstance(schema, BlobSchema):
            raise ValidationError("Supply the installed BlobSchema declaration")
        rows = tx.sql(
            "SELECT definition FROM ms_schemas WHERE name=? AND version=?",
            (schema.name, schema.version),
        )
        if rows != [{"definition": schema.definition}]:
            raise SchemaConflictError("Schema is not installed or its definition differs")
        if write:
            migrations.writable(tx, schema)
        name, sql = payload_catalog.guard(schema, evolved=migrations.evolved(tx, schema.name))
        if tx.sql("SELECT sql FROM sqlite_master WHERE name=?", (name,)) != [{"sql": sql}]:
            raise SchemaConflictError("Call Store.install_schema before using payload APIs")

    @_payload_operation
    def prepare_file(self, source, *, schema, handler="file"):
        self.catalog.require_idle()
        self._check_file_schema(schema, handler)
        with self._snapshot(source, schema) as (staged, size, digest):
            return self._prepare_snapshot(staged, schema, size, digest)

    def _check_file_schema(self, schema, handler):
        with self.catalog.transaction() as tx:
            self._schema(schema, tx, write=True)
            if handler != "file" or handler not in schema.handlers:
                raise ValidationError("File import requires the allowed 'file' passthrough handler")

    @contextmanager
    def _snapshot(self, source, schema):
        with self.storage.snapshot(source) as staged:
            size, digest = hash_file(staged)
            schema.validate_payload(staged)
            if schema.payload_validator is not None and hash_file(staged) != (size, digest):
                raise IntegrityError("Payload validator modified the passthrough file")
            yield staged, size, digest

    def _prepare_snapshot(self, staged, schema, size, digest, *, encoding=None):
        prepared = PreparedFile(
            uuid4().hex,
            self.storage.storage_id,
            "objects/" + uuid4().hex,
            size,
            digest,
            "xxh3_128",
            "file",
            1,
            schema.name,
            schema.version,
        )
        if encoding is not None:
            prepared = PreparedValue(**asdict(prepared), **encoding)
        self.storage.upload(staged, prepared.object_key)
        # Journal the completed upload in its own short transaction; it isn't a blob yet.
        with self.catalog.transaction() as tx:
            self._schema(schema, tx, write=True)
            if encoding is not None:
                encodings.verify(tx)
                tx.sql(
                    "INSERT INTO ms_encodings VALUES(?,?,?,?)",
                    (
                        prepared.token,
                        prepared.encoding_id,
                        prepared.encoding_version,
                        prepared.descriptor,
                    ),
                )
            values = encodings.physical(prepared)
            tx.sql(
                "INSERT INTO ms_prepared ("
                + ",".join(values)
                + ") VALUES ("
                + ",".join("?" for _ in values)
                + ")",
                tuple(values.values()),
            )
        return prepared

    def finalize(self, prepared, *, tx, schema, metadata, id):
        self.catalog.require_transaction(tx)
        with tx.operation():
            identifier(id)
            self._schema(schema, tx, write=True)
            values = schema.normalize_metadata(metadata)
            if not isinstance(prepared, PreparedFile):
                raise ValidationError("Expected a PreparedFile token")
            if (
                not encodings.matches(tx, prepared)
                or prepared.storage_id != self.storage.storage_id
            ):
                raise ValidationError("Unknown, altered or foreign prepared token")
            if "ms_gc" in lifecycle._objects(tx) and tx.sql(
                "SELECT 1 FROM ms_gc WHERE token=?", (prepared.token,)
            ):
                raise ConflictError("Prepared token has been revoked for cleanup")
            if (prepared.schema_name, prepared.schema_version) != (schema.name, schema.version):
                raise ValidationError("Prepared token belongs to another schema")
            if tx.sql("SELECT id FROM ms_blobs WHERE id=?", (id,)):
                existing = self._record(id, tx, ready_only=False)
                if existing["token"] != prepared.token or existing["metadata"] != values:
                    raise ConflictError("Blob ID already identifies a different operation")
                return existing
            if tx.sql("SELECT blob_id FROM ms_objects WHERE token=?", (prepared.token,)):
                raise ConflictError("Prepared token is already associated with another blob")
            tx.sql(
                "INSERT INTO ms_blobs (id, schema_name, schema_version) VALUES (?, ?, ?)",
                (id, schema.name, schema.version),
            )
            columns = ["id", "schema_version", *values]
            tx.sql(
                f"INSERT INTO {q(schema.table_name)} ("
                + ",".join(q(n) for n in columns)
                + ") VALUES ("
                + ",".join("?" for _ in columns)
                + ")",
                (id, schema.version, *values.values()),
            )
            tx.sql("INSERT INTO ms_objects VALUES (?, ?)", (id, prepared.token))
            return self._record(id, tx, ready_only=False)

    def publish(self, id, *, tx):
        self.catalog.require_transaction(tx)
        with tx.operation():
            record = self._record(id, tx, ready_only=False)
            if record["storage_id"] != self.storage.storage_id:
                raise ValidationError("Blob belongs to another storage root")
            if record["state"] not in {"publishing", "ready"}:
                raise ConflictError("Retired blob cannot be published")
            tx.sql("UPDATE ms_blobs SET state='ready' WHERE id=? AND state='publishing'", (id,))
            return self._record(id, tx)

    def _record(self, id, tx, *, ready_only=True):
        rows = tx.sql(
            "SELECT b.*, p.token, p.storage_id, p.object_key, p.byte_size, p.digest, "
            "p.hash_algorithm, p.handler_id, p.handler_version, "
            "p.schema_name AS prepared_schema_name, p.schema_version AS prepared_schema_version "
            "FROM ms_blobs b LEFT JOIN ms_objects o ON b.id=o.blob_id "
            "LEFT JOIN ms_prepared p ON o.token=p.token WHERE b.id=?"
            + (" AND b.state='ready'" if ready_only else ""),
            (id,),
        )
        if not rows:
            raise NotFoundError(f"No {'ready ' if ready_only else ''}blob: {id}")
        record = rows[0]
        prepared_name = record.pop("prepared_schema_name")
        prepared_version = record.pop("prepared_schema_version")
        if (
            record["token"] is None
            or prepared_name != record["schema_name"]
            or prepared_version > record["schema_version"]
        ):
            raise IntegrityError("Blob object association is missing or inconsistent")
        table = "ms_data_" + record["schema_name"].encode().hex()
        metadata = tx.sql(f"SELECT * FROM {q(table)} WHERE id=?", (id,))
        if not metadata:
            raise IntegrityError("Blob metadata is missing")
        fields = metadata[0]
        if fields["schema_version"] != record["schema_version"]:
            raise IntegrityError("Blob metadata version is inconsistent")
        record["version"] = fields.pop("version")
        declaration = migrations.decode(
            tx.sql(
                "SELECT definition FROM ms_schemas WHERE name=? AND version=?",
                (record["schema_name"], record["schema_version"]),
            )[0]["definition"]
        )
        record["metadata"] = {name: fields[name] for name in declaration.fields}
        encoding = encodings.read(tx, record["token"])
        if encoding is not None:
            record["handler_id"] = encoding["encoding_id"]
            record["handler_version"] = encoding["encoding_version"]
            record["descriptor"] = json.loads(encoding["descriptor"])
        return record

    @contextmanager
    def _serialize(self, value, schema, handler, handler_version):
        codec = self.handlers.get(handler, handler_version)
        with self.catalog.transaction() as tx:
            self._schema(schema, tx, write=True)
            encodings.verify(tx)
        if handler not in schema.handlers:
            raise ValidationError("Handler is not allowed by this schema")
        schema.validate_payload(value)
        with TemporaryDirectory(prefix="meldstore-encode-") as directory:
            path = Path(directory) / "payload"
            descriptor = descriptor_json(codec.write(value, path))
            # Writer has closed the file, including any header rewrites.
            size, digest = hash_file(path)
            yield (
                path,
                size,
                digest,
                {
                    "encoding_id": handler,
                    "encoding_version": handler_version,
                    "descriptor": descriptor,
                },
            )

    @_payload_operation
    def prepare(self, value, *, schema, handler, handler_version=1):
        with self._serialize(value, schema, handler, handler_version) as (
            path,
            size,
            digest,
            encoding,
        ):
            return self._prepare_snapshot(path, schema, size, digest, encoding=encoding)

    @_payload_operation
    def put(self, value, *, schema, metadata, handler, handler_version=1, id=None):
        id = uuid4().hex if id is None else identifier(id)
        values = schema.normalize_metadata(metadata)
        metadata = dict(metadata)
        with self._serialize(value, schema, handler, handler_version) as (
            path,
            size,
            digest,
            encoding,
        ):
            with self.catalog.transaction() as tx:
                result = self._retry(id, schema, values, size, digest, tx, encoding=encoding)
                if result is not None:
                    return result
            prepared = self._prepare_snapshot(path, schema, size, digest, encoding=encoding)
        with self.catalog.transaction() as tx:
            result = self._retry(id, schema, values, size, digest, tx, encoding=encoding)
            if result is None:
                self.finalize(prepared, tx=tx, schema=schema, metadata=metadata, id=id)
                result = self.publish(id, tx=tx)
        return result

    @_payload_operation
    def get(self, id):
        record = self.stat(id)
        if record["handler_id"] == "file":
            raise UnsupportedHandlerError("File passthrough has no decoder; use materialize")
        codec = self.handlers.get(record["handler_id"], record["handler_version"])
        with self.materialize(id) as path:
            return codec.read(path, record["descriptor"])

    def stat(self, id):
        identifier(id)
        with self.catalog.transaction(write=False) as tx:
            return self._record(id, tx)

    def find(self, *, schema, where=None, predicates=(), order_by=(), limit=100, after=None):
        """Ready records with typed scalar predicates and stable keyset pagination."""
        with self.catalog.transaction(write=False) as tx:
            self._schema(schema, tx)
            sql, params = query.compile_query(
                schema,
                where=where,
                predicates=predicates,
                order_by=order_by,
                after=after,
                limit=limit,
            )
            rows = tx.sql(sql, params)
            return [self._record(row["id"], tx) for row in rows]

    @staticmethod
    def cursor(record, *, schema, order_by=()):
        return query.cursor(record, schema, order_by)

    def update_metadata(self, id, *, schema, changes, expected_version, tx=None):
        if tx is None:
            with self.catalog.transaction() as owned:
                return self.update_metadata(
                    id, schema=schema, changes=changes, expected_version=expected_version, tx=owned
                )
        self.catalog.require_transaction(tx)
        with tx.operation():
            identifier(id)
            self._schema(schema, tx, write=True)
            if type(expected_version) is not int or not 1 <= expected_version < 2**63 - 1:
                raise ValidationError("expected_version must be a positive, incrementable int64")
            if (
                not isinstance(changes, Mapping)
                or not changes
                or changes.keys() - schema.fields.keys()
            ):
                raise ValidationError(
                    "changes must be a nonempty mapping of declared metadata fields"
                )
            values = {}
            for name, value in changes.items():
                if schema.fields[name].immutable:
                    raise ValidationError(f"Immutable metadata field: {name}")
                values[name] = schema.fields[name].normalize(value)
            current = self._record(id, tx)
            if (current["schema_name"], current["schema_version"], current["version"]) != (
                schema.name,
                schema.version,
                expected_version,
            ):
                raise ConflictError("Metadata schema or concurrency version is stale")
            rows = tx.sql(
                f"UPDATE {q(schema.table_name)} SET "
                + ",".join(f"{q(name)}=?" for name in values)
                + ", version=version+1 WHERE id=? AND schema_version=? AND version=? RETURNING id",
                (*values.values(), id, schema.version, expected_version),
            )
            if not rows:
                raise ConflictError("Metadata changed before the guarded update")
            return self._record(id, tx)

    def migrate(self, plan, *, batch_size=100, max_batches=None, dry_run=False):
        return migrations.run(
            self, plan, batch_size=batch_size, max_batches=max_batches, dry_run=dry_run
        )

    def migration_status(self, name):
        identifier(name)
        with self.catalog.transaction(write=False) as tx:
            return migrations.status(tx, name)

    def delete(self, id, *, expected_version, tx=None):
        if tx is None:
            with self.catalog.transaction() as owned:
                return self.delete(id, expected_version=expected_version, tx=owned)
        self.catalog.require_transaction(tx)
        with tx.operation():
            return lifecycle.delete(self, id, expected_version, tx)

    def discard_prepared(self, prepared, *, tx=None):
        if tx is None:
            with self.catalog.transaction() as owned:
                return self.discard_prepared(prepared, tx=owned)
        self.catalog.require_transaction(tx)
        with tx.operation():
            return lifecycle.discard(self, prepared, tx)

    def resolve(self, id, *, prepared, schema, metadata):
        with self.catalog.transaction(write=False) as tx:
            lifecycle.verify(tx)
            return lifecycle.resolve(self, id, prepared, schema, metadata, tx)

    def deletion_status(self, id):
        identifier(id)
        with self.catalog.transaction(write=False) as tx:
            lifecycle.verify(tx)
            result = lifecycle.retired(tx, id)
            if result is None:
                raise NotFoundError(f"No retirement: {id}")
            return result

    def reconcile(self, *, verify=False, max_entries=10000):
        from .maintenance import reconcile

        return reconcile(self, verify=verify, max_entries=max_entries)

    def queue_orphans(self, keys):
        from .maintenance import queue_orphans

        return queue_orphans(self, keys)

    def cleanup(self, *, limit=100):
        from .maintenance import cleanup

        return cleanup(self, limit=limit)

    def backup(self, destination, *, application, max_entries=10000):
        from .backup import backup

        return backup(self, destination, application=application, max_entries=max_entries)

    def transfer(self, destination, *, application, max_entries=10000):
        from .backup import transfer

        return transfer(self, destination, application=application, max_entries=max_entries)

    def transfer_to_s3(self, destination, *, application, max_entries=10000, **s3_options):
        from .backup import transfer_to_s3

        return transfer_to_s3(
            self, destination, application=application, max_entries=max_entries, **s3_options
        )

    @_payload_operation
    def import_file(self, source, *, schema, metadata, id=None, handler="file"):
        self.catalog.require_idle()
        id = uuid4().hex if id is None else identifier(id)
        self._check_file_schema(schema, handler)
        values = schema.normalize_metadata(metadata)
        metadata = dict(metadata)
        with self._snapshot(source, schema) as (staged, size, digest):
            with self.catalog.transaction() as tx:
                existing = self._retry(id, schema, values, size, digest, tx)
                if existing is not None:
                    return existing
            prepared = self._prepare_snapshot(staged, schema, size, digest)
        with self.catalog.transaction() as tx:
            # Another process may have committed the same caller ID while we uploaded.
            result = self._retry(id, schema, values, size, digest, tx)
            if result is None:
                self.finalize(prepared, tx=tx, schema=schema, metadata=metadata, id=id)
                result = self.publish(id, tx=tx)
        return result

    def _retry(self, id, schema, values, size, digest, tx, *, encoding=None):
        if "ms_retired" in lifecycle._objects(tx) and tx.sql(
            "SELECT 1 FROM ms_retired WHERE blob_id=?", (id,)
        ):
            raise ConflictError("Retired blob IDs cannot be reused")
        if not tx.sql("SELECT id FROM ms_blobs WHERE id=?", (id,)):
            return None
        try:
            existing = self._record(id, tx, ready_only=False)
        except NotFoundError as exc:
            raise ConflictError("Blob ID exists without a complete object association") from exc
        if (
            existing["schema_name"],
            existing["schema_version"],
            existing["metadata"],
            existing["storage_id"],
            existing["byte_size"],
            existing["digest"],
            existing["handler_id"],
            existing["handler_version"],
            existing["state"],
        ) != (
            schema.name,
            schema.version,
            values,
            self.storage.storage_id,
            size,
            digest,
            "file" if encoding is None else encoding["encoding_id"],
            1 if encoding is None else encoding["encoding_version"],
            "ready",
        ):
            raise ConflictError("Existing ID is different or unfinished; resolve explicitly")
        if encoding is not None and existing.get("descriptor") != json.loads(
            encoding["descriptor"]
        ):
            raise ConflictError("Existing ID has a different encoding descriptor")
        return existing

    @_payload_operation
    def export_file(self, id, destination):
        """Copy exact stored bytes to a new persistent local file; return its Path.

        The parent must exist. Existing files, directories and symlinks conflict.
        Neither the blob nor any import source is deleted or modified.
        """
        from .exporting import export_file

        return export_file(self, id, destination)

    @contextmanager
    def materialize(self, id):
        self.catalog.require_idle()
        self.catalog._readers += 1
        try:
            record = self.stat(id)
            if record["storage_id"] != self.storage.storage_id:
                raise ValidationError("Configured local storage identity does not match this blob")
            with self.storage.materialize(
                record["object_key"], byte_size=record["byte_size"], digest=record["digest"]
            ) as path:
                yield path
        finally:
            self.catalog._readers -= 1
