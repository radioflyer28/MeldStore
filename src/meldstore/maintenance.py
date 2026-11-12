"""Exclusive reporting and explicit, restartable object cleanup. No automatic GC."""

from . import lifecycle
from .dialect import for_transaction
from .errors import ConflictError, IntegrityError, StorageError, ValidationError
from .storage import CLEANUP_PATTERN


def _bound(value, maximum=1000000):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValidationError(f"Bound must be an integer between 1 and {maximum}")


def _constraint_errors(tx, *, validate=True):
    if for_transaction(tx).postgres:
        # Ordinary SQL constraints are enforced by PostgreSQL. Refuse maintenance
        # if unvalidated constraints or disabled triggers undermine that guarantee.
        if validate:
            tx.validate_constraints()
        return tx.sql("""SELECT conname AS error FROM pg_constraint
            WHERE connamespace=current_schema()::regnamespace AND NOT convalidated
            UNION ALL SELECT tgname AS error FROM pg_trigger t
            JOIN pg_class c ON c.oid=t.tgrelid
            WHERE c.relnamespace=current_schema()::regnamespace AND t.tgenabled='D'""")
    return tx.sql("SELECT * FROM pragma_foreign_key_check")


def _check(store, tx):
    lifecycle.verify(tx)
    if _constraint_errors(tx):
        raise IntegrityError("Catalog has foreign key violations; cleanup is unsafe")


def reconcile(store, *, verify=False, max_entries=10000):
    store.catalog.require_maintenance(store.storage.root)
    _bound(max_entries)
    if type(verify) is not bool:
        raise ValidationError("verify must be boolean")
    with store.catalog.transaction() as tx:
        bind = for_transaction(tx).bind
        lifecycle.verify(tx)
        prepared = tx.sql(
            f"SELECT p.*,o.blob_id,b.state AS blob_state FROM ms_prepared p LEFT JOIN ms_objects o ON o.token=p.token LEFT JOIN ms_blobs b ON b.id=o.blob_id WHERE p.storage_id={bind} LIMIT {bind}",
            (store.storage.storage_id, max_entries + 1),
        )
        garbage = tx.sql(
            f"SELECT * FROM ms_gc WHERE storage_id={bind} LIMIT {bind}",
            (store.storage.storage_id, max_entries + 1),
        )
        blobs = tx.sql(f"SELECT id FROM ms_blobs LIMIT {bind}", (max_entries + 1,))
        if any(len(rows) > max_entries for rows in (prepared, garbage, blobs)):
            raise ValidationError("Catalog report exceeds max_entries")
        errors = _constraint_errors(tx)[:max_entries + 1]
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
        bind = for_transaction(tx).bind
        _check(store, tx)
        for key in keys:
            if tx.sql(
                f"SELECT 1 FROM ms_prepared WHERE storage_id={bind} AND object_key={bind}",
                (store.storage.storage_id, key),
            ):
                raise ConflictError(
                    "Known prepared objects are protected; resolve or discard their token explicitly"
                )
            tx.sql(
                f"INSERT INTO ms_gc(storage_id,object_key,reason) VALUES({bind},{bind},'orphan') ON CONFLICT DO NOTHING",
                (store.storage.storage_id, key),
            )
    return list(keys)


def cleanup(store, *, limit=100):
    store.catalog.require_maintenance(store.storage.root)
    _bound(limit, 1000)
    with store.catalog.transaction() as tx:
        bind = for_transaction(tx).bind
        _check(store, tx)
        jobs = tx.sql(
            f"SELECT * FROM ms_gc WHERE storage_id={bind} AND state='pending' ORDER BY object_key LIMIT {bind}",
            (store.storage.storage_id, limit),
        )
        # Validate the entire selected batch before deleting any bytes.
        for job in jobs:
            if tx.sql(
                f"SELECT 1 FROM ms_prepared p JOIN ms_objects o ON o.token=p.token WHERE p.storage_id={bind} AND p.object_key={bind}",
                (job["storage_id"], job["object_key"]),
            ) or (
                job["blob_id"] and tx.sql(f"SELECT 1 FROM ms_blobs WHERE id={bind}", (job["blob_id"],))
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
            bind = for_transaction(tx).bind
            tx.sql(
                f"UPDATE ms_gc SET state={bind},last_error={bind} WHERE storage_id={bind} AND object_key={bind} AND state='pending'",
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
