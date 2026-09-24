# Design

## Context

See proposal.md for motivation and the relational-metadata delta for normative behavior. The current catalog module owns a transaction wrapper over both adapters, uses extra direct connections for journal selection and maintenance, classifies reads by first keyword, and has a catalog-level broken flag. Its SQL exception translator recognizes upstream errors largely by class name. Close stops at the first failure, potentially leaving leases unreleased.

Existing tests in test_catalog.py and test_sqlite_tuning.py cover ownership, caught-error poisoning, WAL snapshots, maintenance, import independence, cross-adapter reopen and deferred-constraint commit failure. They provide concrete extension points rather than requiring a new generic adapter framework. Existing backup/export/store tests exercise the same catalog. No blob module redesign is needed.

Locally inspected upstream contracts include `open(..., journal_mode=...)`, `sqlite_runtime()`, `maintain_sqlite(statistics=..., checkpoint=...)`, engine-enforced read scopes and public outcome error classes. The proposed exact SHA is not publicly resolvable at planning time. Reconfirm these contracts against the clean-fetched approved revision before adoption, not against an arbitrary working tree.

## Goals / Non-Goals

**Goals:** Remove duplicate database policy only on the MeldDB path; independently implement equivalent direct SQLite outcomes; preserve all payload and persisted SQL contracts. Public outcome fields must be inspectable without reaching into a connection.

**Non-Goals:** No shared driver framework, external connection borrowing, SQL portability translator, automatic retries, general untrusted-SQL sandbox, read-only Catalog constructor, or schema/backup-format migration. The shared provider spec is reference-only and unchanged.

## Decisions

### 1. Delegate runtime policy at the existing adapter seam

For file-backed MeldDB catalogs, pass `journal_mode` and `timeout` to public `melddb.open`. For in-memory catalogs omit the file-only journal option, then expect `memory` in the report. Validate report `journal_mode`, `synchronous == 2` and `foreign_keys is True` without modifying it. Use upstream runtime policy rather than copying its version allowlist into the MeldDB branch.

Keep direct SQLite setup and its runtime check independent. Keep cooperative leases around catalog ownership and release them on failed opens. Do not mistake intentional lease connections for redundant control connections: delegation tests must observe the catalog setup/maintenance seam, not globally forbid sqlite3.connect (which MeldDB itself uses). Opening can change journal policy, not install schema. Failed opens must not leave held locks or usable half-open handles.

Alternative rejected: pre-creating a catalog and checking PRAGMA via raw SQL preserves duplicate policy and bypasses the new interface. A global ban on sqlite3 would break the independent adapter and leases.

### 2. Normalize maintenance instead of changing callers

Preserve outer exclusive, idle, file-backed and no-reader checks. Delegate MeldDB maintenance with `statistics='analyze' if analyze else 'optimize'` and the existing checkpoint argument. Return only the current MeldStore fields. Map upstream `statistics.action` to the statistics string, boolean busy to integer 0/1, and inactive-WAL null frame counts to the existing direct-SQLite sentinel -1. Test values and types, including DELETE mode, not just dictionary keys. Preserve busy results without claiming completion.

Direct SQLite retains its control connection and live planner-statistics reload. Do not expose upstream `complete`, `wal_active`, or `mode` yet. Alternative rejected: forwarding the richer report would silently change a public result contract.

### 3. One explicit read lifecycle per adapter

The catalog continues to own its public transaction handle. On MeldDB, enter and exit the upstream transaction exactly once and delegate read enforcement to `transaction(write=False)`. Standalone catalog SQL can remain composed through that same wrapper, giving equivalent upstream read semantics without introducing a second owned transaction. No double exit, repair attempt or automatic reconnect around quarantined upstream handles.

For direct SQLite, track the previous query-only value, enable and verify it before deferred BEGIN, then finalize and restore/verify it on every path. Track whether begin occurred and whether commit/rollback was confirmed. If failed activation/begin can be fully recovered, report the initiating error normally; uncertain recovery raises an outcome error and quarantines. A restoration failure after confirmed commit reports cleanup/committed; after confirmed rollback it reports cleanup/rolled_back. Never report a failed commit as safe to retry merely because a best-effort rollback was attempted.

Remove read/write keyword allowlisting, but retain the ownership rejection of control statements and EXPLAIN control disguises. Read-only CTEs can now succeed; WITH-prefixed writes must reach engine enforcement and fail. Keep binding validation and poison caught SQL failures. Known managed writes should reject a read scope early where practical. Query-only constrains database mutation on the owned connection, not arbitrary Python UDF side effects or unrelated connections; no broader sandbox guarantee follows.

Alternative rejected: expanding the keyword classifier perpetuates incomplete syntax classification; silently inferring write mode would alter explicit transaction semantics.

### 4. Preserve structured errors and quarantine once

Export `TransactionOutcomeError(TransactionError)`, `CommitError(TransactionOutcomeError)` and `RollbackError(TransactionOutcomeError)`. Use optional keyword evidence fields so existing message-only CommitError construction/catching remains valid. Map upstream classes using adapter-local imports/type checks, never an unconditional MeldDB import or only exception-name matching for outcome errors.

Preserve upstream phase, outcome, initiating_error, backend_error and upstream cause. These fields retain exception evidence, not JSON-serialized errors or live database handles; serialization is not promised. Commit failures map to CommitError, rollback failures to RollbackError, and uncertain begin/cleanup failures to TransactionOutcomeError unless the upstream subtype carries a more specific applicable meaning. The operation-level translator must not collapse these into ValidationError. Confirmed rollback and cleanup re-raise the identical initiating application exception.

Retain one catalog unusable guard across adapters. All entry points, including schema registration, payload operations, snapshot and maintenance, check it before I/O. Expire transaction handles on every exit. Preserve thread confinement and nested rejection. Direct SQLite constructs the same public outcome types from controlled driver failures. Upstream handles own their own recovery; MeldStore only mirrors quarantine and translates evidence.

Close attempts the database handle and every lease even after individual failures. Preserve an already propagating outcome as primary. Record secondary cleanup exceptions in an inspectable `cleanup_errors` tuple on the primary error (and chain/annotate without replacing it); when close alone fails, raise TransactionError with the same tuple and first failure as cause. Never claim a failed resource release succeeded. Once closed or unusable, no ordinary operation is permitted. Tests define this ancillary evidence convention without adding a new public Catalog mode.

Alternative rejected: flattening errors to strings loses recovery information; generic context-manager cleanup can accidentally mask an uncertain commit with a later close exception.

### 5. Compatibility proof at public interfaces

Extend existing adapter-parameterized tests before implementation. Assert actual durable rows/schema and connection reuse, not only exception text. Fault injection covers activation, begin recovery, commit, rollback, query-only restoration and close/lease failures. Use upstream public outcome-shaped errors for MeldDB mapping and controlled driver faults for direct SQLite; keep real-engine read-only and concurrency tests separate from fakes.

Use valid SQLite WITH-prefixed writes and trigger/UDF-mediated writes on the same connection; do not call unsupported PostgreSQL data-modifying-CTE syntax an enforcement test. Retain EXPLAIN-control rejection. Assert cross-adapter reopen, application FK restrictions, independent imports and no payload I/O after quarantine. Synthetic interrupted-publication tests reopen and inspect caller IDs rather than asserting a guessed commit result.

## Risks / Trade-offs

- Result drift → normalize boolean/null checkpoint values to the existing integer/sentinel contract and test exact shape.
- Query-only leakage or hidden recovery → model activation through restoration as one lifecycle and quarantine whenever evidence is insufficient.
- Cleanup masks initiating failure → preserve primary outcome and collect secondary cleanup evidence while attempting all releases.
- Overbroad delegation tests → observe only MeldStore-owned control connections; allow leases, upstream drivers and independent verification.
- Unreachable dependency → stop implementation until publication and clean fetch succeed; no local override in final artifacts.
- Qualified upstream behavior mistaken for consumer support → run MeldStore conformance and keep SQLite/single-host claims unchanged.

## Migration Plan

1. Obtain a separate upstream publication action and clean-fetch the approved SHA. Review any replacement SHA explicitly; do not silently select latest main.
2. Add targeted failing conformance tests, update only the Git pin using uv, and implement the adapter/runtime/error changes against those tests.
3. Verify existing catalogs reopen both ways with unchanged schema, IDs and payload digests; no data migration is run. Document the newly permitted read syntax and outcome subclasses.
4. Run full uv tests, Ruff, build and fresh core/all-format wheel/sdist smoke checks in a fresh version-specific output directory. Run synthetic Docker-local RustFS when available and record any environment gap. Do not overwrite published release artifacts or rerun private workload data for this metadata-only change.
5. Record exact revision, lock diff, runtime versions, counts and skips. Only after verification and an explicit apply workflow should tasks be completed and the local delta synced/archived. No shared-store writes or package publication.

Rollback of code/dependency requires closing all handles and inspecting uncertain durable state first. With unchanged schema and formats, the prior implementation can reopen the catalog, but reverting code restores its older guarantees; it does not resolve an uncertain transaction.
