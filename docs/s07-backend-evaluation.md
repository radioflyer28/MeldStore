# S07 Docker-local S3 backend evaluation

Researched 2026-09-16 using official documentation, release-tagged source, and
publisher Docker Hub metadata. This note records source research; subsequent
executed evidence is in [S07 verification](s07-verification.md).

## Recommendation and exact image pins

### Selected executable-probe pin: 1.0.0-rc.6

The coordinating task selected **1.0.0-rc.6** from its official release-channel
listing. Keep the executable evidence on that selected version; do not silently
upgrade it to the newer tag discovered below. Registry metadata independently
confirms this immutable reference:

`rustfs/rustfs:1.0.0-rc.6@sha256:97171b3d72cd47dc81000f92ea84de25608bfc35a94c965501afaeb5d99f6035`

[Exact rc.6 registry metadata](https://hub.docker.com/v2/repositories/rustfs/rustfs/tags/1.0.0-rc.6)
reports the image update at 2026-09-11 04:54 UTC (September 10 in US Eastern).
The source checks were repeated against rc.6: its
[multipart handler](https://github.com/rustfs/rustfs/blob/1.0.0-rc.6/rustfs/src/app/multipart_usecase.rs)
implements UploadPartCopy with source ranges and passes completion header
options to storage; its
[storage completion](https://github.com/rustfs/rustfs/blob/1.0.0-rc.6/crates/ecstore/src/set_disk/ops/multipart.rs)
acquires the destination object write lock before checking write preconditions.
Its [conditional tests](https://github.com/rustfs/rustfs/blob/1.0.0-rc.6/crates/e2e_test/src/reliant/conditional_writes.rs)
expect `PreconditionFailed` for completion with `If-None-Match: *` over an
existing key and verify staged parts remain after rejection. This supports the
staging/copy proposal on rc.6, subject to the race and client-integration probes
below. Replace the image reference in the setup example with this selected pin.

### Additional release discovery

**The initial research recommended RustFS 1.0.0.** It has source and upstream-test evidence for the
critical combination: UploadPartCopy plus conditional CompleteMultipartUpload.
Garage v2.4.1 implements multipart copy, but its write handlers do not enforce
the destination conditions needed for create-only publication. Neither backend
is qualified by this research alone.

| Backend | Current stable release | Verified immutable image reference |
| --- | --- | --- |
| RustFS | 1.0.0, released September 16 | `rustfs/rustfs:1.0.0@sha256:8cc9801755448b71a786705ce76692c77e14936cccd87cf2fc31842e58f4d1ff` |
| Garage | v2.4.1, released September 8 | `dxflrs/garage:v2.4.1@sha256:9c96caa2612d3411acc5b0e6701fb238dbfba33e533a6d7d3d811a4b12d0d020` |

These are registry-reported multi-platform digests, checked through the exact
tag endpoints; both include Linux amd64 and arm64. Use Docker Desktop's Linux
container mode on Windows. RustFS also published **1.0.1-preview.1** on September
16; it is a prerelease, not the recommended stable pin. Do not substitute
`latest`, `main-latest`, or Garage development commit tags.
[RustFS releases](https://github.com/rustfs/rustfs/releases),
[RustFS image metadata](https://hub.docker.com/v2/repositories/rustfs/rustfs/tags/1.0.0),
[Garage releases](https://garagehq.deuxfleurs.fr/_releases.html),
[Garage image metadata](https://hub.docker.com/v2/repositories/dxflrs/garage/tags/v2.4.1).

## Operation evidence at the pinned versions

| Required operation | RustFS 1.0.0 | Garage v2.4.1 |
| --- | --- | --- |
| PutObject with `If-None-Match: *` | Implemented; tests cover absent-key creation and failed matching conditions. Storage rechecks write conditions at commit. [R1][R2] | No destination-condition handling found in the PUT handler or dispatch; treat as unsupported, with silent overwrite a probe risk. [G1] |
| CompleteMultipartUpload with `If-None-Match: *` | Explicit upstream test expects `PreconditionFailed` over an existing key. Completion passes request-header options to storage, which acquires an object write lock before checking the condition. [R1][R3][R4] | Completion reads checksum headers and commits the upload without a destination `If-None-Match`/`If-Match` check. Treat as unsupported; ordinary completion support is insufficient. [G2] |
| UploadPartCopy | Implemented, including source range and source ETag conditions. This supplies copied parts; destination exclusion belongs to completion. [R3] | Listed as implemented in the official compatibility matrix. [G3] |
| Multipart create/upload/list/abort | Handlers exist for CreateMultipartUpload, UploadPart, ListMultipartUploads, ListParts, and AbortMultipartUpload. Upstream conditional-completion test also checks staged parts survive rejected completion. [R1][R3] | All listed as implemented, including ListParts and ListMultipartUploads. [G3] |

The Garage conditional-write conclusion is **source inspection**, not a measured
HTTP response. Its generic compatibility checkmarks do not promise conditional
headers. RustFS evidence is stronger, but upstream test source does not prove
those tests passed against the exact published image.

## Priority probe: bounded-memory publication with obstore 0.11.1

Use the user's pinned-client finding as a constraint: `mode="create"` disables
multipart and materializes input in obstore 0.11.1. Do not use it as evidence of
bounded-memory large-object publication. The proposed path is:

1. Stream a multipart PUT to a fresh, private staging UUID key.
2. With `copy_if_not_exists="multipart"`, call
   `obstore.copy(..., overwrite=False)` to the final immutable key. Confirm the
   wire path uses UploadPartCopy followed by CompleteMultipartUpload carrying
   `If-None-Match: *`.
3. Only after confirmed publication, delete staging. On ambiguous completion,
   retain recovery information and verify the destination before cleanup.

This is a **candidate**, not a verified obstore 0.11.1 integration. Current
upstream object_store documentation describes this multipart-copy strategy and
warns that it does not preserve source tags/attributes and only attempts failure
cleanup on a best-effort basis; verify those details against the pinned client's
actual dependency and behavior.
[Official object_store API](https://docs.rs/object_store/latest/object_store/aws/enum.S3CopyIfNotExists.html).

Required executable gates, in priority order:

- Copy to an absent final key succeeds; copy to an existing final key fails and
  preserves its bytes. Stage parts first, then create the final key before
  completion: completion must reject it. Race two distinct staging payloads to
  the same final key: exactly one may publish. Include PUT-versus-copy races.
- Measure process peak memory with streamed 100–300 MB payloads and increasing
  sizes; confirm the staging upload is multipart and copy is server-side.
  Check empty/small objects, multi-part ranges, metadata needs, and copy size
  limits separately. Verify XXH3-128 and length; ETags are not integrity digests.
- Force completion failure and connection loss after sending completion. Check
  destination visibility, retained staging, ListMultipartUploads/ListParts
  pagination, explicit abort, repeated cleanup, and no unrelated-key deletion.
  Record the client's exception mapping and whether it aborts failed copy MPUs.
- Verify immediate HEAD/GET/LIST after publication and deletion, then run the
  MeldStore lifecycle, recovery, and LocalStore-to-S3 relocation cases.

AWS documents 412 for failed create-only conditions and possible 409 conflicts;
multipart 409 recovery requires a new upload. Capture actual backend behavior
instead of assuming identical retry semantics.
[AWS conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).

## Minimal local setup (instructions only; not executed)

For RustFS, set throwaway `RUSTFS_ACCESS_KEY` and `RUSTFS_SECRET_KEY` environment
variables in PowerShell, then use:

```powershell
docker run -d --name meldstore-s07-rustfs -p 127.0.0.1:9000:9000 -e RUSTFS_ACCESS_KEY -e RUSTFS_SECRET_KEY -v meldstore-s07-rustfs-data:/data rustfs/rustfs:1.0.0@sha256:8cc9801755448b71a786705ce76692c77e14936cccd87cf2fc31842e58f4d1ff /data
```

Create a disposable `meldstore-s07` bucket through the S3 API. Configure the
client with `http://127.0.0.1:9000`, explicit credentials, region `us-east-1`,
path-style addressing, local HTTP allowed, and multipart copy-if-absent.
The image runs as UID/GID 10001; bind mounts must be writable by that identity.
Named-volume initialization/permissions and effective client region remain
startup checks. No console or observability stack is required.
[Official Docker setup](https://rustfs.org/installation/docker/),
[release README](https://github.com/rustfs/rustfs/blob/1.0.0/README.md).

For Garage, mount a `garage.toml` at `/etc/garage.toml` with persistent metadata
and data directories, `db_engine="sqlite"`, `replication_factor=1`,
`consistency_mode="consistent"`, a random 32-byte hex `rpc_secret`,
`rpc_bind_addr="0.0.0.0:3901"`, and
`rpc_public_addr="127.0.0.1:3901"`. Under `[s3_api]`, set
`s3_region="garage"` and `api_bind_addr="0.0.0.0:3900"`. Run the pinned image
with `/garage server --single-node --default-bucket`; pass
`GARAGE_DEFAULT_ACCESS_KEY` (`GK` plus 32 hex characters),
`GARAGE_DEFAULT_SECRET_KEY`, and `GARAGE_DEFAULT_BUCKET=meldstore-s07`.
Publish only `127.0.0.1:3900:3900`; use that HTTP endpoint, region `garage`,
and path-style requests. Since v2.3.0 these flags automate layout/key/bucket
bootstrap. Persist both directories for restart probes.
[Official quick start](https://garagehq.deuxfleurs.fr/documentation/quick-start/).

## Consistency, licensing, and scope

RustFS's locked condition checks support choosing it for the race probes; they
are not a proof of linearizability, distributed failure behavior, or crash
durability. Single-node/single-disk mode has no redundancy. Keep qualification
claims limited to the tested image, configuration, client, and operations.
[Storage implementation][R2], [completion locking][R4],
[deployment guidance](https://docs.rustfs.com/en/installation).

Garage's default `consistent` mode promises read-after-write through quorums;
`degraded`/`dangerous` modes weaken this. Garage deliberately avoids consensus
ordering of requests, so read-after-write is not an atomic create-if-absent
guarantee. Metadata and data fsync are disabled by default; explicitly record
those settings for restart/failure tests. A one-node test does not qualify
multi-node behavior.
[Configuration](https://garagehq.deuxfleurs.fr/documentation/reference-manual/configuration/),
[Architecture/features](https://garagehq.deuxfleurs.fr/documentation/reference-manual/features/).

RustFS is Apache-2.0; Garage is AGPLv3. These are the server licenses, not new
MeldStore core dependencies. Review the actual terms if modifying or
redistributing server images.
[RustFS pinned license](https://github.com/rustfs/rustfs/blob/1.0.0/LICENSE),
[Garage pinned license](https://github.com/deuxfleurs-org/garage/blob/v2.4.1/LICENSE).

The user explicitly permits Docker-local RustFS/Garage qualification for S07,
superseding the earlier AWS-only destination requirement for this work. Passing
these probes establishes **S3-compatible qualification on the named backend**,
not AWS S3 qualification or AWS certification. Existing AWS-specific acceptance
claims remain unproven. The [verification record](s07-verification.md) separates
executed results from the source-level findings in this research note.

[R1]: https://github.com/rustfs/rustfs/blob/1.0.0/crates/e2e_test/src/reliant/conditional_writes.rs
[R2]: https://github.com/rustfs/rustfs/blob/1.0.0/crates/ecstore/src/set_disk/ops/object.rs
[R3]: https://github.com/rustfs/rustfs/blob/1.0.0/rustfs/src/app/multipart_usecase.rs
[R4]: https://github.com/rustfs/rustfs/blob/1.0.0/crates/ecstore/src/set_disk/ops/multipart.rs
[G1]: https://github.com/deuxfleurs-org/garage/blob/v2.4.1/src/api/s3/put.rs
[G2]: https://github.com/deuxfleurs-org/garage/blob/v2.4.1/src/api/s3/multipart.rs
[G3]: https://garagehq.deuxfleurs.fr/documentation/reference-manual/s3-compatibility/
