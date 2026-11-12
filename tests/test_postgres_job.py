"""The mandatory live CI job must fail for absent or skipped acceptance tests."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def test_required_postgres_fixture_cannot_skip_without_dsn():
    environment = dict(os.environ)
    environment.pop('MELDSTORE_TEST_POSTGRES', None)
    result = subprocess.run(
        [sys.executable, '-m', 'pytest',
         'tests/postgres_provider/test_catalog_postgres.py::test_catalog_open_sql_bulk_read_and_close',
         '--postgres-provider-required', '-q', '--tb=short'],
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert 'Required PostgreSQL provider fixture has no disposable database' in result.stdout


def test_required_postgres_job_rejects_a_skipped_acceptance_case(tmp_path):
    root = Path(__file__).resolve().parents[1]
    shutil.copyfile(root / 'tests/postgres_provider/conftest.py', tmp_path / 'conftest.py')
    (tmp_path / 'test_skipped.py').write_text(
        "import pytest\ndef test_acceptance():\n    pytest.skip('synthetic missing fixture')\n",
        encoding='utf-8',
    )
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', str(tmp_path), '--postgres-provider-required', '-q'],
        capture_output=True, text=True, timeout=30,
    )
    assert '1 skipped' in result.stdout
    assert result.returncode != 0
