"""Exclusive reporting and explicit, restartable object cleanup. No automatic GC."""

from . import lifecycle
from .errors import ConflictError, IntegrityError, StorageError, ValidationError
from .storage import CLEANUP_PATTERN


def _bound(value, maximum=1000000):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValidationError(f"Bound must be an integer between 1 and {maximum}")


def _check(store, tx):
    lifecycle.verify(tx)
    if tx.sql("SELECT * FROM pragma_foreign_key_check LIMIT 1"):
        raise IntegrityError("Catalog has foreign key violations; cleanup is unsafe")


def reconcile(store, *, verify=False, max_entries=10000):
    store.catalog.require_maintenance(store.storage.root)
    _bound(max_entries)
    if type(verify) is not bool:
        raise ValidationError("verify must be boolean")
    with store.catalog.transaction() as tx:
        lifecycle.verify(tx)
        prepared = tx.sql(
            "SELECT p.*,o.blob_id,b.state AS blob_state FROM ms_prepared p LEFT JOIN ms_objects o ON o.token=p.token LEFT JOIN ms_blobs b ON b.id=o.blob_id WHERE p.storage_id=? LIMIT ?",
            (store.storage.storage_id, max_entries + 1),
        )
        garbage = tx.sql(
            "SELECT * FROM ms_gc WHERE storage_id=? LIMIT ?",
            (store.storage.storage_id, max_entries + 1),
        )
        blobs = tx.sql("SELECT id FROM ms_blobs LIMIT ?", (max_entries + 1,))
        if any(len(rows) > max_entries for rows in (prepared, garbage, blobs)):
            raise ValidationError("Catalog report exceeds max_entries")
        errors = tx.sql("SELECT * FROM pragma_foreign_key_check LIMIT ?", (max_entries + 1,))
        for blob in blobs:
            try:
                store._record(blob["id"], tx, ready_only=False)
            except IntegrityError as exc:
                errors.append({"id": blob["id"], "error": str(exc)})
    inventory = store.storage.inventory(max_entries=max_entries)
    known = {p["object_key"]: p for p in prepared}
    queued = {g["object_key"]: g for g in garbage}
    report = {
        "ready": [],
        "publishing": [],
        "prepared": [],
        "pending": [],
        "missing": [],
        "corrupt": [],
        "orphans": [],
        "staging": [],
        "unrecognized": [],
        "catalog_errors": errors,
    }
    for row in prepared:
        key = row["object_key"]
        if key in queued:
            if queued[key]["state"] == "pending":
                report["pending"].append(queued[key])
            continue
        kind = row["blob_state"] or "prepared"
        if kind not in ("ready", "publishing", "prepared"):
            report["catalog_errors"].append(
                {"id": row["blob_id"], "error": "Unexpected persistent lifecycle state"}
            )
            continue
        report[kind].append(row)
        if key not in inventory:
            report["missing"].append(key)
        elif inventory[key] != row["byte_size"]:
            report["corrupt"].append(key)
        elif verify:
            try:
                with store.storage.materialize(
                    key, byte_size=row["byte_size"], digest=row["digest"]
                ):
                    pass
            except IntegrityError:
                report["corrupt"].append(key)
    for key, item in queued.items():
        if key not in known and item["state"] == "pending":
            report["pending"].append(item)
    for key in inventory:
        if key in known or key in queued:
            if key in queued and queued[key]["state"] == "done":
                report["catalog_errors"].append(
                    {"object_key": key, "error": "Object reappeared after completed cleanup"}
                )
            continue
        if key in {
            "meldstore-storage.json",
            "meldstore-access.sqlite",
            "meldstore-access.sqlite-journal",
        }:
            continue
        kind = "orphans" if key.startswith("objects/") else "staging"
        report[kind if CLEANUP_PATTERN.fullmatch(key) else "unrecognized"].append(key)
    return report


def queue_orphans(store, keys):
    store.catalog.require_maintenance(store.storage.root)
    if (
        not isinstance(keys, (tuple, list))
        or not 1 <= len(keys) <= 1000
        or any(not isinstance(k, str) for k in keys)
        or len(set(keys)) != len(keys)
    ):
        raise ValidationError("Supply 1..1000 distinct exact keys from reconciliation")
    for key in keys:
        store.storage.cleanup_key(key)
    with store.catalog.transaction() as tx:
        _check(store, tx)
        for key in keys:
            if tx.sql(
                "SELECT 1 FROM ms_prepared WHERE storage_id=? AND object_key=?",
                (store.storage.storage_id, key),
            ):
                raise ConflictError(
                    "Known prepared objects are protected; resolve or discard their token explicitly"
                )
            tx.sql(
                "INSERT OR IGNORE INTO ms_gc(storage_id,object_key,reason) VALUES(?,?,'orphan')",
                (store.storage.storage_id, key),
            )
    return list(keys)


def cleanup(store, *, limit=100):
    store.catalog.require_maintenance(store.storage.root)
    _bound(limit, 1000)
    with store.catalog.transaction() as tx:
        _check(store, tx)
        jobs = tx.sql(
            "SELECT * FROM ms_gc WHERE storage_id=? AND state='pending' ORDER BY object_key LIMIT ?",
            (store.storage.storage_id, limit),
        )
        # Validate the entire selected batch before deleting any bytes.
        for job in jobs:
            if tx.sql(
                "SELECT 1 FROM ms_prepared p JOIN ms_objects o ON o.token=p.token WHERE p.storage_id=? AND p.object_key=?",
                (job["storage_id"], job["object_key"]),
            ) or (
                job["blob_id"] and tx.sql("SELECT 1 FROM ms_blobs WHERE id=?", (job["blob_id"],))
            ):
                raise ConflictError("Queued object still has a live catalog reference")
    results = []
    for job in jobs:
        error = None
        try:
            store.storage.delete_object(job["object_key"])
        except (StorageError, ValidationError) as exc:
            error = str(exc)
        # A crash between deletion and this commit leaves a safely retryable job.
        with store.catalog.transaction() as tx:
            tx.sql(
                "UPDATE ms_gc SET state=?,last_error=? WHERE storage_id=? AND object_key=? AND state='pending'",
                ("pending" if error else "done", error, job["storage_id"], job["object_key"]),
            )
        results.append(
            {
                "object_key": job["object_key"],
                "state": "pending" if error else "done",
                "error": error,
            }
        )
    return results
