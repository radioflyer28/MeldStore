# PostgreSQL provider qualification

## Corrected provider qualification (task 1.2)

On November 11, 2026 the unchanged 61-test fixture passed in full, with zero skips,
against published MeldDB commit `bda3ca95fb6f5431e521162087bec97009074840`
on branch `codex/postgresql-provider-contracts`. This closes the provider gate;
it does not qualify a PostgreSQL blob-store deployment.

| Item | Corrected run |
| --- | --- |
| MeldDB exact Git pin | `bda3ca95fb6f5431e521162087bec97009074840` |
| Server / image | PostgreSQL 17.11, same digest as the baseline below |
| Client | Windows, Python 3.13.15, Psycopg/binary 3.3.8, pytest 9.1.1 |
| Run | `20261111T172146Z-db6077` |
| Result | 61 passed, zero failed/errors/skipped, exit 0 |
| Cleanup | Disposable container removed |

The fixture and assertions were not relaxed. Its provenance checks verified the
installed Git commit and rejected checkout shadowing. Raw/bulk decorated controls
now fail before execution, read scopes cannot be upgraded by SET, and disconnect
causes retain ConnectionError inside structured unknown outcomes and quarantine.
Existing constraint, concurrency and rollback cases continue to pass.

`pyproject.toml` and `uv.lock` now retain the corrected exact Git dependency.
It remains a direct standard package dependency, so GitHub wheel/sdist consumers
receive the same pin. A setup-only attempt caught uv moving it into
`tool.uv.sources`; that normalization was reverted before the passing run.
No PostgreSQL runtime extra is enabled by this step: Psycopg remains in the
`postgres-test` development group. MeldDB core remains dependency-free.

MeldDB's owning change is `harden-postgresql-provider-contracts`. Shared change
`define-postgresql-provider-safety-contract` is synced into the canonical
`metadata-provider-contract`: control ownership and disconnect evidence are
explicit, while MeldStore retains payload recovery and deployment qualification.
Both changes remain active for review; neither has been archived or merged.

Post-update MeldStore regressions: 622 passed, 94 skipped (including 61 opt-in
provider cases, which passed separately in the mandatory live run).

Ruff and strict owning-change validation passed. `uv build` produced a wheel and
sdist in a dedicated qualification directory; `tools/package_smoke.py --dist-dir`
passed clean installs and both SQLite adapters from each artifact. Inspection
confirmed the wheel retains the exact Git dependency with no runtime Psycopg
requirement, and the sdist contains both provider fixture files and the lab tool.
The initial default dist-directory smoke attempt correctly rejected mixed old
artifacts; no older artifacts were removed. Shared canonical validation passed
with its pre-existing long-requirement warning; the new shared delta passed strict
validation. All disposable conformance containers were removed.

The failing baseline below is retained as historical evidence. The remaining
catalog, cutover, recovery and integrated deployment tasks are still open.


## Historical baseline (task 1.1)

November 11, 2026: task 1.1 of `add-postgresql-metadata-support` is complete.
The baseline pinned provider **does not pass** the required PostgreSQL boundary: 34 tests
passed, 27 failed, zero errors and zero skips. At baseline, task 1.2 remained open. Do not
enable a MeldStore PostgreSQL adapter or advance its support claim on this evidence.

The failures are ordinary assertions, not expected failures or suppressed errors.
The test runner exits nonzero until the provider meets the contract.

## Reproduce

From the MeldStore checkout, with Docker running:

```powershell
uv sync --frozen --extra test --group postgres-test
uv run --frozen --extra test --group postgres-test python tools/postgres_lab.py
```

The test-only `postgres-test` dependency group supplies Psycopg. It adds no
PostgreSQL runtime dependency or backend selection to MeldStore. The provider
fixture checks the installed package's Git provenance against the exact
`pyproject.toml` pin and checks that Python has not imported a sibling checkout.

The lab uses a uniquely named container, random ephemeral credentials, a
loopback-only dynamically assigned port, and a disposable tmpfs database. It
ignores ambient database DSNs, uses synthetic SQL rows only, and removes its
own container on exit. Each test owns a separate generated SQL schema.
No Parquet inputs, payload objects, RustFS resources, or user databases are used.

Local `environment.json` and JUnit `results.xml` are written beneath ignored
`.qualification/postgres-provider/`. The environment report records versions,
image identity, provider provenance, test exit code, and container cleanup without
credentials. The fixture uses a redacted representation for connection context.

Without an explicit disposable DSN the regular suite skips these tests. The lab
sets `MELDSTORE_TEST_POSTGRES` and `--postgres-provider-required`: missing setup,
dependencies, wrong provider pin, non-17 server, no collected tests, or skipped
tests must not produce a successful qualification run. Do not set that DSN to an
application database: the fixture creates and drops schemas and terminates its
own provider sessions to exercise connection failure.

## Baseline recorded environment

| Component | Qualified fixture input |
| --- | --- |
| MeldDB revision | `fa74f11efffc0ab182984e949c0d09fe1e9a1244` |
| PostgreSQL server | `17.11 (Debian 17.11-1.pgdg13+2)` |
| Docker image | `postgres:17@sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675` |
| Python / client host | 3.13.15 / Windows |
| Psycopg / binary distribution | 3.3.8 / 3.3.8 |
| pytest / uv | 9.1.1 / 0.13.6 |
| Final live run | `20261111T170556Z-846441` |
| Result | 34 passed, 27 failed, zero skipped; exit 1 |

The image is pinned to the tested digest; pulling the mutable `postgres:17` tag
does not change the lab's image. This is evidence for that environment only.

## Passing behavior

- Raw positional and named bindings round-trip quoted text, bytes, null, and
  microsecond UTC instants through the public provider boundary.
- Bulk positional and named bindings consume a generator once, report submitted
  and affected-row counts, and handle empty input without executing SQL.
- Constraint, parameter production, binding, and returned-row failures poison
  the bulk transaction and roll back both earlier application work and bulk rows.
- An unmodified read scope is engine-enforced read-only and retains its
  repeatable-read snapshot while another connection commits. Persistent DML,
  data-modifying CTEs, and DDL fail in that scope; bulk fails before iteration.
- Read-only connection intent and expired transaction ownership are enforced.
- Simple guarded control words and multiple-statement input are rejected.
- Unique, foreign-key, not-null, and check violations retain their SQLSTATE and
  map to the documented constraint errors.
- Real row lock timeout (`55P03`), serialization conflict (`40001`), and deadlock
  (`40P01`) map to `BusyError`. The deadlock fixture has one winner and one loser,
  with no implicit retry.
- Connection refusal maps to `ConnectionError`. Termination of live server
  sessions produces structured unknown outcomes at begin, commit, and rollback;
  the handle is quarantined. Reopening verifies rollback of the terminated
  session's pending rows.

## Baseline blocking failures

### Transaction and connection control escapes

Twenty-two assertions fail across raw and bulk APIs. `COMMIT;`, `END;`, and
nested-comment-prefixed `COMMIT` reach the engine. `SET`, `RESET`, `DISCARD`, and
`START TRANSACTION` also reach it. `PREPARE TRANSACTION` reaches the engine and
fails only because prepared transactions are disabled in the lab, rather than
being rejected at the provider boundary.

Three additional assertions demonstrate durable effects:

1. `SET TRANSACTION READ WRITE` upgrades a public read scope and commits a row.
2. Raw `COMMIT;` commits an inserted row before context exit. Raising a caller
   exception afterward does not remove it.
3. Bulk `COMMIT;` causes the same early-commit behavior.

These are persistence failures, not merely differences in exception naming.
The provider's current first-word/comment regular expression is insufficient
for PostgreSQL syntax. The punctuation handling is in shared code, so its fix
also needs SQLite regression coverage.

### Disconnect causes are misclassified

Two assertions fail: server termination (`57P01`) and the subsequent closed
connection error without SQLSTATE become `ValidationError`, rather than
`ConnectionError`. The enclosing structured `RollbackError`, unknown outcome,
and quarantine behavior still work. Preserve those properties when correcting
driver error translation; do not turn an unknown outcome into an assumed rollback.

## Original owning-repository follow-up

Before task 1.2 can close:

1. Open a MeldDB-local change for robust raw/bulk transaction and session-control
   rejection, non-upgradable read scopes, and disconnect cause classification.
   Include ordinary SQL literals/comments/identifiers and both backends in its
   regressions. Do not implement a duplicate provider or use private driver
   state in MeldStore to bypass the failed public contract.
2. Coordinate the PostgreSQL provider guarantees in the `meld-platform-specs`
   metadata-provider contract. It currently defines ownership and bulk atomicity
   but does not spell out PostgreSQL session-control and disconnect classification.
   Keep provider implementation in MeldDB and blob lifecycle in MeldStore.
3. Qualify the corrected published revision with this same fixture before
   changing MeldStore's Git pin and lockfile. Preserve this failing baseline.
4. Resume the fresh-catalog, SQLite cutover, native archive/restore, and integrated
   PostgreSQL/RustFS tasks. Passing this fixture alone will not complete them.

## Regression checks and limits

- `uv run --extra test pytest -q`: 622 passed, 94 skipped. The skips include all
  61 opt-in provider tests plus the pre-existing 33 optional/environment cases.
- `uv run --extra test ruff check .`: passed.
- MeldDB baseline: `uv run --frozen --extra test pytest -q`: 333 passed,
  3 skipped, with one pytest-cache filesystem permission warning.
- MeldDB Ruff passed after removing temporary staging files; filesystem access
  warnings were reported for existing restricted directories.
- Explicit mandatory-fixture run without a DSN: exit 1 with a setup error,
  confirming absent PostgreSQL cannot count as a qualification success.
- Strict OpenSpec validation passed. Both disposable lab containers were removed.

The live tests exercise PostgreSQL server-session termination, not a network
proxy dropping only the acknowledgement of a successfully durable commit.
That ambiguous-publication injection remains part of task 2.5. No blob adapter,
payload lifecycle, PostgreSQL backup, SQLite cutover, RustFS integration, AWS,
multi-host deployment, or complete MeldDB PostgreSQL capability matrix is
qualified by this provider-only run.
