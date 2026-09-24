import pytest

import meldstore
from meldstore import (
    Catalog,
    CommitError,
    LocalStorage,
    Store,
    TransactionError,
    ValidationError,
)


class FaultConnection:
    """Inject one driver-boundary failure while delegating all other behavior."""

    def __init__(self, connection, callback):
        self.connection = connection
        self.callback = callback

    def execute(self, statement, *args, **kwargs):
        replacement = self.callback(statement, self.connection)
        if replacement is not None:
            return replacement
        return self.connection.execute(statement, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.connection, name)


def direct_catalog(tmp_path):
    return Catalog(tmp_path / "catalog.db", adapter="sqlite", journal_mode="delete")


def assert_quarantined_before_io(catalog, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("quarantined catalog performed database I/O")

    monkeypatch.setattr(catalog._connection, "execute", unexpected)
    with pytest.raises(TransactionError):
        catalog.sql("SELECT 1", write=False)


def test_outcome_error_hierarchy_retains_optional_evidence():
    outcome_type = getattr(meldstore, "TransactionOutcomeError")
    rollback_type = getattr(meldstore, "RollbackError")
    assert issubclass(outcome_type, TransactionError)
    assert issubclass(CommitError, outcome_type)
    assert issubclass(rollback_type, outcome_type)

    assert CommitError("legacy message").phase is None
    backend = RuntimeError("backend failed")
    initiating = RuntimeError("application failed")
    error = rollback_type(
        "rollback failed",
        phase="rollback",
        outcome="unknown",
        initiating_error=initiating,
        backend_error=backend,
    )
    assert error.initiating_error is initiating
    assert error.backend_error is backend


def test_direct_activation_failure_recovers_and_leaves_catalog_reusable(tmp_path):
    catalog = direct_catalog(tmp_path)
    marker = RuntimeError("query-only activation failed")

    def fail_activation(statement, connection):
        if statement.upper().replace(" ", "").startswith("PRAGMAQUERY_ONLY=ON"):
            raise marker
        return None

    catalog._connection = FaultConnection(catalog._connection, fail_activation)
    try:
        with pytest.raises(ValidationError) as caught:
            with catalog.transaction(write=False):
                pass
        assert caught.value.__cause__ is marker
        catalog.sql("CREATE TABLE reusable(value INTEGER)")
    finally:
        catalog.close()


def test_direct_uncertain_begin_reports_evidence_and_quarantines(
    tmp_path, monkeypatch
):
    catalog = direct_catalog(tmp_path)
    initiating = RuntimeError("begin acknowledgement lost")
    backend = RuntimeError("begin recovery failed")

    def fail_begin_and_recovery(statement, connection):
        keyword = statement.strip().split(maxsplit=1)[0].upper()
        if keyword == "BEGIN":
            connection.execute(statement)
            raise initiating
        if keyword == "ROLLBACK":
            raise backend
        return None

    catalog._connection = FaultConnection(catalog._connection, fail_begin_and_recovery)
    try:
        outcome_type = getattr(meldstore, "TransactionOutcomeError")
        with pytest.raises(outcome_type) as caught:
            with catalog.transaction():
                pass
        assert caught.value.phase == "begin"
        assert caught.value.outcome == "unknown"
        assert caught.value.initiating_error is initiating
        assert caught.value.backend_error is backend
        assert_quarantined_before_io(catalog, monkeypatch)
    finally:
        catalog.close()


def test_direct_commit_failure_reports_unknown_and_quarantines(tmp_path, monkeypatch):
    catalog = direct_catalog(tmp_path)
    backend = RuntimeError("commit acknowledgement lost")

    def fail_commit(statement, connection):
        if statement.strip().upper() == "COMMIT":
            raise backend
        return None

    catalog._connection = FaultConnection(catalog._connection, fail_commit)
    try:
        with pytest.raises(CommitError) as caught:
            with catalog.transaction() as tx:
                tx.sql("CREATE TABLE uncertain(value INTEGER)")
        assert caught.value.phase == "commit"
        assert caught.value.outcome == "unknown"
        assert caught.value.backend_error is backend
        assert caught.value.__cause__ is backend
        assert_quarantined_before_io(catalog, monkeypatch)
    finally:
        catalog.close()


def test_direct_rollback_failure_preserves_initiating_error_and_quarantines(
    tmp_path, monkeypatch
):
    catalog = direct_catalog(tmp_path)
    initiating = RuntimeError("application failed")
    backend = RuntimeError("rollback failed")

    def fail_rollback(statement, connection):
        if statement.strip().upper() == "ROLLBACK":
            raise backend
        return None

    catalog._connection = FaultConnection(catalog._connection, fail_rollback)
    try:
        rollback_type = getattr(meldstore, "RollbackError")
        with pytest.raises(rollback_type) as caught:
            with catalog.transaction():
                raise initiating
        assert caught.value.phase == "rollback"
        assert caught.value.outcome == "unknown"
        assert caught.value.initiating_error is initiating
        assert caught.value.backend_error is backend
        assert caught.value.__cause__ is initiating
        assert_quarantined_before_io(catalog, monkeypatch)
    finally:
        catalog.close()


@pytest.mark.parametrize("committed", [False, True])
def test_direct_read_restoration_failure_reports_known_outcome(
    tmp_path, committed
):
    catalog = direct_catalog(tmp_path)
    initiating = RuntimeError("application failed")
    backend = RuntimeError("query-only restoration failed")
    finalized = False

    def fail_restoration(statement, connection):
        nonlocal finalized
        normalized = statement.strip().upper().replace(" ", "")
        if normalized in {"COMMIT", "ROLLBACK"}:
            result = connection.execute(statement)
            finalized = True
            return result
        if finalized and normalized.startswith("PRAGMAQUERY_ONLY="):
            raise backend
        return None

    catalog._connection = FaultConnection(catalog._connection, fail_restoration)
    try:
        expected = CommitError if committed else getattr(meldstore, "RollbackError")
        with pytest.raises(expected) as caught:
            with catalog.transaction(write=False):
                if not committed:
                    raise initiating
        assert caught.value.phase == "cleanup"
        assert caught.value.outcome == ("committed" if committed else "rolled_back")
        assert caught.value.initiating_error is (None if committed else initiating)
        assert caught.value.backend_error is backend
    finally:
        catalog.close()


def test_melddb_outcome_is_mapped_without_second_recovery(tmp_path, monkeypatch):
    catalog = Catalog(
        tmp_path / "melddb.db", adapter="melddb", journal_mode="delete"
    )
    marker = RuntimeError("commit acknowledgement lost")
    commit_calls = 0

    def fail_commit():
        nonlocal commit_calls
        commit_calls += 1
        raise marker

    monkeypatch.setattr(catalog._connection._backend, "commit", fail_commit)
    try:
        with pytest.raises(CommitError) as caught:
            with catalog.transaction() as tx:
                tx.sql("CREATE TABLE uncertain(value INTEGER)")
        assert caught.value.phase == "commit"
        assert caught.value.outcome == "unknown"
        assert caught.value.backend_error is marker
        assert commit_calls == 1
        with pytest.raises(TransactionError):
            catalog.sql("SELECT 1", write=False)
    finally:
        catalog.close()


def test_quarantine_rejects_catalog_and_payload_io(tmp_path, monkeypatch):
    catalog = direct_catalog(tmp_path)
    storage = LocalStorage(tmp_path / "objects")
    store = Store(catalog, storage)
    backend = RuntimeError("commit failed")

    def fail_commit(statement, connection):
        if statement.strip().upper() == "COMMIT":
            raise backend
        return None

    catalog._connection = FaultConnection(catalog._connection, fail_commit)
    try:
        with pytest.raises(CommitError):
            with catalog.transaction():
                pass

        def unexpected(*args, **kwargs):
            pytest.fail("quarantined operation reached database or payload I/O")

        monkeypatch.setattr(catalog._connection, "execute", unexpected)
        monkeypatch.setattr(storage, "materialize", unexpected)
        with pytest.raises(TransactionError):
            catalog.snapshot(tmp_path / "snapshot.db")
        with pytest.raises(TransactionError):
            catalog.maintain_sqlite()
        with pytest.raises(TransactionError):
            store.get("missing")
    finally:
        catalog.close()


def test_close_attempts_every_resource_and_preserves_primary_outcome(tmp_path):
    catalog = direct_catalog(tmp_path)
    backend = RuntimeError("commit failed")

    def fail_commit(statement, connection):
        if statement.strip().upper() == "COMMIT":
            raise backend
        return None

    catalog._connection = FaultConnection(catalog._connection, fail_commit)
    with pytest.raises(CommitError) as caught:
        with catalog.transaction():
            pass
    primary = caught.value
    raw = catalog._connection.connection
    calls = []
    database_cleanup = RuntimeError("database close failed")
    lease_cleanup = RuntimeError("lease close failed")

    def fail_database_close():
        calls.append("database")
        raise database_cleanup

    class Resource:
        def __init__(self, name, error=None):
            self.name = name
            self.error = error

        def close(self):
            calls.append(self.name)
            if self.error is not None:
                raise self.error

    catalog._connection.close = fail_database_close
    catalog._leases = {
        "first": Resource("first", lease_cleanup),
        "second": Resource("second"),
    }
    try:
        with pytest.raises(CommitError) as closing:
            catalog.close()
        assert closing.value is primary
        assert primary.cleanup_errors == (database_cleanup, lease_cleanup)
        assert calls == ["database", "first", "second"]
    finally:
        raw.close()
