# Representative workload verification

Completed 2026-11-13: all three adapters passed the full representative profile.
This qualifies the recorded Windows 11 / Docker Desktop / PostgreSQL 17.11 /
RustFS deployment with one persistent single-host coordinator. It does not qualify
AWS, multiple coordinating hosts, or full-scale Linux operation.

[Machine-readable results](evidence/representative-workloads-2026-11-13.json)
retain phase results, query plans, counts, distributions and source fingerprints.
The [synthetic corpus manifest](evidence/representative-corpus-2026-11-13.json)
contains generation settings and per-file sizes/hashes, without payload data.

## Reproduction and provenance

~~~powershell
uv run --frozen --extra test --extra arrow --extra postgres python tools/qualify_representative.py --profile representative --keep-awake --request-timeout-seconds 300 --output .qualification/representative-full-06
~~~

The command performs capacity preflight before generation. Declared limits were
14,400 seconds overall, 4,000,000,000 bytes of client memory and a 2,000,000,000-byte
reserve on monitored filesystems. Preflight observed 905.75 GB host and 903.37 GB
Docker free space; its conservative combined host allowance was 274 GB. S3 used a
300-second whole-request timeout and zero retries. The idle-sleep hold was enabled.

- Corpus: **100 valid Parquet files, 20,038,514,268 bytes**, each
  103,161,284-297,494,487 bytes; **300,000 SQL relationship rows**.
- Generator fixed-binary-v1, seed 20261112, uncompressed 4,096-row groups.
  All values and identifiers are synthetic.
- Windows 11 AMD64, 22 logical CPUs, 68.14 GB physical memory; CPython 3.13.15,
  SQLite 3.53.1. Host filesystem NTFS, 2.033 TB capacity. Docker Linux reports
  54.74 GB memory and overlayfs; its data volumes are disk-backed.
- PyArrow 23.0.1, obstore 0.11.1, xxhash 3.8.1, Psycopg/binary 3.3.8.
- PostgreSQL/native archive tools 17.11 and RustFS 1.0.0-rc.6; exact immutable
  image digests are in the JSON.
- MeldStore base 4aebe955bd4cc7518409786d279889cf7e95f8c2, dirty tree.
  All 43 runtime source fingerprints were checked unchanged after execution.
- Exact installed MeldDB 01e27441d7512f85663c8a86986429d62a69bff9;
  lock identity and dependency versions are retained in the report.

Total elapsed: **12981.27 seconds (3.61 hours)**. Maximum heartbeat gap: 1.177 seconds, below the 30-second limit; no execution continuity loss.

## Completed operations

Every adapter passed independent SQL/payload oracles, pagination, versions, FKs,
shared-publication rollback, 1/2/4 independent clients, uncertain-publication
reopen/inspection, migration dry-run and durable interruption/resume, interrupted
backup, full backup/restore, corruption refusal and occupied-destination refusal.
Both SQLite adapters also passed full PostgreSQL cutover under an explicit
303,000-row ceiling. Source and destination payloads were verified in full.

Times are seconds; import throughput is decimal MB/s including integrity work.
Restore includes verifying every restored payload. Cutover destination verification
also runs afterward, included in adapter total. PostgreSQL does not run a
SQLite-source cutover, so adapter totals are not equivalent workloads.

| Adapter | Operations | Total s | Import s (MB/s) | Relationship load s | Backup s | Restore s | Cutover s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Direct SQLite | 35 | 4373.86 | 394.26 (50.82) | 27.24 | 292.73 | 910.15 | 667.46 |
| MeldDB SQLite | 35 | 4348.36 | 466.92 (42.92) | 37.52 | 357.50 | 904.61 | 633.37 |
| MeldDB PostgreSQL | 33 | 3316.96 | 413.55 (48.45) | 49.82 | 286.67 | 949.56 | N/A |

## Queries, batches and concurrency

Each interval case has five warm-ups and 100 measured calls. Values are p50/p95
milliseconds. The end arrangement adds an end-first index alongside the start-first
index; the optimizer may select either index or a scan. Plans precede timing.
These sequential observations include instrumentation and uncontrolled cache/
statistics effects. They are not controlled causal A/B measurements or an SLA.

| Adapter | Index arrangement | Interval offsets (seconds) | p50 ms | p95 ms |
| --- | --- | --- | ---: | ---: |
| Direct SQLite | start | [0, 1] | 0.216 | 0.277 |
| Direct SQLite | start | [120, 121] | 1.094 | 1.493 |
| Direct SQLite | start | [30, 3000] | 1393.274 | 1628.280 |
| Direct SQLite | start | [0, 6120] | 2908.011 | 3138.545 |
| Direct SQLite | end | [0, 1] | 56.553 | 73.310 |
| Direct SQLite | end | [120, 121] | 54.275 | 72.568 |
| Direct SQLite | end | [30, 3000] | 1426.133 | 1576.099 |
| Direct SQLite | end | [0, 6120] | 2722.800 | 2908.791 |
| MeldDB SQLite | start | [0, 1] | 0.279 | 0.468 |
| MeldDB SQLite | start | [120, 121] | 0.858 | 1.120 |
| MeldDB SQLite | start | [30, 3000] | 1523.906 | 1673.656 |
| MeldDB SQLite | start | [0, 6120] | 3026.990 | 3238.085 |
| MeldDB SQLite | end | [0, 1] | 56.483 | 71.788 |
| MeldDB SQLite | end | [120, 121] | 57.796 | 74.343 |
| MeldDB SQLite | end | [30, 3000] | 1489.863 | 1668.866 |
| MeldDB SQLite | end | [0, 6120] | 2859.689 | 3103.496 |
| MeldDB PostgreSQL | start | [0, 1] | 2.780 | 3.252 |
| MeldDB PostgreSQL | start | [120, 121] | 3.256 | 4.134 |
| MeldDB PostgreSQL | start | [30, 3000] | 47.170 | 55.459 |
| MeldDB PostgreSQL | start | [0, 6120] | 66.749 | 76.656 |
| MeldDB PostgreSQL | end | [0, 1] | 2.990 | 3.531 |
| MeldDB PostgreSQL | end | [120, 121] | 2.739 | 3.388 |
| MeldDB PostgreSQL | end | [30, 3000] | 48.547 | 56.621 |
| MeldDB PostgreSQL | end | [0, 6120] | 67.540 | 75.599 |

Clients each made 105 full 100-record find calls, including five warm-ups.
The table gives ranges of individual clients' percentiles, not pooled percentiles.
Every case had all disjoint edits succeed, one shared-edit winner, N-1 expected
conflicts and zero errors. SQL calls include the few stat/edit checks as well as reads.

| Adapter | Clients | p50 range ms | p95 range ms | Public SQL calls per client |
| --- | ---: | ---: | ---: | ---: |
| Direct SQLite | 1 | 15.27-15.27 | 18.55-18.55 | 73972-73972 |
| Direct SQLite | 2 | 14.98-15.54 | 21.47-22.68 | 73964-73972 |
| Direct SQLite | 4 | 16.82-17.77 | 22.85-24.19 | 73964-73972 |
| MeldDB SQLite | 1 | 17.28-17.28 | 23.46-23.46 | 73972-73972 |
| MeldDB SQLite | 2 | 18.76-18.93 | 26.28-27.80 | 73964-73972 |
| MeldDB SQLite | 4 | 20.71-21.14 | 28.49-30.30 | 73964-73972 |
| MeldDB PostgreSQL | 1 | 2649.87-2649.87 | 3257.10-3257.10 | 317643-317643 |
| MeldDB PostgreSQL | 2 | 2793.19-2801.62 | 3385.62-3412.35 | 317613-317643 |
| MeldDB PostgreSQL | 4 | 2857.29-2863.08 | 3284.41-3313.66 | 317613-317643 |

Bulk samples insert and roll back 10,000 rows. Each elapsed time below is one
trial in seconds, not a distribution. API calls do not count network round trips.
The JSON retains both index arrangements and parameter-set counts.

| Adapter (start index) | Batch 100 | Batch 1,000 | Batch 10,000 | Repeated SQL |
| --- | ---: | ---: | ---: | ---: |
| Direct SQLite | 1.027 | 0.188 | 0.158 | 0.221 |
| MeldDB SQLite | 0.215 | 0.186 | 0.177 | 0.309 |
| MeldDB PostgreSQL | 2.292 | 1.287 | 1.248 | 9.035 |

## Resource observations

MB/GB are decimal. Main-client RSS is an OS lifetime peak in a fresh adapter
process. Aggregate client RSS samples parent and active workers every 50 ms, not
a sum of independent peaks. Variant disk is sampled every 250 ms and excludes
the 20.04 GB source corpus.

| Adapter | Main client peak MB | Max sampled aggregate client MB | Variant staging/catalog/archive GB |
| --- | ---: | ---: | ---: |
| Direct SQLite | 348.56 | 210.08 | 20.719 |
| MeldDB SQLite | 349.37 | 213.58 | 20.719 |
| MeldDB PostgreSQL | 77.79 | 262.35 | 20.340 |

Shared-lab service-volume high-water observation: **82.908 GB**.
These counters can carry previous adapter observations and are not isolated
per-adapter comparisons. Docker memory/CPU readings are end-of-variant snapshots,
not peaks. Disappearing temporary files invalidate traversal samples with an
explicit reason. Samples can miss brief peaks. Client limits do not establish
a server-memory ceiling; SQLite adapters also do extra cutover work.

## Ranked follow-ups and contract decision

1. **MeldStore: batch record loading in Store.find().** Per-result record loading
   repeatedly reads descriptors and validates schemas. Observed SQL-call
   amplification makes this the first optimization candidate for slow PostgreSQL
   client reads; direct joins were much faster in their own fixture. Use bounded
   set-based reads and validation once per distinct schema within one explicit
   read snapshot. Preserve detached values, pagination, corruption checks, schema
   drift detection and zero payload I/O; qualify all adapters. Existing public
   SQL is sufficient; this does not require provider changes.
2. **Application-owned interval tuning experiments.** SQLite used range indexes
   and temporary grouping; narrow queries were slower in the arrangement where
   the end index was chosen. PostgreSQL broad cases used parallel scans/hash aggregation and
   narrow cases retained the start index. Test covering indexes, statistics and
   controlled cache budgets before selecting application-specific indexes.
3. **Operational budgets.** Document finite transfer row allowances and whole-
   response S3 timeouts. This deployment qualified 300 seconds with zero retries;
   it changes neither public defaults nor network throughput guarantees.
4. **MeldDB follow-ups.** Consider a stable module-level portable-JSON loader to
   avoid Psycopg's per-connection closure warning; the warning alone is not proof
   of a measured leak. Stronger PostgreSQL structural inspection remains its
   next separate provider feature before broader managed-schema migrations.

The meld-platform workset and local-only shared metadata-provider contract were
reviewed. No provider correction or changed guarantee was required: harness
settings and consumer measurements use existing qualified public APIs. Keep the
exact MeldDB pin and shared contract unchanged. MeldDB PostgreSQL remains
experimental overall; this result does not complete its capability matrix.

## Regression and integration evidence

| Check | Result |
| --- | --- |
| uv run --frozen --all-extras pytest -q (Windows, exact runtime source) | 706 passed, 255 optional/live skips |
| Separate core-only uv environment, test extra | 629 passed, 320 optional/live skips |
| Focused Windows qualification tests | 52 passed, 5 opt-in live skips |
| Focused Linux qualification tests | 49 passed, 8 opt-in live/Windows skips |
| Original all-adapter live harness tests | 3 passed |
| Both 300,000-row small-payload cutover regressions | 2 passed |
| uv run --frozen --all-extras python tools/postgres_lab.py --with-rustfs | 217 passed, zero skipped |
| uv run --frozen --extra test ruff check . | Passed |
| MeldDB uv run --extra test pytest and Ruff (Python 3.13.3) | 363 passed, 41 optional skips; Ruff passed |

Complete Windows and native-WSL smoke runs passed all adapters in 115.62 and
53.83 seconds respectively. Separate custom four-file 100-300 MB diagnostics
also passed on both platforms under the 300-second request budget. Those custom
runs overlapped and are not platform timing comparisons. All smaller profiles
leave representative qualification false. Native WSL lacked full preflight
capacity; no filesystem was resized or capacity gate weakened.

Required all-adapter smoke is added to the existing PostgreSQL/RustFS CI job,
preserving its provider suite. These are local results, not a claim that the
unpublished branch ran GitHub Actions. Packaging, dependencies and installed-tool
discovery are unchanged, so no new artifact build/install gate is triggered.
A pre-existing MeldDB pytest cache-permission warning did not fail its checks.

## Earlier attempts retained as failed or incomplete evidence

| Attempt | Outcome and correction |
| --- | --- |
| 01 | Interrupted during generation after a live-volume traversal race; explicitly handle disappearing temporary files. |
| 02 | Interrupted after Windows memory sampling retained ctypes classes; cache bindings and verify bounded repeated sampling. |
| 03 | Host standby disrupted timing; restore timed out. Add opt-in idle-sleep hold and independent continuity checks. |
| 04 | Reached cutover but exceeded its default 100,000-row bound. Pass finite workload-derived limits and test both 300,000-row cutovers. |
| 05 | Response-body timeout at 60 seconds despite continuous execution. Declare 300 seconds, retain zero retries and all workload gates. |
| 06 | Full representative comparison passed; this is the capacity evidence above. |

Transient Windows sharing denials in report/ownership-file replacement use
bounded atomic-file retries. Persistent denial fails; SQL and payload work gain
no retries. Raw logs, generated payloads, private runtime paths and credentials
remain ignored. Public evidence contains only reviewed synthetic metadata.
Strict OpenSpec validation and final diff/privacy checks are recorded in the
completed change tasks. The runner exited successfully, and all run-owned Docker
containers and volumes were verified removed. No release or publication is implied.

## Spec closeout

Synced all eight requirements and sixteen scenarios into the canonical
representative-workload-qualification specification and archived the completed
change on 2026-11-13. The new canonical spec passes strict validation; all eight
canonical specs pass standard validation. Repository-wide strict validation
reports five pre-existing over-500-character requirements in relational-metadata,
which this change leaves unchanged.

The final staged-file review removed trailing blank lines from eight new tools
modules. Their parsed Python syntax trees are identical before and after cleanup.
The full-run report remains unchanged and describes the actual executed bytes;
[the closeout fingerprint record](evidence/representative-source-closeout-2026-11-13.json)
maps those original hashes to the final source. No executable code changed, and
the full representative run was not repeated for whitespace normalization.

After the whitespace-only closeout, the full uv regression rerun passed again:
706 passed, 255 skipped in 68.39 seconds; Ruff passed. The companion MeldDB
documentation check passed 363 tests with 41 skips in 29.92 seconds; Ruff passed.
Its existing pytest cache-permission warning remains non-fatal.

## PR review corrections

Review added per-run Docker ownership labels, deadline-bound disk probes and a
completion gate that runs after cleanup. The gate now checks the full interval,
bulk and concurrency matrices. The workload validates complete concurrent read
results, rollback of both application and publication rows, and durable migration
progress after reopening. RustFS credentials are supplied through the environment.

The corrected runner passed the three-adapter smoke and its targeted failure
regressions. The 20.04 GB report records the earlier runner and its original
source fingerprints. Repeating all strengthened correctness checks at 20 GB
remains pending.
