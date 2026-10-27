"""Every schema is valid JSON Schema 2020-12; every good fixture passes it and every bad fixture fails it."""
import pathlib

import pytest

from odw import schemas

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "schemas"
NAMES = ["task", "decisions", "check", "tools", "check-result", "matrix", "requirements", "verification",
         "rename-plan", "cli-inventory"]


def cases(kind):
    for name in NAMES:
        for path in sorted((FIXTURES / name / kind).glob("*")):
            yield pytest.param(name, path, id=f"{name}/{kind}/{path.name}")


@pytest.mark.parametrize("name", NAMES)
def test_schema_is_valid(name):
    schemas.check_schema(name)


@pytest.mark.parametrize("name, path", list(cases("good")))
def test_good_fixture_passes(name, path):
    assert schemas.errors(schemas.load_document(path), name) == []


@pytest.mark.parametrize("name, path", list(cases("bad")))
def test_bad_fixture_fails(name, path):
    assert schemas.errors(schemas.load_document(path), name) != []


def test_every_schema_has_good_and_bad_fixtures():
    for name in NAMES:
        assert list((FIXTURES / name / "good").glob("*")), name
        assert list((FIXTURES / name / "bad").glob("*")), name


def test_unknown_schema_name_is_an_error():
    with pytest.raises(KeyError):
        schemas.schema("no-such-schema")
