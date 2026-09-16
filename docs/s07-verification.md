# S07 verification — S3-compatible payload storage

2026-09-16. S06/S06a were fast-forward merged to main (`992c4a8`); S07
branches from that baseline. User-approved Docker qualification replaces the
AWS-only S07 gate; **AWS itself remains unqualified**. The MVP still needs S08.

## Backend and runtime

- Docker Desktop 4.41.2 / Engine 28.1.1, Linux amd64 server on Windows.
- RustFS `1.0.0-rc.6`, immutable image
  `rustfs/rustfs:1.0.0-rc.6@sha256:97171b3d72cd47dc81000f92ea84de25608bfc35a94c965501afaeb5d99f6035`.
- Loopback-only dynamic port, unique disposable volumes, ephemeral explicit
  credentials, path-style requests, region `us-east-1`.
- Python 3.12.13 / SQLite 3.53.1; locked obstore 0.11.1 and xxhash 3.8.1.
- MeldDB source pin remains `8970099be98c6b4b6e0af30ac562ae65536e6403`.

## Executed local evidence

`uv run --frozen --no-sync --extra test pytest -p no:cacheprovider`:
**498 passed, 25 skipped**. Skips: 21 explicitly opt-in live S3 cases and
four Windows symlink-permission cases. Includes 106 network-free configuration
and identity regressions; those are not a substitute for server evidence.

`uv run --frozen --no-sync --extra test python tools/s3_lab.py`:
**21 live tests passed**, plus capability and actual transport-outage probes:

- Conditional PutObject, multipart upload, UploadPartCopy/conditional completion,
  competing writers to one absent destination, and zero-byte create-only writes.
- Uploaded-but-uncompleted 5 MiB part remains absent from ordinary object listing;
  ListMultipartUploads finds it and explicit AbortMultipartUpload removes it.
- Docker pause makes payload reads time out while SQL metadata stays readable;
  deletion stays pending and resumes after unpause. This is not a power-loss test.
- Both SQL adapters: lifecycle, application FK restriction, reopen, backup to
  local, restore, offline local-to-S3 transfer preserving IDs and application SQL.
- Missing/corrupt objects, orphan/staging reporting after injected lost copy
  acknowledgement, pending deletion after injected SDK transport failure.
- Both adapters resolve injected lost SQL commit acknowledgements with and
  without the commit having happened, without creating another logical record.
- Abrupt process exit after preparation, before finalization, leaves a prepared
  operation and no ready blob. Interrupted transfer never publishes its catalog.
- NumPy NPZ/Blosc2 and pandas/Polars/PyArrow Parquet remote round trips.

Ruff, wheel/sdist build, and fresh core/all-format installs of both artifacts
passed. No dependency was added for Docker administration: a small test-only
SigV4 helper handles bucket creation and MPU cleanup. Its protocol-mandated
SHA-256 does not change XXH3 blob hashes or introduce entry fingerprints.

## Streaming measurements

Fresh process per payload, both using direct sqlite3; synthetic repeating bytes,
local Docker endpoint, filesystem snapshots and verified downloads. These are
observations, not throughput guarantees or the full S08 consumer workload.

| Payload | Baseline peak RSS | Final peak RSS | Additional peak RSS | Import | Materialize/compare |
| --- | ---: | ---: | ---: | ---: | ---: |
| 32 MiB | 34,131,968 B | 50,171,904 B | 16,039,936 B | 0.687 s | 0.428 s |
| 256 MiB | 34,045,952 B | 50,094,080 B | 16,048,128 B | 4.393 s | 3.025 s |

The 8x payload increase did not produce proportional client-memory growth.
Original source + one whole-file private snapshot or downloaded temporary file
are needed locally; remote publication temporarily has staging and final copies.
Offline transfer additionally holds a complete local backup. Server memory,
disk amplification and 10–30 GB integrated workloads are not qualified here.

## CI and remaining limits

A dedicated Linux Docker job executes the same pinned lab, all live tests, and
fresh-process memory measurements. Existing Windows/Linux Python 3.12–3.14 jobs
cover both adapters, core/all extras, lint, build and fresh installs. CI results
will be recorded after the feature branch run completes.

Only this pinned RustFS single-node configuration is qualified by these tests.
Garage was source-evaluated, not executed; RustFS 1.0.0 was discovered but not
silently substituted for the tested release candidate. AWS, multi-node failure,
IAM policies, TLS deployment, bucket versioning/Object Lock, macOS, PostgreSQL,
power loss and automatic incomplete-MPU lifecycle expiration remain unqualified.
Incomplete MPUs require external operator cleanup; normal reconciliation lists
completed objects only. All participants need the same local catalog and
path-bound coordination directory on one host. No distributed lease is claimed.

The lab removes only its own uniquely named container and test-data volumes
after success/failure. These disposable data are intentionally not recoverable;
the pinned Docker image remains cached. No user/AWS bucket was accessed.
