# Tasks

## 1. Reproducible corpus and safe execution

- [x] 1.1 Add the generic tools-local runner, explicit smoke/representative/custom profiles, bounded Parquet generator and versioned manifest. Verify deterministic generation under pinned versions, valid Parquet row groups, actual size/count rejection, and smoke-not-representative reporting with focused tests; document uv invocation and profile sizes.
- [x] 1.2 Reuse/extract disposable PostgreSQL/RustFS lifecycle helpers while preserving existing lab commands. Add host/Docker capacity preflight, configurable resource limits, fresh-target/containment checks and owned-resource cleanup. Verify insufficient-space, occupied-target, overlapping-path, interrupted-startup and unrelated-resource preservation tests; document preflight and cleanup commands.

## 2. Comparable catalog workloads

- [x] 2.1 Implement fixture-owned schema, relationships and independent manifest-derived oracles across direct SQLite, MeldDB SQLite and MeldDB PostgreSQL. Verify identical IDs, metadata, UTC interval boundary results, joins, pagination, versions and payload hashes in the live smoke matrix; document fixture semantics and keep all domain types out of core.
- [x] 2.2 Add bounded repeated-SQL/bulk-SQL measurements, interval selectivity cases and disposable fixture index comparisons. Verify full rollback on invalid application foreign keys, zero storage calls for metadata-only phases and payload I/O outside write transactions; document API-call versus wire-round-trip limits and retain before/after query plans.
- [x] 2.3 Add independent-process concurrent clients with deterministic disjoint and conflicting edits. Verify client counts, success/conflict/error accounting, no lost updates and correct handle/coordinator ownership at smoke concurrency; document timeouts and absence of automatic retries.

## 3. Operational qualification

- [x] 3.1 Add metadata migration dry-run, interrupted backfill and resume phases for all three adapters. Verify unchanged dry-run state and durable progress, IDs, foreign keys and expected final versions after resume in focused/live smoke tests; document phase boundaries.
- [x] 3.2 Add whole-store backup/restore and offline cutover from both SQLite adapters to fresh PostgreSQL/S3 destinations using public workflows. Verify all source/destination payloads and application SQL, source preservation, no-overwrite and corrupt-payload refusal in smoke tests; document sequential copy lifetimes and native archive prerequisites.
- [x] 3.3 Add focused publication-outcome and backup/restore interruption probes in the populated workload. Verify close/reopen/inspect recovery, no duplicate or overwritten object, and no premature destination readiness; test unexpected runner termination separately from expected injected faults and document recovery evidence.

## 4. Evidence and continuous qualification

- [x] 4.1 Implement atomic versioned reports, exact installed-provider/dependency/image provenance, timing distributions, query plans, per-worker/aggregate client memory and staging/service disk measurements. Verify report schema, sample counts, units, unavailable-metric reasons, incomplete status and secret/path redaction with tests; document measurement boundaries and overhead.
- [x] 4.2 Integrate required all-adapter smoke qualification into the live PostgreSQL/RustFS CI path while preserving existing conformance checks. Verify missing dependencies/services or skipped required phases fail, existing Windows/Linux harness checks pass, and public CI generates only synthetic data; document that smoke cannot qualify representative scale.

## 5. Representative evidence and closeout

- [x] 5.1 Run the documented preflight and full 100-file profile on an explicitly recorded Windows or Linux host with 100-300 MB encoded files and 300,000 relationship rows. Verify all three adapters, required concurrency levels and operational phases complete with correctness, client-memory and staging-disk evidence; retain sanitized reports and commands. Insufficient resources or incomplete phases leave this task open rather than substituting smoke results.
- [x] 5.2 Review measured limits, query plans and provider findings through the meld-platform workset; deliver ranked follow-ups and an explicit contract/pin decision. If a provider fix or changed guarantee is required, coordinate its MeldDB/shared-store changes and exact-pin requalification before counting affected evidence as passing. Otherwise record why the shared contract and pin remain unchanged. Update the roadmap and qualification record with only demonstrated topology claims.
- [x] 5.3 Run final integration checks using uv: full pytest and Ruff, existing live PostgreSQL/RustFS qualification with required phases, and strict OpenSpec validation. Run build/fresh-artifact smoke checks if dependency, packaging or installed-tool discovery changes. Verify tracked diffs contain no private inputs, generated payloads, credentials or absolute personal paths; record commands, counts and remaining limitations without claiming unrun full profiles passed.

## Workflow follow-up

- Review completed evidence, sync the delta into MeldStore canonical specs and archive this change after all tracked tasks are complete.
- Keep meld-platform-specs local-only; archive any separately required provider/shared changes in their owning roots.
- Commit/merge and package publication follow their separately authorized workflows; this proposal does not authorize a new release.




## Completion evidence

Completed 2026-11-13. See [the verification record](../../../../docs/representative-workloads-verification.md)
for the full 100-file/300,000-relationship comparison, exact-source regression
results, measured follow-ups, unchanged provider/shared-contract decision and
qualification limits. All run-owned Docker containers/volumes were removed;
reviewed public evidence contains no payload data or private runtime paths.
Implementation is complete. Canonical specs were synced and this change archived
on 2026-11-13. Git publication remains separate.
