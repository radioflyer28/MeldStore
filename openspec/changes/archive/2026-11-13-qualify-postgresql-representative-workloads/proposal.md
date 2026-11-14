# Proposal

## Why

MeldStore v0.1.0rc3 qualifies PostgreSQL 17 correctness and recovery with small synthetic fixtures, but the existing 100-file S08 harness is SQLite-only and coupled to a private cUAS example. We need reproducible, generic workload evidence before claiming practical capacity for 100-300 MB Parquet objects or choosing further database optimizations.

## What Changes

- Add an opt-in synthetic Parquet qualification runner with distinct smoke and representative profiles; representative means 100 files of 100-300 MB each, measured after encoding.
- Compare direct SQLite, MeldDB SQLite and MeldDB PostgreSQL using the same corpus, application-owned SQL relationships and pinned RustFS service on one host.
- Exercise bounded bulk writes, selective interval joins, concurrent clients, metadata migration, offline SQLite-to-PostgreSQL cutover, backup/restore and interrupted-operation recovery with independent correctness oracles.
- Produce sanitized machine-readable measurements and a human evidence record covering throughput, query latency, memory, staging disk, query plans, exact dependencies and service topology.
- Require resource preflight and explicit full-run selection; add the small profile to CI without interpreting it as full-scale qualification.
- Publish measured limits and ranked follow-ups. Keep production tuning and new provider features outside this change unless separately proposed and coordinated.

## Capabilities

### New Capabilities

- `representative-workload-qualification`: Reproducible generic workload profiles, safe execution, cross-adapter correctness and resource/performance evidence with explicit qualification boundaries.

### Modified Capabilities

None. Existing metadata, blob lifecycle, backup/transfer and shared provider contracts remain unchanged; this adds qualification behavior, not new storage semantics.

## Impact

New development tooling and synthetic fixtures in MeldStore, focused harness tests, a small live-service CI run, and qualification documentation. Reuse or extract service lifecycle and measurement helpers from `tools/s3_lab.py`, `tools/postgres_lab.py` and `tools/qualify_local.py`; preserve their existing commands. Keep the private S08 runner separate. No core domain entities, mandatory runtime dependencies, public API changes, automatic tuning, new release or private data publication.

The meld-platform workset supplies context from MeldDB and the local-only meld-platform-specs store. The existing exact MeldDB pin and provider contract are the starting point. Provider defects or changed guarantees require an owning MeldDB change, shared-contract review where applicable, and explicit pin requalification before adoption. Stronger MeldDB PostgreSQL structural inspection is a separate roadmap follow-up, not a prerequisite for benchmarking existing application-owned SQL.
