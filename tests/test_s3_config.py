"""Network-free S3 constructor validation and identity regression tests."""

import json
import os
import shutil
from unittest.mock import Mock

import obstore
import pytest
from obstore.store import MemoryStore, S3Store

from meldstore import StorageError, ValidationError
from meldstore.access import Lease, canonical
from meldstore.s3 import S3Storage


@pytest.fixture
def options(tmp_path):
    return {
        "bucket": "test-bucket",
        "prefix": "qualification/run-01",
        "coordination_directory": tmp_path / "coordinator",
        "config": {"endpoint": "https://storage.example.invalid"},
    }


@pytest.fixture
def no_backend(monkeypatch):
    backend = Mock(side_effect=AssertionError("Invalid input must not construct S3Store"))
    monkeypatch.setattr("meldstore.s3.S3Store", backend)
    return backend


@pytest.mark.parametrize("bucket", [None, 1, "", "ab", "a" * 64, "UPPER", "a/b", "a..b"])
def test_invalid_bucket_precedes_backend(options, no_backend, bucket):
    options["bucket"] = bucket
    with pytest.raises(ValidationError):
        S3Storage(**options)
    no_backend.assert_not_called()
    assert not options["coordination_directory"].exists()


@pytest.mark.parametrize("prefix", [
    None, 7, "", "/root", "root/", "a//b", ".", "..", "a/../b", "a/./b",
    "a\\b", "a b", "a%2fb", "a?b", "a#b", "a\x00b", "a\nb",
])
def test_invalid_prefix_precedes_backend(options, no_backend, prefix):
    options["prefix"] = prefix
    with pytest.raises(ValidationError):
        S3Storage(**options)
    no_backend.assert_not_called()
    assert not options["coordination_directory"].exists()


@pytest.mark.parametrize("config", [None, [], "endpoint", {}, {"endpoint": None}])
def test_missing_endpoint_precedes_backend(options, no_backend, config):
    options["config"] = config
    with pytest.raises(ValidationError):
        S3Storage(**options)
    no_backend.assert_not_called()


@pytest.mark.parametrize("endpoint", [
    "", 1, "storage.example.invalid", "ftp://storage.example.invalid", "https:///",
    "https://user@storage.example.invalid", "https://user:pass@storage.example.invalid",
    "https://storage.example.invalid?", "https://storage.example.invalid#",
    "https://storage.example.invalid?token=secret", "https://storage.example.invalid#fragment",
    "https://storage.example.invalid:invalid", "https://storage.example.invalid:65536",
    "https://[invalid", " https://storage.example.invalid", "https://storage.\nexample.invalid",
    "https://storage.example.invalid\\path",
])
def test_invalid_endpoint_precedes_backend(options, no_backend, endpoint):
    options["config"]["endpoint"] = endpoint
    with pytest.raises(ValidationError):
        S3Storage(**options)
    no_backend.assert_not_called()
    assert not options["coordination_directory"].exists()


@pytest.mark.parametrize(("key", "value"), [
    ("copy_if_not_exists", None), ("copy_if_not_exists", "header:x:y"),
    ("conditional_put", "disabled"), ("conditional_put", False),
    ("bucket", "other-bucket"), ("aws_bucket", "other-bucket"),
    ("aws_endpoint", "https://other.example.invalid"),
    ("aws_endpoint_url_s3", "https://other.example.invalid"),
    (1, "invalid-key"),
])
def test_conflicting_config_precedes_backend(options, no_backend, key, value):
    options["config"][key] = value
    with pytest.raises(ValidationError):
        S3Storage(**options)
    no_backend.assert_not_called()


@pytest.mark.parametrize(("key", "value"), [
    ("bucket_name", "other-bucket"), ("AWS_BUCKET", "other-bucket"),
    ("AWS_BUCKET_NAME", "other-bucket"),
    ("endpoint_url", "https://other.example.invalid"),
    ("AWS_ENDPOINT", "https://other.example.invalid"),
    ("AWS_ENDPOINT_URL", "https://other.example.invalid"),
    ("AWS_ENDPOINT_URL_S3", "https://other.example.invalid"),
    ("AWS_COPY_IF_NOT_EXISTS", "header:x:y"), ("AWS_CONDITIONAL_PUT", "disabled"),
    ("Bucket_Name", "other-bucket"), ("Bucket", "other-bucket"),
    ("Endpoint", "https://other.example.invalid"),
    ("Endpoint_Url", "https://other.example.invalid"),
    ("endpoint_url_s3", "https://other.example.invalid"),
    ("Aws_Endpoint_Url_S3", "https://other.example.invalid"),
    ("Conditional_Put", "disabled"), ("Copy_If_Not_Exists", "header:x:y"),
])
def test_location_and_safety_aliases_rejected_before_backend(options, no_backend, key, value):
    """Do not let obstore aliases bypass the persisted location or safety policy."""
    options["config"][key] = value
    with pytest.raises(ValidationError):
        S3Storage(**options)
    no_backend.assert_not_called()
    assert not options["coordination_directory"].exists()


@pytest.mark.parametrize(("identity", "fresh"), [
    ("a" * 32, False), ("", True), ("A" * 32, True), (1, True), ("a" * 31, True),
])
def test_invalid_replica_identity_precedes_backend(options, no_backend, identity, fresh):
    with pytest.raises(ValidationError):
        S3Storage(**options, _storage_id=identity, _fresh=fresh)
    no_backend.assert_not_called()


@pytest.mark.parametrize("directory", [None, ""])
def test_missing_coordination_directory_precedes_backend(options, no_backend, directory):
    options["coordination_directory"] = directory
    with pytest.raises(ValidationError):
        S3Storage(**options)
    no_backend.assert_not_called()


@pytest.fixture
def memory_backend(monkeypatch):
    backend = MemoryStore()
    factory = Mock(return_value=backend)
    monkeypatch.setattr("meldstore.s3.S3Store", factory)
    return backend, factory


def test_config_copied_credentials_not_persisted_and_identity_reopens(options, memory_backend):
    _, factory = memory_backend
    options["config"].update(access_key_id="test-access", secret_access_key="test-secret")
    before = dict(options["config"])
    first = S3Storage(**options)
    second = S3Storage(**options)
    assert first.storage_id == second.storage_id
    assert first.coordinator_id == second.coordinator_id
    assert options["config"] == before
    sent = factory.call_args.kwargs["config"]
    assert sent["copy_if_not_exists"] == "multipart"
    assert sent["conditional_put"] == "etag"
    assert sent["aws_endpoint_url_s3"] == before["endpoint"]
    raw = (first.root / "meldstore-s3.json").read_text()
    assert "test-access" not in raw and "test-secret" not in raw
    assert json.loads(raw)["location"] == {
        "endpoint": before["endpoint"], "bucket": options["bucket"], "prefix": options["prefix"],
    }


@pytest.mark.parametrize("matching_bucket_config", [False, True])
def test_explicit_location_dominates_environment_without_network(
    options, monkeypatch, matching_bucket_config,
):
    """Construct the real obstore backend, but route every operation to memory.

    Public backend config proves the pinned settings are accepted by obstore;
    never invoke get/list/put on that real backend in this network-free suite.
    """
    hostile = {
        "AWS_BUCKET": "environment-bucket",
        "AWS_BUCKET_NAME": "environment-bucket",
        "AWS_ENDPOINT": "https://environment.example.invalid",
        "AWS_ENDPOINT_URL": "https://environment.example.invalid",
        "AWS_ENDPOINT_URL_S3": "https://service-environment.example.invalid",
        "AWS_COPY_IF_NOT_EXISTS": "header:unsafe:yes",
        "AWS_CONDITIONAL_PUT": "disabled",
    }
    for name, value in hostile.items():
        monkeypatch.setenv(name, value)
    options["config"].update(
        region="us-east-1", access_key_id="test-only", secret_access_key="test-only",
    )
    if matching_bucket_config:
        options["config"]["bucket"] = options["bucket"]
    original = dict(options["config"])
    captured = []
    memory = MemoryStore()

    def construct(*args, **kwargs):
        backend = S3Store(*args, **kwargs)
        captured.append(backend.config)
        return memory

    monkeypatch.setattr("meldstore.s3.S3Store", construct)
    storage = S3Storage(**options)
    expected_endpoint = original["endpoint"]
    assert captured[0]["bucket"] == options["bucket"]
    assert captured[0]["endpoint"] == expected_endpoint
    assert captured[0]["endpoint_url_s3"] == expected_endpoint
    assert captured[0]["copy_if_not_exists"] == "multipart"
    assert captured[0]["conditional_put"] == "etag"
    assert options["config"] == original
    assert all(os.environ[name] == value for name, value in hostile.items())
    marker = json.loads((storage.root / "meldstore-s3.json").read_text())
    assert marker["location"] == {
        "endpoint": expected_endpoint, "bucket": options["bucket"], "prefix": options["prefix"],
    }


def test_copied_marker_cannot_create_another_same_host_gate(options, memory_backend, tmp_path):
    first = S3Storage(**options)
    other = tmp_path / "other-coordinator"
    other.mkdir()
    shutil.copyfile(first.root / "meldstore-s3.json", other / "meldstore-s3.json")
    options["coordination_directory"] = other
    with pytest.raises(StorageError):
        S3Storage(**options)


def test_initialization_lease_serializes_constructor(options, memory_backend):
    root = options["coordination_directory"]
    root.mkdir()
    lease = Lease(root / "meldstore-init.sqlite", owner=canonical(root), exclusive=True)
    try:
        with pytest.raises(StorageError):
            S3Storage(**options)
        assert not (root / "meldstore-s3.json").exists()
    finally:
        lease.close()


def test_existing_local_missing_remote_fails_closed(options, memory_backend):
    backend, _ = memory_backend
    first = S3Storage(**options)
    original = (first.root / "meldstore-s3.json").read_bytes()
    obstore.delete(backend, "meldstore-storage.json")
    with pytest.raises(StorageError):
        S3Storage(**options)
    assert (first.root / "meldstore-s3.json").read_bytes() == original
    assert not list(obstore.list(backend))


@pytest.mark.parametrize("bad_marker", [
    b"[]", b"null", b"{", b"x" * 2049,
    b'{"version":1,"version":1,"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
    b'"coordinator_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}',
])
def test_invalid_remote_identity_fails_closed(options, memory_backend, bad_marker):
    backend, _ = memory_backend
    first = S3Storage(**options)
    original = (first.root / "meldstore-s3.json").read_bytes()
    obstore.put(backend, "meldstore-storage.json", bad_marker)
    with pytest.raises(StorageError):
        S3Storage(**options)
    assert (first.root / "meldstore-s3.json").read_bytes() == original


@pytest.mark.parametrize(("field", "value"), [
    ("version", True), ("version", 1.0), ("version", "1"), ("version", 2),
    ("id", None), ("id", 7), ("id", "A" * 32), ("id", "d" * 32),
    ("coordinator_id", None), ("coordinator_id", "e" * 32),
])
def test_remote_identity_types_and_mismatches_fail_closed(options, memory_backend, field, value):
    backend, _ = memory_backend
    S3Storage(**options)
    raw = bytes(obstore.get(backend, "meldstore-storage.json").bytes())
    marker = json.loads(raw)
    marker[field] = value
    changed = json.dumps(marker).encode()
    obstore.put(backend, "meldstore-storage.json", changed)
    with pytest.raises(StorageError):
        S3Storage(**options)
    assert bytes(obstore.get(backend, "meldstore-storage.json").bytes()) == changed


@pytest.mark.parametrize("bad_marker", [b"[]", b"null", b"{", b"x" * 2049])
def test_invalid_local_marker_precedes_remote_access(
    options, memory_backend, monkeypatch, bad_marker,
):
    first = S3Storage(**options)
    marker = first.root / "meldstore-s3.json"
    marker.write_bytes(bad_marker)
    forbidden = Mock(side_effect=AssertionError("Invalid local marker must precede remote I/O"))
    for method in ("get", "list", "put"):
        monkeypatch.setattr(obstore, method, forbidden)
    with pytest.raises(StorageError):
        S3Storage(**options)
    forbidden.assert_not_called()
    assert marker.read_bytes() == bad_marker


def test_fresh_replica_preserves_id_and_cannot_reuse_directory(options, memory_backend):
    identity = "c" * 32
    first = S3Storage(**options, _fresh=True, _storage_id=identity)
    assert first.storage_id == identity
    with pytest.raises(StorageError):
        S3Storage(**options, _fresh=True, _storage_id=identity)
    assert S3Storage(**options).storage_id == identity
