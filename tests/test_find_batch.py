"""SQLite adapters execute the same SQL-only contracts as the live provider."""

import pytest

from meldstore import Catalog
from tools.qualification.find_checks import FindChecks, harness_for


@pytest.fixture(params=["sqlite", "melddb"])
def harness(request, tmp_path):
    path = tmp_path / "find.db"

    def open_writer():
        return Catalog(path, adapter=request.param, journal_mode="wal")

    with open_writer() as catalog:
        yield harness_for(catalog, open_writer)


class TestSQLiteFind(FindChecks):
    pass
