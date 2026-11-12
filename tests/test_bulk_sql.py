"""Transaction-scoped bulk SQL conformance for both metadata adapters."""

from concurrent.futures import ThreadPoolExecutor

import obstore
import pytest

from meldstore import (
    BlobSchema,
    Catalog,
    ConstraintError,
    LocalStorage,
    Store,
    Text,
    TransactionError,
    ValidationError,
)


@pytest.fixture(params=["sqlite", "melddb"])
def catalog(request, tmp_path):
    with Catalog(tmp_path / "catalog.db", adapter=request.param) as value:
        yield value


def test_bulk_sql_consumes_generator_once_in_order(catalog):
    catalog.sql("CREATE TABLE app_event(position INTEGER PRIMARY KEY, label TEXT NOT NULL)")
    yielded = []

    def parameters():
        for position in range(1000):
            yielded.append(position)
            yield (position, f"event-{position}")

    source = parameters()
    with catalog.transaction() as tx:
        result = tx.sql_many(
            "INSERT INTO app_event VALUES (?, ?)",
            source,
        )

    assert result == {"parameter_sets": 1000, "affected_rows": 1000}
    assert yielded == list(range(1000))
    assert list(source) == []
    assert catalog.sql(
        "SELECT position FROM app_event ORDER BY position", write=False
    ) == [{"position": position} for position in range(1000)]
    assert not hasattr(catalog, "sql_many")


def test_empty_bulk_input_does_not_execute(catalog):
    with catalog.transaction() as tx:
        tx._execute_many = lambda *_: pytest.fail("adapter executor called")
        assert tx.sql_many("INSERT INTO missing_table VALUES (?)", iter(())) == {
            "parameter_sets": 0,
            "affected_rows": 0,
        }


def test_read_transaction_rejects_before_parameter_consumption(catalog):
    consumed = False

    def parameters():
        nonlocal consumed
        consumed = True
        yield (1,)

    with pytest.raises(ValidationError, match="writable"):
        with catalog.transaction(write=False) as tx:
            tx.sql_many("INSERT INTO app_event VALUES (?)", parameters())
    assert consumed is False


def test_expired_and_wrong_thread_handles_are_rejected(catalog):
    catalog.sql("CREATE TABLE app_event(position INTEGER PRIMARY KEY)")
    with catalog.transaction() as expired:
        pass
    with pytest.raises(TransactionError, match="expired"):
        expired.sql_many("INSERT INTO app_event VALUES (?)", [(1,)])

    with catalog.transaction() as tx:
        with ThreadPoolExecutor(1) as pool:
            error = pool.submit(
                tx.sql_many,
                "INSERT INTO app_event VALUES (?)",
                [(2,)],
            ).exception()
        assert isinstance(error, TransactionError)
        assert "creating thread" in str(error)
        assert tx.sql_many("INSERT INTO app_event VALUES (?)", [(3,)]) == {
            "parameter_sets": 1,
            "affected_rows": 1,
        }
    assert catalog.sql("SELECT position FROM app_event", write=False) == [{"position": 3}]


@pytest.mark.parametrize(
    "statement",
    ["BEGIN", "-- comment\nCOMMIT", "PRAGMA foreign_keys=OFF"],
)
def test_bulk_control_sql_is_rejected_before_parameter_consumption(catalog, statement):
    consumed = False

    def parameters():
        nonlocal consumed
        consumed = True
        yield ()

    with pytest.raises(ValidationError, match="control"):
        with catalog.transaction() as tx:
            tx.sql_many(statement, parameters())
    assert consumed is False


@pytest.mark.parametrize("kind", ["select", "returning"])
def test_result_producing_bulk_sql_rolls_back(catalog, kind):
    catalog.sql("CREATE TABLE app_event(position INTEGER PRIMARY KEY)")
    statement = (
        "SELECT ? AS position"
        if kind == "select"
        else "INSERT INTO app_event VALUES (?) RETURNING position"
    )
    with pytest.raises(ValidationError):
        with catalog.transaction() as tx:
            tx.sql_many(statement, [(1,), (2,)])
    assert catalog.sql("SELECT * FROM app_event", write=False) == []


def test_constraint_failure_rolls_back_mixed_sql(catalog):
    with catalog.transaction() as tx:
        tx.sql("CREATE TABLE app_parent(id TEXT PRIMARY KEY)")
        tx.sql(
            "CREATE TABLE app_event("
            "position INTEGER PRIMARY KEY, "
            "parent_id TEXT NOT NULL REFERENCES app_parent(id))"
        )

    with pytest.raises(ConstraintError):
        with catalog.transaction() as tx:
            tx.sql("INSERT INTO app_parent VALUES (?)", ("parent",))
            tx.sql_many(
                "INSERT INTO app_event VALUES (?, ?)",
                [(1, "parent"), (2, "missing")],
            )

    assert catalog.sql("SELECT * FROM app_parent", write=False) == []
    assert catalog.sql("SELECT * FROM app_event", write=False) == []


def test_caught_bulk_failure_poisoned(catalog):
    catalog.sql("CREATE TABLE app_event(position INTEGER PRIMARY KEY)")
    with pytest.raises(TransactionError, match="Failed transaction"):
        with catalog.transaction() as tx:
            with pytest.raises(ConstraintError):
                tx.sql_many("INSERT INTO app_event VALUES (?)", [(1,), (1,)])
            with pytest.raises(TransactionError, match="failed"):
                tx.sql("SELECT 1")
    assert catalog.sql("SELECT * FROM app_event", write=False) == []


def test_parameter_iteration_failure_rolls_back(catalog):
    catalog.sql("CREATE TABLE app_event(position INTEGER PRIMARY KEY)")

    def parameters():
        yield (1,)
        yield (2,)
        raise RuntimeError("parameter source failed")

    with pytest.raises(ValidationError, match="parameter source failed"):
        with catalog.transaction() as tx:
            tx.sql_many("INSERT INTO app_event VALUES (?)", parameters())
    assert catalog.sql("SELECT * FROM app_event", write=False) == []


def test_binding_and_driver_failures_roll_back(catalog):
    catalog.sql("CREATE TABLE app_event(position INTEGER PRIMARY KEY)")
    with pytest.raises(ValidationError):
        with catalog.transaction() as tx:
            tx.sql_many("INSERT INTO app_event VALUES (?)", [(1,), ()])
    assert catalog.sql("SELECT * FROM app_event", write=False) == []

    with pytest.raises(ValidationError):
        with catalog.transaction() as tx:
            tx.sql_many("INSERT INTO missing_table VALUES (?)", [(2,)])
    assert catalog.sql("SELECT * FROM app_event", write=False) == []


def test_adapter_completion_summary_is_validated(catalog):
    catalog.sql("CREATE TABLE app_event(position INTEGER PRIMARY KEY)")
    with pytest.raises(ValidationError, match="parameter-set count"):
        with catalog.transaction() as tx:
            tx.sql("INSERT INTO app_event VALUES (?)", (1,))

            def malformed(_statement, parameter_sets):
                list(parameter_sets)
                return {"parameter_sets": 99, "affected_rows": 1}

            tx._execute_many = malformed
            tx.sql_many("INSERT INTO app_event VALUES (?)", [(2,)])
    assert catalog.sql("SELECT * FROM app_event", write=False) == []


def test_unavailable_affected_rows_is_preserved(catalog):
    with catalog.transaction() as tx:

        def unavailable(_statement, parameter_sets):
            submitted = len(list(parameter_sets))
            return {"parameter_sets": submitted, "affected_rows": None}

        tx._execute_many = unavailable
        assert tx.sql_many("INSERT INTO app_event VALUES (?)", [(1,), (2,)]) == {
            "parameter_sets": 2,
            "affected_rows": None,
        }


@pytest.mark.parametrize("first", ["sqlite", "melddb"])
def test_bulk_application_rows_reopen_under_other_adapter(tmp_path, first):
    path = tmp_path / "portable.db"
    with Catalog(path, adapter=first) as catalog:
        with catalog.transaction() as tx:
            tx.sql("CREATE TABLE app_parent(id TEXT PRIMARY KEY)")
            tx.sql(
                "CREATE TABLE app_event("
                "position INTEGER PRIMARY KEY, "
                "parent_id TEXT NOT NULL REFERENCES app_parent(id))"
            )
            tx.sql("INSERT INTO app_parent VALUES (?)", ("parent",))
            tx.sql_many(
                "INSERT INTO app_event VALUES (?, ?)",
                ((position, "parent") for position in range(3)),
            )

    other = "melddb" if first == "sqlite" else "sqlite"
    with Catalog(path, adapter=other) as catalog:
        assert catalog.sql(
            "SELECT position, parent_id FROM app_event ORDER BY position",
            write=False,
        ) == [
            {"position": position, "parent_id": "parent"}
            for position in range(3)
        ]


def test_bulk_sql_performs_no_object_storage_io(catalog, monkeypatch):
    catalog.sql("CREATE TABLE app_event(position INTEGER PRIMARY KEY)")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("unexpected object-storage I/O")

    for name in ("get", "put", "copy", "rename", "delete", "list"):
        monkeypatch.setattr(obstore, name, unexpected)

    with catalog.transaction() as tx:
        assert tx.sql_many("INSERT INTO app_event VALUES (?)", [(1,), (2,)]) == {
            "parameter_sets": 2,
            "affected_rows": 2,
        }


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_bulk_constraint_rolls_back_blob_publication_metadata(tmp_path, adapter):
    path = tmp_path / "catalog.db"
    source = tmp_path / "source.bin"
    source.write_bytes(b"prepared outside the SQL transaction")
    schema = BlobSchema("bulk", {"label": Text(required=True)})

    with Catalog(path, adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        store.install_schema(schema)
        with catalog.transaction() as tx:
            tx.sql("CREATE TABLE app_owner(id TEXT PRIMARY KEY)")
            tx.sql(
                "CREATE TABLE app_ref("
                "blob_id TEXT NOT NULL REFERENCES ms_blobs(id), "
                "owner_id TEXT NOT NULL REFERENCES app_owner(id), "
                "PRIMARY KEY(blob_id, owner_id))"
            )
            tx.sql("INSERT INTO app_owner VALUES (?)", ("valid",))

        prepared = store.prepare_file(source, schema=schema)
        with pytest.raises(ConstraintError):
            with catalog.transaction() as tx:
                store.finalize(
                    prepared,
                    tx=tx,
                    schema=schema,
                    metadata={"label": "example"},
                    id="blob",
                )
                tx.sql_many(
                    "INSERT INTO app_ref VALUES (?, ?)",
                    [("blob", "valid"), ("blob", "missing")],
                )
                store.publish("blob", tx=tx)

        assert store.find(schema=schema) == []
        assert catalog.sql("SELECT * FROM app_ref", write=False) == []
        assert catalog.sql("SELECT * FROM ms_blobs", write=False) == []
