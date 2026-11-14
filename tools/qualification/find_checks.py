"""Shared Store.find contracts, collected by SQLite and live PostgreSQL wrappers."""

import json
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from meldstore import (
    BlobSchema,
    CommitError,
    FindCursor,
    IntegrityError,
    MetadataMigration,
    NotFoundError,
    Order,
    Predicate,
    SchemaConflictError,
    Text,
    TransactionError,
    ValidationError,
)
from meldstore.catalog import Transaction
from meldstore.dialect import for_transaction
from tools.qualification.find_fixture import (
    DESCRIPTOR,
    metadata_values,
    record_id,
    seed_find,
    sql_only_store,
)


def setup_find(harness, **kwargs):
    schema = seed_find(harness.catalog, **kwargs)
    return sql_only_store(harness.catalog), schema


def expected_record(schema, i, encoded=False):
    return {
        "id": record_id(i), "schema_name": schema.name, "schema_version": 1,
        "state": "ready", "token": f"token-{i:04d}", "storage_id": "synthetic-storage",
        "object_key": f"synthetic/{i:04d}", "byte_size": 0, "digest": "0" * 32,
        "hash_algorithm": "xxh3_128",
        "handler_id": "synthetic.unavailable" if encoded else "file",
        "handler_version": 7 if encoded else 1, "version": 1,
        "metadata": schema.normalize_metadata(metadata_values(i)),
        **({"descriptor": deepcopy(DESCRIPTOR)} if encoded else {}),
    }


def selection(statement):
    return statement.startswith("SELECT b.id FROM ")


def association_rows(rows):
    return bool(rows and "prepared_schema_name" in rows[0])


@contextmanager
def observe_sql(monkeypatch, catalog, callback):
    original = Transaction.sql

    def wrapped(tx, statement, params=()):
        rows = original(tx, statement, params)
        if tx._catalog is catalog:
            replacement = callback(tx, statement, params, rows)
            if replacement is not None:
                return replacement
        return rows

    with monkeypatch.context() as patch:
        patch.setattr(Transaction, "sql", wrapped)
        yield


class FindChecks:
    @pytest.mark.parametrize("count", [0, 1, 100, 255, 256, 257, 1000])
    def test_page_bounds_and_independent_scalar_parity(self, harness, count):
        store, schema = setup_find(harness, count=count, encoding="mixed")
        rows = store.find(schema=schema, limit=max(1, count))
        expected = [expected_record(schema, i, i % 2 == 1) for i in range(count)]
        assert rows == expected
        # Scalar implementation remains a separate oracle; sample all chunk edges.
        for i in sorted({0, 1, 99, 254, 255, 256, count - 1} & set(range(count))):
            assert store.stat(record_id(i)) == expected[i]
            assert list(rows[i]["metadata"]) == list(store.stat(record_id(i))["metadata"])

    @pytest.mark.parametrize("encoding", ["absent", "empty", "mixed", "all"])
    def test_encoding_layout_shapes_and_no_codec_dependency(self, harness, encoding):
        store, schema = setup_find(harness, count=5, encoding=encoding)
        expected = [expected_record(schema, i, encoding == "all" or
                    (encoding == "mixed" and i % 2 == 1)) for i in range(5)]
        assert store.find(schema=schema) == expected
        assert [store.stat(record_id(i)) for i in range(5)] == expected
        # The SQL-only storage object rejects any attempted storage method access;
        # the registry contains no codecs, including the synthetic logical handler.
        assert store.find(schema=schema, where={"id": "missing"}) == []

    @pytest.mark.parametrize("encoding", ["absent", "empty", "mixed"])
    def test_sql_growth_is_per_chunk_and_bounded(self, harness, monkeypatch, encoding):
        store, schema = setup_find(harness, count=1000, encoding=encoding)
        counts, retrievals = {}, {}
        for size in [1, 100, 255, 256, 257, 1000]:
            calls = []

            def capture(tx, statement, params, rows):
                calls.append((statement, params, association_rows(rows)))

            with observe_sql(monkeypatch, harness.catalog, capture):
                assert len(store.find(schema=schema, limit=size)) == size
            counts[size] = len(calls)
            retrievals[size] = sum(is_association for _, _, is_association in calls)
            assert all(len(params) <= 256 for _, params, _ in calls)
            assert all(sql.lstrip().upper().startswith("SELECT") for sql, _, _ in calls)
        assert counts[1] == counts[100] == counts[255] == counts[256]
        assert 0 < counts[257] - counts[256] <= 3
        assert 0 < counts[1000] - counts[257] <= 6
        assert retrievals == {1: 1, 100: 1, 255: 1, 256: 1, 257: 2, 1000: 4}

    @pytest.mark.parametrize("op,value", [
        ("eq", 2), ("ne", 2), ("lt", 2), ("lte", 2), ("gt", 2), ("gte", 2),
        ("in", [1, 2]), ("not_in", [1, 2]), ("is_null", None), ("not_null", None),
    ])
    def test_predicates_match_independent_values(self, harness, op, value):
        store, schema = setup_find(harness, count=15, encoding="mixed")

        def matches(number):
            if op == "is_null":
                return number is None
            if op == "not_null":
                return number is not None
            if number is None:
                return False
            return {
                "eq": lambda: number == value, "ne": lambda: number != value,
                "lt": lambda: number < value, "lte": lambda: number <= value,
                "gt": lambda: number > value, "gte": lambda: number >= value,
                "in": lambda: number in value, "not_in": lambda: number not in value,
            }[op]()

        expected = [expected_record(schema, i, i % 2 == 1) for i in range(15)
                    if matches(metadata_values(i)["number"])]
        assert store.find(schema=schema, predicates=(Predicate("number", op, value),)) == expected
        rows = store.find(schema=schema, where={"測定 'field": "λ'quoted", "ok": True})
        assert [r["id"] for r in rows] == [record_id(i) for i in range(0, 15, 2)]

    @pytest.mark.parametrize("descending", [False, True])
    def test_nullable_order_and_both_cursor_forms(self, harness, descending):
        store, schema = setup_find(harness, count=30, encoding="mixed")
        indices = list(range(30))
        nonnull = [i for i in indices if metadata_values(i)["number"] is not None]
        nulls = [i for i in indices if metadata_values(i)["number"] is None]
        nonnull.sort(key=lambda i: (
            -metadata_values(i)["number"] if descending else metadata_values(i)["number"], i
        ))
        expected = nonnull + nulls if descending else nulls + nonnull
        order = (Order("number", descending=descending),)
        rows, after = [], None
        while page := store.find(schema=schema, order_by=order, limit=7, after=after):
            rows.extend(page)
            after = store.cursor(page[-1], schema=schema, order_by=order)
        assert [r["id"] for r in rows] == [record_id(i) for i in expected]
        assert store.find(schema=schema, after=record_id(27)) == [
            store.stat(record_id(28)), store.stat(record_id(29))
        ]
        with pytest.raises(ValidationError):
            store.find(schema=schema, after=after)
        with pytest.raises(ValidationError):
            store.find(schema=schema, after=record_id(1), order_by=order)

    @pytest.mark.parametrize("kwargs", [
        {"limit": 0}, {"limit": True}, {"limit": 1001}, {"limit": -1},
        {"order_by": (Order("missing"),)},
        {"order_by": (Order("id"), Order("label"))},
        {"predicates": (Predicate("number", "in", []),)},
        {"predicates": (Predicate("number", "eq", True),)},
        {"where": {"unknown": 1}},
        {"after": FindCursor("wrong", 1, (Order("id"),), ("x",))},
    ])
    def test_invalid_queries_keep_validation_errors(self, harness, kwargs):
        store, schema = setup_find(harness, count=0)
        with pytest.raises(ValidationError):
            store.find(schema=schema, **kwargs)

    def test_only_ready_records_are_selected(self, harness):
        store, schema = setup_find(harness, count=2)
        with harness.catalog.transaction() as tx:
            d = for_transaction(tx)
            b = d.bind
            tx.sql(f"INSERT INTO ms_blobs(id,schema_name,schema_version) VALUES({b},{b},1)",
                   ("unpublished", schema.name))
            tx.sql(f"INSERT INTO {d.quote(d.table(schema))}(id,label) VALUES({b},{b})",
                   ("unpublished", "a"))
        assert [r["id"] for r in store.find(schema=schema)] == [record_id(0), record_id(1)]

    def test_evolution_projects_requested_version_and_keeps_old_preparation(self, harness):
        store, schema = setup_find(harness, count=4, encoding="mixed")
        original = [store.stat(record_id(i)) for i in range(4)]
        fields = {key: value for key, value in schema.fields.items() if key != "number"}
        target = BlobSchema(schema.name, {**fields, "added": Text()}, version=2,
                            handlers=schema.handlers)
        plan = MetadataMigration("find-v2", schema, target, "remove-number-add-text",
                                 lambda row: {**{k: v for k, v in row.items() if k != "number"},
                                              "added": "new"})
        store.migrate(plan, batch_size=2, max_batches=1)
        assert store.find(schema=schema) == original[2:]
        evolved = store.find(schema=target)
        assert evolved == [store.stat(record_id(i)) for i in range(2)]
        assert all(r["metadata"]["added"] == "new" and "number" not in r["metadata"]
                   and r["schema_version"] == 2 and r["version"] == 2 for r in evolved)
        assert evolved[1]["descriptor"] == original[1]["descriptor"]
        store.migrate(plan)
        assert store.find(schema=schema) == []
        assert store.find(schema=target) == [store.stat(record_id(i)) for i in range(4)]

    def test_nested_results_are_detached_with_equal_descriptors(self, harness):
        store, schema = setup_find(harness, count=258, encoding="all")
        rows = store.find(schema=schema, limit=1000)
        rows[0]["descriptor"]["nested"]["items"][1]["value"] = "changed"
        rows[0]["descriptor"]["nested"]["items"].append("extra")
        rows[0]["metadata"]["label"] = "changed"
        for i in [1, 255, 256, 257]:
            assert rows[i] == expected_record(schema, i, True)
        assert store.find(schema=schema, limit=1) == [expected_record(schema, 0, True)]
        assert store.stat(record_id(0)) == expected_record(schema, 0, True)

    @pytest.mark.parametrize("damage", ["partial", "format", "trigger"])
    def test_extension_damage_is_checked_afresh_but_empty_pages_skip_it(
        self, harness, damage
    ):
        store, schema = setup_find(harness, count=2, encoding="mixed")
        assert len(store.find(schema=schema)) == 2
        if damage == "partial":
            harness.catalog.sql("DROP TABLE ms_encoding_format")
        elif damage == "format":
            harness.catalog.sql("DELETE FROM ms_encoding_format")
        else:
            suffix = " ON ms_encodings" if harness.catalog.adapter == "postgres" else ""
            harness.catalog.sql("DROP TRIGGER ms_encoding_update" + suffix)
        with pytest.raises(SchemaConflictError):
            store.find(schema=schema)
        with pytest.raises(SchemaConflictError):
            store.stat(record_id(0))
        assert store.find(schema=schema, where={"id": "missing"}) == []
        with pytest.raises(ValidationError) as caught:
            store.find(schema=schema, limit=0)
        assert type(caught.value) is ValidationError

    def test_invalid_descriptor_json_keeps_scalar_exception_and_reuses_catalog(self, harness):
        store, schema = setup_find(harness, count=1, encoding="all", descriptor="{broken")
        for read in [lambda: store.stat(record_id(0)), lambda: store.find(schema=schema)]:
            with pytest.raises(json.JSONDecodeError):
                read()
        assert harness.catalog.sql("SELECT 1 AS reusable", write=False) == [{"reusable": 1}]

    @pytest.mark.parametrize("damage,error", [
        ("blob_missing", NotFoundError), ("association_missing", IntegrityError),
        ("prepared_name", IntegrityError), ("prepared_version", IntegrityError),
        ("metadata_missing", IntegrityError), ("metadata_version", IntegrityError),
    ])
    def test_selected_row_error_types_match_scalar(self, harness, monkeypatch, damage, error):
        store, schema = setup_find(harness, count=1, encoding="empty")

        def corrupt(tx, statement, params, rows):
            # Result-boundary faults model inconsistent *selected* data without
            # disabling constraints or expecting selection's inner join to find
            # a preexisting missing metadata row.
            if association_rows(rows):
                if damage == "blob_missing":
                    return []
                for row in rows:
                    if damage == "association_missing":
                        row["token"] = None
                    elif damage == "prepared_name":
                        row["prepared_schema_name"] = "other"
                    elif damage == "prepared_version":
                        row["prepared_schema_version"] = 2
            elif rows and "label" in rows[0] and "version" in rows[0]:
                if damage == "metadata_missing":
                    return []
                if damage == "metadata_version":
                    rows[0]["schema_version"] = 2

        with observe_sql(monkeypatch, harness.catalog, corrupt):
            with pytest.raises(error):
                store.stat(record_id(0))
            with pytest.raises(error):
                store.find(schema=schema)
        assert store.find(schema=schema) == [expected_record(schema, 0)]

    @pytest.mark.parametrize("when", ["selection", "first_batch"])
    def test_writer_commit_preserves_later_chunk_snapshot(self, harness, monkeypatch, when):
        store, schema = setup_find(harness, count=300, encoding="mixed")
        expected = expected_record(schema, 256)
        changed = False

        def write_after_read(tx, statement, params, rows):
            nonlocal changed
            trigger = selection(statement) if when == "selection" else association_rows(rows)
            if trigger and not changed:
                changed = True
                with harness.open_writer() as writer:
                    sql_only_store(writer).update_metadata(
                        record_id(256), schema=schema, changes={"label": "after"},
                        expected_version=1,
                    )

        with observe_sql(monkeypatch, harness.catalog, write_after_read):
            rows = store.find(schema=schema, limit=257)
        assert changed and len(rows) == 257
        assert rows[-1] == expected
        later = store.find(schema=schema, where={"id": record_id(256)})
        assert later[0]["metadata"]["label"] == "after"
        assert later[0]["version"] == 2
        # Read-only cleanup restored an idle, writable catalog.
        store.update_metadata(record_id(256), schema=schema, changes={"label": "again"},
                              expected_version=2)

    def test_later_batch_failure_returns_nothing_and_cleans_up(self, harness, monkeypatch):
        store, schema = setup_find(harness, count=300, encoding="mixed")
        marker = ValidationError("injected second batch read failure")
        batches = 0
        original = Transaction.sql

        def fail(tx, statement, params=()):
            nonlocal batches
            if tx._catalog is not harness.catalog:
                return original(tx, statement, params)
            original_execute = tx._execute

            def execute(*args):
                nonlocal batches
                rows = original_execute(*args)
                if association_rows(rows):
                    batches += 1
                    if batches == 2:
                        raise marker
                return rows

            # Inject through the execution boundary so Transaction.sql still
            # performs its ordinary error translation and poisoning.
            tx._execute = execute
            try:
                return original(tx, statement, params)
            finally:
                tx._execute = original_execute

        sentinel = object()
        returned = sentinel
        with monkeypatch.context() as patch:
            patch.setattr(Transaction, "sql", fail)
            with pytest.raises(ValidationError) as caught:
                returned = store.find(schema=schema, limit=300)
        assert caught.value is marker
        assert returned is sentinel and batches == 2
        assert harness.catalog._active is None
        assert len(store.find(schema=schema, limit=300)) == 300

    @pytest.mark.parametrize("phase", ["commit", "cleanup"])
    def test_find_finalization_failure_withholds_page_and_quarantines(
        self, harness, monkeypatch, phase
    ):
        store, schema = setup_find(harness, count=257)
        backend = RuntimeError("injected finalization failure")
        catalog = harness.catalog
        loaded_ids = set()

        def loaded(tx, statement, params, rows):
            if rows and "label" in rows[0] and "version" in rows[0]:
                loaded_ids.update(row["id"] for row in rows)
        if catalog.adapter == "sqlite":
            if phase == "commit":
                def fail_commit(manager):
                    raise backend
                monkeypatch.setattr(catalog, "_commit", fail_commit)
            else:
                original_commit = catalog._commit

                def committed(manager):
                    original_commit(manager)
                    def fail_cleanup(value):
                        raise backend
                    monkeypatch.setattr(catalog, "_set_query_only", fail_cleanup)
                monkeypatch.setattr(catalog, "_commit", committed)
        else:
            # Inject at the public provider context boundary, retaining the
            # provider's structured phase/outcome evidence across translation.
            from melddb.errors import CommitError as ProviderCommitError

            original_transaction = catalog._connection.transaction

            class FailedFinalization:
                def __init__(self, manager):
                    self.manager = manager

                def __enter__(self):
                    return self.manager.__enter__()

                def __exit__(self, kind, value, tb):
                    self.manager.__exit__(kind, value, tb)
                    raise ProviderCommitError(
                        "injected provider finalization failure", phase=phase,
                        outcome="committed" if phase == "cleanup" else "unknown",
                        backend_error=backend,
                    ) from backend

            monkeypatch.setattr(catalog._connection, "transaction",
                                lambda **kw: FailedFinalization(original_transaction(**kw)))
        sentinel = object()
        returned = sentinel
        with observe_sql(monkeypatch, catalog, loaded):
            with pytest.raises(CommitError) as caught:
                returned = store.find(schema=schema, limit=257)
        assert loaded_ids == {record_id(i) for i in range(257)}
        assert returned is sentinel
        assert caught.value.phase == phase
        assert caught.value.outcome == ("committed" if phase == "cleanup" else "unknown")
        assert caught.value.backend_error is backend
        assert catalog._active is None
        with pytest.raises(TransactionError):
            store.find(schema=schema)
        with pytest.raises(TransactionError):
            catalog.sql("SELECT 1", write=False)


    def test_descending_selection_order_survives_multiple_chunks(self, harness):
        store, schema = setup_find(harness, count=300, encoding="mixed")
        rows = store.find(schema=schema, order_by=(Order("id", descending=True),), limit=257)
        assert rows == [expected_record(schema, i, i % 2 == 1) for i in range(299, 42, -1)]
        after = store.cursor(rows[-1], schema=schema, order_by=(Order("id", descending=True),))
        following = store.find(schema=schema, order_by=(Order("id", descending=True),),
                               after=after, limit=1000)
        assert [r["id"] for r in following] == [record_id(i) for i in range(42, -1, -1)]

    def test_empty_selection_does_not_inspect_encoding(self, harness, monkeypatch):
        from meldstore import encodings

        store, schema = setup_find(harness, count=1)

        def forbidden(tx):
            pytest.fail("Empty selection inspected the encoding extension")

        monkeypatch.setattr(encodings, "inspect", forbidden)
        assert store.find(schema=schema, where={"id": "missing"}) == []
        with pytest.raises(ValidationError):
            store.find(schema=schema, limit=0)

    def test_declaration_damage_is_not_cached_even_for_empty_selection(self, harness):
        store, schema = setup_find(harness, count=1)
        assert len(store.find(schema=schema)) == 1
        with harness.catalog.transaction() as tx:
            b = for_transaction(tx).bind
            tx.sql(f"UPDATE ms_schemas SET definition={b} WHERE name={b}",
                   (schema.definition + " ", schema.name))
        with pytest.raises(SchemaConflictError):
            store.find(schema=schema)
        with pytest.raises(SchemaConflictError):
            store.find(schema=schema, where={"id": "missing"})

    def test_schema_conflict_precedes_query_validation(self, harness):
        store, schema = setup_find(harness, count=0)
        wrong = BlobSchema(schema.name, {}, handlers=schema.handlers)
        with pytest.raises(SchemaConflictError):
            store.find(schema=wrong, limit=0)



    def test_publication_guard_drift_is_detected_on_next_call(self, harness):
        store, schema = setup_find(harness, count=2)
        expected = [store.stat(record_id(i)) for i in range(2)]
        assert store.find(schema=schema) == expected
        with harness.catalog.transaction() as tx:
            d = for_transaction(tx)
            guard = d.quote(d.table(schema) + "_ready")
            suffix = " ON ms_blobs" if d.postgres else ""
            tx.sql("DROP TRIGGER " + guard + suffix)
        # This is an existing _schema check on every adapter, including empty
        # selections; it does not introduce broader SQLite schema verification.
        with pytest.raises(SchemaConflictError):
            store.find(schema=schema)
        with pytest.raises(SchemaConflictError):
            store.find(schema=schema, where={"id": "missing"})


def harness_for(catalog, open_writer):
    return SimpleNamespace(catalog=catalog, open_writer=open_writer)
