"""Install each built artifact in a fresh environment, outside the source checkout."""

import argparse
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--formats", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    artifacts = list((root / "dist").glob("*.whl")) + list((root / "dist").glob("*.tar.gz"))
    if len(artifacts) != 2:
        raise RuntimeError("Expected exactly one wheel and one sdist in dist/")
    code = """
from pathlib import Path
from meldstore import BlobSchema, Catalog, Integer, LocalStorage, MetadataMigration, Predicate, Store, Text, restore_backup
Path('source.bin').write_bytes(b'installed artifact payload')
for adapter in ('sqlite', 'melddb'):
    with Catalog(adapter + '.db', adapter=adapter) as catalog:
        schema = BlobSchema('smoke', {'title': Text(required=True)})
        assert catalog.install_schema(schema) == schema.table_name
        assert catalog.install_schema(schema) == schema.table_name
        store = Store(catalog, LocalStorage(adapter + '-objects'))
        store.install_schema(schema)
        assert catalog.sql('SELECT * FROM pragma_journal_mode', write=False) == [{'journal_mode': 'wal'}]
        assert store.install_query_indexes() == ['ms_gc_pending']
        assert store.install_query_indexes() == []
        blob = store.import_file('source.bin', schema=schema, metadata={'title': 'smoke'})
        target = BlobSchema('smoke', {'title': Text(required=True), 'number': Integer(required=True)}, version=2)
        migration = MetadataMigration('smoke-v2', schema, target, 'add-number', lambda row: dict(row, number=1))
        assert store.migrate(migration, dry_run=True)['migrated_rows'] == 1
        assert store.migrate(migration)['state'] == 'complete'
        edited = store.update_metadata(blob['id'], schema=target, changes={'number': 2}, expected_version=2)
        assert edited['version'] == 3
        assert len(store.find(schema=target, predicates=(Predicate('number', 'gte', 2),))) == 1
        with store.materialize(blob['id']) as payload:
            assert payload.read_bytes() == b'installed artifact payload'
        store.delete(blob['id'], expected_version=3)
        assert store.deletion_status(blob['id'])['state'] == 'pending'
    with Catalog(adapter + '.db', adapter=adapter, maintenance=True) as catalog:
        store = Store(catalog, LocalStorage(adapter + '-objects'))
        assert catalog.maintain_sqlite()['checkpoint']['busy'] == 0
        assert len(store.reconcile()['pending']) == 1
        assert store.cleanup()[0]['state'] == 'done'
        assert store.deletion_status(blob['id'])['state'] == 'done'
        live = store.import_file('source.bin', schema=target, metadata={'title': 'backup', 'number': 4}, id='backup-live')
        catalog.sql('CREATE TABLE app_links(id TEXT REFERENCES ms_blobs(id))')
        catalog.sql("INSERT INTO app_links VALUES('backup-live')")
        store.backup(adapter + '-backup', application={'id':'smoke','schema_revision':'1'})
    restored = restore_backup(adapter + '-backup', adapter + '-restored')
    with Catalog(restored['catalog'], adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(restored['storage']))
        assert store.stat('backup-live') == live
        assert catalog.sql('SELECT id FROM app_links') == [{'id': 'backup-live'}]
        with store.materialize('backup-live') as payload:
            assert payload.read_bytes() == b'installed artifact payload'
print('Both adapters passed from installed artifact')
"""
    if args.formats:
        code += """
import numpy as np
import pandas as pd
import polars as pl
import pyarrow as pa
from polars.testing import assert_frame_equal
for adapter in ('sqlite', 'melddb'):
    schema = BlobSchema('formats', {}, handlers=(
        'bytes', 'numpy.npz', 'numpy.blosc2', 'pandas.parquet', 'polars.parquet', 'pyarrow.parquet'))
    with Catalog(adapter + '-formats.db', adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(adapter + '-formats-objects'))
        store.install_schema(schema)
        values = {'bytes': b'hello', 'numpy.npz': np.arange(6), 'numpy.blosc2': np.arange(6),
                  'pandas.parquet': pd.DataFrame({'x': [1, 2]}),
                  'polars.parquet': pl.DataFrame({'x': [1, 2]}),
                  'pyarrow.parquet': pa.table({'x': [1, 2]})}
        for handler, value in values.items():
            store.put(value, schema=schema, metadata={}, handler=handler, id=handler)
    with Catalog(adapter + '-formats.db', adapter=adapter, maintenance=True) as catalog:
        store = Store(catalog, LocalStorage(adapter + '-formats-objects'))
        store.backup(adapter + '-formats-backup', application={'id':'formats','schema_revision':'1'})
    restored = restore_backup(adapter + '-formats-backup', adapter + '-formats-restored')
    with Catalog(restored['catalog'], adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(restored['storage']))
        for handler, value in values.items():
            result = store.get(handler)
            if handler.startswith('numpy.'):
                np.testing.assert_array_equal(result, value, strict=True)
            elif handler == 'pandas.parquet':
                pd.testing.assert_frame_equal(result, value)
            elif handler == 'polars.parquet':
                assert_frame_equal(result, value)
            elif handler == 'pyarrow.parquet':
                assert result.equals(value)
            else:
                assert result == value
print('Every optional handler passed from installed artifact')
"""
    for artifact in artifacts:
        with tempfile.TemporaryDirectory(prefix="meldstore-package-") as directory:
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--no-project",
                    "--isolated",
                    "--with",
                    str(artifact) + ("[parquet,numpy,blosc2,polars,arrow]" if args.formats else ""),
                    "python",
                    "-I",
                    "-c",
                    code,
                ],
                cwd=directory,
                check=True,
            )
        print(f"Verified {artifact.name}", flush=True)


if __name__ == "__main__":
    main()
