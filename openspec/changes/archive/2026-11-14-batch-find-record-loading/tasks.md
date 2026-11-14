# Tasks

## 1. Read compatibility and bounded loading

- [x] 1.1 Add independent result-parity and SQL/storage instrumentation fixtures for direct SQLite, MeldDB SQLite and PostgreSQL. Cover legacy file-only catalogs, mixed file/encoded records, schema-version evolution, descriptor presence, existing filters/order/cursors and page sizes 0/1/100/255/256/257/1000. Verify expected records against stable scalar reads and capture baseline find-only SQL counts against the preserved pre-change source.
- [x] 1.2 Factor call-scoped encoding inspection and implement bounded selected-record loading through existing transaction/dialect APIs. Preserve scalar encoding readers and _record callers. Verify absent/partial/installed extension cases, selected association failures and public stat/publication parity with focused uv tests.
- [x] 1.3 Wire Store.find to the batched path with a 256-ID internal chunk and ordered reassembly. Verify constant retrieval query counts within a chunk, at most three additional retrieval statements per extra chunk, page limits/validation and zero payload I/O. Update docs/metadata.md to describe bounded page loading and the existing per-call transaction scope.

## 2. Snapshot and integrity regression coverage

- [x] 2.1 Add coordinated two-connection tests that commit after page selection on PostgreSQL and SQLite WAL. Verify the original snapshot during loading, freshness on the next call, transaction cleanup and no partial page on a failure after the first batch or during transaction finalization, using a later-chunk concurrent edit. Verify existing outcome/quarantine tests still pass through all adapters.
- [x] 2.2 Verify catalog drift and unsupported encoding format detection between consecutive calls, malformed selected associations, migrated prepared-schema compatibility and independent nested values across records/calls. Cover damaged-encoding empty/invalid selection ordering and the documented individual error matrix; simultaneous independent corruption precedence is unspecified. Run focused metadata/handler tests and document legacy-extension and integrity behavior alongside the read contract.

## 3. Measurement and integrated acceptance

- [x] 3.1 Add a focused reproducible metadata comparison using existing qualification helpers, baseline 51bc084 and the candidate revision with identical pinned dependencies. Exercise 100/1000 records, 300,000 relationships and 1/2/4 clients with five warm-ups and 100 measured calls per client. Deliver sanitized SQL counts, p50/p95, memory and error/conflict accounting, verify at least 90% fewer PostgreSQL find SQL calls at 100 records, and explicitly label the fixture metadata-focused. Verify every measured page/cardinality, require 100 successful measured reads per client and zero unexpected errors, and review material same-host latency/memory regressions.
- [x] 3.2 Run uv full pytest and Ruff, the required PostgreSQL/RustFS provider suite and the existing all-adapter workload smoke. Verify required live phases execute, record Windows/Linux coverage and any genuine environment limits, and run build/fresh-install checks if dependency, packaging or installed-module changes require them. Record qualification PR #2's current head; reconcile any changes before landing and repeat affected checks. An unchanged open PR does not block implementation.
- [x] 3.3 Review the measured results and provider seam through meld-platform, record the exact-pin/shared-contract decision and update the roadmap/evidence record. Validate this OpenSpec change strictly and audit the intended diff for private inputs, generated payloads and credentials. Verify every spec scenario and task has its documented result before closeout.

## Workflow follow-up

- Review the complete implementation, sync the delta into MeldStore canonical specs and archive it in MeldStore.
- Coordinate any separately required provider guarantee changes in their owning MeldDB/shared-store roots.
- Commit, push and merge through the separately authorized Git workflow.
