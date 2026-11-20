# Interval-query tuning verification

Change: `tune-application-interval-queries`, based on merged MeldStore main
`a50824b2ab799dff9b1f711b59aee307f41160f8`. Exact runner/helper/package fingerprints
are retained in the evidence because the implementation was uncommitted during
those measurements.

## Scope and ownership

MeldStore owns this development-only runner, synthetic application fixtures,
evidence and guidance. The shared `metadata-provider-contract` was reviewed through
the `meld-platform` workset. Existing public SQL, transactions and maintenance are
sufficient. No core storage API, schema default, MeldDB source or shared behavioral
contract changed. The provider remains pinned to
`01e27441d7512f85663c8a86986429d62a69bff9`. MeldDB's broader PostgreSQL support status
remains experimental.

The test fabricates catalog references and never creates payload data. All measured
metadata operations reject obstore payload calls. Exact application rows, strict
interval results and explicit rollback outcomes are checked independently of SQL.
These results do not qualify payload integrity or the pending corrected full
20.04 GB representative workload rerun.

## Verification on November 16, 2026

- `uv run --frozen --extra test pytest`: 938 passed, 315 skipped on Windows/Python
  3.13.15. Skips include separate PostgreSQL/S3 deployment and live-workload gates;
  they are not counted as passing live interval coverage.
- `uv run --frozen --extra test ruff check .`: passed.
- Focused interval/ownership/resource/evidence regressions: 53 passed. The final
  optional-dependency loading adjustment was additionally checked by its nine
  resource tests and a fresh full-matrix live smoke.
- `uv run --frozen --extra test --group postgres-test python tools/qualify_intervals.py
  --profile smoke --output .qualification/interval-smoke-04 --seconds 900`: all 24
  treatment cells passed, with all three real adapters and successful cleanup.
- `openspec validate tune-application-interval-queries --strict`: passed.

The [sanitized smoke evidence](evidence/interval-smoke-2026-11-16.json) records the
installed sources, runtime, pinned PostgreSQL image, effective settings and complete
raw results. Smoke is explicitly not full metadata acceptance. The new required CI
step exercises this same smoke command; remote CI has not been run for this working
tree. Packaging and runtime dependencies are unchanged.

Initial development smoke caught PostgreSQL's canonical timeout representation
(`1min` for a requested `60s`), which the runner now uses and tests. An unavailable
SQLite mmap-size diagnostic is reported with a reason. Failed development outputs
remain local and are not reused as successful comparisons.

## Resumption on November 18, 2026

Source fingerprints and complete runtime/dependency provenance still matched the
November 16 verified smoke. The required pytest suite was refreshed: 938 passed,
315 skipped in 193.07 seconds. Ruff also passed. A fresh full run was started in
`.qualification/interval-full-02` with the default budget. Its first two treatments
took 210 and 429 seconds, projecting beyond the four-hour allowance on this host.
That run was stopped early, retained as incomplete and its owned service removed.
The complete unchanged matrix was restarted as `.qualification/interval-full-03`
with a finite 21,600-second budget and Windows idle-sleep hold. Earlier partial
results are not combined with this run. Memory, disk, service limits and sample
counts are unchanged. The user then requested shutdown after 53 completed
treatments. Its process tree was stopped and owned PostgreSQL resources removed.
The report remains paused/unqualified, with local partial artifacts retained.
It predates the checkpoint protocol and cannot be resumed through schema v2.

## Full metadata acceptance

**Passed November 20, 2026.** The complete 100-reference/300,000-relationship matrix
passed all 72 adapter/layout/statistics/round treatments, 11,520 measured reads
and 648 insertion/rollback trials, with successful cleanup. This includes direct
SQLite, MeldDB SQLite and the pinned real PostgreSQL 17 service on Windows.

The [full raw report](evidence/interval-full-2026-11-20.json),
[independent verification](evidence/interval-full-verification-2026-11-20.json),
[all-round summary](evidence/interval-full-summary-2026-11-20.md) and
[measured application guidance](interval-query-results.md) retain costs, regressions
and inconclusive results. Raw report XXH3-128: `0068977bd658f9e73d69f8f96e7cd701`.

The v2 run spans three measurement sessions: 1-21 (November 18 clean pause),
22-65 (November 20 interrupted prefix recovered explicitly), and 66-72 (fresh
resumed resources, completed). Session 2 ends at checkpoint 65, not at process exit;
continuity after that checkpoint is unknown. Actual recovery cleanup has its own
later audit timestamp. Cross-session comparisons are disclosed, not treated as
uninterrupted paired measurements. Original 21/65 reports and the accepted 65-cell
prefix/checksums remain unchanged. Measured provenance matched the report at
full-run closeout; later PR fixes are recorded below. All three runs' owned
containers, volumes and worker scratch were confirmed absent.
No legacy v1 measurements were reused. No private Parquet files were used.

Recovery and final resume used:

```console
uv run --frozen --extra test --group postgres-test python tools/recover_intervals.py --source .qualification/interval-full-v2-02/report.json --output .qualification/interval-full-v2-02-recovered
uv run --frozen --extra test --group postgres-test python tools/qualify_intervals.py --resume-from .qualification/interval-full-v2-02-recovered/report.json --output .qualification/interval-full-v2-03 --seconds 21600 --keep-awake
```

Tasks 4.2/4.3 are complete. The corrected full-payload 20.04 GB rerun, AWS, full-scale
Linux and multi-host qualification remain pending. No release is implied.
Historical attempts and checkpoint verification follow below.

## Checkpoint amendment on November 18, 2026

The interval CLI now creates schema-v2 per-treatment checkpoints and supports
`--pause-after`, a `PAUSE` marker, Ctrl+C cleanup and `--resume-from` into a fresh
output directory. Resume validates all saved results, session history, checksums,
source/runtime identity, profile/seed, fixed budgets and service configuration.
Interrupted treatments restart from scratch. Summaries disclose session boundaries
and baseline comparisons spanning sessions. Legacy v1 reports, unclean shutdowns
and source/runtime changes are refused rather than silently merged.

The implementation was developed in an isolated worktree while the v1 run was
active, then consolidated into the original interval branch after the requested
shutdown. No provider, shared-contract or library storage API changes were needed.

- Required `uv run --frozen --extra test pytest`: **910 passed, 371 skipped** in
  211.21 seconds on Windows/Python 3.13.15. This environment was synced with the
  test extra and postgres-test group; optional array/dataframe handlers and separate
  live deployment gates account for skips. These are not passing live coverage.
- `uv run --frozen --extra test ruff check .`: passed.
- Focused interval suite: 88 passed; the five subsequently added cleanup/continuity
  tests also passed and are included in the complete suite above. Coverage includes
  readonly resume sources, corrupted/incompatible checkpoints, interrupted retry,
  pause markers, cleanup failure, execution gaps, safe scratch removal and actual
  Windows worker-process-tree termination.
- `openspec validate tune-application-interval-queries --strict`: passed.

Real all-adapter pause/resume verification passed using these commands:

```console
uv run --frozen --extra test --group postgres-test python tools/qualify_intervals.py --profile smoke --pause-after 12 --seconds 900 --output .qualification/interval-checkpoint-smoke-paused-01
uv run --frozen --extra test --group postgres-test python tools/qualify_intervals.py --resume-from .qualification/interval-checkpoint-smoke-paused-01/report.json --seconds 900 --output .qualification/interval-checkpoint-smoke-resumed-01
```

The first session paused with 12 validated treatments and confirmed cleanup. The
second started at treatment 13 and finished the 24-treatment matrix across direct
SQLite, MeldDB SQLite and real PostgreSQL 17. The saved source report is byte-for-
byte unchanged; its first 12 treatment results are identical in the final report.
Only treatment directories 13–24 were created by the resumed session. Independent
post-run validation confirmed matching final sources, both session cleanups, no
remaining owned Docker resources or temporary catalogs, and no full metadata or
payload qualification claim. The [sanitized checkpoint smoke evidence](evidence/interval-checkpoint-smoke-2026-11-18.json)
retains both sessions and every raw result. CI now requires the same pause/resume
sequence; remote CI has not yet run for these uncommitted changes.

Tasks 5.1/5.2 are complete. On November 18, a fresh full schema-v2 run started
with a 21,600-second budget and the Windows idle-sleep hold. Source/runtime
provenance matched the completed checkpoint smoke exactly before startup. Its
72-treatment matrix uses no legacy partial results. At the user's request it
paused cleanly after 21/72 treatments, with successful cleanup, continuity and
resume eligibility. The saved prefix was independently revalidated and all owned
service resources and temporary catalogs were confirmed absent. The remaining
51 treatments can continue in a fresh output with unchanged sources/runtime.
Full acceptance and measured guidance (4.2/4.3) remain pending.


## Verified interrupted-prefix recovery on November 20, 2026

Added explicit Windows-only `tools/recover_intervals.py`; see the
[recovery protocol](interval-query-tuning.md#explicit-recovery-after-abrupt-termination).
It preserves original reports, revalidates accepted measurements, verifies stopped
processes and owned cleanup, and publishes a fresh stopped prefix with a recovery
audit. Measurement ends at the saved checkpoint; subsequent continuity is unknown
and cleanup has a separate later timestamp. The measured runner, provider pin,
lockfile and library sources remain unchanged. The new orchestration tool is
independently fingerprinted in recovery evidence.

Verification with the locked uv environment:

- `uv run --frozen --extra test pytest`: **934 passed, 372 skipped** in 108.68s.
  This includes 24 passing recovery regressions; an additional symlink-creation
  test was skipped because host permissions disallow it. Junction refusal is
  tested separately. Other skips retain optional-format/live-gate limitations.
- `uv run --frozen --extra test ruff check .`: passed.
- `openspec validate tune-application-interval-queries --strict`: passed.
- Live smoke was force-terminated after two accepted treatments, recovered and
  resumed to **24/24** across direct SQLite, MeldDB SQLite and PostgreSQL 17.
  Original source digest and accepted prefix were unchanged; independent final
  validation passed and every owned container, volume and scratch catalog was absent.
  Windows taskkill returned 255 during the deliberate interruption; independent
  process inspection established both parent and workers were gone before recovery.
- Final publication-order handling was exercised again against the stopped source,
  including already-absent resources. A retry during the unit suite correctly
  refused an unreadable Python command line; after the suite exited it passed.

The required tests cover active/uninspectable processes, post-cleanup process
appearance, corrupt checksums/samples/guard history, changed provenance/source,
manifest mismatch, occupied/overlapping output, stale locks, unsafe worker controls,
junctions, cleanup failure and failed final publication validation. Packaging and
runtime dependencies are unchanged. These are local checks; remote CI is pending.

Tasks 6.1/6.2 are complete. Full metadata acceptance still requires all 72 treatments
and final cleanup; recovery itself confers neither metadata nor payload qualification.


The full v2 run subsequently recovered **65/72** accepted treatments. Independent
checks confirmed all retained cells/checksums, prior closed sessions and saved
execution guard were unchanged. Both the original 21-cell clean report and the
65-cell interrupted report remained unchanged; all interrupted-run owned resources
were absent. The third measurement session resumed at treatment 66 in fresh resources and
completed all 72 with cleanup. See the full acceptance record above.


## Spec sync and archive

On November 20, 2026, synced eight interval/checkpoint/recovery requirements and
18 scenarios into MeldStore's canonical
[representative-workload-qualification spec](../openspec/specs/representative-workload-qualification/spec.md).
The original eight requirements and 16 scenarios remain unchanged. The resulting
16-requirement, 34-scenario spec passes strict validation. All eight canonical
specs pass standard validation; five pre-existing long-description warnings in
relational-metadata remain outside this change.

Verified every synced requirement against its delta and archived the completed
[change](../openspec/changes/archive/2026-11-20-tune-application-interval-queries/proposal.md)
with all 17 tasks complete and every artifact, including `.openspec.yaml`, preserved
unchanged. No implementation or measured-source edits were needed for closeout.
At spec archival, changes were uncommitted; subsequent PR review is recorded below.
No package release is implied.


## PR #4 review fixes and revalidation

[PR #4](https://github.com/radioflyer28/MeldStore/pull/4) review found two issues:
PostgreSQL startup could report socket readiness before TCP was accepting
connections, and resume/completion did not validate the recovery audit fields.
The service now probes `127.0.0.1` explicitly. Validation binds each audit to its
recovered session, checks digest formats, original interruption state, process
checks, measurement boundary and later cleanup chronology, and rejects missing,
duplicate or malformed attestations. Fourteen regressions reproduced the issues
before the fixes; a further test covers rejection at full completion.

- Focused interval/recovery suite: 131 passed, 1 host-permission skip.
- `uv run --frozen --extra test --extra arrow --extra postgres --group postgres-test pytest`:
  **974 passed, 359 skipped** in 254.21 seconds. The locked Arrow extra is now
  installed for the upcoming payload rerun; remaining skips include optional
  formats, separately gated live deployments and the symlink-permission test.
- `uv run --frozen --extra test ruff check .`: passed.
- A fresh real forced stop after two treatments, audited recovery and resumed
  all-adapter smoke passed **24/24**, preserving the original report and prefix
  and confirming all owned resources absent. See the
  [review smoke report](evidence/interval-review-smoke-2026-11-20.json).
- Both independent standards/spec reviewers confirmed their findings resolved
  and found no new actionable issues in the fixes.

The initial push-triggered CI failed on the first PostgreSQL treatment after
resume; its parallel PR-triggered run passed. The fixed head must pass CI before
merge. Readiness is now checked over the same TCP transport used by treatments.

The original 72-treatment report remains byte-for-byte unchanged and passes the
strengthened validator. It describes the pre-review code, not a rerun of the
fixed head. The [source closeout record](evidence/interval-source-closeout-2026-11-20.json)
records the two changed helper hashes and new verification. Changes are confined
to startup before treatments and audit validation outside measured worker calls;
all other attested source files, including query/fixture/worker code, are unchanged.
No full metadata matrix was rerun after these review fixes. Historical measured
results retain their original provenance and cross-session limitations.
