# S08 consumer and release qualification

September 17, 2026. Consumer and operational qualification completed; package
release remains gated by dependency licensing/distribution. This is not a
package-release or unrestricted production-readiness declaration.
S08 extends examples, tests, qualification tooling, and packaging. The generic
library implementation is unchanged from S07.

Implementation commit: `c984645`. Local environment: Windows, Python 3.12.13,
SQLite 3.53.1, MeldDB 0.1.0rc1 at the pinned commit, obstore 0.11.1, xxhash
3.8.1, NumPy 2.5.3, PyArrow 23.0.1, pandas 3.0.5, Polars 1.44.2, Blosc2
3.12.2, pytest 9.1.1 and Ruff 0.16.6. Dependencies were not changed for S08.

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

The local MeldDB run completed successfully: 100 files totaling **16,371,717,660
bytes**, ranging from 101,462,014 to 267,115,585 bytes. Projected indexing found
**76,188,349 rows**, **301,296 track occurrences**, two sensors, and no rows with
missing track UUIDs. Backup, restoration, and the final original-file hash check
all passed; temporary copies from this successful run were removed.

| Measurement | Local / MeldDB | RustFS / sqlite3 |
| --- | ---: | ---: |
| Independent source hashing, including hydration/cache effects | 528.62 s | 12.38 s |
| Projected Parquet indexing | 15.67 s | 16.14 s |
| Import/publication | 64.83 s | 299.26 s |
| Materialization plus independent verification | 97.75 s | 185.65 s |
| 100 interval queries | 14.396 s | 13.300 s |
| Full verified backup | 249.17 s | 293.09 s |
| Full verified restore | 239.04 s | 192.28 s |
| Total runner elapsed time | 1,361.43 s | 1,142.53 s |
| Baseline peak process RSS, bytes | 28,307,456 | 28,200,960 |
| Final peak process RSS, bytes | 221,757,440 | 229,957,632 |
| Observed work-directory bytes after restore | 49,468,554,501 | 33,096,849,323 |
| WAL size with pinned reader, bytes | 7,543,752 | 7,543,752 |

There were 307,580 public SQL calls during import/retrieval, dominated by the
application's per-occurrence inserts. The 100 interval queries issued 100 public
SQL calls and no instrumented object operations; each selected one recording.
An ordinary sqlite3 query returned the same count and foreign-key checks passed.
Twenty-five annotation edits committed while the reader retained its original
snapshot. Exclusive maintenance returned `busy=0`, with all three reported WAL
frames checkpointed. Restored metadata retained version 26 and all occurrences.

The integrated plan scanned `c_occurrence` using its `(blob_id, track_uuid)`
primary-key index, then searched `ms_blobs` by ID. It did **not** select the
declared start/end interval index. At this distribution and selectivity,
the average query was about 144 ms. This is a measured limitation, not an index
speedup or a failed correctness test. End-time-leading indexes, alternative
join/order strategies and application-side bulk insertion merit separate
before/after experiments; no universal domain index or speculative core tuning
is introduced by S08.

The RustFS/direct-sqlite3 run also completed all 100 files, with identical byte,
row, occurrence and sensor totals, unchanged originals, restored metadata and
references, and successful cleanup. It produced the same SQL plan, query result
count, SQL-call counts, pinned-reader WAL size and successful checkpoint result.
The lab's own disposable container and test-data volumes were removed afterward.
The pinned image is
`rustfs/rustfs:1.0.0-rc.6@sha256:97171b3d72cd47dc81000f92ea84de25608bfc35a94c965501afaeb5d99f6035`.

The runs partially overlapped on one desktop; RustFS read the same originals
after local reads had largely hydrated them. In particular, the difference in
source-hashing time is **not** an XXH3 or adapter speed comparison. RustFS server
storage is excluded from its smaller client work-directory observation. The
earlier interrupted local run is not included in these measurements; its private
residue was retained rather than silently deleted.

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
- Backup/restore is not a single filesystem copy: each payload is materialized
  and verified at the source, uploaded to the destination, then materialized
  and verified there. The runner additionally materializes every restored blob.
  Whole-file temporary writes and repeated verification contribute substantial
  I/O; consider this cost when sizing an offline maintenance window.
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
data; both artifacts passed those checks before the implementation push.

The pinned MeldDB commit `8970099be98c6b4b6e0af30ac562ae65536e6403` has no root
LICENSE and no license declaration in its project metadata. Its required Git
dependency is a development distribution arrangement. Dependency licensing and
a package-release-compatible dependency source must be resolved before release.
MeldStore's license selection does not relicense MeldDB. No package is published.

## Regression and CI

Local final implementation verification: **519 passed, 29 skipped** in 44.51 s,
then Ruff, wheel/sdist builds, archive checks, and fresh core/all-format installs
passed. The 29 skips are 25 opt-in S3 tests and four Windows symlink-permission
cases. Separately, all **25 live RustFS tests passed** in 17.24 s, with real
conditional/multipart and outage probes. The 32/256 MiB client probes added
16,084,992 / 16,158,720 bytes of peak RSS respectively; these are separate
processes from the large Arrow/indexing workload.

All seven jobs passed in [S08 CI](https://github.com/radioflyer28/MeldStore/actions/runs/35235382099)
for implementation commit `c984645`. Each Windows/Linux Python 3.12–3.14 job
passed 471 core tests (77 expected skips), then **523 all-extra tests** (25 live
S3 skips), lint, builds, and fresh core/all-format wheel/sdist installs. The
separate RustFS job passed 25 live tests plus capability/outage and memory
probes. The CI environment covers the four local symlink skips. No private
inputs, private paths, or checkpoint history were pushed or supplied to CI.

Both large-workload reports completed successfully and remain private local
aggregate reports; only the aggregate measurements above are published. AWS,
Garage, macOS, PostgreSQL, multi-host catalogs, and power-loss durability remain
unqualified. No branch merge or package publication was performed.
