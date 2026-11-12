"""Consumer qualification, not a claim that MeldStore supports PostgreSQL yet.

Requirements intentionally remain failing when the pinned provider has a gap.
Only public MeldDB operations are used; Psycopg administers isolated test fixtures.
"""

import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import melddb
import pytest
from melddb.errors import (
    AlreadyExistsError,
    BusyError,
    CommitError,
    ConnectionError,
    ConstraintError,
    OwnershipError,
    RollbackError,
    TransactionError,
    TransactionOutcomeError,
    UnsupportedError,
    ValidationError,
)


def install(db):
    db.sql("CREATE TABLE app_parent(id INTEGER PRIMARY KEY, label TEXT NOT NULL)")
    db.sql("CREATE TABLE app_child(id INTEGER PRIMARY KEY, parent INTEGER REFERENCES app_parent(id))")


@pytest.mark.parametrize("named", [False, True])
def test_raw_native_bindings(pg, named):
    db = pg.connect()
    value = "quotes ' and % and ?; -- remain data"
    instant = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)
    params = {"label": value, "instant": instant, "data": b"\x00\xff", "missing": None}
    if named:
        statement = "SELECT %(label)s::text AS label, %(instant)s::timestamptz AS instant, "
        statement += "%(data)s::bytea AS data, %(missing)s::text AS missing"
    else:
        statement = "SELECT %s::text AS label, %s::timestamptz AS instant, "
        statement += "%s::bytea AS data, %s::text AS missing"
        params = tuple(params.values())
    assert db.sql(statement, params, write=False) == [
        {"label": value, "instant": instant, "data": b"\x00\xff", "missing": None}
    ]


@pytest.mark.parametrize("named", [False, True])
def test_bulk_native_bindings_and_single_pass_summary(pg, named):
    db = pg.connect()
    install(db)
    consumed = []

    def parameters():
        for i in range(20):
            consumed.append(i)
            yield {"id": i, "label": str(i)} if named else (i, str(i))

    statement = "INSERT INTO app_parent VALUES (%(id)s, %(label)s)" if named else (
        "INSERT INTO app_parent VALUES (%s, %s)"
    )
    with db.transaction() as tx:
        assert tx.sql_many(statement, parameters()) == {"parameter_sets": 20, "affected_rows": 20}
        assert tx.sql_many("UPDATE app_parent SET label=%s WHERE id=%s", [("new", 0), ("x", 99)]) == {
            "parameter_sets": 2, "affected_rows": 1,
        }
    assert consumed == list(range(20))
    assert db.sql("SELECT id FROM app_parent ORDER BY id", write=False) == [
        {"id": i} for i in range(20)
    ]


def test_bulk_empty_does_not_execute(pg):
    with pg.connect().transaction() as tx:
        assert tx.sql_many("INSERT INTO absent_table VALUES (%s)", iter(())) == {
            "parameter_sets": 0, "affected_rows": 0,
        }


@pytest.mark.parametrize("failure", ["constraint", "iterator", "binding", "returning"])
def test_bulk_failure_poisoning_rolls_back_mixed_work(pg, failure):
    db = pg.connect()
    install(db)

    def parameters():
        yield (1, 1)
        if failure == "iterator":
            raise RuntimeError("synthetic parameter failure")
        yield (2,) if failure == "binding" else (2, 999 if failure == "constraint" else 1)

    expected = {"constraint": ConstraintError, "iterator": RuntimeError,
                "binding": ValidationError, "returning": UnsupportedError}[failure]
    statement = "INSERT INTO app_child VALUES (%s, %s)"
    if failure == "returning":
        statement += " RETURNING id"
    with pytest.raises(TransactionError):
        with db.transaction() as tx:
            tx.sql("INSERT INTO app_parent VALUES (1, 'pending')")
            with pytest.raises(expected):
                tx.sql_many(statement, parameters())
            with pytest.raises(TransactionError):
                tx.sql("SELECT 1")
    assert db.sql("SELECT * FROM app_parent", write=False) == []
    assert db.sql("SELECT * FROM app_child", write=False) == []


def test_read_scope_is_repeatable_snapshot(pg):
    reader, writer = pg.connect(), pg.connect()
    install(reader)
    reader.sql("INSERT INTO app_parent VALUES (1, 'before')")
    with reader.transaction(write=False) as tx:
        assert tx.sql("SHOW transaction_read_only") == [{"transaction_read_only": "on"}]
        assert tx.sql("SHOW transaction_isolation") == [{"transaction_isolation": "repeatable read"}]
        before = tx.sql("SELECT * FROM app_parent")
        writer.sql("UPDATE app_parent SET label='after'")
        assert tx.sql("SELECT * FROM app_parent") == before
    assert reader.sql("SELECT label FROM app_parent", write=False) == [{"label": "after"}]


@pytest.mark.parametrize("statement", [
    "INSERT INTO app_parent VALUES (1, 'bad')",
    "WITH inserted AS (INSERT INTO app_parent VALUES (1, 'bad') RETURNING *) SELECT * FROM inserted",
    "CREATE TABLE forbidden(id INTEGER)",
])
def test_engine_rejects_persistent_writes_in_read_scope(pg, statement):
    db = pg.connect()
    install(db)
    with pytest.raises(ValidationError) as caught:
        with db.transaction(write=False) as tx:
            tx.sql(statement)
    assert caught.value.__cause__.sqlstate == "25006"
    assert db.sql("SELECT * FROM app_parent", write=False) == []


def test_read_scope_rejects_bulk_before_consumption(pg):
    consumed = []

    def parameters():
        consumed.append(True)
        yield (1,)

    with pytest.raises(UnsupportedError):
        with pg.connect().transaction(write=False) as tx:
            tx.sql_many("INSERT INTO absent_table VALUES (%s)", parameters())
    assert consumed == []


def test_readonly_connection_and_expired_ownership(pg):
    db = pg.connect(readonly=True)
    with pytest.raises(UnsupportedError):
        with db.transaction(write=True):
            pytest.fail("read-only connection admitted a writer")
    with db.transaction(write=False) as expired:
        assert expired.sql("SELECT 1 AS value") == [{"value": 1}]
    with pytest.raises(OwnershipError):
        expired.sql("SELECT 1")


@pytest.mark.parametrize("statement", [
    "COMMIT", "-- comment\nROLLBACK", "SAVEPOINT forbidden",
    "COMMIT;", "END;", "/* outer /* nested */ tail */ COMMIT",
    "SET TRANSACTION READ WRITE", "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
    "SET lock_timeout='10s'", "RESET lock_timeout", "RESET ALL", "DISCARD PLANS",
    "START TRANSACTION", "PREPARE TRANSACTION 'forbidden'",
])
@pytest.mark.parametrize("bulk", [False, True])
def test_transaction_and_connection_controls_rejected(pg, statement, bulk):
    db = pg.connect()
    install(db)
    with pytest.raises(UnsupportedError):
        with db.transaction() as tx:
            tx.sql("INSERT INTO app_parent VALUES (1, 'pending')")
            if bulk:
                tx.sql_many(statement, [()])
            else:
                tx.sql(statement)
    assert db.sql("SELECT * FROM app_parent", write=False) == []


def test_read_scope_cannot_be_upgraded(pg):
    db = pg.connect()
    install(db)
    try:
        with db.transaction(write=False) as tx:
            tx.sql("SET TRANSACTION READ WRITE")
            tx.sql("INSERT INTO app_parent VALUES (1, 'escaped')")
    except UnsupportedError:
        pass
    assert db.sql("SELECT * FROM app_parent", write=False) == []


@pytest.mark.parametrize("bulk", [False, True])
def test_caller_abort_cannot_leave_durable_rows_after_control_sql(pg, bulk):
    db = pg.connect()
    install(db)
    with pytest.raises((UnsupportedError, RuntimeError)):
        with db.transaction() as tx:
            tx.sql("INSERT INTO app_parent VALUES (1, 'must roll back')")
            if bulk:
                tx.sql_many("COMMIT;", [()])
            else:
                tx.sql("COMMIT;")
            raise RuntimeError("caller aborts before the transaction context exits")
    assert db.sql("SELECT * FROM app_parent", write=False) == []


@pytest.mark.parametrize("bulk", [False, True])
def test_multiple_statements_rejected_atomically(pg, bulk):
    db = pg.connect()
    install(db)
    with pytest.raises((UnsupportedError, ValidationError)):
        with db.transaction() as tx:
            tx.sql("INSERT INTO app_parent VALUES (1, 'pending')")
            statement = "INSERT INTO app_parent VALUES (2, 'bad'); COMMIT"
            if bulk:
                tx.sql_many(statement, [()])
            else:
                tx.sql(statement)
    assert db.sql("SELECT * FROM app_parent", write=False) == []


@pytest.mark.parametrize("statement, expected, state", [
    ("INSERT INTO app_parent VALUES (1, 'duplicate')", AlreadyExistsError, "23505"),
    ("INSERT INTO app_parent VALUES (2, NULL)", ConstraintError, "23502"),
    ("INSERT INTO app_child VALUES (1, 999)", ConstraintError, "23503"),
    ("INSERT INTO app_check VALUES (-1)", ConstraintError, "23514"),
])
def test_constraint_error_mapping(pg, statement, expected, state):
    db = pg.connect()
    install(db)
    db.sql("INSERT INTO app_parent VALUES (1, 'kept')")
    db.sql("CREATE TABLE app_check(value INTEGER CHECK(value > 0))")
    with pytest.raises(expected) as caught:
        db.sql(statement)
    assert caught.value.__cause__.sqlstate == state
    assert db.sql("SELECT label FROM app_parent", write=False) == [{"label": "kept"}]


def test_lock_timeout_maps_to_busy_and_rolls_back(pg):
    holder, contender = pg.connect(), pg.connect(timeout=0.1)
    install(holder)
    holder.sql("INSERT INTO app_parent VALUES (1, 'kept')")
    with holder.transaction() as tx:
        tx.sql("UPDATE app_parent SET label='held' WHERE id=1")
        with pytest.raises(BusyError) as caught:
            with contender.transaction() as competing:
                competing.sql("INSERT INTO app_parent VALUES (2, 'pending')")
                competing.sql("UPDATE app_parent SET label='lost' WHERE id=1")
        assert caught.value.__cause__.sqlstate == "55P03"
    assert contender.sql("SELECT id FROM app_parent", write=False) == [{"id": 1}]


def test_serialization_failure_maps_to_busy(pg):
    db, writer = pg.connect(), pg.connect()
    install(db)
    db.sql("INSERT INTO app_parent VALUES (1, 'before')")
    options = pg.driver.conninfo.conninfo_to_dict(pg.dsn)["options"]
    dsn = pg.driver.conninfo.make_conninfo(
        pg.dsn, options=options + " -c default_transaction_isolation=serializable"
    )
    with melddb.connect(dsn) as serial:
        with pytest.raises(BusyError) as caught:
            with serial.transaction() as tx:
                tx.sql("SELECT * FROM app_parent")
                writer.sql("UPDATE app_parent SET label='winner' WHERE id=1")
                tx.sql("UPDATE app_parent SET label='loser' WHERE id=1")
        assert caught.value.__cause__.sqlstate == "40001"
        assert serial.sql("SELECT label FROM app_parent", write=False) == [{"label": "winner"}]


def test_deadlock_maps_to_busy_without_retry(pg):
    db = pg.connect()
    install(db)
    db.sql("INSERT INTO app_parent VALUES (1, 'one'), (2, 'two')")
    barrier = threading.Barrier(2)

    def compete(first, second):
        with melddb.connect(pg.dsn) as worker:
            try:
                with worker.transaction() as tx:
                    tx.sql("UPDATE app_parent SET label='held' WHERE id=%s", (first,))
                    barrier.wait(timeout=5)
                    tx.sql("UPDATE app_parent SET label='won' WHERE id=%s", (second,))
            except BusyError as exc:
                return exc.__cause__.sqlstate
        return "committed"

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(compete, 1, 2), pool.submit(compete, 2, 1)]
        results = [future.result(timeout=15) for future in futures]
    assert sorted(results) == ["40P01", "committed"]


@pytest.mark.parametrize("phase", ["commit", "rollback"])
def test_disconnect_preserves_unknown_outcome_and_quarantines(pg, phase):
    db = pg.connect()
    install(db)
    error = CommitError if phase == "commit" else RollbackError
    with pytest.raises(error) as caught:
        with db.transaction() as tx:
            tx.sql("INSERT INTO app_parent VALUES (1, 'pending')")
            pid = tx.sql("SELECT pg_backend_pid() AS pid")[0]["pid"]
            assert pg.admin.execute("SELECT pg_terminate_backend(%s, 5000)", (pid,)).fetchone()[0]
            if phase == "rollback":
                tx.sql("SELECT 1")
    assert caught.value.phase == phase
    assert caught.value.outcome == "unknown"
    assert caught.value.backend_error is not None
    if phase == "rollback":
        assert caught.value.initiating_error is not None
    with pytest.raises(TransactionError, match="close and reopen"):
        db.sql("SELECT 1", write=False)
    assert pg.connect().sql("SELECT * FROM app_parent", write=False) == []


@pytest.mark.parametrize("attribute", ["initiating_error", "backend_error"])
def test_disconnect_errors_are_connection_errors(pg, attribute):
    db = pg.connect()
    with pytest.raises(RollbackError) as caught:
        with db.transaction() as tx:
            pid = tx.sql("SELECT pg_backend_pid() AS pid")[0]["pid"]
            assert pg.admin.execute("SELECT pg_terminate_backend(%s, 5000)", (pid,)).fetchone()[0]
            tx.sql("SELECT 1")
    assert isinstance(getattr(caught.value, attribute), ConnectionError)


def test_disconnect_before_begin_is_structured_and_quarantined(pg):
    db = pg.connect()
    pid = db.sql("SELECT pg_backend_pid() AS pid", write=False)[0]["pid"]
    assert pg.admin.execute("SELECT pg_terminate_backend(%s, 5000)", (pid,)).fetchone()[0]
    with pytest.raises(TransactionOutcomeError) as caught:
        with db.transaction():
            pytest.fail("Disconnected provider yielded a transaction")
    assert caught.value.phase == "begin"
    assert caught.value.outcome == "unknown"
    assert caught.value.initiating_error is not None
    assert caught.value.backend_error is not None
    with pytest.raises(TransactionError, match="close and reopen"):
        db.sql("SELECT 1", write=False)


def test_connection_refusal_is_connection_error(provider_environment):
    # A bound, non-listening socket reserves a local port without a race with another service.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        dsn = provider_environment.driver.conninfo.make_conninfo(
            host="127.0.0.1", port=reserved.getsockname()[1], connect_timeout=1,
            dbname="unused", user="unused", password="synthetic",
        )
        with pytest.raises(ConnectionError):
            melddb.connect(dsn)
