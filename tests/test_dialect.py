import pytest

from meldstore import BlobSchema, Text, ValidationError
from meldstore.dialect import POSTGRES, SQLITE
from meldstore.query import compile_query


def test_generated_bindings_do_not_rewrite_identifiers_or_values():
    field = 'question?%s"'
    schema = BlobSchema("x", {field: Text()})
    for dialect in [SQLITE, POSTGRES]:
        sql, params = compile_query(
            schema,
            dialect=dialect,
            where={field: "literal?%s"},
            predicates=(),
            order_by=(),
            after=None,
            limit=1,
        )
        assert dialect.quote(field) in sql
        assert params == ["x", 1, 1, "literal?%s", 1]
        assert sql.endswith("LIMIT " + dialect.bind)


def test_identifier_mapping_preserves_sqlite_names_and_is_bounded():
    schemas = [BlobSchema("x" * n, {}) for n in range(1, 49)]
    names = [s.table_name_for("postgres") for s in schemas]
    assert len(set(names)) == len(names)
    assert max(len(n) for n in names) <= 48
    for s in schemas:
        assert s.table_name_for("sqlite") == s.table_name_for("melddb") == s.table_name
    with pytest.raises(ValidationError):
        schemas[0].table_name_for("unknown")


@pytest.mark.parametrize("value", ["bad\x00text", "bad\ud800text"])
def test_postgres_values_fail_before_sql(value):
    with pytest.raises(ValidationError):
        POSTGRES.value(value)
    assert SQLITE.value(value) == value


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_pending_constraint_validation_rolls_back_and_poisons(tmp_path, adapter):
    from meldstore import Catalog, ConstraintError, TransactionError

    with Catalog(tmp_path / "constraints.db", adapter=adapter) as catalog:
        catalog.sql("CREATE TABLE app_parent(id INTEGER PRIMARY KEY)")
        catalog.sql(
            "CREATE TABLE app_child(id INTEGER REFERENCES app_parent(id) DEFERRABLE INITIALLY DEFERRED)"
        )
        with pytest.raises(TransactionError):
            with catalog.transaction() as tx:
                tx.sql("INSERT INTO app_child VALUES(1)")
                with pytest.raises(ConstraintError):
                    tx.validate_constraints()
        assert catalog.sql("SELECT * FROM app_child") == []
        with pytest.raises(RuntimeError):
            with catalog.transaction() as tx:
                tx.sql("INSERT INTO app_child VALUES(1)")
                tx.sql("INSERT INTO app_parent VALUES(1)")
                assert tx.validate_constraints() is None
                raise RuntimeError("dry run")
        assert catalog.sql("SELECT * FROM app_parent") == []
        with pytest.raises(ValidationError):
            with catalog.transaction(write=False) as tx:
                tx.validate_constraints()
