"""Live PostgreSQL Store.find checks; synthetic metadata, no payload qualification."""

import pytest

from meldstore import Catalog
from tools.qualification.find_checks import FindChecks, harness_for


@pytest.fixture
def harness(pg):
    def open_writer():
        return Catalog.connect_postgres(pg.dsn)

    with open_writer() as catalog:
        yield harness_for(catalog, open_writer)


class TestPostgresFind(FindChecks):
    pass
