"""Configuration/redaction tests do not need a running PostgreSQL server."""

import builtins
import traceback

import pytest

from meldstore import Catalog, CatalogConnectionError, ValidationError


@pytest.mark.parametrize("dsn", [None, "", "   ", 12])
def test_invalid_dsn_before_io(dsn):
    with pytest.raises(ValidationError, match="explicit nonempty"):
        Catalog.connect_postgres(dsn)


@pytest.mark.parametrize("options", [
    {"timeout": 0.0001}, {"timeout": 0}, {"timeout": -1}, {"timeout": float('inf')}, {"timeout": True},
    {"timeout": float('nan')}, {"connect_timeout": 0}, {"connect_timeout": 1.5},
    {"connect_timeout": True},
])
def test_invalid_timeouts_before_io(options):
    with pytest.raises(ValidationError):
        Catalog.connect_postgres("host=localhost dbname=test user=test", **options)


def test_missing_extra_is_actionable_and_lazy(monkeypatch):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "psycopg" or name.startswith("psycopg."):
            raise ImportError("synthetic missing optional dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ValidationError, match=r"meldstore\[postgres\]"):
        Catalog.connect_postgres("host=localhost dbname=test user=test")
    with Catalog(":memory:", adapter="sqlite") as sqlite:
        assert sqlite.sql("SELECT 1 AS value", write=False) == [{"value": 1}]


@pytest.mark.parametrize("dsn", [
    "postgresql://user:synthetic-secret@localhost/test",
    "host=localhost user=test password=synthetic-secret dbname=test",
])
def test_dsn_never_becomes_sqlite_filename(dsn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValidationError, match="connect_postgres"):
        Catalog(dsn)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("dsn", [
    "password='synthetic-secret", "dbname=test user=test", "host=localhost dbname=test",
    "host=localhost user=test", "service=example host=localhost dbname=test user=test",
])
def test_bad_configuration_redacts_input(dsn):
    pytest.importorskip("psycopg")
    with pytest.raises(ValidationError) as caught:
        Catalog.connect_postgres(dsn)
    assert "synthetic-secret" not in ''.join(traceback.format_exception(caught.value))


def test_provider_connection_error_redacts_dsn_and_traceback(monkeypatch):
    pytest.importorskip("psycopg")
    import melddb
    from melddb.errors import ConnectionError

    def fail(*args, **kwargs):
        raise ConnectionError("password synthetic-secret host private-host")

    monkeypatch.setattr(melddb, "connect", fail)
    dsn = "host=private-host dbname=test user=test password=synthetic-secret"
    with pytest.raises(CatalogConnectionError) as caught:
        Catalog.connect_postgres(dsn)
    assert 'synthetic-secret' not in ''.join(traceback.format_exception(caught.value))
    assert 'private-host' not in str(caught.value)


def test_open_close_does_not_install_schema_or_store_credentials(monkeypatch):
    pytest.importorskip("psycopg")
    import melddb
    from psycopg.conninfo import conninfo_to_dict

    calls = []

    class Provider:
        def close(self):
            calls.append("closed")

    def connect(dsn, **kwargs):
        fields = conninfo_to_dict(dsn)
        assert fields['connect_timeout'] == '3'
        assert kwargs == {'timeout': 2}
        return Provider()

    monkeypatch.setattr(melddb, "connect", connect)
    with Catalog.connect_postgres(
        "host=localhost dbname=test user=test password=synthetic-secret", timeout=2, connect_timeout=3
    ) as catalog:
        assert catalog.adapter == 'postgres'
        assert catalog.path is None
        assert 'synthetic-secret' not in repr(catalog)
        assert not hasattr(catalog, 'dsn')
        with pytest.raises(ValidationError, match='Bind PostgreSQL'):
            catalog.bind_storage('must-not-be-created')
    assert calls == ['closed']


def test_close_failure_is_redacted(monkeypatch):
    pytest.importorskip("psycopg")
    import melddb
    from melddb.errors import ConnectionError

    from meldstore import TransactionError

    class Provider:
        def close(self):
            raise ConnectionError("synthetic-secret-in-close")

    monkeypatch.setattr(melddb, "connect", lambda *args, **kwargs: Provider())
    catalog = Catalog.connect_postgres("host=localhost dbname=test user=test")
    with pytest.raises(TransactionError) as caught:
        catalog.close()
    assert "synthetic-secret-in-close" not in str(caught.value.cleanup_errors)
    assert "synthetic-secret-in-close" not in ''.join(traceback.format_exception(caught.value))
