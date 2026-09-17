"""Synthetic application fixture, NOT a MeldStore feature or prescribed domain model.

The application owns every c_* table/trigger and all correlation decisions.
Run: uv run --extra arrow python -m examples.cuas_catalog
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from meldstore import BlobSchema, Catalog, Index, LocalStorage, Store, Text, Timestamp
from meldstore import quote_identifier as q

RECORDING = BlobSchema("recording", {
    "start_time": Timestamp(required=True, immutable=True),
    "end_time": Timestamp(required=True, immutable=True),
    "annotation": Text(),
}, indexes=(Index("start_time", "end_time"),))
TABLE = q(RECORDING.table_name)


def instant(value):
    return Timestamp(required=True).normalize(value)


def install(store):
    """Install into a fresh example catalog, not a domain-schema migration engine."""
    store.install_schema(RECORDING)
    statements = [
        "CREATE TABLE c_sensor(id TEXT PRIMARY KEY NOT NULL)",
        """CREATE TABLE c_sensor_config(id TEXT PRIMARY KEY NOT NULL,
            sensor_id TEXT NOT NULL REFERENCES c_sensor(id), details TEXT NOT NULL)""",
        "CREATE TABLE c_aircraft(id TEXT PRIMARY KEY NOT NULL)",
        """CREATE TABLE c_aircraft_config(id TEXT PRIMARY KEY NOT NULL,
            aircraft_id TEXT NOT NULL REFERENCES c_aircraft(id), details TEXT NOT NULL)""",
        f"""CREATE TABLE c_recording_source(
            blob_id TEXT PRIMARY KEY NOT NULL REFERENCES {TABLE}(id) ON DELETE RESTRICT,
            sensor_config_id TEXT NOT NULL REFERENCES c_sensor_config(id),
            kind TEXT NOT NULL CHECK(kind IN ('radar','truth')),
            aircraft_config_id TEXT REFERENCES c_aircraft_config(id),
            CHECK((kind='truth' AND aircraft_config_id IS NOT NULL) OR
                  (kind='radar' AND aircraft_config_id IS NULL)))""",
        """CREATE TABLE c_occurrence(
            blob_id TEXT NOT NULL REFERENCES c_recording_source(blob_id) ON DELETE RESTRICT,
            track_uuid TEXT NOT NULL CHECK(length(track_uuid)>0),
            start_time TEXT NOT NULL, end_time TEXT NOT NULL CHECK(end_time>start_time),
            PRIMARY KEY(blob_id,track_uuid))""",
        "CREATE INDEX c_occurrence_interval ON c_occurrence(start_time,end_time,blob_id)",
        """CREATE TABLE c_test_run(id TEXT PRIMARY KEY NOT NULL,
            start_time TEXT NOT NULL, end_time TEXT NOT NULL CHECK(end_time>start_time))""",
        """CREATE TABLE c_correlation(
            run_id TEXT NOT NULL REFERENCES c_test_run(id),
            radar_blob TEXT NOT NULL, radar_track TEXT NOT NULL,
            truth_blob TEXT NOT NULL, truth_track TEXT NOT NULL,
            evidence TEXT NOT NULL,
            PRIMARY KEY(run_id,radar_blob,radar_track,truth_blob,truth_track),
            FOREIGN KEY(radar_blob,radar_track) REFERENCES c_occurrence(blob_id,track_uuid),
            FOREIGN KEY(truth_blob,truth_track) REFERENCES c_occurrence(blob_id,track_uuid))""",
        "CREATE INDEX c_correlation_truth ON c_correlation(truth_blob,truth_track)",
        """CREATE TRIGGER c_correlation_types BEFORE INSERT ON c_correlation
            WHEN (SELECT kind FROM c_recording_source WHERE blob_id=NEW.radar_blob)!='radar'
              OR (SELECT kind FROM c_recording_source WHERE blob_id=NEW.truth_blob)!='truth'
            BEGIN SELECT RAISE(ABORT,'correlation must link radar to truth'); END""",
        f"""CREATE TRIGGER c_occurrence_insert BEFORE INSERT ON c_occurrence
            WHEN (SELECT state FROM ms_blobs WHERE id=NEW.blob_id)!='publishing'
              OR NEW.start_time < (SELECT start_time FROM {TABLE} WHERE id=NEW.blob_id)
              OR NEW.end_time > (SELECT end_time FROM {TABLE} WHERE id=NEW.blob_id)
            BEGIN SELECT RAISE(ABORT,'occurrences require an unpublished containing recording'); END""",
        """CREATE TRIGGER c_occurrence_update BEFORE UPDATE ON c_occurrence
            BEGIN SELECT RAISE(ABORT,'occurrences are immutable'); END""",
        f"""CREATE TRIGGER c_publish BEFORE UPDATE OF state ON ms_blobs
            WHEN NEW.schema_name='recording' AND NEW.state='ready' AND (
                NOT EXISTS(SELECT 1 FROM c_recording_source WHERE blob_id=NEW.id)
                OR NOT EXISTS(SELECT 1 FROM c_occurrence WHERE blob_id=NEW.id)
                OR (SELECT end_time<=start_time FROM {TABLE} WHERE id=NEW.id)
                OR ((SELECT kind FROM c_recording_source WHERE blob_id=NEW.id)='truth'
                    AND (SELECT count(*) FROM c_occurrence WHERE blob_id=NEW.id)!=1))
            BEGIN SELECT RAISE(ABORT,'recording publication requires source and valid tracks'); END""",
    ]
    for table in ("c_sensor_config", "c_aircraft_config", "c_recording_source", "c_correlation"):
        statements.append(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE ON {table}
            BEGIN SELECT RAISE(ABORT,'application identity/evidence is immutable'); END""")
    with store.catalog.transaction() as tx:
        for statement in statements:
            tx.sql(statement)


def publish_recording(store, path, *, id, sensor_config, start, end, tracks,
                      aircraft_config=None):
    """Application validates its payload index, then publishes all SQL rows together.

tracks is an iterable of (UUID, aware start, exclusive aware end). The application
must derive it from its payload; this helper does not claim to validate arbitrary
Parquet schemas. Failed publication leaves an explicit prepared operation.
"""
    token = store.prepare_file(path, schema=RECORDING)
    with store.catalog.transaction() as tx:
        store.finalize(token, schema=RECORDING, id=id, metadata={
            "start_time": start, "end_time": end, "annotation": None}, tx=tx)
        tx.sql("INSERT INTO c_recording_source VALUES(?,?,?,?)",
               (id, sensor_config, "truth" if aircraft_config else "radar", aircraft_config))
        for track, begin, finish in tracks:
            tx.sql("INSERT INTO c_occurrence VALUES(?,?,?,?)",
                   (id, track, instant(begin), instant(finish)))
        store.publish(id, tx=tx)
    return store.stat(id)


CORRELATIONS = """SELECT c.radar_blob,c.radar_track,c.truth_blob,c.truth_track,
    s.sensor_id,a.aircraft_id
    FROM c_correlation c
    JOIN c_test_run r ON r.id=c.run_id
    JOIN c_occurrence o ON (o.blob_id=c.radar_blob AND o.track_uuid=c.radar_track)
    JOIN c_occurrence t ON (t.blob_id=c.truth_blob AND t.track_uuid=c.truth_track)
    JOIN c_recording_source rs ON rs.blob_id=o.blob_id
    JOIN c_sensor_config s ON s.id=rs.sensor_config_id
    JOIN c_recording_source ts ON ts.blob_id=t.blob_id
    JOIN c_aircraft_config a ON a.id=ts.aircraft_config_id
    JOIN ms_blobs rb ON rb.id=o.blob_id AND rb.state='ready'
    JOIN ms_blobs tb ON tb.id=t.blob_id AND tb.state='ready'
    WHERE c.run_id=? AND o.start_time<r.end_time AND o.end_time>r.start_time
      AND t.start_time<r.end_time AND t.end_time>r.start_time
      AND o.start_time<t.end_time AND o.end_time>t.start_time
    ORDER BY c.radar_blob,c.radar_track,c.truth_blob,c.truth_track"""


def selected_recordings(rows):
    """Deduplicate before materializing: tracks do not imply separate files."""
    return sorted({row[key] for row in rows for key in ("radar_blob", "truth_blob")})


def fixture(store, directory):
    import pyarrow as pa
    import pyarrow.parquet as pq

    install(store)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(seconds=10)
    with store.catalog.transaction() as tx:
        for sensor in ("radar-sensor", "truth-logger"):
            tx.sql("INSERT INTO c_sensor VALUES(?)", (sensor,))
            tx.sql("INSERT INTO c_sensor_config VALUES(?,?,?)", (sensor + "-config", sensor, '{}'))
        tx.sql("INSERT INTO c_aircraft VALUES('synthetic-aircraft')")
        tx.sql("INSERT INTO c_aircraft_config VALUES('aircraft-config','synthetic-aircraft','{}')")
        tx.sql("INSERT INTO c_test_run VALUES(?,?,?)", ("run", instant(start), instant(end)))
    for id, tracks, sensor, aircraft in (
        ("radar", ["radar-track-a", "radar-track-b"], "radar-sensor-config", None),
        ("truth", ["truth-track"], "truth-logger-config", "aircraft-config"),
    ):
        path = Path(directory) / (id + ".parquet")
        pq.write_table(pa.table({"track_uuid": tracks, "time": [start] * len(tracks)}), path)
        publish_recording(store, path, id=id, sensor_config=sensor, start=start, end=end,
                          tracks=[(track, start, end) for track in tracks], aircraft_config=aircraft)
    with store.catalog.transaction() as tx:
        for track in ("radar-track-a", "radar-track-b"):
            tx.sql("INSERT INTO c_correlation VALUES(?,?,?,?,?,?)",
                   ("run", "radar", track, "truth", "truth-track", "synthetic test evidence"))
    return start, end


def exercise(store, directory):
    fixture(store, directory)
    rows = store.catalog.sql(CORRELATIONS, ("run",), write=False)
    ids = selected_recordings(rows)
    count = 0
    for id in ids:
        with store.materialize(id) as path:
            assert path.read_bytes() == (Path(directory) / (id + ".parquet")).read_bytes()
        count += 1
    return {"correlations": len(rows), "recordings": ids, "materializations": count}


def main():
    with TemporaryDirectory(prefix="meldstore-cuas-example-") as directory:
        root = Path(directory)
        with Catalog(root / "catalog.db") as catalog:
            print(exercise(Store(catalog, LocalStorage(root / "objects")), root))


if __name__ == "__main__":
    main()
