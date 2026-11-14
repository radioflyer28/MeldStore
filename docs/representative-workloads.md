# Representative workload qualification

This development harness implements `qualify-postgresql-representative-workloads`.
The full representative profile passed on all three adapters on 2026-11-13.
See the verification record for measured results, topology limits and earlier
failed attempts; smoke remains a separate correctness qualification.

Use uv with the locked test, Arrow and PostgreSQL extras:

```powershell
uv run --frozen --extra test --extra arrow --extra postgres python tools/qualify_representative.py --profile smoke --generate-only --output .qualification/corpus-smoke
```

Output must be fresh. Generation uses only synthetic values, bounded 4,096-row
Parquet groups, no compression or dictionary encoding, and a recorded seed and
PyArrow version. Each file contains many rows and remains intact. A versioned
manifest records encoded decimal byte sizes, row counts, synthetic identities
and XXH3-128 digests. The generator validates every file before returning.

| Profile | Files | Encoded size per file | SQL relationship rows | Clients |
| --- | --- | --- | --- | --- |
| smoke | 4 | 1-4 MB | 2,000 | 1, 2 |
| representative | 100 | 100-300 MB | 300,000 | 1, 2, 4 |
| custom | explicit | explicit | explicit | 1, 2 |

The representative profile must be explicitly selected. Overrides require
`--profile custom` with `--files`, `--min-bytes`, `--max-bytes` and
`--relationships`; customized or smoke output never inherits representative
qualification. Reproducibility is scoped to the generator and dependency versions.
The manifest is written only after generation; interruption leaves an incomplete
corpus. A corpus-only result is never a completed workload report.

Generated files and raw evidence belong under ignored `.qualification/`.
Do not commit Parquet data, private source paths, credentials or real identifiers.

## Full workflow and resource preflight

The complete CLI now runs all three adapters against pinned disposable services:

```powershell
uv run --frozen --extra test --extra arrow --extra postgres python tools/qualify_representative.py --profile smoke --output .qualification/workload-smoke
uv run --frozen --extra test --extra arrow --extra postgres python tools/qualify_representative.py --profile representative --preflight-only --request-timeout-seconds 300 --output .qualification/workload-preflight
uv run --frozen --extra test --extra arrow --extra postgres python tools/qualify_representative.py --profile representative --request-timeout-seconds 300 --output .qualification/workload-full
```

Preflight inspects free bytes on PostgreSQL and RustFS mounted Docker volumes
separately from host free space. It conservatively budgets five corpus copies
on the host and four in Docker, plus reserves, and accounts for Docker backing
storage sharing the host disk. Failure does not reduce the workload. Services use
loopback endpoints, ephemeral credentials and run-owned disk-backed volumes.

`--memory-bytes` defaults to 4,000,000,000 per adapter including its concurrent
clients; `--seconds` defaults to 14,400 for the complete run; `--reserve-bytes`
defaults to 2,000,000,000 on each monitored filesystem. Generation and workload
boundaries check limits; the supervisor monitors process memory and runtime.
Service disk is checked at two-second intervals. The runner retains partial
reports on failure. No existing destination is adopted or overwritten.

To clean Docker resources after interruption, use the original output directory:

```powershell
uv run --frozen --extra test --extra arrow --extra postgres python tools/qualify_representative.py --cleanup --output .qualification/workload-full
```

Cleanup validates the ownership token and service labels. It removes only the
container/volume names derived from that token, never arbitrary manifest paths,
external endpoints or unrelated resources. Local evidence remains for inspection.

## SQL fixture and operation boundaries

The fixture owns configurations and many interval associations per intact file.
All relationships use SQL foreign keys; no domain types enter the library.
Interval joins use half-open `[start, end)` semantics, normalized UTC text and
an independent generated oracle. Pagination and byte verification cover every
file. Queries cover exact boundaries and several selectivities.

Batch samples compare repeated SQL with `sql_many` at 100/1,000/10,000 parameter
sets, bounded by the profile size. Index experiments affect only disposable
application tables. Reports contain plans, index build time and write/query cost;
one API call does not imply one network round trip. The report label `start`
means the start-first index alone; `end` means an additional end-first index
exists alongside it. The optimizer can choose either index or a scan. These are
sequential observations, not controlled causal measurements of index benefit.
Metadata phases assert zero
payload I/O and instrumented writes reject storage calls in SQL write scopes.

Client admission is explicitly serialized because S3 coordinator initialization
takes an exclusive lease. After admission, independent processes execute
concurrent reads, disjoint edits and competing guarded edits. There are no
connection/transaction retries. Expected stale-version conflicts are counted
separately from successful operations and unexpected failures.

Operational phases cover dry-run/no-change, durable migration interruption and
reopen/resume, uncertain publication with explicit reopen/inspection, interrupted
backup without a completion manifest, full backup/restore, corrupt artifact and
occupied-destination rejection, and SQLite-to-PostgreSQL cutover. Every successful
copy verifies the entire payload corpus and application rows. A failed SQLite
restore may retain staging files; it must not publish a ready catalog. Source
state and payloads are verified after operational phases.

## Interpreting evidence

`report.json` is atomically replaced and starts incomplete. Each adapter runs in
a fresh process and keeps its own partial report. JSON contains exact installed
MeldDB provenance, source revision/dirty state, lock identity, service digests,
codec/client/native archive versions, actual counts and phase results.
`summary.txt` is written only after all required phases pass. A smoke success
explicitly leaves representative qualification false. Missing prerequisites or
phases fail instead of skipping.

Query latency uses five warm-up calls and at least 100 measured calls per case;
p50/p95 include units and sample counts. First-pass I/O is not labeled cold-cache.
Measurements include instrumentation overhead and are observations, not an SLA.
RSS includes per-process lifetime peaks; concurrent aggregate RSS is sampled at
50 ms intervals. Staging/catalog/archive disk is sampled at 250 ms intervals and
excludes the source corpus. Samples can miss short peaks. Client usage is not
PostgreSQL/RustFS memory usage. Unknown metrics must not be represented as zero.

Raw reports and datasets remain ignored. Review sanitized evidence before
committing a summary; no paths, credentials, private identities or payloads belong
in public artifacts. Full-scale qualification is limited to the host actually run.


Service volume usage is sampled separately with mounted-volume `du`; disappearing
service temporary files invalidate that sample, not the workload. The sampler
retries a bounded three times, records unavailable samples with their reason,
and fails on permission or other unexpected errors. `docker stats` supplies a
separate end-of-variant service memory/CPU snapshot, not a service peak claim.
Service-volume high-water counters can carry earlier observations across adapter
processes. Interpret their maximum as a shared-lab high-water observation, not
as isolated per-adapter disk usage; local variant disk measurements are separate.
Source-file fingerprints accompany the Git revision so dirty working-tree runs
identify the exact implementation used. SQL/storage call counts cover wrapped
public APIs; absent counts in uninstrumented operational phases are unavailable,
not zero. Native archive subprocess work is included in elapsed times, not
interpreted as public SQL-call counts.

See [the verification record](representative-workloads-verification.md) for
completed runs, measured follow-ups and qualification boundaries.

## Uninterrupted measurements on Windows

For a long run on Windows, add `--keep-awake` to request an idle-sleep hold for
that process only. It does not change the power plan or keep the display on,
and cannot override lid-close or explicit sleep. See Microsoft's
[SetThreadExecutionState contract](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate).
The hold is released on exit. Keep the machine powered and open during the run.

A separate heartbeat detects scheduling or wall-clock gaps exceeding 30 seconds.
Such a gap invalidates the run; required resource checks stop further work and
reports retain the interruption evidence. This detects disrupted measurements,
not a guaranteed diagnosis of sleep versus severe scheduling delay or clock change.
There are no automatic retries of interrupted database or blob operations.


The cutover phases pass explicit limits derived from the declared workload:
`max_entries = file_count` and
`max_rows = relationship_count + 10 * file_count + 1000`. The bounded allowance
includes blob descriptors, schema/migration state and configuration rows. The
100-file profile therefore permits at most 303,000 total metadata rows; it does
not inherit the public transfer API's 100,000-row default or disable that guard.
Reports record both limits for preflight and transfer. Separate opt-in live tests
exercise 300,000 relationships with small payloads on both SQLite adapters; those
tests are regression evidence, never a substitute for representative file sizes.
Ownership records use the same bounded Windows sharing-error replacement as
evidence reports; persistent denial still fails and preserves the prior record.


## Large-file request budgets

`--request-timeout-seconds` sets a finite, positive timeout for a complete S3
request, including response-body consumption. It defaults to 60 seconds; use
`--request-timeout-seconds 300` for the representative command above. The report
records this explicit client policy and `max_retries: 0`. The overall runtime,
memory, disk, correctness and complete-workload gates remain in force. No
database or payload operation is automatically retried.

A fifth Windows attempt hit a response-body timeout during restored payload
verification despite continuous host execution. The timeout is not limited to
connection establishment: consuming a large stream must finish within it. See
[obstore's client configuration](https://developmentseed.org/obstore/latest/api/store/config/#obstore.store.ClientConfig.timeout).
A larger declared budget is a measured deployment setting, not a throughput SLA
or a change to MeldStore's public storage defaults. Custom 100-300 MB diagnostics
remain explicitly non-representative. The full 100-file run passed separately
on Windows using the recorded 300-second setting.
