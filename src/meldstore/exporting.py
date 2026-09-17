"""Verified copy-out to a new local filename; never a move or payload rewrite."""

import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from .access import canonical
from .errors import ConflictError, IntegrityError, StorageError, ValidationError
from .storage import CHUNK_SIZE, hash_file


def export_file(store, id, destination):
    try:
        try:
            requested = Path(destination)
            destination = requested.parent.resolve(strict=True) / requested.name
        except (TypeError, ValueError) as exc:
            raise ValidationError("Export requires a local destination filename") from exc
        if os.path.lexists(destination):
            raise ConflictError("Export destination already exists; it is never overwritten")
        target = canonical(destination)
        catalog = canonical(store.catalog.path)
        protected = {
            base + suffix
            for base in (catalog, catalog + ".meldstore-access")
            for suffix in ("", "-wal", "-shm", "-journal")
        }
        if target in protected or Path(target).is_relative_to(Path(canonical(store.storage.root))):
            raise ValidationError("Export destination must be outside managed storage/catalog files")
        record = store.stat(id)
        with store.materialize(id) as verified:
            # Same filesystem as destination: linking publishes a complete file
            # create-only, even if another exporter races after the early check.
            with TemporaryDirectory(prefix=".meldstore-export-", dir=destination.parent) as directory:
                staged = Path(directory) / "payload"
                with verified.open("rb") as incoming, staged.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, CHUNK_SIZE)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                if hash_file(staged) != (record["byte_size"], record["digest"]):
                    raise IntegrityError("Exported copy fails size/XXH3-128 verification")
                os.link(staged, destination)
        return destination
    except FileExistsError as exc:
        raise ConflictError("Export destination was created concurrently; it was not overwritten") from exc
    except OSError as exc:
        raise StorageError(
            "Local export failed; inspect the destination for an already published complete file"
        ) from exc
