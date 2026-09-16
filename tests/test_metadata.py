from datetime import datetime, timedelta, timezone

import obstore
import pytest

from meldstore import (
    BlobSchema,
    Boolean,
    Catalog,
    ConflictError,
    ConstraintError,
    Float,
    Index,
    Integer,
    LocalStorage,
    NotFoundError,
    Order,
    Predicate,
    Store,
    Text,
    Timestamp,
    ValidationError,
)


@pytest.fixture(params=["sqlite", "melddb"])
def setup(request, tmp_path):
    schema = BlobSchema(
        "測定 'data",
        {
            "label": Text(required=True),
            "number": Integer(),
            "score": Float(),
            "at": Timestamp(),
            "fixed": Text(immutable=True),
            "ok": Boolean(),
        },
        indexes=(Index("label", "at"),),
    )
    source = tmp_path / "source"
    source.write_bytes(b"immutable bytes")
    with Catalog(tmp_path / "catalog.db", adapter=request.param) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        store.install_schema(schema)
        yield store, schema, source


def add(setup, id, **metadata):
    store, schema, source = setup
    return store.import_file(source, schema=schema, metadata={"label": "a", **metadata}, id=id)


def test_interval_query_preserves_microseconds_and_timezone(setup):
    store, schema, _ = setup
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(3):
        add(setup, str(i), at=start + timedelta(microseconds=i))
    lower = (start + timedelta(microseconds=1)).astimezone(timezone(timedelta(hours=-5)))
    rows = store.find(
        schema=schema,
        where={"label": "a"},
        predicates=(
            Predicate("at", "gte", lower),
            Predicate("at", "lt", start + timedelta(microseconds=2)),
        ),
    )
    assert [r["id"] for r in rows] == ["1"]
    assert rows[0]["metadata"]["at"] == "2026-01-01T00:00:00.000001Z"
    with pytest.raises(ValidationError):
        store.find(schema=schema, where={"at": datetime(2026, 1, 1)})


@pytest.mark.parametrize(
    "descending,expected", [(False, ["a", "b", "c", "d"]), (True, ["d", "b", "c", "a"])]
)
def test_nullable_ordering_and_tie_breaker_pages(setup, descending, expected):
    store, schema, _ = setup
    for id, number in zip("abcd", [None, 1, 1, 2]):
        add(setup, id, number=number)
    ordering = (Order("number", descending=descending),)
    ids, after = [], None
    while page := store.find(schema=schema, order_by=ordering, limit=1, after=after):
        ids.append(page[0]["id"])
        after = store.cursor(page[-1], schema=schema, order_by=ordering)
    assert ids == expected
    with pytest.raises(ValidationError):
        store.find(schema=schema, after=after)


def test_scalar_predicates_and_validation(setup):
    store, schema, _ = setup
    add(setup, "one", number=1, ok=True, score=1.5)
    add(setup, "two", number=2, ok=False, score=3.0)
    add(setup, "null")
    assert [
        r["id"]
        for r in store.find(
            schema=schema,
            predicates=(Predicate("number", "in", [1, 2]), Predicate("score", "lte", 2.0)),
        )
    ] == ["one"]
    assert [
        r["id"] for r in store.find(schema=schema, predicates=(Predicate("number", "is_null"),))
    ] == ["null"]
    for predicate in [
        Predicate("number", "eq", True),
        Predicate("number", "eq", 2**63),
        Predicate("at", "gte", "2026-01-01"),
        Predicate("score", "eq", float("nan")),
        Predicate("missing", "eq", 1),
        Predicate("number", "in", [None]),
        Predicate("label", "DROP TABLE", "x"),
    ]:
        with pytest.raises(ValidationError):
            store.find(schema=schema, predicates=(predicate,))


def test_guarded_updates_are_metadata_only_and_return_new_version(setup, monkeypatch):
    store, schema, _ = setup
    original = add(setup, "edit", number=1, fixed="keep")

    def forbidden(*args, **kwargs):
        pytest.fail("Metadata operation called object storage")

    monkeypatch.setattr(obstore, "get", forbidden)
    monkeypatch.setattr(obstore, "put", forbidden)
    edited = store.update_metadata("edit", schema=schema, changes={"number": 2}, expected_version=1)
    assert edited["version"] == 2
    assert edited["metadata"]["number"] == 2
    assert (edited["digest"], edited["object_key"]) == (original["digest"], original["object_key"])
    with pytest.raises(ConflictError):
        store.update_metadata("edit", schema=schema, changes={"number": 3}, expected_version=1)
    for changes in [{"fixed": "changed"}, {"id": "other"}, {"number": True}, {"label": None}, {}]:
        with pytest.raises(ValidationError):
            store.update_metadata("edit", schema=schema, changes=changes, expected_version=2)
    with pytest.raises(NotFoundError):
        store.update_metadata("missing", schema=schema, changes={"number": 1}, expected_version=1)


def test_shared_update_rolls_back_and_sql_version_guard_remains(setup):
    from meldstore import quote_identifier

    store, schema, _ = setup
    add(setup, "edit")
    with pytest.raises(RuntimeError):
        with store.catalog.transaction() as tx:
            store.update_metadata(
                "edit", schema=schema, changes={"number": 9}, expected_version=1, tx=tx
            )
            tx.sql("CREATE TABLE application_change (note TEXT)")
            raise RuntimeError("abort")
    assert store.stat("edit")["version"] == 1
    with pytest.raises(ConstraintError):
        store.catalog.sql(
            f"UPDATE {quote_identifier(schema.table_name)} SET number=3 WHERE id='edit'"
        )


def test_typed_cursors_bounds_and_composite_index(setup):
    from meldstore import quote_identifier
    from meldstore.query import compile_query

    store, schema, _ = setup
    instant = datetime(2026, 1, 1, tzinfo=timezone.utc)
    add(setup, "a", at=instant, ok=False, number=-(2**63))
    add(setup, "b", at=instant + timedelta(microseconds=1), ok=True, number=2**63 - 1)
    for order in [(Order("at"),), (Order("ok"),)]:
        first = store.find(schema=schema, order_by=order, limit=1)
        after = store.cursor(first[0], schema=schema, order_by=order)
        assert store.find(schema=schema, order_by=order, after=after)[0]["id"] == "b"
    for kwargs in [
        {"limit": 0},
        {"limit": True},
        {"limit": 1001},
        {"order_by": (Order("missing"),)},
        {"order_by": (Order("id"), Order("label"))},
        {"predicates": (Predicate("label", []),)},
        {"predicates": (Predicate("number", "in", []),)},
        {"where": {"number": -(2**63) - 1}},
    ]:
        with pytest.raises(ValidationError):
            store.find(schema=schema, **kwargs)
    sql, params = compile_query(
        schema,
        where={"label": "a"},
        predicates=(Predicate("at", "gte", instant),),
        order_by=(Order("at"),),
        after=None,
        limit=100,
    )
    details = store.catalog.sql("EXPLAIN QUERY PLAN " + sql, params)
    assert any(schema.table_name + "_i0" in row["detail"] for row in details)
    # SQL shape checks remain separate from Python's strict calendar validation.
    with pytest.raises(ConstraintError):
        store.catalog.sql(
            f"UPDATE {quote_identifier(schema.table_name)} SET at='not a timestamp',version=version+1 WHERE id='a'"
        )
