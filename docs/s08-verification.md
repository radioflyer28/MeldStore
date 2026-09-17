# S08 consumer and release qualification

September 17, 2026. In progress; this record does not declare MVP completion.
S08 extends examples, tests, qualification tooling, and packaging. The generic
library implementation is unchanged from S07.

## Consumer evidence

The [consumer guide](consumers.md) explains the independent dataset/document
application and separate synthetic cUAS SQL fixture. Both documented commands
were executed on Windows. The generic example selects one document and verifies
its bytes after a metadata edit. The cUAS example produces two correlation rows
but materializes only the two distinct whole recordings.

`tests/test_consumers.py`: **21 passed** on Windows with both adapters.
Coverage includes application-owned SQL references, explicit link removal and
blob retirement, optional arrays/tables, rollback of invalid truth publication,
half-open intervals, stale edits, restrictive deletion, backup restoration, and
process exit before/after publication commit. A plain sqlite3 subprocess with
site packages disabled runs the correlation query and foreign-key check without
MeldDB or MeldStore importable. Direct consumers still honor the catalog access
protocol; SQL portability is not permission to bypass coordination.

Native nanosecond Parquet timestamps remain unchanged. The read-only application
indexer rounds microsecond bounds outward and groups track UUIDs within each
recording. Truth correlation uses synthetic evidence only. The representative
radar inputs contain no configuration identifier, so configuration provenance
is explicitly unknown, not inferred.

## Representative workload

The opt-in runner selects 100 original files in the 100–300 MB range, imports
and verifies them, queries occurrence intervals without object I/O, exercises
25 annotation commits during a pinned read snapshot, checkpoints under exclusive
maintenance, backs up and restores all payloads, then independently rechecks the
originals. Final reports are written only after success and temporary cleanup.

Local MeldDB and disposable RustFS/direct-sqlite3 results are pending. An
interrupted earlier run is not evidence of complete qualification.

Interpretation limits:

- Runs use a Windows desktop and cloud-synced source files. Source hashing may
  include OneDrive hydration; warm/cold cache state is not controlled. Other
  validation tasks may execute concurrently. These are operational measurements,
  not isolated hash-throughput or adapter/backend comparisons.
- RSS is the process peak, including Python, Arrow and the application indexer;
  it is not a library-only memory cap. Track-index memory grows with distinct
  track count even though Parquet column reads are batched.
- Reported work-directory size is an observation after restore, not a measured
  peak. Maximum per-blob staging bytes describes the largest selected payload,
  not total temporary space or all simultaneous copies. S3 server/volume memory
  and disk usage are outside the client report.
- SQL counters count public transaction SQL calls, not every adapter/internal
  statement. Object counters wrap selected public obstore operations, not wire
  requests, retries, or multipart subrequests.
- Interval selectivity and plan choice depend on the supplied distribution.
  S06a's separate before/after index experiment remains the index-cost baseline;
  enabling WAL or exercising one interval query is not a blanket speed claim.
- There is no inferred real aircraft truth. Large radar ingestion and the
  synthetic correlation fixture establish different pieces of acceptance.

## Packaging and release gates

Apache-2.0 was selected for MeldStore. Fresh core and all-format wheel/sdist
installs passed on Windows with both adapters, verifying license metadata and
the packaged license file. Examples belong in the source distribution only.
The archive-safety checks additionally reject local handoffs and private binary
data. Rebuild and rerun these checks after final changes.

The pinned MeldDB commit `8970099be98c6b4b6e0af30ac562ae65536e6403` has no root
LICENSE and no license declaration in its project metadata. Its required Git
dependency is a development distribution arrangement. Dependency licensing and
a package-release-compatible dependency source must be resolved before release.
MeldStore's license selection does not relicense MeldDB. No package is published.

Final regression totals, large-workload results, and S08 Windows/Linux CI remain
pending. S07 CI is not evidence for S08 additions. AWS, Garage, macOS,
PostgreSQL, multi-host catalogs, and power-loss durability remain unqualified.
