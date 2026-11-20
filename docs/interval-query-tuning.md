# Application-owned interval query tuning

This development tool compares SQL metadata only. It seeds synthetic catalog rows
with no payload files. These catalogs are disposable experiment inputs, not usable
blob stores. MeldStore installs no application interval indexes or tuning defaults.

Run from the repository with the locked uv environment and Docker running:

```console
uv run --frozen --extra test --group postgres-test python tools/qualify_intervals.py --profile smoke --output .qualification/interval-smoke
uv run --frozen --extra test --group postgres-test python tools/qualify_intervals.py --profile full --output .qualification/interval-full --seconds 21600
```

Each destination must be new. `--keep-awake` optionally holds Windows idle sleep
while running, but does not prevent lid-close or explicit sleep. The tool detects
execution discontinuities and refuses a completed result. It creates a dedicated
loopback PostgreSQL 17 container using the existing pinned image, temporary
credentials and run-owned resources. No ambient DSN or production database is used.

## Pause and resume

New runs write schema-v2 checkpoints to `report.json` after each complete treatment.
A treatment includes all queries, warm-ups, samples, writes and correctness checks;
individual timing samples are never resumed. To plan a short session:

```console
uv run --frozen --extra test --group postgres-test python tools/qualify_intervals.py --profile smoke --pause-after 6 --output .qualification/interval-session-1
uv run --frozen --extra test --group postgres-test python tools/qualify_intervals.py --resume-from .qualification/interval-session-1/report.json --output .qualification/interval-session-2
```

For a running session, create an empty `PAUSE` file in that session's output
directory. In PowerShell, for example:

```powershell
New-Item -ItemType File .qualification/interval-session-1/PAUSE
```

The runner finishes the current treatment, checkpoints it, cleans up the service,
and prints its paused/resume-eligible status. Wait for the command to exit before
closing the lid or shutting down. Ctrl+C interrupts the current worker and attempts
cleanup immediately; its incomplete treatment will run again in full. A clean
boundary pause exits 0; Ctrl+C exits 130; failures exit 1. Only `status: passed`
means the declared matrix completed. Check `resume_eligible: true` before resuming.

Resume inherits the exact profile, seed, schedule and fixed memory/disk/reserve
budgets. Explicit conflicting options are rejected. `--seconds` supplies a fresh
finite wall budget for each session; `--keep-awake` must be requested again when
wanted. The destination must be fresh and outside the source report directory.
All retained results are revalidated against the oracle, schedule, checksums,
session history and source/runtime attestations. The resumed process recreates
its own service and databases; it does not modify the source report or reuse its
catalogs. Keep sources, Git HEAD, lockfile, environment and service image unchanged
between sessions. A source or environment update requires a new experiment.

XXH3 checksums detect accidental evidence damage; they do not authenticate an
untrusted report. Use reports from your own runs. Active/unclean reports, abrupt
process termination, power loss and schema-v1 evidence are not automatically
resumable. In particular, the previously interrupted v1 full runs cannot be
converted by simply changing their status or schema version.

Session IDs, UTC boundaries, resource limits and execution continuity are retained
in evidence and summarized with cross-session baseline pairs. Continuity is checked
within sessions, not across the pause. Host load and OS caches can change between
sessions; resumed comparisons are disclosed as multi-session observations. The
entire original matrix and every session's cleanup must still pass for acceptance.

## Explicit recovery after abrupt termination

Ordinary resume still refuses an unclean report. On Windows, an explicit offline
recovery command can validate an interrupted v2 prefix and prepare a new report:

```console
uv run --frozen --extra test --group postgres-test python tools/recover_intervals.py --source .qualification/interval-interrupted/report.json --output .qualification/interval-recovered
uv run --frozen --extra test --group postgres-test python tools/qualify_intervals.py --resume-from .qualification/interval-recovered/report.json --output .qualification/interval-next --keep-awake
```

Recovery accepts only still-open `incomplete` v2 reports with at least one accepted
treatment in the interrupted session. It cannot override a finalized failed
session, lost continuity in the saved prefix, or an empty new session. Keep the
original run directory (ownership manifest and worker controls included) on the
same host. Stop interval runners/workers first; recovery never kills them.
Run recovery exclusively, with no concurrently launched interval experiment or
manual resource changes. Docker must be available for PostgreSQL cleanup. CIM
inspection must expose all Python/uv command lines; unavailable inspection or an
active runner refuses recovery. Other operating systems currently refuse it.
A stale `recovery.lock` requires confirming no recovery process is alive before
manual removal; there is no automatic lock stealing.

The tool validates the entire saved prefix and exact measured provenance, checks
canonical ownership and paths, then removes only owned disposable resources and
worker scratch. It preserves the original report, logs and control files. A fresh
report records source/ownership/tool digests and the original interruption state.
Its recovered measurement session ends at the last verified checkpoint. Actual
cleanup occurs later and has its own timestamp. Continuity after that checkpoint
is **unknown**, and no incomplete-treatment samples are retained. Inspect the
`recoveries` audit and `recovery.md`; existing generated summaries list measurement
boundaries but do not distinguish later recovery cleanup on their own.

The recovery command is separate orchestration, independently fingerprinted in
its audit. Measured source fingerprints, dependencies and HEAD must still match
exactly. This permits recovery tooling to be added without changing the code that
produced the measurements. It does not permit importing changed benchmark code,
legacy reports or untrusted evidence. Recovery remains partial evidence until the
original full matrix, fresh resumed sessions and final cleanup all pass.

## Profiles and semantics

| Profile | Synthetic blob references | Relationship rows | Rounds | Warm-ups per query/round | Measured calls per query/round |
| --- | ---: | ---: | ---: | ---: | ---: |
| smoke | 4 | 2,000 | 1 | 1 | 3 |
| full | 100 | 300,000 | 3 | 5 | 40 |

Both profiles require direct SQLite, MeldDB SQLite and MeldDB PostgreSQL. The full
matrix has 72 independently prepared treatments and 11,520 measured reads.
Overrides require `--profile custom --files N --relationships N`; optional
`--adapters sqlite melddb` produces custom evidence only. Seed and randomized
execution/query orders appear in the report. Fixed profiles cannot silently shrink.

The fixture reuses `app_configs` and `app_intervals`, referencing public blob IDs.
Its timestamps are normalized UTC ISO text with six fractional digits. Each
positive-duration query uses strict overlap, `start_time < query_end AND end_time >
query_start`. A row merely touching either boundary does not match. Queries join
configuration and blob references, group counts by blob ID and order those IDs.
Two narrow and two broad selections are measured. Empty results, touching endpoints
and adjacent microseconds receive additional correctness checks. Expected results
come from the Python generator independently of SQL query execution.

## Compared indexes

| Layout | Columns, in order |
| --- | --- |
| start | `(start_time,end_time)` |
| start-plus-end | `(start_time,end_time)` plus `(end_time,start_time)` |
| covering-start | `(start_time,end_time,config_id,blob_id)` |
| covering-end | `(end_time,start_time,config_id,blob_id)` |

Covering variants replace the start index. Primary keys and join tables are
unchanged. A covering definition does not guarantee an index-only plan. Every
layout is tested on independently loaded fresh and explicitly analyzed fixtures.
PostgreSQL autovacuum is disabled only in this disposable experimental service to
keep the untreated statistics state stable. No vacuum/visibility preparation is
performed. Statistics are recorded before and after reads/writes and must agree.
SQLite analysis uses the public exclusive maintenance API.

## Interpreting results

`report.json` contains exact source/helper/lockfile fingerprints, provider pin,
Python/package/database versions, effective settings, service limits, plans, raw
latencies, correctness flags, allocations, write costs and cleanup state.
`summary.md` shows every case and round, including regressions. Lower p50 ratios
mean faster reads relative to start-only in the same adapter/statistics/round.
Compare all rounds, selection widths, index space and insertion costs before making
an application-specific choice. Mixed or noisy results are inconclusive.

Timings include the public catalog call and result materialization, but exclude
oracle computation, comparison, plan collection, fixture setup and warm-ups.
Index construction is timed separately. Write probes insert up to 10,000 rows in
batches of 100, 1,000 and 10,000, then deliberately roll back and verify every
persisted application row. Full-profile write probes repeat three times per batch.
These are insertion-plus-rollback costs, not durable commit throughput.

Memory settings remain fixed across layouts within each adapter. SQLite cache size,
page size, journal mode, synchronization and runtime version are recorded. Optional
mmap diagnostics can be unavailable on a runtime. The public catalog does not expose
a cache-size setter. PostgreSQL uses 128 MB shared buffers, 4 MB work memory,
512 MB planner cache estimate, at most two parallel workers and a one-minute
statement timeout, within a 2 GiB container memory/swap ceiling. Neither connection
reopening nor fresh fixtures imply cold OS caches. Cross-adapter measurements have
different memory models and are separate observations.

SQLite space measures allocated database pages and excludes WAL; per-index size is
reported unavailable. PostgreSQL records whole-database and individual interval
index allocations. Read phases precede rollback probes to avoid their write effects.
Client/host budgets are monitored between calls and by a supervising process;
PostgreSQL volume capacity/usage is sampled at treatment boundaries and every ten
seconds while a worker runs. Monitoring overhead is present in every treatment.

Default limits are four hours, 4 GB client memory, 4 GB working disk and a 2 GB disk
reserve, plus the separate 2 GiB service ceiling. Preflight checks available host
memory/disk; workers have hard process deadlines and per-call checks. Resource
exhaustion or teardown failure leaves failed evidence, never a smaller passed run.
Only fresh run-owned catalogs and service resources are cleaned; reports and local
control/log files remain under the ignored output directory. Share only the
sanitized report and summary, not internal control files containing local paths.

Completion requires every declared matrix cell, exact query outcomes, preserved
rollback state, matching source attestations/settings, valid sample counts and
successful cleanup. `full_metadata_qualified` is true only for the full profile.
`representative_qualified` always remains false. Payload integrity, corrected full
20.04 GB workload acceptance, AWS and multiple coordinating hosts remain separate.

The full matrix passed on November 20, 2026. See the [measured guidance](interval-query-results.md)
and [verification record](interval-query-verification.md) for all-round tradeoffs
and session/recovery limitations. No shared metadata-provider contract or MeldDB
pin changes were required.
