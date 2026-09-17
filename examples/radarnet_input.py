"""Read-only RadarNet adapter for qualification, not part of MeldStore.

Scan projected columns in batches. Preserve ns payload timestamps; catalog
intervals round outward to microseconds and use an exclusive upper bound.
"""

from datetime import datetime, timedelta, timezone


def bounds(low, high):
    origin = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return origin + timedelta(microseconds=low // 1000), origin + timedelta(microseconds=high // 1000 + 1)


def inspect_recording(path):
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    tracks, sensors = {}, set()
    low = high = None
    missing_tracks = 0
    with pq.ParquetFile(path) as file:
        if file.schema_arrow.field("time").type != pa.timestamp("ns", tz="UTC"):
            raise ValueError("RadarNet qualification expects UTC nanosecond timestamps")
        rows = file.metadata.num_rows
        for batch in file.iter_batches(batch_size=65536, columns=["time", "sns_uuid", "trk_uuid"]):
            times, sensor, track = batch.columns
            if times.null_count or sensor.null_count:
                raise ValueError("Recording has missing timestamps or sensor identities")
            sensors.update(pc.unique(sensor).to_pylist())
            span = pc.min_max(times.cast(pa.int64())).as_py()
            if span["min"] is None:
                continue
            low = span["min"] if low is None else min(low, span["min"])
            high = span["max"] if high is None else max(high, span["max"])
            missing_tracks += track.null_count
            grouped = pa.table({"track": track, "stamp": times.cast(pa.int64())}).group_by(
                "track").aggregate([("stamp", "min"), ("stamp", "max")])
            for item in grouped.to_pylist():
                key = item["track"]
                if key is None:
                    continue
                if not key:
                    raise ValueError("Empty track identity")
                previous = tracks.get(key, (item["stamp_min"], item["stamp_max"]))
                tracks[key] = (min(previous[0], item["stamp_min"]), max(previous[1], item["stamp_max"]))
    if len(sensors) != 1 or low is None or not tracks:
        raise ValueError("Expected one sensor and at least one timestamped track per recording")
    start, end = bounds(low, high)
    return {"rows": rows, "sensor": next(iter(sensors)), "start": start, "end": end,
            "missing_track_rows": missing_tracks,
            "tracks": [(key, *bounds(*span)) for key, span in sorted(tracks.items())]}
