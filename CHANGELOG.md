# Changelog

## 0.1.0rc1 — GitHub prerelease (2026-09-17)

Initial MeldStore MVP, approved for GitHub prerelease distribution. The tested
MeldDB Git pin is retained; Git is required for installation. No PyPI release
is included; see the [release checklist](docs/release.md).

### Included

- Persistent `export_file(id, destination)` with size/XXH3-128 verification,
  create-only publication and no source deletion; move semantics are deferred.
- Immutable file/byte storage with user-defined relational metadata, stable
  public IDs, explicit shared SQL transactions, and optimistic metadata edits.
- Local and S3-compatible payloads through obstore, verified with XXH3-128 before
  successful materialization/deserialization. Existing files retain exact bytes.
- SQLite metadata through MeldDB or direct sqlite3, with WAL policy, read
  transactions, declared indexes, and explicit maintenance.
- Optional pandas, Polars and PyArrow Parquet handlers; NumPy NPZ and native
  Blosc2 array handlers; explicit custom handler registration.
- Versioned metadata declarations and explicit resumable migrations; publication
  recovery, restrictive retirement, reconciliation and exclusive cleanup.
- Verified offline catalog/payload backup, fresh restore and relocation.
- Independent generic examples plus separate application-owned SQL integration
  fixtures. No built-in sensor, aircraft, track, or other domain model.
- Apache-2.0 licenses for MeldStore and its pinned MeldDB dependency,
  source-distribution examples, and packaging
  checks that reject private test data and local handoffs.

### Qualification and limits

Windows/Linux Python 3.12–3.14 and pinned Docker-local RustFS are qualified as
recorded in the [S08 evidence](docs/s08-verification.md). Representative local
and RustFS tests each passed 100 files totaling 16.37 GB, including backup,
restore, and unchanged-original verification.

This is a synchronous, single-host catalog/coordinator system, including when
payloads are remote. PostgreSQL and cross-host coordination are a future
milestone. AWS, Garage and macOS are not qualified. Whole-object verification
and offline maintenance require substantial temporary space. Interval indexing
and bulk ingestion have measured application-level follow-ups.

XXH3-128 detects accidental corruption; it is not an authenticity signature.
No caching policies, automatic deduplication, pickle fallback, payload editing,
distributed leases or automatic correlation are included. Release-candidate
status does not establish an unrestricted production-support guarantee.
