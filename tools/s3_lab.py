"""Disposable, loopback-only RustFS qualification. Never uses ambient AWS credentials.

Run: uv run --frozen python tools/s3_lab.py [--probe-only]
Creates only uniquely named test resources; removes its own container and volumes.
The tiny SigV4 helper is qualification tooling, not a library S3 implementation.
"""

import argparse
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import obstore
from obstore.store import S3Store

from meldstore import BlobSchema, Catalog, S3Storage, StorageError, Store

IMAGE = "rustfs/rustfs:1.0.0-rc.6@sha256:97171b3d72cd47dc81000f92ea84de25608bfc35a94c965501afaeb5d99f6035"
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


class Admin:
    """Minimal SigV4 for the disposable local bucket's test administration."""

    def __init__(self, endpoint, access_key, secret_key):
        url = urllib.parse.urlsplit(endpoint)
        if url.scheme != "http" or url.hostname != "127.0.0.1" or url.path:
            raise ValueError("Qualification admin accepts a loopback HTTP endpoint only")
        self.endpoint, self.access_key, self.secret_key = endpoint, access_key, secret_key

    def request(self, method, path, *, query=(), body=b"", headers=None, timeout=20):
        instant = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        date = instant[:8]
        digest = hashlib.sha256(body).hexdigest()
        canonical_path = urllib.parse.quote(path, safe="/-_.~")
        canonical_query = "&".join(
            f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
            for k, v in sorted(query)
        )
        signed = {
            "host": urllib.parse.urlsplit(self.endpoint).netloc,
            "x-amz-content-sha256": digest,
            "x-amz-date": instant,
            **(headers or {}),
        }
        names = ";".join(sorted(signed))
        canonical = "\n".join([
            method, canonical_path, canonical_query,
            "".join(f"{k}:{signed[k].strip()}\n" for k in sorted(signed)), names, digest,
        ])
        scope = f"{date}/us-east-1/s3/aws4_request"
        to_sign = f"AWS4-HMAC-SHA256\n{instant}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
        key = ("AWS4" + self.secret_key).encode()
        for item in (date, "us-east-1", "s3", "aws4_request"):
            key = hmac.new(key, item.encode(), hashlib.sha256).digest()
        signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
        signed["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={names}, Signature={signature}"
        )
        url = self.endpoint + canonical_path + ("?" + canonical_query if query else "")
        request = urllib.request.Request(url, data=body, headers=signed, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as result:
            return result.read()


class Lab:
    def __init__(self):
        self.name = "meldstore-s07-" + secrets.token_hex(6)
        self.access_key = "test" + secrets.token_hex(12)
        self.secret_key = secrets.token_hex(32)
        self.bucket = "meldstore-qualification"
        self.config = None
        self.started = False

    def __enter__(self):
        try:
            subprocess.run([
                "docker", "run", "--detach", "--name", self.name,
                "--label", "meldstore.qualifier=s07", "--publish", "127.0.0.1::9000",
                "--mount", f"type=volume,source={self.name}-data,target=/data",
                "--mount", f"type=volume,source={self.name}-logs,target=/logs",
                "--env", f"RUSTFS_ACCESS_KEY={self.access_key}",
                "--env", f"RUSTFS_SECRET_KEY={self.secret_key}",
                "--env", "RUSTFS_CONSOLE_ENABLE=false", IMAGE,
            ], check=True, capture_output=True, text=True)
            self.started = True
            bindings = json.loads(subprocess.check_output([
                "docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", self.name
            ], text=True))["9000/tcp"]
            if len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
                raise RuntimeError(f"Docker did not honor the loopback-only binding: {bindings!r}")
            self.endpoint = "http://127.0.0.1:" + bindings[0]["HostPort"]
            self.admin = Admin(self.endpoint, self.access_key, self.secret_key)
            self.config = {
                "endpoint": self.endpoint, "region": "us-east-1",
                "access_key_id": self.access_key, "secret_access_key": self.secret_key,
                "allow_http": True, "virtual_hosted_style_request": False,
                "conditional_put": "etag", "copy_if_not_exists": "multipart",
            }
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    self.admin.request("PUT", "/" + self.bucket, timeout=1)
                    break
                except (urllib.error.URLError, OSError):
                    time.sleep(0.5)
            else:
                raise RuntimeError("RustFS did not become ready within 30 seconds")
            print(f"RustFS ready: {IMAGE}; endpoint={self.endpoint}", flush=True)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.started:
            subprocess.run(["docker", "rm", "--force", self.name], check=True, capture_output=True)
            subprocess.run(["docker", "volume", "rm", self.name + "-data", self.name + "-logs"],
                           check=True, capture_output=True)
            print("Removed this run's disposable RustFS container and test-data volumes.", flush=True)

    def probe(self):
        store = S3Store(self.bucket, prefix="capability-probe", config=self.config,
                        retry_config={"max_retries": 0})
        obstore.put(store, "marker", b"first", mode="create")
        try:
            obstore.put(store, "marker", b"second", mode="create")
        except obstore.exceptions.AlreadyExistsError:
            pass
        else:
            raise AssertionError("Backend ignored conditional PutObject")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload"
            with path.open("wb") as output:
                for _ in range(12):
                    output.write(b"a" * 1024 * 1024)
            obstore.put(store, "stage", path, use_multipart=True, chunk_size=5*1024*1024,
                        max_concurrency=2)
        obstore.copy(store, "stage", "final", overwrite=False)
        try:
            obstore.copy(store, "marker", "final", overwrite=False)
        except (obstore.exceptions.AlreadyExistsError, obstore.exceptions.PreconditionError):
            pass
        else:
            raise AssertionError("Backend ignored conditional multipart completion")
        result = obstore.get(store, "final")
        assert result.meta["size"] == 12 * 1024 * 1024
        assert all(bytes(chunk) == b"a" * len(chunk) for chunk in result.stream())
        response = self.admin.request("POST", f"/{self.bucket}/capability-probe/abandoned",
                                      query=(("uploads", ""),))
        upload_id = ET.fromstring(response).findtext("s3:UploadId", namespaces=NS)
        assert upload_id
        # Leave real uploaded parts without completing: object listings cannot
        # discover these, and ordinary object deletion is not an MPU abort.
        self.admin.request("PUT", f"/{self.bucket}/capability-probe/abandoned",
                           query=(("uploadId", upload_id), ("partNumber", "1")),
                           body=b"a" * (5 * 1024 * 1024))
        assert all(item["path"] != "abandoned" for batch in obstore.list(store) for item in batch)
        uploads = self.admin.request("GET", "/" + self.bucket,
                                     query=(("uploads", ""), ("prefix", "capability-probe/")))
        assert upload_id in uploads.decode()
        self.admin.request("DELETE", f"/{self.bucket}/capability-probe/abandoned",
                           query=(("uploadId", upload_id),))
        uploads = self.admin.request("GET", "/" + self.bucket,
                                     query=(("uploads", ""), ("prefix", "capability-probe/")))
        assert upload_id not in uploads.decode()
        print("PASS conditional PUT, multipart upload/copy/create-only completion, list/abort MPU", flush=True)

    def outage_probe(self):
        """Exercise actual transport timeouts while the backend is paused."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = S3Storage(self.bucket, prefix="outage-probe",
                                coordination_directory=root / "coordinator", config=self.config,
                                retry_config={"max_retries": 0}, client_options={"timeout": "2s"})
            with Catalog(root / "catalog.db", maintenance=True) as catalog:
                store = Store(catalog, storage)
                schema = BlobSchema("dataset", {}, handlers=("bytes",))
                store.install_schema(schema)
                store.put(b"survives restart", schema=schema, metadata={}, handler="bytes", id="one")
                subprocess.run(["docker", "pause", self.name], check=True,
                               capture_output=True)
                assert store.stat("one")["state"] == "ready"
                try:
                    store.get("one")
                except StorageError:
                    pass
                else:
                    raise AssertionError("Paused server unexpectedly returned payload bytes")
                store.delete("one", expected_version=1)
                assert store.cleanup()[0]["state"] == "pending"
                subprocess.run(["docker", "unpause", self.name], check=True, capture_output=True)
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        self.admin.request("GET", "/" + self.bucket, timeout=1)
                        break
                    except (urllib.error.URLError, OSError):
                        time.sleep(0.5)
                else:
                    raise RuntimeError("RustFS did not resume within 30 seconds")
                assert store.cleanup()[0]["state"] == "done"
                assert store.cleanup() == []
        print("PASS real server outage: SQL remains readable, payload read fails, deletion resumes", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--workload-source-root", help="Opt-in private RadarNet input directory; never used by CI")
    parser.add_argument("--workload-output", help="New aggregate-only JSON report outside the input directory")
    args = parser.parse_args()
    if bool(args.workload_source_root) != bool(args.workload_output) or (args.probe_only and args.workload_source_root):
        parser.error("Workload requires both source root and output, without --probe-only")
    with Lab() as lab:
        lab.probe()
        if not args.probe_only:
            lab.outage_probe()
            env = dict(os.environ, MELDSTORE_S3_CONFIG=json.dumps(lab.config),
                       MELDSTORE_S3_BUCKET=lab.bucket)
            subprocess.run([sys.executable, "-m", "pytest", "tests/test_s3.py", "-q", "--tb=short", "-p", "no:cacheprovider"],
                           env=env, check=True)
            for size in (32, 256):
                subprocess.run([sys.executable, "tools/qualify_s3.py", "--mib", str(size)],
                               env=env, check=True)
            if args.workload_source_root:
                subprocess.run([sys.executable, "-m", "tools.qualify_workload", "--s3",
                                "--source-root", args.workload_source_root,
                                "--output", args.workload_output, "--adapter", "sqlite"],
                               env=env, check=True)


if __name__ == "__main__":
    main()
