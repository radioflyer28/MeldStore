# MeldStore contributor guidance

- Read docs/mvp.md and docs/implementation-plan.md before implementing a slice.
- Keep the core generic: no aircraft, sensor, recording, track, or truth entities.
- Applications own domain SQL tables and constraints. All references use SQL;
  do not depend on MeldDB document/graph APIs or managed physical table names.
- Keep the documented SQL schema usable through both MeldDB and sqlite3.
- Use obstore for local/S3 operations, XXH3-128 for blob integrity, explicit I/O,
  immutable payloads, and application-visible transaction boundaries.
- No caching policies or SHA-256 entry fingerprints in this MVP.
- Preserve stable public IDs and SQL foreign-key targets across migrations.
- No storage I/O inside a long-lived SQL write transaction.
- Acceptance changed September 16, 2026: real Docker-local RustFS or Garage
  S3-compatible qualification is authorized for S07. Tests require an explicitly
  designated disposable bucket/prefix; local services use loopback endpoints and
  ephemeral credentials. Do not infer AWS qualification from compatible-service
  results. AWS-specific evidence is deferred and remains unqualified.
- S07 qualification is recorded in docs/s07-verification.md. S08 progress and
  release gates are recorded in docs/s08-verification.md;
  preserve the distinction between tested RustFS behavior and unqualified AWS.
- S3 participants must use one local catalog and the same persistent, path-bound
  coordination directory on one host. No distributed leases or marker adoption.
- Keep S3 endpoint/bucket/prefix explicit, use canonical obstore config keys,
  and keep credentials out of catalog, identity markers, and transfer results.
- Preserve create-only multipart-copy publication, zero-byte conditional PUT,
  and explicit recovery. Incomplete MPUs require external list/abort cleanup.
- Offline transfer requires fresh destinations, verifies payloads before catalog
  publication, preserves IDs/source, and never performs automatic cutover.
- Run uv run --extra test pytest and uv run --extra test ruff check . for changes;
  test package build/install when packaging changes. Report qualification gaps.
- Never claim scaffold checks establish blob integrity or recovery guarantees.
- Keep consumer-specific schemas in examples/tests, never in the generic core.
- Never commit private Parquet inputs, derived payloads, source paths, real IDs,
  or local handoff history. Public CI uses generated synthetic data only.
- MeldStore is Apache-2.0. Dependency distribution remains a separate release
  gate; do not infer permission to relicense MeldDB or publish packages.
