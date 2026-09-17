"""Opt-in, private-data S08 qualification. Never publishes source paths or IDs.

Run as a module from the checkout. Copies live only in a uniquely created
temporary directory and (with --s3) the disposable loopback lab's private prefix.
Results contain aggregate measurements only. No aircraft truth is inferred.
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import obstore

from examples.cuas_catalog import RECORDING, TABLE, install, instant, publish_recording
from examples.radarnet_input import inspect_recording
from meldstore import Catalog, LocalStorage, S3Storage, Store, catalog_access, restore_backup
from meldstore.catalog import Transaction
from meldstore.storage import hash_file
from tools.qualify_local import peak_rss


def run(source_root, *, files, adapter, output, s3=False, min_bytes=100_000_000, max_bytes=300_000_000):
    root = Path(source_root).resolve(strict=True)
    paths = sorted(p for p in root.glob("*.mt.parquet")
                   if re.fullmatch(r"202\d-\d{2}-\d{2}_EchoK-\d+\.mt\.parquet", p.name)
                   and min_bytes <= p.stat().st_size <= max_bytes)[:files]
    if len(paths) != files:
        raise ValueError("Not enough matching original files in the requested size range")
    total = sum(p.stat().st_size for p in paths)
    output = Path(output).resolve()
    if output.exists() or output.is_relative_to(root):
        raise ValueError("Report must be new and outside the source directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < total * 4 + 1_000_000_000:
        raise ValueError("Insufficient space for private imports, backup and restore")
    counters = Counter()
    methods = {name: getattr(obstore, name) for name in ("get", "put", "copy", "delete", "list")}
    sql = Transaction.sql
    def count_sql(self, *args, **kwargs):
        counters["sql_api_calls"] += 1
        return sql(self, *args, **kwargs)
    def counted(name):
        def call(*args, **kwargs):
            counters[name] += 1
            return methods[name](*args, **kwargs)
        return call
    report = {"files": files, "bytes": total, "adapter": adapter, "backend": "rustfs" if s3 else "local",
              "file_size_min": min(p.stat().st_size for p in paths),
              "file_size_max": max(p.stat().st_size for p in paths),
              "rows": 0, "track_occurrences": 0, "missing_track_rows": 0,
              "hash_seconds": 0, "index_seconds": 0, "import_seconds": 0,
              "materialize_seconds": 0, "sqlite_version": sqlite3.sqlite_version,
              "config_provenance": "unverified placeholder; configuration IDs absent from input"}
    started = time.perf_counter()
    baseline = peak_rss()
    originals = []
    try:
        Transaction.sql = count_sql
        for name in methods:
            setattr(obstore, name, counted(name))
        with tempfile.TemporaryDirectory(prefix="s08-private-", dir=output.parent) as directory:
            work = Path(directory)
            if s3:
                config = json.loads(os.environ["MELDSTORE_S3_CONFIG"])
                if not config["endpoint"].startswith("http://127.0.0.1:"):
                    raise ValueError("Only the disposable loopback lab is authorized")
                storage = S3Storage(os.environ["MELDSTORE_S3_BUCKET"], prefix="workload/" + uuid4().hex,
                                    coordination_directory=work / "storage", config=config)
            else:
                storage = LocalStorage(work / "storage")
            db = work / "catalog.db"
            sensors = set()
            with Catalog(db, adapter=adapter) as catalog:
                store = Store(catalog, storage)
                install(store)
                store.install_query_indexes()
                for number, path in enumerate(paths):
                    begin = time.perf_counter()
                    descriptor = hash_file(path)
                    report["hash_seconds"] += time.perf_counter() - begin
                    originals.append((path, descriptor))
                    begin = time.perf_counter()
                    index = inspect_recording(path)
                    report["index_seconds"] += time.perf_counter() - begin
                    report["rows"] += index["rows"]
                    report["track_occurrences"] += len(index["tracks"])
                    report["missing_track_rows"] += index["missing_track_rows"]
                    sensor = index["sensor"]
                    if sensor not in sensors:
                        with catalog.transaction() as tx:
                            tx.sql("INSERT INTO c_sensor VALUES(?)", (sensor,))
                            tx.sql("INSERT INTO c_sensor_config VALUES(?,?,?)",
                                   (sensor, sensor, '{"configuration":"unknown; qualification placeholder"}'))
                        sensors.add(sensor)
                    begin = time.perf_counter()
                    record = publish_recording(store, path, id=f"recording-{number}", sensor_config=sensor,
                                               start=index["start"], end=index["end"], tracks=index["tracks"])
                    report["import_seconds"] += time.perf_counter() - begin
                    assert (record["byte_size"], record["digest"]) == descriptor
                    begin = time.perf_counter()
                    with store.materialize(record["id"]) as verified:
                        assert hash_file(verified) == descriptor
                    report["materialize_seconds"] += time.perf_counter() - begin
                    if number % 10 == 0 or number == files - 1:
                        print(json.dumps({"completed_files": number + 1, "of": files,
                                          "elapsed_seconds": time.perf_counter() - started}), flush=True)
                report["sensors"] = len(sensors)
                report["import_and_retrieval_calls"] = dict(counters)
                # Application-owned interval index; DISTINCT prevents per-track retrieval.
                query = """SELECT DISTINCT o.blob_id FROM c_occurrence o
                    JOIN ms_blobs b ON b.id=o.blob_id AND b.state='ready'
                    WHERE o.start_time<? AND o.end_time>? ORDER BY o.blob_id"""
                params = (instant(index["end"]), instant(index["start"]))
                report["query_plan"] = catalog.sql("EXPLAIN QUERY PLAN " + query, params, write=False)
                counters.clear()
                begin = time.perf_counter()
                for _ in range(100):
                    selected = catalog.sql(query, params, write=False)
                report["query_100_seconds"] = time.perf_counter() - begin
                report["selected_recordings"] = len(selected)
                report["metadata_query_calls"] = dict(counters)
                assert sum(counters[name] for name in methods) == 0
                # Same catalog through ordinary SQL, without MeldDB's managed APIs.
                with catalog_access(db), closing(sqlite3.connect(db)) as plain:
                    assert plain.execute("SELECT count(*) FROM ms_blobs WHERE state='ready'").fetchone()[0] == files
                    assert not plain.execute("PRAGMA foreign_key_check").fetchall()
                    report["plain_sql_query_count"] = len(plain.execute(query, params).fetchall())
                def write_annotations():
                    with Catalog(db, adapter=adapter) as writer:
                        updating = Store(writer, storage)
                        for version in range(1, 26):
                            updating.update_metadata("recording-0", schema=RECORDING,
                                                     changes={"annotation": "qualification"}, expected_version=version)
                with catalog.transaction(write=False) as tx:
                    assert tx.sql(f"SELECT annotation FROM {TABLE} WHERE id='recording-0'")[0]["annotation"] is None
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        pool.submit(write_annotations).result(timeout=60)
                    assert tx.sql(f"SELECT annotation FROM {TABLE} WHERE id='recording-0'")[0]["annotation"] is None
                    report["wal_bytes_with_reader"] = Path(str(db) + "-wal").stat().st_size
                assert store.stat("recording-0")["version"] == 26
            with Catalog(db, adapter=adapter, maintenance=True) as catalog:
                store = Store(catalog, storage)
                report["sqlite_maintenance"] = catalog.maintain_sqlite()
                begin = time.perf_counter()
                store.backup(work / "backup", application={"id": "s08-cuas-fixture", "schema_revision": "1"})
                report["backup_seconds"] = time.perf_counter() - begin
            begin = time.perf_counter()
            restored = restore_backup(work / "backup", work / "restored")
            report["restore_seconds"] = time.perf_counter() - begin
            with Catalog(restored["catalog"], adapter=adapter) as catalog:
                restored_store = Store(catalog, LocalStorage(restored["storage"]))
                assert catalog.sql("SELECT count(*) AS n FROM c_occurrence", write=False)[0]["n"] == report["track_occurrences"]
                assert restored_store.stat("recording-0")["version"] == 26
                for number, (_, descriptor) in enumerate(originals):
                    with restored_store.materialize(f"recording-{number}") as verified:
                        assert hash_file(verified) == descriptor
            report["observed_work_bytes_after_restore"] = sum(p.stat().st_size for p in work.rglob("*") if p.is_file())
            report["max_per_blob_staging_bytes"] = max(p.stat().st_size for p in paths)
            for path, descriptor in originals:
                assert hash_file(path) == descriptor
            report["originals_unchanged"] = True
            report["final_peak_rss_bytes"] = peak_rss()
            report["baseline_peak_rss_bytes"] = baseline
            report["elapsed_seconds"] = time.perf_counter() - started
        # This report intentionally excludes paths, UUIDs, coordinates and digests.
        with output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
        print(json.dumps(report), flush=True)
        return report
    finally:
        Transaction.sql = sql
        for name, method in methods.items():
            setattr(obstore, name, method)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--files", type=int, default=100)
    parser.add_argument("--adapter", choices=("sqlite", "melddb"), default="melddb")
    parser.add_argument("--output", required=True)
    parser.add_argument("--s3", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.files <= 1000:
        parser.error("--files must be between 1 and 1000")
    run(args.source_root, files=args.files, adapter=args.adapter, output=args.output, s3=args.s3)


if __name__ == "__main__":
    main()
