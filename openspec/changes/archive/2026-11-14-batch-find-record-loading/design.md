# Design

## Context

See proposal.md for the measured motivation. Store.find in store.py opens a read-only catalog transaction, validates the supplied schema, runs query.compile_query to select ordered IDs, then calls _record for every ID. Each _record reloads blob/prepared associations, metadata and the stored schema declaration. encodings.read inspects the encoding extension and repeats structural verification for each token. PostgreSQL structural verification uses many catalog queries.

query.py bounds pages to 1-1000 ready records of exactly one requested schema version. It implements nullable ordering, ID tie-breakers, typed predicates and both structured and legacy cursors. Metadata migrations can leave a ready blob referencing payloads prepared under an older schema version. encodings.read supports catalogs with no encoding extension and rejects partial extensions. File records can lack an encoding row even when the extension exists.

The pinned MeldDB backend begins read scopes with PostgreSQL REPEATABLE READ READ ONLY. SQLite uses its existing read transaction behavior, including stable WAL snapshots. The shared metadata-provider contract supplies the public SQL and transaction semantics needed here.

## Goals / Non-Goals

Goals are bounded query growth, identical public page results, call-local validation reuse, and reproducible improvement measurements across all three adapters.

Out of scope are changes to public page limits, persistence formats, cross-call caching, provider APIs, payload handling and write batching. Existing stat and publication callers of _record remain compatibility obligations when assembly logic is shared.

## Decisions

### Preserve selection and transaction ownership

Keep query.compile_query as the authority for filtering, order, limits and cursor validation. Select IDs first, then load their records in the same read-only transaction. Reassemble by the original ordered ID list because SQL IN queries do not guarantee order. Empty selections return immediately after existing schema/query validation, matching the current absence of per-record encoding inspection.

A single large joined query would couple query compilation to optional extension tables and legacy layouts. Bounded retrieval after existing selection provides a smaller compatibility surface. Opening new transactions per batch would break the current snapshot and is excluded by the spec.

### Bound parameter sets and share pure record assembly

Use an internal identifier chunk of 256, below supported SQLite parameter budgets and the public 1000-record limit. Bind values through the existing dialect and quote schema table identifiers through its existing helpers. For each chunk, retrieve selected blob/prepared associations, schema metadata rows and available encodings in set-based reads. Reuse the requested declaration after _schema verifies its exact stored definition in the same transaction. Project fields in canonical sorted order, preserving scalar read key order without another declaration query or decode. Proposed cost is fixed schema/extension inspection plus selection plus at most three retrieval statements per identifier chunk.

Preserve individual error classes; precedence among simultaneous independent corruptions is unspecified. Keep the existing association checks, metadata schema/version consistency, prepared schema compatibility and handler override behavior. A missing selected row must fail explicitly. Reuse a small pure assembly/validation function with _record if this avoids duplicating existing checks, while preserving its ready_only behavior for stat and publication. Do not broaden write-path SQL retrieval merely to share the batch loader.

### Validate extension state once for a nonempty call

Factor the existing encoding checks into a shared call-scoped inspection result. It distinguishes a wholly absent extension from a valid installed extension and preserves partial-layout, format-version and structural-drift failures. Reuse existing SQLite and PostgreSQL verifiers. Batch encoding rows only for a verified installed extension. Retain scalar encodings.read behavior for its other callers through the shared verifier.

Existing verification boundaries remain unchanged: both SQLite adapters check the installed declaration and publication guard; PostgreSQL additionally runs its existing full metadata schema verifier. Both verify installed encoding structures. This optimization does not add blanket SQLite index/trigger inspection.

Individual error behavior is retained:

| Condition | Result |
| --- | --- |
| Selected blob absent/not ready | NotFoundError |
| Object/prepared association absent or inconsistent | IntegrityError |
| Selected metadata absent or schema version inconsistent | IntegrityError |
| Partial encoding extension, unsupported format or covered structural drift | SchemaConflictError |
| Malformed stored descriptor JSON | JSONDecodeError |
| Installed extension with no encoding row for a file token | File record with descriptor omitted |

Validation state and decoded declarations live only for this call. Deep descriptor/metadata values must be independently constructed for every result, including distinct records with equal descriptor contents. This avoids stale validation across calls and mutable aliasing across results. There is no dependency on private MeldDB driver access.

### Verify semantics and query growth before tuning latency

Use independent expected records and existing scalar reads on stable fixtures as parity oracles. Cover zero, one, 100, 255, 256, 257 and 1000 results, all current predicate/order/cursor rules, Unicode identifiers, multiple schema versions, empty and populated encoding extensions, legacy file-only catalogs, mixed handlers and distinct tokens with equal descriptor values. Build corruption fixtures using existing supported test seams and assert documented exception behavior.

Instrument public SQL and storage boundaries. For nonempty pages within one chunk, record-loading query counts must remain constant. Crossing a chunk boundary may add at most three retrieval statements. Schema and encoding inspection counts must be independent of page size. Require at least a 90% reduction in total public SQL calls for the qualified 100-record PostgreSQL find case relative to commit 51bc084 under the same instrumentation. Count only find calls for the new comparison and report setup/edit work separately, since the historical 318,000 count included other calls.

A coordinated writer test uses more than 256 records and commits an edit to a later chunk between selection and loading, verifying snapshot results and freshness on the next call on PostgreSQL and SQLite WAL. Other SQLite journal modes retain their existing writer-blocking behavior. Inject failure after one batch and during transaction finalization; verify no partial result escapes and existing cleanup/quarantine behavior remains effective. Mutate returned nested descriptors to test independence. Guard all storage access during find and check catalog and payload state remain unchanged.

### Use a focused before/after qualification

Compare baseline 51bc084 and the implementation using the same exact MeldDB pin, dependency lock, synthetic data and host. Reuse tools/qualification measurement helpers for a focused metadata workload with 100 and 1000 records and the existing 300,000 relationship fixture. Small payloads are sufficient for this read-path comparison. Label these results as metadata-focused evidence.

Measure five warm-ups and at least 100 samples per adapter/client, using 1/2/4 independent clients and capturing p50/p95, SQL counts, memory, errors and conflicts. Record single-client and concurrent measurements with their scopes. Verify every measured page against its expected records and cardinality, require 100 successful measured reads per client and zero unexpected read errors, and review material same-host latency or memory regressions before acceptance. No fixed cross-machine millisecond SLA applies. Record conflicts as zero for this read-only benchmark, separately from existing mixed reader/writer smoke coverage. Run the existing all-adapter smoke and the required PostgreSQL/RustFS provider suite to exercise integrations. Repeat the 20.04 GB workload only if an implementation finding affects payload, recovery or resource behavior beyond the focused read change.

### Coordinate through the workset

MeldStore owns this delta under relational-metadata. The meld-platform workset supplies MeldDB and shared-contract context. Existing public parameterized SQL and read-only snapshots satisfy the design. If implementation exposes an actual provider gap, identify that specific guarantee and coordinate an owning MeldDB/shared-store proposal before adoption. Record the final contract and exact-pin decision in the verification notes.

## Risks / Trade-offs

- Optional encoding tables can change result shape. Explicit absent/partial/installed tests preserve legacy and mixed-record behavior.
- Joined or chunked reads can reorder or silently omit rows. Reassemble from selected IDs and verify every selected association.
- Reusing decoded values can create aliasing. Construct independent nested result values and test mutations.
- Hoisted verification could hide schema damage. Validate afresh on each nonempty call and test damage between calls.
- Timing noise can conceal progress. Gate deterministic SQL-count reductions and report repeated latency distributions with exact provenance.

## Migration Plan

Implement on the proposal branch based on qualification PR #2, record its current head and require reconciliation with any later changes before landing, and keep the measured baseline available. Validate parity and bounded SQL growth, then run focused comparisons and existing integrated acceptance. Update the metadata guide and a concise evidence record with observed results. Persisted data remains compatible with the previous version, so reverting the read-path change restores previous behavior without data conversion.
