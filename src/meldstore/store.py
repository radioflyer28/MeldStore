"""Explicit file preparation, SQL publication and verified temporary materialization."""

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from uuid import uuid4

from . import payload_catalog
from .errors import (
    ConflictError,
    IntegrityError,
    NotFoundError,
    SchemaConflictError,
    ValidationError,
)
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


class Store:
    """Borrows a Catalog; the caller owns its lifetime. S02 supports local files only."""

    def __init__(self, catalog, storage: LocalStorage):
        self.catalog = catalog
        self.storage = storage

    def install_schema(self, schema):
        with self.catalog.transaction() as tx:
            table = self.catalog.install_schema(schema, tx=tx)
            payload_catalog.install(schema, tx)
            return table

    def _schema(self, schema, tx):
        if not isinstance(schema, BlobSchema):
            raise ValidationError("Supply the installed BlobSchema declaration")
        rows = tx.sql(
            "SELECT definition FROM ms_schemas WHERE name=? AND version=?",
            (schema.name, schema.version),
        )
        if rows != [{"definition": schema.definition}]:
            raise SchemaConflictError("Schema is not installed or its definition differs")
        name, sql = payload_catalog.guard(schema)
        if tx.sql("SELECT sql FROM sqlite_master WHERE name=?", (name,)) != [{"sql": sql}]:
            raise SchemaConflictError("Call Store.install_schema before using payload APIs")

    def prepare_file(self, source, *, schema, handler="file"):
        self.catalog.require_idle()
        self._check_file_schema(schema, handler)
        with self._snapshot(source, schema) as (staged, size, digest):
            return self._prepare_snapshot(staged, schema, size, digest)

    def _check_file_schema(self, schema, handler):
        with self.catalog.transaction() as tx:
            self._schema(schema, tx)
            if handler != "file" or handler not in schema.handlers:
                raise ValidationError("S02 requires the allowed 'file' passthrough handler")

    @contextmanager
    def _snapshot(self, source, schema):
        with self.storage.snapshot(source) as staged:
            size, digest = hash_file(staged)
            schema.validate_payload(staged)
            if schema.payload_validator is not None and hash_file(staged) != (size, digest):
                raise IntegrityError("Payload validator modified the passthrough file")
            yield staged, size, digest

    def _prepare_snapshot(self, staged, schema, size, digest):
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
        self.storage.upload(staged, prepared.object_key)
        # Journal the completed upload in its own short transaction; it isn't a blob yet.
        with self.catalog.transaction() as tx:
            values = asdict(prepared)
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
            self._schema(schema, tx)
            values = schema.normalize_metadata(metadata)
            if not isinstance(prepared, PreparedFile):
                raise ValidationError("Expected a PreparedFile token")
            rows = tx.sql("SELECT * FROM ms_prepared WHERE token=?", (prepared.token,))
            if rows != [asdict(prepared)] or prepared.storage_id != self.storage.storage_id:
                raise ValidationError("Unknown, altered or foreign prepared token")
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
            columns = ["id", *values]
            tx.sql(
                f"INSERT INTO {q(schema.table_name)} ("
                + ",".join(q(n) for n in columns)
                + ") VALUES ("
                + ",".join("?" for _ in columns)
                + ")",
                (id, *values.values()),
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
        if record["token"] is None or (
            record.pop("prepared_schema_name"),
            record.pop("prepared_schema_version"),
        ) != (record["schema_name"], record["schema_version"]):
            raise IntegrityError("Blob object association is missing or inconsistent")
        table = "ms_data_" + record["schema_name"].encode().hex()
        metadata = tx.sql(f"SELECT * FROM {q(table)} WHERE id=?", (id,))
        if not metadata:
            raise IntegrityError("Blob metadata is missing")
        fields = metadata[0]
        record["version"] = fields.pop("version")
        for name in ("id", "schema_name", "schema_version"):
            fields.pop(name)
        record["metadata"] = fields
        return record

    def stat(self, id):
        identifier(id)
        with self.catalog.transaction() as tx:
            return self._record(id, tx)

    def find(self, *, schema, where=None, limit=100, after=None):
        """Ready records with equality filters and bounded ID-keyset pagination."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValidationError("limit must be an integer between 1 and 1000")
        where = {} if where is None else where
        if not isinstance(where, Mapping):
            raise ValidationError("where must be a mapping of declared fields")
        if after is not None:
            identifier(after)
        with self.catalog.transaction() as tx:
            self._schema(schema, tx)
            predicates = ["b.state='ready'", "b.schema_name=?", "b.schema_version=?"]
            params = [schema.name, schema.version]
            for name, value in where.items():
                if name not in schema.fields:
                    raise ValidationError("Filter references an undeclared field")
                predicates.append(f"m.{q(name)} IS ?")
                params.append(None if value is None else schema.fields[name].normalize(value))
            if after is not None:
                predicates.append("b.id > ?")
                params.append(after)
            params.append(limit)
            rows = tx.sql(
                f"SELECT b.id FROM ms_blobs b LEFT JOIN {q(schema.table_name)} m ON b.id=m.id "
                "WHERE " + " AND ".join(predicates) + " ORDER BY b.id LIMIT ?",
                params,
            )
            return [self._record(row["id"], tx) for row in rows]

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

    def _retry(self, id, schema, values, size, digest, tx):
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
            "file",
            1,
            "ready",
        ):
            raise ConflictError("Existing ID is different or unfinished; resolve explicitly")
        return existing

    @contextmanager
    def materialize(self, id):
        self.catalog.require_idle()
        record = self.stat(id)
        if record["storage_id"] != self.storage.storage_id:
            raise ValidationError("Configured local storage identity does not match this blob")
        with self.storage.materialize(
            record["object_key"], byte_size=record["byte_size"], digest=record["digest"]
        ) as path:
            yield path
