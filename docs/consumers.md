# Application examples

These examples are for application authors composing blob storage with their
own SQL relationships. They are independent applications, not built-in schemas
or a required domain package. Use a fresh disposable catalog for each example.

## Start with the generic dataset catalog

From a source checkout with the core dependencies installed, run:

```console
uv run python -m examples.dataset_catalog
```

The example creates a temporary catalog and object directory, registers title
and category metadata, and imports a generated document. It prepares the bytes
first, then finalizes the blob, inserts a collection membership, and publishes
inside one shared SQL transaction. It selects the document with SQL, edits its
title using an expected version, and materializes verified bytes. Temporary
artifacts are removed when the example exits.

The optional `exercise_formats` helper adds a NumPy NPZ array and a PyArrow
Parquet table to the same collection. It requires the `numpy` and `arrow` extras.
Neither helper imports the cUAS example. Examples ship in the source
distribution, not the installed library wheel.

Application tables reference public `ms_blobs(id)` identities; schema-specific
links use the table name returned by the schema declaration. Applications own
their migrations and reference cleanup. Removing an application relationship and
retiring a blob should be composed explicitly in the same transaction; the
library does not infer cascade or retention policies.

## Separate synthetic SQL integration fixture

```console
uv run --extra arrow python -m examples.cuas_catalog
```

The fixture generates one radar Parquet containing two tracks and one aircraft
truth Parquet containing a single track. Application-owned tables associate
recordings with sensor and aircraft configurations, index track occurrences,
and link radar observations to truth using explicit correlation evidence.
SQL joins resolve the aircraft and sensor; recording IDs are deduplicated
before retrieval. Two correlation rows therefore cause two whole-file
materializations, not four, and neither Parquet is split by track.

Every domain table, trigger, and query belongs to this example. Publication
checks require a source, a valid containing interval, and exactly one track for
truth. Failed publication rolls back application and blob rows together while
leaving an explicit prepared operation for recovery. Tests also exercise stale
metadata updates, restrictive references, process interruption, and restoration.

This is a publication-focused integration fixture, not a production cUAS
database or complete ERD implementation. Applications must define their own
identity authority, correction/deletion policies, validation, migrations, and
correlation methodology. The fixture does not infer which aircraft a radar
observed, validate arbitrary Parquet semantics, or prescribe a sensor model.

## Private representative workloads

The opt-in qualification runner reads an explicitly supplied directory of
original RadarNet Parquet files. It projects timestamp, sensor UUID, and track
UUID columns in batches; it never re-encodes the original payload. Native
nanosecond samples remain unchanged. Catalog intervals are half-open UTC
microsecond bounds rounded outward so precision conversion cannot exclude a
sample. Missing track rows are counted separately.

Configuration identifiers are absent from these inputs, so the fixture uses
explicitly unknown configuration placeholders. It does not invent historical
configuration provenance or real aircraft truth associations.

```console
uv run --extra arrow python -m tools.qualify_workload --source-root PRIVATE_INPUT_DIRECTORY --files 100 --output .qualification/local.json
uv run --extra arrow --extra test python tools/s3_lab.py --workload-source-root PRIVATE_INPUT_DIRECTORY --workload-output .qualification/rustfs.json
```

The output must be new and outside the input directory. Inputs must match the
runner's original-filename convention and default 100–300 MB size range. The
RustFS command creates a disposable loopback-only container with ephemeral
credentials and removes its own container and volumes afterward. It does not
use an AWS destination. Both commands require substantial temporary disk space.

Reports contain aggregate counts and measurements only: never publish input
files, filenames, real UUIDs, coordinates, or per-file hashes. Interrupted runs
are not resumable and do not produce success reports. Inspect retained private
temporary artifacts before deleting any. Read the S08 verification record for
actual results and limitations; a runnable script alone is not qualification.
