#!/usr/bin/env python3
"""kicad-check: run KiCad ERC or DRC on the KiCad projects that contain the given files.

Usage:
    kicad-check.py erc [--json] FILE...     ERC (kicad-cli sch erc) on each schematic
    kicad-check.py drc [--json] FILE...     DRC with schematic parity (kicad-cli pcb drc) on each board

The engine of the kicad-erc and kicad-drc pre-commit hooks (.pre-commit-hooks.yaml), which pass the changed
files. Only a file with a same-named .kicad_pro next to it is checked (a project's sub-sheets are checked
through its root sheet). Errors only: kicad-cli runs with --severity-error --exit-code-violations. A failing
project prints "KiCad ERC failed: FILE" (or DRC) and the first 60 lines of kicad-cli's report on stderr.
--json also prints an opendap.check-result/v1 document on stdout: one result per file (pass, fail, skip for a
file outside a project, error when kicad-cli gave no report) and one per violation, with its type as `rule`.

kicad-cli is $KICAD_CLI, else KiCad.app's ($KICAD_APP, default /Applications/KiCad/KiCad.app), else the
first on PATH. Python 3.9 or later, standard library only.

Exit status: 0 no errors, 1 a check failed or kicad-cli is missing, 2 usage error.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

TOOL = "kicad-check"
SCHEMA = "opendap.check-result/v1"
REPORT_LINES = 60
COMMANDS = {
    "erc": ["sch", "erc", "--severity-error", "--exit-code-violations"],
    "drc": ["pcb", "drc", "--schematic-parity", "--severity-error", "--exit-code-violations"],
}
SUMMARY = "run KiCad ERC or DRC on the KiCad projects that contain the given files"


class Usage(Exception):
    pass


def kicad_cli():
    """$KICAD_CLI, else KiCad.app's kicad-cli, else the first on PATH; None when there is none."""
    if os.environ.get("KICAD_CLI"):
        return os.environ["KICAD_CLI"]
    app = os.environ.get("KICAD_APP") or "/Applications/KiCad/KiCad.app"
    for c in (os.path.join(app, "Contents", "MacOS", "kicad-cli"), shutil.which("kicad-cli")):
        if c and os.access(c, os.X_OK):
            return c
    return None


def in_project(path):
    base = os.path.splitext(os.path.basename(path))[0]
    return os.path.isfile(os.path.join(os.path.dirname(path), base + ".kicad_pro"))


def violations(mode, report):
    """The violations of a kicad-cli JSON report: (type, message)."""
    if mode == "erc":
        found = [(s.get("path", "/"), v) for s in report.get("sheets", []) for v in s.get("violations", [])]
    else:
        found = [("", v) for key in ("violations", "unconnected_items", "schematic_parity")
                 for v in report.get(key, [])]
    out = []
    for where, v in found:
        items = ", ".join(i.get("description", "") for i in v.get("items", []) if i.get("description"))
        text = v.get("description") or v.get("type") or "violation"
        prefix = f"{where}: " if where else ""
        out.append((v.get("type", "violation"), prefix + text + (f" ({items})" if items else "")))
    return out


def check_file(cli, mode, path, as_json):
    """Run kicad-cli on one file. Returns (failed, results)."""
    rid = f"kicad-{mode}:{path}"
    if not in_project(path):
        return False, [{"id": rid, "verdict": "skip", "message": "not the root of a KiCad project (no "
                        "same-named .kicad_pro next to it)", "location": {"path": path}}]
    with tempfile.TemporaryDirectory() as tmp:
        report = os.path.join(tmp, "report")
        fmt = ["--format", "json"] if as_json else []
        rc = subprocess.run([cli, *COMMANDS[mode], *fmt, "-o", report, path], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL).returncode
        text = open(report, encoding="utf-8", errors="replace").read() if os.path.exists(report) else ""
    if rc == 0:
        return False, [{"id": rid, "verdict": "pass", "message": f"no KiCad {mode.upper()} errors",
                        "location": {"path": path}}]
    print(f"KiCad {mode.upper()} failed: {path}", file=sys.stderr)
    found = []
    if as_json:
        try:
            found = violations(mode, json.loads(text))
        except ValueError:
            found = []
        for kind, msg in found[:REPORT_LINES]:
            print(f"{kind}: {msg}", file=sys.stderr)
    else:
        sys.stderr.write("".join(text.splitlines(True)[:REPORT_LINES]))
    head = {"id": rid, "verdict": "fail" if found or text else "error",
            "message": f"{len(found)} KiCad {mode.upper()} error(s)" if found else
            f"kicad-cli {mode} exited {rc}" + ("" if text else " without a report"), "location": {"path": path}}
    return True, [head] + [{"id": f"{rid}#{n}", "rule": kind, "verdict": "fail", "message": msg,
                            "location": {"path": path}} for n, (kind, msg) in enumerate(found, 1)]


def repo_name():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             env=env).stdout.strip()
    except OSError:
        top = ""
    return os.path.basename(top) if top else os.path.basename(os.getcwd())


def parse(argv):
    if not argv:
        raise Usage("no mode given (erc or drc)")
    mode, rest = argv[0], argv[1:]
    if mode not in COMMANDS:
        raise Usage(f"unknown mode {mode} (erc or drc)")
    as_json = "--json" in rest
    files = [a for a in rest if a != "--json"]
    for a in files:
        if a.startswith("-"):
            raise Usage(f"unknown flag {a}")
    return mode, as_json, files


def main(argv):
    if argv and argv[0] in ("-h", "--help", "help"):
        print(f"{TOOL}: {SUMMARY}\n" + __doc__.split("\n", 1)[1])
        return 0
    try:
        mode, as_json, files = parse(argv)
    except Usage as e:
        print(f"{TOOL}: error input.invalid: {e}\n  hint: the usage is in kicad-check.py --help\n"
              f"  fix: kicad-check.py --help", file=sys.stderr)
        return 2
    cli = kicad_cli()
    if not cli:
        print("kicad-cli not found. Install KiCad 10 or set KICAD_CLI.", file=sys.stderr)
        return 1
    status, results = 0, []
    for f in files:
        failed, res = check_file(cli, mode, f, as_json)
        status = 1 if failed else status
        results += res
    if as_json:
        print(json.dumps({"schema": SCHEMA, "tool": {"name": f"kicad-{mode}"}, "subject": {"repo": repo_name()},
                          "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "results": results}, indent=2, ensure_ascii=False))
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
