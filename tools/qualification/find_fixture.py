"""Synthetic SQL-only find fixtures; no payload existence or integrity qualification.

The caller owns the catalog lifetime. These helpers never create payload files.
Use encoding="empty" for the benchmark and "absent"/"mixed"/"all" for parity.
"""

import json
from datetime import datetime, timedelta, timezone

from meldstore import (
    BlobSchema,
    Boolean,
    Float,
    HandlerRegistry,
    Integer,
    Store,
    Text,
    Timestamp,
    encodings,
    lifecycle,
    payload_catalog,
)
from meldstore.dialect import for_transaction

DESCRIPTOR = {"nested": {"items": [1, {"value": "equal contents"}]}}


class NoPayloadIO:
    root = None

    def __getattr__(self, name):
        raise AssertionError("SQL-only find attempted storage access: " + name)


def sql_only_store(catalog):
    """Bypass storage admission ONLY for fabricated SQL-only test/benchmark rows."""
    store = object.__new__(Store)
    store.catalog = catalog
    store.storage = NoPayloadIO()
    store.handlers = HandlerRegistry(builtins=False)
    return store


def find_schema():
    return BlobSchema(
        "find '測定",
        {"label": Text(required=True), "number": Integer(), "score": Float(),
         "ok": Boolean(), "at": Timestamp(), "測定 'field": Text()},
        handlers=("file", "synthetic.unavailable"),
    )


def record_id(index):
    return f"find-{index:04d}"


def metadata_values(index):
    return {
        "label": "a" if index % 2 == 0 else "b",
        "number": None if index % 5 == 0 else index // 3,
        "score": float(index) / 2,
        "ok": index % 2 == 0,
        "at": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=index),
        "測定 'field": "λ'quoted",
    }


def seed_find(catalog, *, count=1000, encoding="empty", schema=None, descriptor=None):
    """Install a schema and bulk-seed ready rows; return its BlobSchema.

    IDs are record_id(0)..record_id(count-1). Physical descriptors are deliberately
    fabricated (zero bytes, zero digest). They MUST NOT be used to claim payload
    qualification. Encoding rows precede prepared rows in the same transaction.
    """
    if encoding not in {"absent", "empty", "mixed", "all"}:
        raise ValueError("Unknown encoding fixture")
    schema = find_schema() if schema is None else schema
    descriptor = json.dumps(DESCRIPTOR) if descriptor is None else descriptor
    with catalog.transaction() as tx:
        catalog.install_schema(schema, tx=tx)
        payload_catalog.install(schema, tx)
        if encoding != "absent":
            encodings.install(tx)
        lifecycle.install(tx)
        lifecycle.install_schema(schema, tx)
        d = for_transaction(tx)
        b = d.bind
        if encoding in {"mixed", "all"}:
            tx.sql_many(
                f"INSERT INTO ms_encodings VALUES({b},{b},{b},{b})",
                ((f"token-{i:04d}", "synthetic.unavailable", 7, descriptor)
                 for i in range(count) if encoding == "all" or i % 2),
            )
        tx.sql_many(
            "INSERT INTO ms_prepared VALUES(" + ",".join([b] * 10) + ")",
            ((f"token-{i:04d}", "synthetic-storage", f"synthetic/{i:04d}", 0,
              "0" * 32, "xxh3_128", "file", 1, schema.name, schema.version)
             for i in range(count)),
        )
        tx.sql_many(
            f"INSERT INTO ms_blobs(id,schema_name,schema_version) VALUES({b},{b},{b})",
            ((record_id(i), schema.name, schema.version) for i in range(count)),
        )
        fields = list(schema.fields)
        tx.sql_many(
            f"INSERT INTO {d.quote(d.table(schema))} ("
            + ",".join(d.quote(k) for k in ["id", "schema_version", *fields])
            + ") VALUES(" + ",".join([b] * (len(fields) + 2)) + ")",
            ((record_id(i), schema.version,
              *(d.value(v) for v in schema.normalize_metadata(metadata_values(i)).values()))
             for i in range(count)),
        )
        tx.sql_many(
            f"INSERT INTO ms_objects VALUES({b},{b})",
            ((record_id(i), f"token-{i:04d}") for i in range(count)),
        )
        tx.sql("UPDATE ms_blobs SET state='ready' WHERE state='publishing'")
    return schema
