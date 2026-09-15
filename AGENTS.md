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
- Real S3 tests require an explicitly designated disposable bucket/prefix.
- Run uv run --extra test pytest and uv run --extra test ruff check . for changes;
  test package build/install when packaging changes. Report qualification gaps.
- Never claim scaffold checks establish blob integrity or recovery guarantees.
