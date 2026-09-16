"""Deterministic metadata-only SQLite index experiment; no payload performance claim.

Run with: uv run --frozen python tools/sqlite_benchmark.py
Synthetic queue rows model retained history (99.9% done). Uses the real queue DDL.
"""

import json
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path

from meldstore import BlobSchema, Catalog, Index, Integer, LocalStorage, Order, Store
from meldstore.query import compile_query


def measure(catalog, sql, params):
    samples = []
    for _ in range(25):
        start = time.perf_counter()
        rows = catalog.sql(sql, params, write=False)
        samples.append((time.perf_counter() - start) * 1000)
    return {
        "median_ms": round(statistics.median(samples), 4),
        "rows": len(rows),
        "plan": catalog.sql("EXPLAIN QUERY PLAN " + sql, params, write=False),
    }


def write_probe(catalog):
    """Statement + rollback cost, not a durable-commit throughput measurement."""

    class Rollback(Exception):
        pass

    samples = []
    for _ in range(10):
        start = time.perf_counter()
        try:
            with catalog.transaction() as tx:
                tx.sql("""WITH RECURSIVE n(i) AS (VALUES(1) UNION ALL
                    SELECT i+1 FROM n WHERE i<1000)
                    INSERT INTO ms_gc(storage_id,object_key,reason)
                    SELECT 'probe',printf('objects/%08d',i),'orphan' FROM n""")
                tx.sql("UPDATE ms_gc SET state='done' WHERE storage_id='probe'")
                raise Rollback
        except Rollback:
            pass
        samples.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(samples), 4)


def run(adapter, root):
    with Catalog(root / "catalog.db", adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(root / "objects"))
        store.install_schema(BlobSchema("dataset", {}))
        # Deliberate SQL fixture for queue index measurement, not a supported
        # application mutation API or end-to-end object deletion benchmark.
        catalog.sql("""WITH RECURSIVE n(i) AS (VALUES(1) UNION ALL
            SELECT i+1 FROM n WHERE i<50000)
            INSERT INTO ms_gc(storage_id,object_key,reason,state)
            SELECT 'root',printf('objects/%08d',i),'orphan',
            CASE WHEN i%1000=0 THEN 'pending' ELSE 'done' END FROM n""")
        sql = (
            "SELECT * FROM ms_gc WHERE storage_id=? AND state='pending' ORDER BY object_key LIMIT ?"
        )
        before = measure(catalog, sql, ("root", 25))
        write_before = write_probe(catalog)
        page_sql = (
            "SELECT page_count-freelist_count AS used FROM pragma_page_count,pragma_freelist_count"
        )
        pages_before = catalog.sql(page_sql)[0]["used"]
        start = time.perf_counter()
        store.install_query_indexes()
        build_ms = (time.perf_counter() - start) * 1000
        pages_after = catalog.sql(page_sql)[0]["used"]
        after = measure(catalog, sql, ("root", 25))
        write_after = write_probe(catalog)
        return {
            "adapter": adapter,
            "sqlite": sqlite3.sqlite_version,
            "rows": 50000,
            "pending": 50,
            "read_sql_calls": 1,
            "before": before,
            "after": after,
            "index_build_ms": round(build_ms, 3),
            "index_pages": pages_after - pages_before,
            "write_probe_before_ms": write_before,
            "write_probe_after_ms": write_after,
        }


def metadata(adapter, root):
    """SQL-only relational fixtures; no payloads or handler costs are measured."""
    with Catalog(root / "metadata.db", adapter=adapter) as catalog:
        schema = BlobSchema(
            "dataset",
            {"source": Integer(), "captured": Integer()},
            indexes=(Index("source", "captured"),),
        )
        table = catalog.install_schema(schema)
        catalog.sql("""WITH RECURSIVE n(i) AS (VALUES(1) UNION ALL SELECT i+1 FROM n WHERE i<50000)
            INSERT INTO ms_blobs(id,schema_name,schema_version,state)
            SELECT printf('%08d',i),'dataset',1,'ready' FROM n""")
        catalog.sql(
            f"INSERT INTO {table}(id,source,captured) SELECT id,CAST(id AS INTEGER)%100,CAST(id AS INTEGER) FROM ms_blobs"
        )
        sql, params = compile_query(
            schema,
            where={"source": 7},
            predicates=(),
            order_by=(Order("captured"),),
            after=None,
            limit=25,
        )
        filtered = measure(catalog, sql, params)
        sql, params = compile_query(
            schema, where=None, predicates=(), order_by=(), after="00040000", limit=25
        )
        paginated = measure(catalog, sql, params)
        catalog.sql(
            "CREATE TABLE app_refs(id INTEGER PRIMARY KEY,blob_id TEXT REFERENCES ms_blobs(id))"
        )
        catalog.sql("CREATE INDEX app_refs_blob ON app_refs(blob_id)")
        catalog.sql("INSERT INTO app_refs SELECT CAST(id AS INTEGER),id FROM ms_blobs")
        joins = measure(
            catalog,
            "SELECT b.id FROM app_refs a JOIN ms_blobs b ON b.id=a.blob_id WHERE a.id=?",
            (40000,),
        )
        fk_lookup = measure(catalog, "SELECT rowid FROM app_refs WHERE blob_id=?", ("00040000",))
        old = measure(
            catalog,
            f"SELECT * FROM {table} WHERE schema_version=? AND (? IS NULL OR id>?) ORDER BY id LIMIT ?",
            (1, "00040000", "00040000", 100),
        )
        new = measure(
            catalog,
            f"SELECT * FROM {table} WHERE schema_version=? AND id>? ORDER BY id LIMIT ?",
            (1, "00040000", 100),
        )
        return {
            "adapter": adapter,
            "rows": 50000,
            "source_groups": 100,
            "filtered": filtered,
            "keyset": paginated,
            "application_join": joins,
            "application_fk": fk_lookup,
            "migration_before": old,
            "migration_after": new,
        }


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for adapter in ("sqlite", "melddb"):
            target = root / adapter
            target.mkdir()
            print(json.dumps(run(adapter, target), indent=2))
            print(json.dumps(metadata(adapter, target), indent=2))
