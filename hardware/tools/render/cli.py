"""Generate and check a component's committed visual outputs (RFC 0021).

Usage:
    odw render list [PATH...] [--json]
    odw render update [PATH...] [--dry-run] [--json]
    odw render check [PATH...] [--regenerate] [--json]

PATH is a component (a folder with outputs.toml), a folder holding components, or a file inside one (the
component that holds it). Default: the current folder.

list    the components under PATH, their sources, and each adapter's program and version on this machine
        (--json: opendap.render-list/v1).
update  regenerates each component's outputs/ from its sources: the outputs, outputs/README.md and
        outputs/stamp.json. Files that come out the same are not rewritten; a 3D render within the tolerance of
        RFC 0021 §5.4 keeps its committed bytes. --dry-run names what it would write and writes nothing.
check   compares each component's stamp with its sources, options and outputs, without running KiCad, FreeCAD
        or Blender (the outputs-current hook). --regenerate updates the components first and fails when their
        outputs/ then differ from git's index (the outputs-regenerate hook, before a push).

outputs.toml (opendap.outputs/v1), at the component's root, beside export/:

    schema = "opendap.outputs/v1"

    [[source]]
    type = "kicad-sch"               # kicad-sch | kicad-pcb | freecad | blender
    path = "hardware/demo/demo.kicad_sch"   # relative to this folder
    # name = "demo"                  # the folder under outputs/ (default: the file's stem)
    # kicad_theme = "lib/colour-themes/opendap.json"   # KiCad sources: a colour theme file in the component

    [[source]]
    type = "kicad-pcb"
    path = "hardware/demo/demo.kicad_pcb"
    # layers = ["F.Cu", "B.Cu"]      # default: every copper layer, F/B.Silkscreen, F/B.Fab; each with Edge.Cuts
    # views = ["top", "bottom", "iso-front", "iso-back"]
    # size = [1600, 1200]
    # glb = false

    # freecad: glb (default true); views must stay empty (freecadcmd has no 3D view)
    # blender: views (front, back, top, bottom, left, right, iso; default front, top, iso), size, glb (true)

Outputs: outputs/<name>/schematic.pdf, schematic/<sheet>.svg, pcb/<layer>.svg, render/<view>.png, <name>.glb.

Examples:
    odw render list --json
    odw render update board
    odw render check --json
    odw render check --regenerate board/hardware/t1-main-board/t1-main-board.kicad_pcb

Environment: KICAD_CLI, KICAD_APP, KICAD10_3DMODEL_DIR, ODAP_FREECADCMD, ODAP_BLENDER.
Exit status: 0 current or written, 1 stale, failed or a program missing, 2 usage error.
Standard library only (Python 3.11 or later).
"""
import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from clihost import load as load_clikit, summary  # noqa: E402

import core  # noqa: E402

TOOL = "odw render"
clikit = load_clikit(TOOL)
LIST_SCHEMA = "opendap.render-list/v1"


def parser():
    p = clikit.Parser(prog=TOOL, summary=summary(clikit, "render"), add_help=False,
                      description=__doc__.split("\n", 1)[1].split("\nExamples:")[0],
                      formatter_class=__import__("argparse").RawDescriptionHelpFormatter)
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("verb", nargs="?", choices=["list", "update", "check"])
    p.add_argument("paths", nargs="*", metavar="PATH")
    p.add_argument("--json", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--regenerate", action="store_true")
    return p


def _rel(p):
    r = os.path.relpath(p)
    return p if r.startswith("..") else r


def _result(results, path):
    return {"schema": "opendap.check-result/v1", "tool": {"name": TOOL},
            "subject": {"repo": os.path.basename(_top(path) or path), "path": _rel(path)},
            "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "results": [{"id": i, "verdict": v, "message": m} for i, v, m in results]}


def _top(path):
    r = subprocess.run(["git", "-C", path if os.path.isdir(path) else os.path.dirname(path) or ".", "rev-parse",
                        "--show-toplevel"], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    return r.stdout.strip() if r.returncode == 0 else None


def _components(paths):
    comps = []
    for p in paths or ["."]:
        comps += [c for c in core.components(p) if c not in comps]
    return comps


def cmd_list(a, log):
    items = []
    for c in _components(a.paths):
        items.append({"component": _rel(c), "sources": [{k: s[k] for k in ("name", "type", "path")}
                                                         for s in core.load(c)]})
    progs = [{"type": t, "program": p, "version": v, "available": v is not None} for t, p, v in core.programs()]
    if a.json:
        print(json.dumps({"schema": LIST_SCHEMA, "components": items, "adapters": progs}, indent=1))
        return 0
    for it in items:
        print("%s: %s" % (it["component"], ", ".join("%s (%s)" % (s["path"], s["type"]) for s in it["sources"])))
    for p in progs:
        print("adapter %s: %s %s" % (p["type"], p["program"], p["version"] or "not available"))
    return 0


def cmd_update(a, log):
    comps, results = _components(a.paths), []
    for c in comps:
        for rel, action in core.update(c, log, a.dry_run):
            if action != "unchanged":
                verb = ("would be " + action) if a.dry_run else action
                results.append(("%s/outputs/%s" % (_rel(c), rel), "info", verb))
                log("%s: %s/outputs/%s %s" % (TOOL, _rel(c), rel, verb))
    if a.json:
        print(json.dumps(_result(results or [("outputs", "pass", "nothing to write")], "."), indent=1))
    return 0


def _staged_diff(comp):
    out = os.path.join(comp, core.FOLDER)
    r = subprocess.run(["git", "-C", comp, "status", "--porcelain", "--untracked-files=all", "--", core.FOLDER],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    return [ln[3:] for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else [out]


def cmd_check(a, log):
    comps, results = _components(a.paths), []
    for c in comps:
        if a.regenerate:
            core.update(c, log)
            for f in _staged_diff(c):
                results.append((f, "fail", "regenerated output differs from the commit"))
            continue
        for i, v, m in core.check(c):
            results.append(("%s/%s" % (_rel(c), i), v, m))
    failed = [r for r in results if r[1] == "fail"]
    if a.json:
        print(json.dumps(_result(results or [("outputs", "pass", "no components")], "."), indent=1))
    for i, _, m in failed:
        print("%s: %s: %s" % (TOOL, i, m), file=sys.stderr)
    if failed and a.regenerate:
        print("%s: commit the regenerated outputs (git add %s) and push again" % (
            TOOL, " ".join(_rel(os.path.join(c, core.FOLDER)) for c in comps)), file=sys.stderr)
    elif failed:
        print("%s: regenerate them: odw render update %s" % (TOOL, " ".join(_rel(c) for c in comps)), file=sys.stderr)
    return 1 if failed else 0


def body(argv):
    p = parser()
    a = p.parse_args(argv)
    if a.help or not a.verb and not argv:
        print(p.format_help() + "\nExamples:" + __doc__.split("\nExamples:")[1].split("\nEnvironment:")[0] + "\n")
        return 0
    if not a.verb:
        raise clikit.CliError("input.invalid", "a verb is needed: list, update or check",
                              "the usage is in %s --help" % TOOL, "%s --help" % TOOL, 2)
    if a.dry_run and a.verb != "update" or a.regenerate and a.verb != "check":
        raise clikit.CliError("input.invalid", "--dry-run goes with update, --regenerate with check",
                              "the usage is in %s --help" % TOOL, "%s --help" % TOOL, 2)

    def log(s):
        print(s, file=sys.stderr)
    try:
        return {"list": cmd_list, "update": cmd_update, "check": cmd_check}[a.verb](a, log)
    except core.Problem as e:
        raise clikit.CliError(e.code, e.message, e.hint, e.fix, e.status) from None


def main(argv):
    return clikit.main(TOOL, body, argv)
