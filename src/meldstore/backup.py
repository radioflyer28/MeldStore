"""Exclusive local snapshots and fresh-destination restore. No implicit cleanup."""

import json
import math
import os
import re
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from . import encodings, installation, lifecycle, migrations, payload_catalog
from .access import Lease, canonical
from .errors import ConflictError, IntegrityError, ValidationError
from .handlers import descriptor_json
from .maintenance import _bound
from .schema import quote_identifier
from .storage import CHUNK_SIZE, KEY_PATTERN, LocalStorage, hash_file


def _checkpoint(stage):
    """Fault-injection seam; never an application callback."""


def _path(value):
    path = Path(os.path.abspath(value))
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValidationError("Backup/restore paths must not traverse symlinks or junctions")
    return path


def _destination(value, *sources):
    path = _path(value)
    if path.exists():
        raise ConflictError("Destination must not exist, even if empty")
    if not path.parent.is_dir():
        raise ValidationError("Destination parent must exist")
    for source in sources:
        source = _path(source)
        if path.is_relative_to(source) or source.is_relative_to(path):
            raise ValidationError("Destination must not overlap source paths")
    return path


def _application(value):
    value = json.loads(descriptor_json(value))
    if any(type(value.get(key)) is not str or not value[key] for key in ("id", "schema_revision")):
        raise ValidationError("application requires nonempty id and schema_revision strings")
    return value


def _write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def _descriptor(path):
    size, digest = hash_file(path)
    return {"byte_size": size, "digest": digest}


def _verify_file(path, descriptor):
    path = _path(path)
    if not path.is_file() or _descriptor(path) != descriptor:
        raise IntegrityError(f"Backup file fails size/hash verification: {path.name}")


@contextmanager
def _database(path):
    # Immutable standalone artifacts only: never use immutable=1 on a live DB.
    connection = sqlite3.connect(_path(path).as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        connection.execute("PRAGMA trusted_schema=OFF")
        yield connection
    except sqlite3.Error as exc:
        raise IntegrityError("Invalid or unsupported backup SQLite catalog") from exc
    finally:
        connection.close()


class _Query:
    """Read-only SQL adapter for validating detached SQLite snapshots."""

    def __init__(self, connection):
        self.connection = connection

    def sql(self, sql, params=()):
        cursor = self.connection.execute(sql, params)
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor]


def _entries(connection, storage_id, max_entries):
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise IntegrityError("SQLite integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise IntegrityError("Catalog has invalid SQL references")
    tx = _Query(connection)
    objects = installation._objects(tx)
    installation._verify(objects, installation.CORE)
    installation._verify(objects, payload_catalog.DDL)
    lifecycle.verify(tx)
    if "ms_encoding_format" in objects:
        encodings.verify(tx)
    elif any(name in objects for name in encodings.DDL):
        raise IntegrityError("Partial encoding extension")
    if tx.sql("SELECT * FROM ms_format") != [{"singleton": 1, "version": 1}] or tx.sql(
        "SELECT version FROM ms_payload_format"
    ) != [{"version": 1}]:
        raise IntegrityError("Unsupported catalog format")
    if (
        tx.sql("SELECT 1 FROM ms_blobs WHERE state!='ready' LIMIT 1")
        or tx.sql("SELECT 1 FROM ms_gc WHERE state!='done' LIMIT 1")
        or tx.sql("""SELECT 1 FROM ms_prepared p WHERE
        NOT EXISTS(SELECT 1 FROM ms_objects o WHERE o.token=p.token)
        AND NOT EXISTS(SELECT 1 FROM ms_gc g WHERE g.token=p.token AND g.state='done') LIMIT 1""")
    ):
        raise ConflictError(
            "Resolve prepared/publication operations and finish cleanup before backup"
        )
    if "ms_migrations" in objects and tx.sql(
        "SELECT 1 FROM ms_migrations WHERE state!='complete' LIMIT 1"
    ):
        raise ConflictError("Finish metadata migrations before backup")
    if tx.sql("SELECT 1 FROM ms_prepared WHERE storage_id!=? LIMIT 1", (storage_id,)) or tx.sql(
        "SELECT 1 FROM ms_gc WHERE storage_id!=? LIMIT 1", (storage_id,)
    ):
        raise ValidationError("S06 backup supports exactly one logical storage root")
    if tx.sql("SELECT 1 FROM ms_objects o JOIN ms_gc g ON o.token=g.token LIMIT 1"):
        raise IntegrityError("Live object is also queued for cleanup")
    if tx.sql("""SELECT 1 FROM ms_blobs b JOIN ms_objects o ON o.blob_id=b.id
        JOIN ms_prepared p ON p.token=o.token WHERE p.schema_name!=b.schema_name
        OR p.schema_version>b.schema_version LIMIT 1"""):
        raise IntegrityError("Prepared object belongs to a different schema")
    declarations = tx.sql("SELECT name,version,table_name,definition FROM ms_schemas")
    if len(declarations) > max_entries:
        raise ValidationError("Schema count exceeds max_entries")
    for row in declarations:
        schema = migrations.decode(row["definition"])
        if (schema.name, schema.version, schema.table_name, schema.definition) != (
            row["name"],
            row["version"],
            row["table_name"],
            row["definition"],
        ):
            raise IntegrityError("Invalid schema registry definition")
        if not tx.sql(
            "SELECT 1 FROM ms_payload_schemas WHERE schema_name=? AND schema_version=?",
            (schema.name, schema.version),
        ) or not tx.sql("SELECT 1 FROM ms_lifecycle_schemas WHERE schema_name=?", (schema.name,)):
            raise IntegrityError("Schema is not fully enrolled for payload/lifecycle operations")
        if migrations.evolved(tx, schema.name):
            migrations.verify(tx, schema.name)
        else:
            installation._verify(objects, installation._schema_objects(schema))
        name, sql = payload_catalog.guard(schema, evolved=migrations.evolved(tx, schema.name))
        installation._verify(objects, {name: sql})
        if tx.sql(
            f"""SELECT 1 FROM ms_blobs b LEFT JOIN {quote_identifier(schema.table_name)} m
            ON b.id=m.id WHERE b.schema_name=? AND
            (m.id IS NULL OR m.schema_version!=b.schema_version) LIMIT 1""",
            (schema.name,),
        ):
            raise IntegrityError("Blob metadata is missing or inconsistent")
    entries = tx.sql(
        """SELECT b.id,p.token,p.storage_id,p.object_key,p.byte_size,p.digest
        FROM ms_blobs b LEFT JOIN ms_objects o ON o.blob_id=b.id
        LEFT JOIN ms_prepared p ON p.token=o.token ORDER BY b.id LIMIT ?""",
        (max_entries + 1,),
    )
    if len(entries) > max_entries:
        raise ValidationError("Object count exceeds max_entries")
    for entry in entries:
        if (
            entry["storage_id"] != storage_id
            or not isinstance(entry["object_key"], str)
            or not KEY_PATTERN.fullmatch(entry["object_key"])
        ):
            raise IntegrityError("Invalid or missing object association")
    return entries


def _sql_name(name):
    return '"' + name.replace('"', '""') + '"'


def _sql_value(value):
    if value is None:
        return "NULL"
    if isinstance(value, str):
        # SQLite quote() / iterdump truncate embedded NUL text. Hex preserves it.
        return "CAST(X'" + value.encode("utf-8").hex() + "' AS TEXT)"
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    if isinstance(value, float) and math.isinf(value):
        return "9e999" if value > 0 else "-9e999"
    return repr(value)


def _export_sql(connection, path):
    objects = connection.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall()
    tables = [
        (name, sql)
        for kind, name, sql in objects
        if kind == "table" and not name.startswith("sqlite_")
    ]
    table_info = {
        row[1]: row for row in connection.execute("PRAGMA table_list") if row[0] == "main"
    }
    if any(table_info[name][2] != "table" for name, _ in tables):
        raise ValidationError(
            "S06 portable export supports ordinary SQLite tables, not virtual tables"
        )
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write("PRAGMA foreign_keys=OFF;\nBEGIN TRANSACTION;\n")
        for name, sql in tables:
            output.write(sql + ";\n")
            column_info = connection.execute(f"PRAGMA table_xinfo({_sql_name(name)})").fetchall()
            columns = [r[1] for r in column_info if r[6] == 0]
            # Preserve implicit rowids where SQLite exposes an unshadowed alias.
            if not table_info[name][4]:
                alias = next(
                    (
                        a
                        for a in ("rowid", "_rowid_", "oid")
                        if a not in {c[1].lower() for c in column_info}
                    ),
                    None,
                )
                if alias:
                    columns.insert(0, alias)
            names = ",".join(_sql_name(c) for c in columns)
            for row in connection.execute(f"SELECT {names} FROM {_sql_name(name)}"):
                output.write(
                    f"INSERT INTO {_sql_name(name)}({names}) VALUES("
                    + ",".join(_sql_value(v) for v in row)
                    + ");\n"
                )
        if any(name == "sqlite_sequence" for _, name, _ in objects):
            output.write('DELETE FROM "sqlite_sequence";\n')
            for row in connection.execute("SELECT name,seq FROM sqlite_sequence"):
                output.write(
                    'INSERT INTO "sqlite_sequence" VALUES('
                    + ",".join(_sql_value(v) for v in row)
                    + ");\n"
                )
        for kind, _, sql in objects:
            if kind in ("index", "view", "trigger"):
                output.write(sql + ";\n")
        output.write("COMMIT;\nPRAGMA foreign_keys=ON;\n")
        output.flush()
        os.fsync(output.fileno())


def _copy_payloads(source, target, entries, stage):
    for entry in entries:
        key = entry["object_key"]
        source.cleanup_key(key)  # Reject filesystem aliases before reading.
        with source.materialize(key, byte_size=entry["byte_size"], digest=entry["digest"]) as path:
            target.upload(path, key)
        with target.materialize(key, byte_size=entry["byte_size"], digest=entry["digest"]):
            pass
        _checkpoint(stage)


def backup(store, destination, *, application, max_entries=10000):
    store.catalog.require_maintenance(store.storage.root)
    _bound(max_entries)
    application = _application(application)
    destination = _destination(destination, store.storage.root, store.catalog.path)
    destination.mkdir()
    snapshot = destination / "catalog.sqlite"
    store.catalog.snapshot(snapshot)
    _checkpoint("backup_catalog")
    with _database(snapshot) as connection:
        entries = _entries(connection, store.storage.storage_id, max_entries)
        _export_sql(connection, destination / "catalog.sql")
    payloads = LocalStorage._replica(destination / "storage", store.storage.storage_id)
    _copy_payloads(store.storage, payloads, entries, "backup_object")
    manifest = {
        "format": "meldstore-backup",
        "version": 1,
        "hash_algorithm": "xxh3_128",
        "created_at": datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "application": application,
        "storage_id": store.storage.storage_id,
        "catalog": _descriptor(snapshot),
        "sql": _descriptor(destination / "catalog.sql"),
        "identity": _descriptor(payloads.root / "meldstore-storage.json"),
        "objects": entries,
    }
    _checkpoint("backup_manifest")
    _write_json(destination / "manifest.json", manifest)
    return manifest


def _manifest(source, max_entries):
    path = _path(source / "manifest.json")
    if not path.is_file() or path.stat().st_size > 65536 + max_entries * 2048:
        raise IntegrityError("Missing, incomplete, or oversized backup manifest")

    def distinct(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise IntegrityError("Duplicate manifest key")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=distinct)
        if set(value) != {
            "format",
            "version",
            "hash_algorithm",
            "created_at",
            "application",
            "storage_id",
            "catalog",
            "sql",
            "identity",
            "objects",
        }:
            raise ValueError("Unexpected fields")
        if (value["format"], value["version"], value["hash_algorithm"]) != (
            "meldstore-backup",
            1,
            "xxh3_128",
        ) or type(value["version"]) is not int:
            raise ValueError("Unsupported format")
        if not isinstance(value["storage_id"], str) or not re.fullmatch(
            r"[0-9a-f]{32}", value["storage_id"]
        ):
            raise ValueError("Invalid storage ID")
        _application(value["application"])
        if not isinstance(value["created_at"], str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", value["created_at"]
        ):
            raise ValueError("Invalid backup timestamp")
        datetime.fromisoformat(value["created_at"])
        if not isinstance(value["objects"], list) or len(value["objects"]) > max_entries:
            raise ValueError("Object bound exceeded")
        for entry in value["objects"]:
            if not isinstance(entry, dict) or set(entry) != {
                "id",
                "token",
                "storage_id",
                "object_key",
                "byte_size",
                "digest",
            }:
                raise ValueError("Invalid object descriptor")
            if (
                any(
                    type(entry[key]) is not str
                    for key in ("id", "token", "storage_id", "object_key", "digest")
                )
                or type(entry["byte_size"]) is not int
                or entry["byte_size"] < 0
            ):
                raise ValueError("Invalid object descriptor types")
            if not KEY_PATTERN.fullmatch(entry["object_key"]) or not re.fullmatch(
                r"[0-9a-f]{32}", entry["digest"]
            ):
                raise ValueError("Invalid object key or digest")
        for descriptor in (value["catalog"], value["sql"], value["identity"]):
            if (
                set(descriptor) != {"byte_size", "digest"}
                or type(descriptor["byte_size"]) is not int
                or descriptor["byte_size"] < 0
                or not isinstance(descriptor["digest"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", descriptor["digest"])
            ):
                raise ValueError("Invalid file descriptor")
    except (ValueError, TypeError, KeyError, RecursionError, ValidationError) as exc:
        raise IntegrityError("Invalid backup manifest") from exc
    return value


def restore_backup(source, destination, *, max_entries=10000):
    """Restore a trusted backup to a fresh directory; catalog is published last."""
    _bound(max_entries)
    source = _path(source)
    destination = _destination(destination, source)
    manifest = _manifest(source, max_entries)
    _verify_file(source / "catalog.sqlite", manifest["catalog"])
    _verify_file(source / "catalog.sql", manifest["sql"])
    _verify_file(source / "storage" / "meldstore-storage.json", manifest["identity"])
    with _database(source / "catalog.sqlite") as connection:
        entries = _entries(connection, manifest["storage_id"], max_entries)
    if entries != manifest["objects"]:
        raise IntegrityError("Manifest objects differ from the catalog snapshot")
    payloads = LocalStorage(source / "storage")
    if payloads.storage_id != manifest["storage_id"]:
        raise IntegrityError("Backup storage identity differs")
    destination.mkdir()
    catalog = destination / "catalog.sqlite"
    gate = Lease(str(catalog) + ".meldstore-access", owner=canonical(catalog), exclusive=True)
    try:
        storage = LocalStorage._replica(destination / "storage", manifest["storage_id"])
        root_gate = Lease(
            storage.root / "meldstore-access.sqlite", owner=canonical(catalog), exclusive=True
        )
        try:
            _copy_payloads(payloads, storage, entries, "restore_object")
            partial = destination / "catalog.partial"
            with (source / "catalog.sqlite").open("rb") as incoming, partial.open("xb") as output:
                shutil.copyfileobj(incoming, output, CHUNK_SIZE)
                output.flush()
                os.fsync(output.fileno())
            _verify_file(partial, manifest["catalog"])
            _checkpoint("restore_catalog")
            # Create-only atomic publication of the ready catalog, after all bytes.
            os.link(partial, catalog)
            partial.unlink()
            return {
                "catalog": str(catalog),
                "storage": str(storage.root),
                "application": manifest["application"],
            }
        finally:
            root_gate.close()
    finally:
        gate.close()


def transfer(store, destination, *, application, max_entries=10000):
    """Verified, non-destructive offline local relocation to a fresh catalog/root."""
    store.catalog.require_maintenance(store.storage.root)
    _destination(destination, store.storage.root, store.catalog.path)
    with TemporaryDirectory(prefix="meldstore-transfer-") as temporary:
        snapshot = Path(temporary) / "backup"
        backup(store, snapshot, application=application, max_entries=max_entries)
        return restore_backup(snapshot, destination, max_entries=max_entries)
