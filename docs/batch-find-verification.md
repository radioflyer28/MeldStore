# Batched find verification

Change: `batch-find-record-loading`. Baseline: MeldStore `51bc084dcaf80db602bc870898ed7537c51a1735`
(qualification PR #2). The implementation is on `codex/batch-find-record-loading`.

## Scope and contracts

`Store.find` retains the existing query compiler and one read-only transaction,
then loads selected rows in 256-ID chunks. Metadata key order remains canonical,
matching scalar reads even when the supplied declaration uses a different order. Each chunk uses two retrieval statements
without the legacy-optional encoding extension, or three with it. Schema and
encoding validation work is independent of page size. Scalar `stat` and publication
keep their existing `_record` path. There is no public API or catalog migration.

The proposal review clarified empty-page encoding checks, existing adapter-specific
structural verification, individual corruption exceptions, and performance gates.
Precedence among simultaneous independent corruptions is unspecified. Tests cover
later-chunk snapshots, detached values, and failure before returning the complete page.

The `meld-platform` workset was consulted across MeldDB, MeldStore and the local-only
`meld-platform-specs` store. This uses the existing public parameterized SQL and
read-only transaction boundaries. The exact MeldDB pin remains
`01e27441d7512f85663c8a86986429d62a69bff9`; no new provider API, migration or shared
behavioral contract is introduced. The snapshot guarantee is verified against that
pin. MeldDB's broader PostgreSQL status remains experimental.

The PR branch includes qualification PR #2's merged revision `30f11bc`. The full
performance report retains the original comparison baseline and candidate source
fingerprints. All production Python files on the PR branch match the measured
candidate. The merged workload changes affected the lab helpers and required
fresh integration checks.

## Reproducible metadata comparison

Use the same uv lock/environment for both source trees. Extract `src` from the
baseline commit with `git archive --format=zip --output=baseline.zip 51bc084 src`,
then unpack it under an ignored qualification directory. Run:

```shell
uv run --frozen --all-extras python tools/qualify_find.py \
  --baseline-src .qualification/find-baseline/src \
  --output .qualification/find-comparison
```

The runner creates owned disposable loopback PostgreSQL/RustFS services. Metadata
measurements use fabricated ready SQL records and reject all payload access; the
idle RustFS service comes from the existing qualification service helper. This is
not payload or capacity qualification. Each catalog contains 300,000 independently
verified application relationships. Pages actually contain 100 or 1,000 records;
the latter exercises four batches. Separate child processes load baseline or
candidate sources with identical dependencies. Each of 1/2/4 clients performs
five warm-ups and 100 measured calls, checking every complete record against an
independent fixture oracle. Setup counts are separate from find-only SQL counts.
Results include p50/p95, per-client peak RSS, sampled aggregate RSS, errors and
conflicts. Read-only benchmark conflicts must be zero; mixed read/write behavior
is exercised separately by the existing all-adapter workload smoke.

The revised harness verifies baseline Python contents against the named Git tree
(allowing only CRLF-to-LF normalization), checks worker import locations and source
fingerprints before timing, and distinguishes completed measurements from full
acceptance. Smaller CLI runs are explicitly diagnostic. Full acceptance also
requires the complete matrix and an explicit latency/memory review; the report
validator can audit earlier reports without inventing historical worker attestations.
A real-service diagnostic pilot passed across all adapters with source verification
true, diagnostic profile, and full acceptance false, as intended.

A completed baseline can be reused with `--reuse-baseline REPORT.json` while
measuring every candidate case afresh. Reuse requires matching baseline Git
content, runtime/dependency lock, fixture source, complete matrix and sample
profile, plus successful baseline observations. A source change confined to
formatting requires an explicit matching AST-equivalence audit; it is never
silently treated as the same fixture. The report identifies its baseline input
by fingerprint and preserves missing historical worker attestations as missing.

The acceptance gate is at least 90% fewer PostgreSQL SQL calls for a 100-record
page, complete correct samples and zero unexpected read errors. Material same-host
latency or memory regressions require review before closeout; there is no portable
millisecond SLA. The full 20.04 GB payload workload is not repeated for a read-only
metadata change unless evidence exposes a payload/recovery/resource impact.

## Regression and integration results

[Sanitized check evidence](evidence/batch-find-checks-2026-11-14.json) records test
counts, service versions, integration phases and source/artifact checks.

- Windows / Python 3.13.15: `uv run --frozen --extra test pytest -q`:
  **871 passed, 314 skipped**; `uv run --frozen --extra test ruff check .`: passed.
- Linux WSL / managed Python 3.13.15: `uv run --frozen --all-extras pytest -q`:
  **874 passed, 311 skipped**. Optional environment/platform cases account for the
  difference; live database skips are covered by the required run below. The benchmark-reporting and baseline-reuse checks account for **47 cases** in
  the final suites.
- Linux required PostgreSQL 17.11 / pinned RustFS suite:
  **276 passed, zero skipped**, using `tools/postgres_lab.py --with-rustfs`.
  This includes all 59 new find cases, also run through both SQLite adapters
  (118 cases) in the local suites.
- Linux all-adapter workload smoke: passed, **100 recorded operations**
  (34 direct SQLite, 34 MeldDB SQLite, 32 PostgreSQL). This exercises real payload
  publication/retrieval, mixed reader/writer conflicts, recovery, migration and
  cutover. Its result explicitly remains non-representative.
- `uv build` and fresh wheel/sdist installs: passed both SQLite adapters and every
  optional handler; PostgreSQL dependency/pin installation checks passed.

The benchmark ran on the same development host as some independent regression
checks. Timings are observations from that host, not isolated hardware capacity
or latency guarantees; deterministic query counts and full-record checks are the
primary optimization gates.

## Scenario coverage

Shared checks are in `tools/qualification/find_checks.py`, collected by
`tests/test_find_batch.py` and `tests/postgres_provider/test_find_batch_postgres.py`.

| Spec scenario | Executable evidence |
| --- | --- |
| A page fits within one batch | Page bounds, independent scalar parity, per-chunk SQL-growth checks |
| A page spans multiple batches | 257/1000-record bounds and descending multi-chunk cursor tests |
| Selection is empty or invalid | Empty-selection guard, invalid-query matrix and schema-first validation |
| Another connection commits during loading | Two-connection writes after selection and first batch, changing record 257 |
| Returned values are modified | Equal nested descriptors across 258 records and later reads remain detached |
| Loading is interrupted | Injected second-batch failure and commit/cleanup errors; no page escapes; quarantine retained |
| Legacy and encoded records are read | Absent/empty/mixed/all encoding layouts, unavailable codecs, partially migrated schema versions |
| Catalog structure changes between calls | Encoding format/trigger, registered definition and publication-guard damage after successful reads |
| Selected data is inconsistent | Missing selected blob/object/metadata and prepared/metadata-version inconsistency compared with scalar reads |
| Empty or invalid selection with damaged encodings | Damaged-extension empty pages and invalid queries retain original outcomes |

## Performance results

Accepted after review of [all comparison evidence](evidence/batch-find-2026-11-14.json).
All 18 adapter/cardinality/client combinations completed: **8,400 measured reads**
plus 420 warm-ups, with complete record parity, zero unexpected errors/conflicts,
and zero payload calls. Each catalog contained 300,000 application relationships.

Single-client observations (SQL counts are per find; timings are milliseconds):

| Adapter | Records | Baseline SQL | Batched SQL | Baseline p50 | Batched p50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct SQLite | 100 | 704 | 10 | 11.46 | 0.90 |
| Direct SQLite | 1000 | 7,004 | 19 | 115.30 | 6.65 |
| MeldDB SQLite | 100 | 704 | 10 | 13.33 | 0.87 |
| MeldDB SQLite | 1000 | 7,004 | 19 | 136.71 | 6.43 |
| PostgreSQL | 100 | 3,023 | 52 | 1,085.70 | 17.67 |
| PostgreSQL | 1000 | 30,023 | 61 | 8,477.00 | 29.95 |

The same SQL counts held with two and four clients. PostgreSQL reductions were
**98.3% at 100 records** and **99.8% at 1,000 records**. Every measured client p50
and p95 improved. Review found no material memory regression: the largest worker
peak increase was under 2.5 MiB, and the largest sampled aggregate increase was
under 6 MiB. All observed memory remained well below the 4 GB harness ceiling.
These are same-host observations, not a portable latency or capacity promise.

The baseline matrix was captured first; every final candidate case was measured
afresh with verified baseline reuse after the compatibility closeout. Exact
baseline Git contents, locked runtime, service helpers/server version and fixture
identity were checked. The one fixture edit was import-list line wrapping: its
original recorded byte hash was reconstructed and AST equivalence verified.
Windows archive CRLF normalization is recorded explicitly. All final candidate
imports and Python source fingerprints were attested and match the working tree;
the production files also match the built wheel and integrated smoke evidence.
Historical baseline workers without import attestations remain labeled as such,
with explicit source-audit evidence rather than invented attestations.

All owned disposable services were removed. No private inputs, generated payloads,
credentials, provider changes or package publication are part of this change.

## Verification on the merged base

After fast-forwarding to `30f11bc`, the Windows all-extras suite passed 879
tests with 314 skips, and Ruff passed. The required Linux PostgreSQL 17.11 and
RustFS provider suite passed all 276 tests. The corrected workload smoke passed
100 operations across all three adapters.

A diagnostic comparison with the updated lab checked 100- and 1,000-record
pages on each adapter and reproduced every SQL count above. The stored full
benchmark still validates against the exact production source fingerprints.
The diagnostic comparison checks the merged workload helpers at small scale.
