"""Local-only obstore integration. No catalog SQL or cache policy lives here."""

import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import obstore
import xxhash
from obstore.store import LocalStore

from .errors import IntegrityError, StorageError, ValidationError

CHUNK_SIZE = 1024 * 1024
KEY_PATTERN = re.compile(r"objects/[0-9a-f]{32}")
CLEANUP_PATTERN = re.compile(r"(?:objects|staging)/[0-9a-f]{32}")


def hash_file(path):
    digest = xxhash.xxh3_128()
    size = 0
    with Path(path).open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


class LocalStorage:
    """One private object root, with an on-disk identity and bounded-memory transfers."""

    def __init__(self, root, *, staging_directory=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.staging_directory = staging_directory
        self._store = LocalStore(prefix=self.root)
        marker = "meldstore-storage.json"
        try:
            try:
                obstore.put(
                    self._store,
                    marker,
                    json.dumps({"version": 1, "id": uuid4().hex}).encode(),
                    mode="create",
                )
            except obstore.exceptions.AlreadyExistsError:
                pass
            result = obstore.get(self._store, marker)
            if result.meta["size"] > 1024:
                raise ValidationError("Invalid storage identity file")
            data = json.loads(bytes(result.bytes()))
            if data.get("version") != 1 or not re.fullmatch(r"[0-9a-f]{32}", data.get("id", "")):
                raise ValidationError("Unsupported storage identity file")
            self.storage_id = data["id"]
        except Exception as exc:
            raise StorageError("Could not initialize local storage identity") from exc

    @contextmanager
    def snapshot(self, source):
        """Own a closed private copy; never rename or modify the caller's file."""
        with tempfile.TemporaryDirectory(prefix="meldstore-", dir=self.staging_directory) as temp:
            path = Path(temp) / "payload"
            with Path(source).open("rb") as src, path.open("xb") as dst:
                before = os.fstat(src.fileno())
                if not Path(source).is_file():
                    raise ValidationError("Source must be a regular file")
                shutil.copyfileobj(src, dst, CHUNK_SIZE)
                dst.flush()
                os.fsync(dst.fileno())
                after = os.fstat(src.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise IntegrityError("Source changed while taking its snapshot")
            yield path

    def upload(self, path, key):
        """Stream a private stage, then create-only local promotion. Never overwrite an object."""
        self._key(key)
        stage = "staging/" + uuid4().hex
        try:
            obstore.put(
                self._store,
                stage,
                Path(path),
                use_multipart=True,
                chunk_size=5 * CHUNK_SIZE,
                max_concurrency=2,
            )
            obstore.rename(self._store, stage, key, overwrite=False)
        except Exception as exc:
            # Do not delete an uncertain destination or attempt an overwrite retry.
            raise StorageError("Local upload failed; staging/orphan objects may remain") from exc

    def inventory(self, *, max_entries):
        result = {}
        try:
            for batch in obstore.list(self._store, chunk_size=100):
                for item in batch:
                    result[item["path"]] = item["size"]
                    if len(result) > max_entries:
                        raise ValidationError(
                            "Inventory exceeds max_entries; increase the explicit bound"
                        )
        except ValidationError:
            raise
        except Exception as exc:
            raise StorageError("Object inventory failed; no cleanup was performed") from exc
        return result

    def cleanup_key(self, key):
        if not isinstance(key, str) or not CLEANUP_PATTERN.fullmatch(key):
            raise ValidationError("Cleanup accepts only exact MeldStore object/staging keys")
        path = self.root / key
        if not path.resolve().is_relative_to(self.root) or any(
            p.is_symlink() or p.is_junction() for p in (path.parent, path)
        ):
            raise ValidationError("Cleanup will not follow symlinks or junctions")
        if path.exists() and not path.is_file():
            raise ValidationError("Cleanup target must be a regular file")

    def delete_object(self, key):
        self.cleanup_key(key)
        try:
            obstore.delete(self._store, key)
        except (FileNotFoundError, obstore.exceptions.NotFoundError):
            pass
        except Exception as exc:
            raise StorageError("Object deletion failed; cleanup remains pending") from exc

    @staticmethod
    def _key(key):
        if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
            raise ValidationError("Invalid MeldStore object key")

    @contextmanager
    def materialize(self, key, *, byte_size, digest):
        self._key(key)
        with tempfile.TemporaryDirectory(prefix="meldstore-", dir=self.staging_directory) as temp:
            path = Path(temp) / "payload"
            actual = xxhash.xxh3_128()
            size = 0
            try:
                try:
                    result = obstore.get(self._store, key)
                except (FileNotFoundError, obstore.exceptions.NotFoundError) as exc:
                    raise IntegrityError("Catalog references a missing object") from exc
                if result.meta["size"] != byte_size:
                    raise IntegrityError("Stored size differs from catalog")
                with path.open("xb") as target:
                    for chunk in result.stream(min_chunk_size=CHUNK_SIZE):
                        size += len(chunk)
                        if size > byte_size:
                            raise IntegrityError("Stored data exceeds catalog size")
                        target.write(chunk)
                        actual.update(chunk)
            except IntegrityError:
                raise
            except Exception as exc:
                raise StorageError("Could not read the stored object") from exc
            if size != byte_size or actual.hexdigest() != digest:
                raise IntegrityError("Stored bytes fail XXH3-128 verification")
            yield path
