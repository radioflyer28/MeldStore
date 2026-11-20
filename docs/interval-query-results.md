# Measured application interval guidance

The full metadata experiment passed on November 20, 2026: 100 synthetic blob
references, 300,000 application relationships, three adapters, four index layouts,
fresh/analyzed statistics and three rounds (72 treatments). Each query has five
warm-ups and 40 measured reads per round. All 11,520 measured reads matched the
independent oracle; all 648 insertion/rollback trials preserved the fixture.
MeldStore installs none of these application indexes automatically.

Use the [complete comparison tables](evidence/interval-full-tables-2026-11-20.md),
[all-round p50/p95 summary and costs](evidence/interval-full-summary-2026-11-20.md),
[raw evidence](evidence/interval-full-2026-11-20.json) and
[independent completion checks](evidence/interval-full-verification-2026-11-20.json).
The [structured aggregates](evidence/interval-full-aggregates-2026-11-20.json)
retain every round's p50, ratio, plan, allocation and write cost.

## Workload and limits

Queries use strict overlap (`start_time < query_end AND end_time > query_start`),
join application configurations and blob metadata, and return grouped counts by
blob ID. Timestamps are normalized UTC ISO strings with six fractional digits.
Q0 and Q1 select [0,1] and [120,121] seconds from the synthetic origin; Q2 selects
[30,3000], and Q3 [0,6120]. These are selection bounds, not inclusive predicates.
The deterministic fixture is described in the [runner guide](interval-query-tuning.md).
Its time and configuration distribution is not a universal workload model.

Evidence spans three sessions: treatments 1-21 on November 18, 22-65 on November
20, and 66-72 later that day. Session 2 was interrupted after checkpoint 65;
explicit recovery verified the saved prefix and cleaned its owned resources.
Its measurement boundary is the last checkpoint, and continuity afterward is
unknown. The recovery audit separately records the later cleanup time and exact
source/tool hashes. No incomplete samples or legacy v1 results were imported.

Pauses, host load, OS cache state and other session effects remain confounders.
The summary identifies cross-session baseline pairs. These are repeated observed
comparisons, not uninterrupted paired measurements or causal guarantees. Do not
rank adapters from these numbers. Narrow-query sub-millisecond differences and
small mixed changes particularly need a new application-specific experiment.

Memory settings were fixed, not swept: SQLite's recorded cache is -2000 (KiB),
with WAL/FULL and 4096-byte pages; mmap diagnostics were unavailable. PostgreSQL 17
used 128 MB shared buffers, 4 MB work memory, a 512 MB planner cache estimate,
at most two parallel workers and a 2 GiB container ceiling. Fresh fixtures and
warm-ups do not imply cold OS caches. PostgreSQL background autovacuum/analyze was
disabled for the controlled experiment; that is not production tuning advice.

## SQLite: consider start-first covering for broad reads

For both direct SQLite and MeldDB SQLite, replacing the start-only index with
`(start_time,end_time,config_id,blob_id)` reduced broad-query round p50 to roughly
**4-21% of the matching start-only value** in every measured round and statistics
treatment. Captured plans use `USING COVERING INDEX` for this layout. This makes
it a useful candidate for applications dominated by broad interval joins.

There are costs. Whole-database allocation rose from about **47.52 MiB to
52.93-52.94 MiB** (about 11%); SQLite per-index allocation is unavailable and these
figures exclude WAL. For direct SQLite with fresh statistics, median build time
rose from 331 to 850 ms, and the 10,000-row insertion/rollback probe from 28.35 to
70.30 ms. Other rounds and statistics treatments have different write ratios;
all batch sizes and build/allocation measurements remain in the linked tables.
These probes include rollback and do not measure durable commit throughput.

Narrow-query results for the covering index are mixed, including regressions.
Start-only remains a sensible baseline for narrow-selection or write-sensitive
applications. Do not add both layouts without a separate experiment: the tested
covering layout replaces the original start index.

## Do not add the reverse index by assumption

With both `(start_time,end_time)` and `(end_time,start_time)` present, SQLite's
narrow queries regressed in every round: roughly **29-817 times** the matching
start-only p50 across both adapters and statistics treatments. Captured SQLite
plans choose `app_interval_end` for the tested selections. Broad-query outcomes
are mixed or worse; database allocation also rises to about **66.06-66.07 MiB**.
The end-first covering variant improves broad reads but retains large narrow-read
regressions. For this mix, start-first covering is the more useful candidate.

This evidence supports checking plan choice and interval selectivity before
adding a reverse index. It does not establish that end-first indexes are always
wrong for other time distributions or query windows.

## PostgreSQL: retain start-only as the baseline

Start-first covering did not produce a repeatable broad-query improvement across
all three rounds. For analyzed Q3 its ratios were 0.957, 0.808 and 1.024: a useful
single-round improvement does not establish an overall winner. Captured plans
show parallel sequential scans for broad queries even with covering indexes;
analyzed narrow queries can use an index-only scan.

The dual-index layout's narrow queries were about **1.5-2.5 times** the matching
start-only p50 in every round. Its captured narrow plans can still use the start
index, so explain output alone does not establish why timings differ. End-first
covering also regressed narrow reads. No added layout is justified as a default
by this experiment.

The non-primary interval indexes occupy about 2.39 MiB for start-only, 3.17 MiB
for either covering layout and 4.78 MiB for the dual layout. The unchanged primary
index is excluded from those figures. Whole-database allocation, build costs and
all rollback probe sizes are recorded separately. Write results are mixed and
must be weighed alongside read selectivity and extra space.

## Application choices and remaining work

Start with the simpler start-first index, capture your query plans, and test the
start-first covering alternative when broad joins dominate. Preserve narrow cases
and insertion costs in that comparison. Repeat with your own synthetic interval
duration, time distribution, configuration cardinality and selection mix; include
freshly loaded and analyzed data. Statistics maintenance is not uniformly faster
in this study, which does not justify disabling it in an application.

Possible follow-ups are controlled cache-budget sweeps through supported APIs,
single-session confirmation of smaller effects, and PostgreSQL-specific range/GiST
or INCLUDE experiments when a real workload motivates them. Those capabilities
and index families were not evaluated here. No global SQLite settings, library
schema defaults or provider API changes follow from these measurements.

The exact MeldDB pin remains `01e27441d7512f85663c8a86986429d62a69bff9`.
The existing shared SQL/transaction contract suffices; no change to MeldDB or
`meld-platform-specs` is needed. Full payload integrity, the corrected 20.04 GB
rerun, Linux full-scale performance, AWS and multi-host coordination remain
separate qualification work. This is completed metadata evidence, not a release.
