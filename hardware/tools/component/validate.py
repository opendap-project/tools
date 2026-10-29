"""odw component: validate a component's export/component.yaml against the export contract.

Usage:
    odw component validate [--json] [PATH...]

PATH is a component.yaml, an export/ folder, or a component repository (its export/component.yaml).
Default: the current folder.

Checks: the schema (schemas/component.schema.json, spec docs/specs/component-export.md); interface
names and file paths listed once; every listed file exists inside export/ (no links out of it);
files in export/ that are not listed are reported as warnings (README.md and component.yaml are
not exported files).

Examples:
    odw component validate --json .
    odw component validate export/component.yaml

Errors carry a stable dotted code, a hint and a fix (toolkit-host's odw.clikit; with --json, also an
opendap.error/v1 document on stdout).

Exit status: 0 valid, 1 invalid, 2 usage error.
"""
import json
import os
import sys

import jsonschema
import yaml

HERE = os.path.dirname(os.path.realpath(__file__))
SCHEMA = os.path.join(HERE, "..", "..", "schemas", "component.schema.json")
sys.path.insert(0, os.path.join(HERE, ".."))
from clihost import load, summary  # noqa: E402

TOOL = "odw component"
clikit = load(TOOL)
NOT_EXPORTED = {"component.yaml", "README.md", ".DS_Store"}


def locate(path):
    if os.path.isdir(path):
        for cand in (os.path.join(path, "component.yaml"), os.path.join(path, "export", "component.yaml")):
            if os.path.isfile(cand):
                return cand
        return os.path.join(path, "export", "component.yaml")
    return path


def validate(path, schema):
    errors, warnings = [], []
    if not os.path.isfile(path):
        return [f"{path}: not found"], warnings
    try:
        with open(path) as f:
            doc = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"{path}: not valid YAML: {e}"], warnings
    validator = jsonschema.Draft202012Validator(schema)
    for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in e.absolute_path) or "(top level)"
        errors.append(f"{where}: {e.message}")
    if errors or not isinstance(doc, dict):
        return errors, warnings
    names = [i["name"] for i in doc["interfaces"]]
    for n in sorted({n for n in names if names.count(n) > 1}):
        errors.append(f"interfaces: {n} is listed more than once")
    export = os.path.dirname(os.path.abspath(path))
    listed = [f["path"] for f in doc["files"]]
    for p in sorted({p for p in listed if listed.count(p) > 1}):
        errors.append(f"files: {p} is listed more than once")
    for p in listed:
        full = os.path.join(export, p)
        if os.path.basename(p) in NOT_EXPORTED and os.path.dirname(p) == "":
            errors.append(f"files: {p} is not an exported file")
        elif not os.path.realpath(full).startswith(os.path.realpath(export) + os.sep):
            errors.append(f"files: {p} resolves outside export/")
        elif not os.path.isfile(full):
            errors.append(f"files: {p} does not exist in export/")
    present = []
    for root, dirs, files in os.walk(export):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), export)
            if rel not in NOT_EXPORTED:
                present.append(rel)
    for rel in sorted(set(present) - set(listed)):
        warnings.append(f"{rel} is in export/ but not listed in files")
    return errors, warnings


def main(argv):
    if argv and argv[0] in ("-h", "--help", "help"):
        print(f"{TOOL}: {summary(clikit, 'component')}\n\n" + __doc__.split("\n", 1)[1].strip())
        return 0
    as_json = "--json" in argv

    def usage(message):
        return clikit.report(TOOL, clikit.CliError("input.invalid", message, "usage: odw component validate "
                                                   "[--json] [PATH...]", f"{TOOL} --help", status=2), as_json)
    if not argv or argv[0] != "validate":
        return usage(f"unknown command {argv[0]}" if argv else "no command (validate)")
    args = argv[1:]
    paths = [a for a in args if a != "--json"]
    flags = [p for p in paths if p.startswith("-")]
    if flags:
        return usage(f"unknown flag {flags[0]}")
    with open(SCHEMA) as f:
        schema = json.load(f)
    items = []
    for p in paths or ["."]:
        target = locate(p)
        errors, warnings = validate(target, schema)
        items.append({"path": target, "valid": not errors, "errors": errors, "warnings": warnings})
    ok = all(i["valid"] for i in items)
    if as_json:
        print(json.dumps({"schema": "opendap.component-validate/v1", "ok": ok, "items": items}, indent=2))
    else:
        for i in items:
            print(f"{'valid' if i['valid'] else 'INVALID'}  {os.path.relpath(i['path'])}")
            for e in i["errors"]:
                print(f"  error: {e}")
            for w in i["warnings"]:
                print(f"  warning: {w}")
    return 0 if ok else 1

