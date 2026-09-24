# Tasks

## 1. Resolve the upstream prerequisite

- [x] 1.1 Confirm a separately authorized upstream publication makes `ae738562249efd3b4f3173f45358db1e7fd54141` publicly fetchable; verify an isolated exact-SHA Git fetch without local object reuse. Stop if unavailable; do not push MeldDB as part of this change.
- [x] 1.2 Review the fetched revision's public runtime, maintenance, transaction and error contracts against the design; record full SHA and evidence. Require explicit review for any replacement revision rather than substituting latest main.

## 2. Establish focused conformance tests

- [x] 2.1 Extend catalog/SQLite tuning tests for new and existing WAL/DELETE policy, in-memory opening, live settings, unsafe runtime and lock failure with released leases; verify the existing tests still identify current behavior and record failures for newly required delegation.
- [x] 2.2 Add parameterized engine-read tests for INSERT, DDL, valid WITH-prefixed writes, same-connection trigger/UDF mutation, accepted read-only CTEs, caught-failure poisoning and subsequent writable reuse; verify durable rows and schema rather than exception text alone.
- [x] 2.3 Add begin/activation, commit, rollback, restoration and cleanup fault cases through both adapters; verify expected phase/outcome/cause, identity of initiating exceptions, quarantine, and failures before further catalog or payload I/O. Record target-behavior failures before implementation.
- [x] 2.4 Add exact maintenance value/type compatibility tests including inactive WAL and busy results, plus narrowly scoped delegation tests that allow leases and upstream driver connections; verify the current extra-control-connection path is detected.

## 3. Update the tested dependency

- [x] 3.1 Replace only the approved MeldDB Git SHA in pyproject.toml, run `uv lock` and `uv sync --frozen --all-extras`, and verify the lock diff contains no unrelated dependency upgrades or local paths.
- [x] 3.2 Verify the installed source resolves to the exact approved public SHA and both packages retain Apache-2.0 metadata/license files using the existing package smoke checks; do not substitute a same-named index package.

## 4. Adopt runtime and maintenance contracts

- [x] 4.1 Split catalog setup at the existing adapter seam, delegate file journal selection to MeldDB and validate sqlite_runtime(), omitting the file-only option for memory catalogs; verify runtime, settings and no-extra-control-connection tests pass while direct SQLite remains independent.
- [x] 4.2 Delegate MeldDB maintenance with optimize/analyze and checkpoint arguments and normalize action, busy integer and inactive frame sentinels to the current result; verify exact result tests and exclusive/reader precondition tests pass on both adapters.

## 5. Implement enforced reads and outcome handling

- [x] 5.1 Add/export the outcome error hierarchy and evidence fields, with message-only CommitError compatibility and adapter-local upstream mapping; verify class ancestry, preserved causes/evidence and import-blocked direct-adapter tests.
- [x] 5.2 Make the MeldDB transaction wrapper enter/exit its upstream scope once, delegate read enforcement and translate upstream outcome errors without speculative second recovery; verify read/poisoning tests, exception identity and fault-mapping tests.
- [x] 5.3 Implement direct SQLite activation, deferred begin, finalization and verified restoration of previous query-only state, with quarantine on uncertain recovery; verify every fault stage and successful later writable reuse.
- [x] 5.4 Remove only read/write keyword classification, retain ownership-control and EXPLAIN-disguise rejection, and preserve bindings, thread/handle/nesting guards; verify accepted CTE reads, rejected engine writes, existing control-SQL tests and WAL concurrent snapshots.
- [x] 5.5 Ensure all catalog and blob entry points reject unusable handles before I/O; make close attempt every release and retain primary outcome plus inspectable cleanup_errors; verify multiple-failure cleanup, no automatic reconnect/retry and synthetic reopen-by-ID recovery tests.

## 6. Verify compatibility and document evidence

- [x] 6.1 Run cross-adapter reopen/application-FK tests with pre-adoption fixtures and compare schema, IDs, metadata versions and payload digests; verify no catalog or backup-format migration is introduced.
- [x] 6.2 Update SQLite/runtime, transaction/error and recovery documentation plus release notes with delegation, direct-adapter behavior and outcome recovery; verify examples match the public interface and preserve SQLite/single-host, RustFS-not-AWS support limits.
- [x] 6.3 Run `uv run --frozen --all-extras pytest` and `uv run --frozen --extra test ruff check .`; record runtime versions, counts and expected skips, including catalog, migration, lifecycle, backup, file export and all optional-handler regressions.
- [x] 6.4 Use `uv build --out-dir <fresh-candidate-directory>` and run tools/package_smoke.py with that --dist-dir, both core and --formats via uv; verify fresh wheel/sdist installs, licenses, exact public dependency and private-artifact exclusion. Do not overwrite the published rc1 artifacts or upload packages.
- [x] 6.5 Run `uv run --frozen --all-extras python tools/s3_lab.py` against disposable Docker-local RustFS when available; record pass results or the specific environment gap without claiming AWS/PostgreSQL qualification. Use only synthetic inputs.
- [x] 6.6 Record the full adopted SHA, uv lock diff, local/CI evidence and remaining gaps in the validation document; verify no private data, credentials, unrelated user changes or shared-store edits enter the change.
- [x] 6.7 Run strict OpenSpec validation and review every scenario against evidence; after implementation verification and the appropriate sync/archive workflow, sync only the local relational-metadata delta and archive the local change. Verify shared references remain untouched and do not mark unresolved gates complete.
