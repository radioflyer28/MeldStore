"""S3 payload storage with a single, local cooperative coordination directory."""

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

import obstore
from obstore.store import S3Store

from .access import Lease, canonical
from .errors import StorageError, ValidationError
from .storage import CHUNK_SIZE, CLEANUP_PATTERN, LocalStorage

_MARKER_LIMIT = 2048
_REMOTE_MARKER = "meldstore-storage.json"
_IDENTITY = re.compile(r"[0-9a-f]{32}")
_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


def _identity(value):
    return isinstance(value, str) and _IDENTITY.fullmatch(value) is not None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("Duplicate storage identity field")
        result[key] = value
    return result


def _decode(raw, fields):
    if len(raw) > _MARKER_LIMIT:
        raise ValidationError("Storage identity exceeds 2 KiB")
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    if (
        not isinstance(data, dict)
        or set(data) != set(fields)
        or type(data["version"]) is not int
        or data["version"] != 1
        or not _identity(data["coordinator_id"])
    ):
        raise ValidationError("Invalid storage identity")
    return data


def _encode(data):
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > _MARKER_LIMIT:
        raise ValidationError("Storage identity exceeds 2 KiB")
    return raw


class S3Storage(LocalStorage):
    """Remote objects; local staging and access gates, never local payload storage.

    All participants must share this coordination directory and catalog. This is
    not a distributed lease. Keep the directory when reopening the remote store.
    Failed initialization is deliberately not repaired by a subsequent open.
    """

    @classmethod
    def _replica(cls, root, storage_id):
        # The inherited helper writes a LocalStore marker before constructing
        # cls. Never let an S3 transfer accidentally create local object storage.
        raise ValidationError("S3 replicas require explicit location and _fresh=True")

    def __init__(
        self,
        bucket,
        *,
        prefix,
        coordination_directory,
        config=None,
        client_options=None,
        retry_config=None,
        credential_provider=None,
        staging_directory=None,
        _storage_id=None,
        _fresh=False,
    ):
        if (
            not isinstance(bucket, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket)
            or ".." in bucket
        ):
            raise ValidationError("An explicit S3 bucket name is required")
        if not isinstance(prefix, str) or not prefix or any(
            part in ("", ".", "..") or not _SEGMENT.fullmatch(part)
            for part in prefix.split("/")
        ):
            raise ValidationError("S3 prefix requires nonempty canonical safe path segments")
        if not isinstance(config, Mapping):
            raise ValidationError("S3 config must explicitly specify endpoint")
        settings = dict(config)
        endpoint = settings.get("endpoint")
        if not isinstance(endpoint, str) or any(ord(c) <= 32 for c in endpoint):
            raise ValidationError("S3 endpoint must be an explicit HTTP(S) URL")
        try:
            url = urlsplit(endpoint)
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or "?" in endpoint
                or "#" in endpoint
                or "\\" in endpoint
            ):
                raise ValueError("Invalid endpoint")
            url.port  # Validate the port before any filesystem or network operations.
        except ValueError as exc:
            raise ValidationError("Invalid S3 endpoint URL") from exc
        for name, required in (("copy_if_not_exists", "multipart"), ("conditional_put", "etag")):
            if name in settings and settings[name] != required:
                raise ValidationError(f"S3 requires {name}={required!r}")
            settings[name] = required
        # Do not allow config aliases to redirect the explicitly named location
        # or override the required conditional-write behavior.
        for name in settings:
            if (
                not isinstance(name, str)
                or name != name.lower()
                or name.startswith("aws_")
                or name in ("bucket_name", "endpoint_url", "endpoint_url_s3")
            ):
                raise ValidationError("Use canonical obstore S3 config keys")
        if "bucket" in settings and settings["bucket"] != bucket:
            raise ValidationError("S3 config bucket conflicts with bucket argument")
        # Supply the bucket exactly once; obstore rejects duplicate aliases even
        # when their values match. Its explicit argument overrides environment.
        settings.pop("bucket", None)
        # The service-specific endpoint takes precedence over `endpoint`,
        # including AWS_ENDPOINT_URL_S3 inherited from the environment. Pin both
        # explicitly without changing process-wide environment or caller config.
        settings["aws_endpoint_url_s3"] = endpoint
        if _storage_id is not None and (not _fresh or not _identity(_storage_id)):
            raise ValidationError("Replica identity requires a fresh store and a valid identity")
        if coordination_directory is None or str(coordination_directory) == "":
            raise ValidationError("A local coordination directory is required")

        self.root = Path(coordination_directory).resolve()
        self.staging_directory = staging_directory
        self.location = {"endpoint": endpoint, "bucket": bucket, "prefix": prefix}
        # Bind identity to the canonical local path: copying just the marker to
        # another directory must not create an independent maintenance gate.
        coordinator_id = uuid5(NAMESPACE_URL, canonical(self.root)).hex
        marker = self.root / "meldstore-s3.json"
        location_bytes = _encode({
            "version": 1, "location": self.location,
            "storage_id": _storage_id or uuid4().hex, "coordinator_id": coordinator_id,
        })
        try:
            self._store = S3Store(
                bucket, prefix=prefix, config=settings, client_options=client_options,
                retry_config=retry_config, credential_provider=credential_provider,
            )
            self.root.mkdir(parents=True, exist_ok=not _fresh)
            lease = Lease(
                self.root / "meldstore-init.sqlite", owner=canonical(self.root), exclusive=True
            )
            try:
                if marker.exists():
                    with marker.open("rb") as stream:
                        local = _decode(stream.read(_MARKER_LIMIT + 1), (
                            "version", "location", "storage_id", "coordinator_id"
                        ))
                    if (
                        not _identity(local["storage_id"])
                        or local["location"] != self.location
                        or local["coordinator_id"] != coordinator_id
                    ):
                        raise ValidationError("S3 coordination identity/location mismatch")
                    remote = self._remote_identity()
                    if remote != {
                        "version": 1, "id": local["storage_id"],
                        "coordinator_id": coordinator_id,
                    }:
                        raise ValidationError("Remote S3 identity does not match local coordinator")
                else:
                    # A remote marker without its original local marker is not
                    # adoptable, even if the rest of the prefix happens to be empty.
                    for batch in obstore.list(self._store, chunk_size=1):
                        if batch:
                            raise ValidationError("New S3 storage requires an empty prefix")
                    local = _decode(location_bytes, (
                        "version", "location", "storage_id", "coordinator_id"
                    ))
                    with marker.open("xb") as stream:
                        stream.write(location_bytes)
                        stream.flush()
                        os.fsync(stream.fileno())
                    obstore.put(
                        self._store, _REMOTE_MARKER,
                        _encode({"version": 1, "id": local["storage_id"],
                                 "coordinator_id": coordinator_id}),
                        mode="create", use_multipart=False,
                    )
                self.storage_id = local["storage_id"]
                self.coordinator_id = coordinator_id
            finally:
                lease.close()
        except Exception as exc:
            # Retain local/remote evidence after uncertain writes. In particular,
            # never adopt a winning remote marker after AlreadyExistsError.
            raise StorageError("Could not initialize S3 storage; identity artifacts retained") from exc

    def _remote_identity(self):
        result = obstore.get(self._store, _REMOTE_MARKER, options={"range": (0, _MARKER_LIMIT + 1)})
        if result.meta["size"] > _MARKER_LIMIT:
            raise ValidationError("Remote storage identity exceeds 2 KiB")
        data = _decode(bytes(result.bytes()), ("version", "id", "coordinator_id"))
        if not _identity(data["id"]):
            raise ValidationError("Invalid remote storage identity")
        return data

    def upload(self, path, key):
        """Multipart stage followed by conditional copy; never rename or overwrite."""
        self._key(key)
        stage = "staging/" + uuid4().hex
        try:
            if Path(path).stat().st_size == 0:
                # Multipart server-side copy cannot portably copy an empty
                # source. The private snapshot's known empty payload is bounded.
                obstore.put(self._store, key, b"", mode="create", use_multipart=False)
                return
            obstore.put(
                self._store, stage, Path(path), use_multipart=True,
                chunk_size=5 * CHUNK_SIZE, max_concurrency=2,
            )
            obstore.copy(self._store, stage, key, overwrite=False)
            obstore.delete(self._store, stage)
        except Exception as exc:
            raise StorageError("S3 upload failed; staging/orphan objects may remain") from exc

    def cleanup_key(self, key):
        if not isinstance(key, str) or not CLEANUP_PATTERN.fullmatch(key):
            raise ValidationError("Cleanup accepts only exact MeldStore object/staging keys")
