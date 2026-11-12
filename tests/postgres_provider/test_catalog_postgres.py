"""Live tests for the experimental connection selection slice, not blob storage."""

import socket
import traceback

import pytest

from meldstore import (
    Catalog,
    CatalogConnectionError,
    ConstraintError,
    RollbackError,
    TransactionError,
    ValidationError,
)


def test_catalog_open_sql_bulk_read_and_close(pg):
    with Catalog.connect_postgres(pg.dsn) as catalog:
        catalog.sql('CREATE TABLE app_selection(id INTEGER PRIMARY KEY)')
        with catalog.transaction() as tx:
            assert tx.sql_many('INSERT INTO app_selection VALUES (%s)', [(1,), (2,)]) == {
                'parameter_sets': 2, 'affected_rows': 2,
            }
        assert catalog.sql('SELECT id FROM app_selection ORDER BY id', write=False) == [
            {'id': 1}, {'id': 2},
        ]
        with pytest.raises(ValidationError):
            catalog.sql('INSERT INTO app_selection VALUES (3)', write=False)
        with pytest.raises(ConstraintError):
            catalog.sql('INSERT INTO app_selection VALUES (1)')
    with pytest.raises(TransactionError, match='closed'):
        catalog.sql('SELECT 1')


@pytest.mark.parametrize('statement', ['COMMIT;', 'SET TRANSACTION READ WRITE',
                                      '/* outer /* inner */ tail */COMMIT;'])
def test_catalog_controls_fail_without_consumption(pg, statement):
    consumed = []

    def params():
        consumed.append(True)
        yield ()

    with Catalog.connect_postgres(pg.dsn) as catalog:
        with pytest.raises(ValidationError):
            with catalog.transaction() as tx:
                tx.sql_many(statement, params())
    assert consumed == []


def test_postgres_error_text_redacts_values(pg):
    secret = 'synthetic-secret-in-constraint-detail'
    with Catalog.connect_postgres(pg.dsn) as catalog:
        catalog.sql('CREATE TABLE app_secret(value TEXT UNIQUE)')
        catalog.sql('INSERT INTO app_secret VALUES (%s)', (secret,))
        with pytest.raises(ConstraintError) as caught:
            catalog.sql('INSERT INTO app_secret VALUES (%s)', (secret,))
        assert secret not in ''.join(traceback.format_exception(caught.value))


def test_postgres_disconnect_retains_safe_outcome_and_quarantine(pg):
    with Catalog.connect_postgres(pg.dsn) as catalog:
        with pytest.raises(RollbackError) as caught:
            with catalog.transaction() as tx:
                pid = tx.sql('SELECT pg_backend_pid() AS pid')[0]['pid']
                pg.admin.execute('SELECT pg_terminate_backend(%s, 5000)', (pid,))
                tx.sql('SELECT 1')
        assert caught.value.phase == 'rollback'
        assert caught.value.outcome == 'unknown'
        assert caught.value.backend_error.provider_type == 'ConnectionError'
        assert 'redacted' in str(caught.value.backend_error)
        with pytest.raises(TransactionError, match='closing'):
            catalog.sql('SELECT 1')


def test_postgres_unavailable_has_no_fallback(provider_environment):
    with socket.socket() as port:
        port.bind(('127.0.0.1', 0))
        dsn = provider_environment.driver.conninfo.make_conninfo(
            host='127.0.0.1', port=port.getsockname()[1], dbname='missing',
            user='test', password='synthetic-secret',
        )
        with pytest.raises(CatalogConnectionError) as caught:
            Catalog.connect_postgres(dsn, connect_timeout=1)
        assert 'synthetic-secret' not in ''.join(traceback.format_exception(caught.value))


def test_bad_password_is_redacted(pg):
    dsn = pg.driver.conninfo.make_conninfo(pg.dsn, password="synthetic-wrong-password")
    with pytest.raises(CatalogConnectionError) as caught:
        Catalog.connect_postgres(dsn)
    assert "synthetic-wrong-password" not in ''.join(traceback.format_exception(caught.value))
