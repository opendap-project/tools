"""odw rename config: a cut root's shared configuration (RFC 0017 §7 step 4), after `odapkit rename apply`.

Usage: odapkit rename config ROOT [--workspace DIR] [--map TSV] [--root-dir DIR] [--landing REPO=DIR] [--dry-run]
       [--json | --text]

Each folder brought its repository's configuration. The step hoists what the root shares (`LICENSES/` as the union,
`.claude/`, `.github/`, `.markdownlint-cli2.jsonc`), joins the pre-commit configurations into one with each folder's
hooks scoped to it (odw/rename_precommit.py), moves a hook manifest to the root, keeps one managed CLAUDE.md block
(the root's), and writes the root README, CLAUDE.md and REUSE.toml when the root has none. One local commit; it
never pushes. `odapkit sync --apply` then renders the root's managed blocks. Exit status: 0 done or nothing to do,
1 refused, 2 usage. Spec: docs/specs/rename.md.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import posixpath
import re
import subprocess
import sys

from odw import audit, clikit, rename_map, rename_precommit as rp, rename_rewrite as rw, workspace

TOOL = "odapkit rename config"
SCHEMA = "opendap.rename-config/v1"
HOISTED = ("LICENSES/", ".claude/", ".github/", ".markdownlint-cli2.jsonc")
PRECOMMIT, MANIFEST = ".pre-commit-config.yaml", ".pre-commit-hooks.yaml"
BLOCK = re.compile(r"\n?<!-- >>> odapkit sync:.*?<!-- <<< odapkit sync -->\n", re.S)
RFC = "https://github.com/opendap-project/rfcs/blob/main/text/0017-naming-and-topology.md"


class Refused(clikit.CliError):
    """A refused step: a stable code, a hint and a fix (odw.clikit)."""


def _git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=audit.git_env())
    if r.returncode:
        raise Refused("config.git", f"git {' '.join(args[:2])} failed: {r.stderr.strip()}",
                      "the root checkout refused a git operation", f"git -C {cwd} status")
    return r.stdout


def _and(names):
    names = [f"`{n}`" for n in names]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _summary(readme):
    """The first sentence of a README's first paragraph after its title."""
    if not readme.is_file():
        return ""
    paras = [p.strip() for p in readme.read_text().split("\n\n")]
    para = next((p for p in paras if p and not p.startswith(("#", "|", "<", "```", "-"))), "")
    first = re.split(r"(?<=[.!?])\s", " ".join(para.split()), maxsplit=1)[0]
    return first.rstrip(".").replace("|", "\\|")


def _rebased(text, folder):
    """A folder README's relative Markdown links, as seen from the root (HL-75)."""
    def sub(m):
        target = m.group(3)
        if not target or target.startswith(("#", "/")) or rw.SCHEME.match(target):
            return m.group(0)
        return m.group(0).replace(target, posixpath.normpath(f"{folder}/{target}"), 1)
    return rw.LINK.sub(sub, text)


def readme(root, top, folders, names):
    rows = "".join(f"| [`{f}/`]({f}/README.md) | {_rebased(_summary(top / f / 'README.md'), f)} |\n"
                   for f in folders)
    year = dt.date.today().year
    return (f"# {root}\n\n| Folder | Content |\n| --- | --- |\n{rows}\nEach folder keeps its own `README.md`, "
            f"`CLAUDE.md` and `REUSE.toml`; the licence texts are in `LICENSES/`. The folders came from the former "
            f"{_and(names)} repositories with their history ([RFC 0017]({RFC})).\n\n## License\n\nThis repository "
            "follows the [REUSE](https://reuse.software) layout: licence texts in `LICENSES/`, and each folder's "
            f"`REUSE.toml` maps its paths. Copyright © {year} The OpenDAP Project.\n")


def claude(root, folders, block, requirements):
    lines = [f"# {root}", "", f"One folder per former repository: {_and([f + '/' for f in folders])}.", ""]
    lines += [block.strip("\n"), ""] if block else []
    lines += ["## Working here", "", "- Each folder has its own `CLAUDE.md` with its rules; read the one for the "
              "folder you change."]
    if requirements:
        lines.append("- Requirements are edited in each folder's `docs/requirements/requirements.toml`; its README "
                     "is rendered with `odapkit req render` and never edited by hand.")
    return "\n".join(lines) + "\n"


def reuse(manifest):
    year = dt.date.today().year
    config = [PRECOMMIT, *([MANIFEST] if manifest else []), ".gitignore", ".gitattributes",
              ".markdownlint-cli2.jsonc", "REUSE.toml"]
    head = f'SPDX-FileCopyrightText = "{year} The OpenDAP Project"'
    return ('version = 1\n\n[[annotations]]\npath = ["README.md", "CLAUDE.md", ".claude/**", ".github/**"]\n'
            f'{head}\nSPDX-License-Identifier = "CC-BY-SA-4.0"\n\n[[annotations]]\npath = '
            f'{json.dumps(config)}\n{head}\nSPDX-License-Identifier = "MIT"\n')


def plan(m, root, top):
    """What the step does, and the new texts: (document, {path: text}, [(from, to)] moves, [paths] removed)."""
    srcs = sorted((s for s in m.sources_of(root) if s.landing and (top / s.landing).is_dir()), key=lambda s: s.landing)
    folders, names = [s.landing for s in srcs], [s.name for s in srcs]
    files = set(audit.tracked(top))
    targets = {}
    for f in folders:
        for p in sorted(x for x in files if x.startswith(f + "/")):
            rel = p[len(f) + 1:]
            if rel.startswith(HOISTED[:3]) or rel == HOISTED[3]:
                targets.setdefault(rel, []).append(p)
    differs = [t for t, ps in targets.items()
               if len({(top / p).read_bytes() for p in ps + ([t] if t in files else [])}) > 1]
    if differs:
        raise Refused("config.differs", f"{len(differs)} files differ between folders: {', '.join(sorted(differs)[:5])}",
                      "a shared file is hoisted only when every copy is the same; make them equal, or keep one "
                      "folder's copy by hand first", f"git -C {top} diff --no-index <one copy> <another>")
    texts, moves, removed = {}, [], []
    for t, ps in sorted(targets.items()):
        if t in files:
            removed += ps
        else:
            moves.append((ps[0], t))
            removed += ps[1:]
    configs = {f: (top / f / PRECOMMIT).read_text() for f in folders if f"{f}/{PRECOMMIT}" in files}
    hooks = {"shared": 0, "scoped": 0, "provided": 0, "dropped": []}
    warnings = []
    if configs:
        if PRECOMMIT in files:
            raise Refused("config.exists", f"the root has a {PRECOMMIT} and {len(configs)} folders have their own",
                          "join them by hand, or remove the root's before the step", f"git -C {top} rm {PRECOMMIT}")
        texts[PRECOMMIT], report = rp.merge(configs, set(names))
        warnings += report.pop("warnings")
        hooks = report
        first = sorted(configs)[0]
        moves.append((f"{first}/{PRECOMMIT}", PRECOMMIT))
        removed += [f"{f}/{PRECOMMIT}" for f in sorted(configs) if f != first]
    providers = [f for f in folders if f"{f}/{MANIFEST}" in files]
    if providers and MANIFEST not in files:
        texts[MANIFEST] = rp.render_manifest([rp.manifest((top / f / MANIFEST).read_text(), f) for f in providers])
        moves.append((f"{providers[0]}/{MANIFEST}", MANIFEST))
        removed += [f"{f}/{MANIFEST}" for f in providers[1:]]
    block, stripped = "", []
    for f in folders:
        path = f"{f}/CLAUDE.md"
        if path in files:
            text = (top / path).read_text()
            found = BLOCK.search(text)
            if found:
                block = block or found.group(0)
                texts[path] = BLOCK.sub("\n", text, count=1).replace("\n\n\n", "\n\n")
                stripped.append(path)
    kept, licences = [], {t[len("LICENSES/"):] for t in targets if t.startswith("LICENSES/")}
    licences |= {p[len("LICENSES/"):] for p in files if p.startswith("LICENSES/")}
    requirements = any(f"{f}/docs/requirements/requirements.toml" in files for f in folders)
    for path, text in (("README.md", lambda: readme(root, top, folders, names)),
                       ("CLAUDE.md", lambda: claude(root, folders, block, requirements)),
                       ("REUSE.toml", lambda: reuse(bool(providers) or MANIFEST in files))):
        if path in files:
            kept.append(path)
        else:
            texts[path] = text()
    for lic in ("CC-BY-SA-4.0.txt", "MIT.txt"):
        if "REUSE.toml" not in kept and lic not in licences:
            warnings.append({"code": "config.licence-missing", "message": f"the root REUSE.toml names "
                             f"{lic[:-4]}, but no folder brought LICENSES/{lic}"})
    doc = {"schema": SCHEMA, "root": root, "folders": folders,
           "hoisted": [{"to": t, "from": ps} for t, ps in sorted(targets.items())],
           "removed": sorted(removed), "written": sorted(p for p in texts if p not in stripped), "kept": kept,
           "stripped": stripped, "hooks": hooks, "warnings": warnings}
    return doc, texts, moves, removed


def apply(m, root, top, dry_run=False):
    if not (top / ".git").exists():
        raise Refused("config.no-root", f"no root checkout at {top}", "the step runs in the merged root, after "
                      "`odapkit rename merge` and `apply`", f"odapkit rename merge {root} --into {top}")
    dirty = _git(top, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    if dirty:
        raise Refused("config.dirty", f"{len(dirty)} uncommitted or untracked files: "
                      + ", ".join(line[3:] for line in dirty[:5]), "the step never runs over uncommitted work",
                      f"git -C {top} status")
    doc, texts, moves, removed = plan(m, root, top)
    base = _git(top, "rev-parse", "HEAD").strip()
    doc.update(mode="plan" if dry_run else "apply", base=base, commits=[])
    if dry_run or not (texts or moves or removed):
        return doc
    for frm, to in moves:
        (top / to).parent.mkdir(parents=True, exist_ok=True)
        _git(top, "mv", "--", frm, to)
    if removed:
        _git(top, "rm", "-q", "--", *removed)
    for path, text in texts.items():
        (top / path).write_text(text)
    _git(top, "add", "--", *texts)
    _git(top, "commit", "-q", "-m", f"{root}: one shared configuration at the root\n\n"
         f"{len(doc['hoisted'])} shared files hoisted, {doc['hooks']['shared']} shared and {doc['hooks']['scoped']} "
         f"scoped pre-commit hooks, {len(doc['stripped'])} folder CLAUDE.md blocks left for the root's "
         f"(odapkit rename config {root}).")
    doc["commits"] = [_git(top, "rev-parse", "HEAD").strip()]
    return doc


def _text(doc):
    out = [f"{doc['mode']} {doc['root']}: folders {', '.join(doc['folders'])}"]
    out += [f"hoist {', '.join(h['from'])} -> {h['to']}" for h in doc["hoisted"]]
    out += [f"write {p}" for p in doc["written"]] + [f"keep {p}" for p in doc["kept"]]
    out += [f"strip the managed block from {p}" for p in doc["stripped"]]
    out += [f"hooks: {doc['hooks']['shared']} shared, {doc['hooks']['scoped']} scoped, "
            f"{doc['hooks']['provided']} provided; dropped {', '.join(doc['hooks']['dropped']) or 'none'}"]
    out += [f"warning {w['code']}: {w['message']}" for w in doc["warnings"]]
    out += [f"commit {c}" for c in doc["commits"]]
    return "\n".join(out)


def _parser():
    ap = clikit.Parser(prog=TOOL, formatter_class=argparse.RawDescriptionHelpFormatter,
                       description="odapkit rename config: a cut root's shared configuration (RFC 0017 §7 step 4). "
                                   "One local commit; never pushes.",
                       epilog="examples:\n  odapkit rename config tools --map rename-map.tsv --root-dir ../cut/tools "
                              "--dry-run\n  odapkit rename config tools --map rename-map.tsv --root-dir ../cut/tools")
    ap.add_argument("root")
    ap.add_argument("--workspace")
    ap.add_argument("--map", help="the rename map (default: $ODAP_RENAME_MAP)")
    ap.add_argument("--root-dir", help="the merged root checkout (default: <workspace>/public/<root>)")
    ap.add_argument("--landing", action="append", default=[], metavar="REPO=DIR",
                    help="where a source repository lands in the root, when the map does not say")
    ap.add_argument("--dry-run", action="store_true", help="list what the step does and change nothing")
    fmt = ap.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="JSON (the default when stdout is not a terminal)")
    fmt.add_argument("--text", action="store_true")
    return ap


def main(argv=None):
    return clikit.main(TOOL, _main, argv)


def _main(argv):
    a = _parser().parse_args(argv)
    as_json = a.json or (not a.text and not sys.stdout.isatty())
    try:
        ws = pathlib.Path(a.workspace) if a.workspace else workspace.root()
        if ws is None:
            raise Refused("config.no-workspace", "not inside a workspace (no repos.tsv)", "run it from the workspace",
                          "odapkit rename config ROOT --workspace <workspace>", 2)
        map_path = a.map or os.environ.get("ODAP_RENAME_MAP")
        if not map_path or not pathlib.Path(map_path).is_file():
            raise Refused("config.no-map", f"no rename map ({map_path or 'none given'})",
                          "pass the map with --map or set ODAP_RENAME_MAP",
                          f"odapkit rename config {a.root} --map <rename-map.tsv>", 2)
        m = rename_map.load(ws, map_path, dict(x.split("=", 1) for x in a.landing if "=" in x))
        if a.root not in m.roots:
            raise Refused("config.unknown-root", f"the map names no root {a.root!r}", f"roots: {', '.join(m.roots)}",
                          f"odapkit rename config {m.roots[0]} --dry-run", 2)
        top = pathlib.Path(a.root_dir) if a.root_dir else ws / "public" / a.root
        doc = apply(m, a.root, top, a.dry_run)
    except Refused as e:
        return clikit.report(TOOL, e, as_json)
    except ValueError as e:
        return clikit.report(TOOL, Refused("config.bad-map", str(e), "the map needs old, new, kind and action "
                                           "columns", "head -1 <rename-map.tsv>", 2), as_json)
    print(json.dumps(doc, ensure_ascii=False, indent=1) if as_json else _text(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
