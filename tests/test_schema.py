from datetime import datetime, timedelta, timezone

import pytest

from meldstore import BlobSchema, Boolean, Float, Index, Integer, Text, Timestamp, ValidationError


def test_declarations_are_defensive_and_canonical():
    fields = {"title": Text(required=True)}
    schema = BlobSchema("dataset", fields, handlers=["file", "numpy.npz"])
    fields["extra"] = Text()
    assert list(schema.fields) == ["title"]
    with pytest.raises(TypeError):
        schema.fields["extra"] = Text()
    other = BlobSchema("dataset", {"title": Text(required=True)}, handlers=["numpy.npz", "file"])
    assert other.definition == schema.definition


@pytest.mark.parametrize(
    "fields",
    [
        {"ID": Text()},
        {"a": Text(), "A": Text()},
        {"ms_state": Text()},
        {"": Text()},
        {"bad\x00name": Text()},
        {"a": "text"},
    ],
)
def test_invalid_fields(fields):
    with pytest.raises(ValidationError):
        BlobSchema("dataset", fields)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"version": True},
        {"version": 0},
        {"handlers": []},
        {"handlers": "file"},
        {"indexes": [Index("missing")]},
        {"validator_id": "v1"},
        {"payload_validator": lambda x: None},
    ],
)
def test_invalid_declarations(kwargs):
    with pytest.raises(ValidationError):
        BlobSchema("dataset", {}, **kwargs)


def test_metadata_normalization():
    schema = BlobSchema(
        "generic",
        {
            "count": Integer(required=True),
            "score": Float(),
            "valid": Boolean(),
            "at": Timestamp(),
            "note": Text(),
        },
    )
    instant = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=timezone(timedelta(hours=2)))
    values = schema.normalize_metadata(
        {"count": 2**63 - 1, "score": 1.5, "valid": True, "at": instant}
    )
    assert values == {
        "count": 2**63 - 1,
        "score": 1.5,
        "valid": 1,
        "at": "2026-01-02T01:04:05.123456Z",
        "note": None,
    }
    with pytest.raises(ValidationError):
        schema.normalize_metadata({"count": 1, "unexpected": "value"})
    with pytest.raises(ValidationError):
        schema.normalize_metadata({})


@pytest.mark.parametrize(
    "spec,value",
    [
        (Integer(), True),
        (Integer(), 2**63),
        (Integer(), "1"),
        (Float(), float("inf")),
        (Float(), float("nan")),
        (Float(), 1),
        (Boolean(), 1),
        (Text(), 3),
        (Timestamp(), datetime(2026, 1, 1)),
        (Timestamp(), "2026-01-01T00:00:00.000000Z"),
    ],
)
def test_no_implicit_coercion(spec, value):
    with pytest.raises(ValidationError):
        spec.normalize(value)


def test_payload_validation_is_explicit():
    seen = []
    schema = BlobSchema(
        "generic", {}, validator_id="application.validate.v1", payload_validator=seen.append
    )
    assert "application.validate.v1" in schema.definition
    schema.normalize_metadata({})
    assert seen == []
    schema.validate_payload("value")
    assert seen == ["value"]
