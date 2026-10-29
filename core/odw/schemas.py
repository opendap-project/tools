"""The workspace schemas (schemas/*.schema.json) and validation of TOML or JSON documents against them."""
import functools
import json
import pathlib
import tomllib

import jsonschema

# Installed, the schemas ship inside the package (odw/schemas); in a checkout they are the schemas domain at the
# tools root (schemas/, HL-71), the nearest folder above the package that holds them.
_HERE = pathlib.Path(__file__).resolve().parent
SCHEMAS = _HERE / "schemas" if (_HERE / "schemas").is_dir() else next(
    (p / "schemas" for p in _HERE.parents if (p / "schemas" / "task.schema.json").is_file()), _HERE / "schemas")


@functools.cache
def schema(name):
    """The schema called `name` (`task`, `matrix`, …); KeyError when there is none."""
    path = SCHEMAS / f"{name}.schema.json"
    if not path.is_file():
        raise KeyError(name)
    return json.loads(path.read_text())


def names():
    return sorted(p.name.removesuffix(".schema.json") for p in SCHEMAS.glob("*.schema.json"))


def check_schema(name):
    """Raise jsonschema.SchemaError when the schema itself is not valid JSON Schema 2020-12."""
    jsonschema.Draft202012Validator.check_schema(schema(name))


def load_document(path):
    """A TOML or JSON document as Python data."""
    path = pathlib.Path(path)
    if path.suffix == ".toml":
        return tomllib.loads(path.read_text())
    return json.loads(path.read_text())


def errors(document, name):
    """Every validation error of `document` against schema `name`, as 'path: message' strings."""
    validator = jsonschema.Draft202012Validator(schema(name), format_checker=jsonschema.FormatChecker())
    out = []
    for e in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in e.absolute_path) or "(root)"
        out.append(f"{where}: {e.message}")
    return out
