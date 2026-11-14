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


def test_oversized_nul_descriptor_is_rejected_without_transferring_its_text(harness, monkeypatch):
    import json

    from tools.qualification.find_checks import observe_sql, setup_find

    malformed = "{}\x00" + "x" * (1024 * 1024)
    store, schema = setup_find(harness, count=2, encoding="all", descriptor=malformed)
    seen = []

    def capture(tx, statement, params, rows):
        if rows and "descriptor" in rows[0]:
            seen.extend(row["descriptor"] for row in rows)

    with observe_sql(monkeypatch, harness.catalog, capture), pytest.raises(json.JSONDecodeError):
        store.find(schema=schema)
    assert seen == [None]
