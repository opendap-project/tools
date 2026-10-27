"""odw sync: shared boilerplate rendered from the workspace templates.

Usage: odapkit sync [--workspace DIR] [--templates DIR] (--check | --apply [--dry-run]) [--json]

`<templates>/sync.toml` (opendap.sync/v1) lists [[file]] entries: a `template` path, the `target` path inside each
repository, the repository globs it applies to (`repos`, over the paths in every repos.tsv) and `when`: `always`
(the file must exist and match) or `present` (it must match where a repository has it). [[generated]] entries render a `target` per repository
from a workspace `source` with a named `generator` (`codeowners`: .github/CODEOWNERS from owners.tsv, odw.owners);
`repos` may include `.`, the workspace repository. --check reports every
target that is missing or differs (exit 1); --apply writes them, and with --dry-run only names them. --json prints
opendap.check-result/v1: with --check each drift fails; with --apply each target written (or that would be) is
`info`. Default templates: <workspace>/templates.
Spec: docs/specs/workspace-sync.md.
"""
import dataclasses
import datetime as dt
import fnmatch
import json
import pathlib
import shutil
import subprocess
import sys
import tomllib

from odw import audit, clikit, owners, workspace

TOOL = "odapkit sync"


@dataclasses.dataclass
class Drift:
    repo: str
    target: str
    template: str
    state: str  # missing | differs


def _entries(templates):
    doc = tomllib.loads((pathlib.Path(templates) / "sync.toml").read_text())
    return doc.get("file", [])


BEGIN = "{c} >>> odapkit sync: managed block from the workspace templates; edit them, not this block"
END = "{c} <<< odapkit sync"


def blocks(templates):
    return tomllib.loads((pathlib.Path(templates) / "sync.toml").read_text()).get("block", [])


def _tracked(ws, repo):
    try:
        return audit.tracked(pathlib.Path(ws) / repo)
    except Exception:
        return []


def _markers(block):
    """The block's first and last lines: the target's comment syntax (`comment`, and `comment_end` for Markdown)."""
    c, e = block.get("comment", "#"), block.get("comment_end", "")
    tail = f" {e}" if e else ""
    return BEGIN.format(c=c) + tail, END.format(c=c) + tail


def render_block(ws, templates, repo, block):
    """The managed block for `repo`: the fragments whose `when_tracked` patterns match a tracked file."""
    b, e = _markers(block)
    files = _tracked(ws, repo)
    names = [pathlib.PurePosixPath(f).name for f in files]
    parts = []
    for frag in block["fragment"]:
        pats = frag.get("when_tracked")
        if pats and not any(fnmatch.fnmatch(n, p) for n in names for p in pats):
            continue
        parts.append((pathlib.Path(templates) / frag["template"]).read_text().strip("\n"))
    if not parts:
        return None
    return b + "\n" + "\n\n".join(parts) + "\n" + e + "\n"


def _split(text, block):
    b, e = _markers(block)
    if b in text and e in text:
        i, j = text.index(b), text.index(e) + len(e)
        return text[:i], text[i:j] + "\n", text[j:].lstrip("\n")
    return None, None, text


def _merge(rendered, rest, c):
    """The block first, then the repository's own lines minus exact duplicates of block lines and empty headers."""
    inside = {l.rstrip("/") for l in rendered.splitlines() if l and not l.startswith(c)}
    lines = [l for l in rest.splitlines() if l.rstrip("/") not in inside or not l.strip()]
    out = []
    for k, l in enumerate(lines):
        if l.startswith(c):
            nxt = next((x for x in lines[k + 1:] if x.strip()), None)
            if nxt is None or nxt.startswith(c):
                continue
        if not l.strip() and (not out or not out[-1].strip()):
            continue
        out.append(l)
    tail = "\n".join(out).strip("\n")
    return rendered + ("\n" + tail + "\n" if tail else "")


def _after_intro(rendered, text):
    """Markdown: the block after the title and its first paragraph, minus lines of the rest that repeat the block's."""
    inside = {l for l in rendered.splitlines()[1:-1] if l.strip()}
    lines, out = [l for l in text.splitlines() if l not in inside], []
    for l in lines:
        if not l.strip() and (not out or not out[-1].strip()):
            continue
        out.append(l)
    k = next((i + 1 for i, l in enumerate(out) if l.startswith("# ")), 0)
    while k < len(out) and not out[k].strip():
        k += 1
    while k < len(out) and out[k].strip():
        k += 1
    head, tail = "\n".join(out[:k]).strip("\n"), "\n".join(out[k:]).strip("\n")
    return (head + "\n\n" if head else "") + rendered + ("\n" + tail + "\n" if tail else "")


def _compose(blk, want, text):
    prefix, current, rest = _split(text, blk)
    if blk.get("place", "top") == "after-intro":
        if current is not None:
            return prefix + want + ("\n" + rest if rest.strip() else "")
        return _after_intro(want, text)
    return _merge(want, rest, blk.get("comment", "#"))


def _block_drift(ws, templates):
    out = []
    repos = [r for r in audit.repositories(ws) if (ws / r / ".git").exists()]
    for blk in blocks(templates):
        for r in repos:
            if not any(fnmatch.fnmatch(r, g) for g in blk["repos"]) or any(fnmatch.fnmatch(r, g) for g in blk.get("exclude", [])):
                continue
            path = ws / r / blk["target"]
            want = render_block(ws, templates, r, blk)
            if want is None:
                continue
            if not path.exists():
                # A Markdown block lives inside the repository's own document; it never creates one.
                if blk.get("place", "top") == "top":
                    out.append((Drift(r, blk["target"], "block", "missing"), want, "", blk))
                continue
            text = path.read_text()
            _, current, _ = _split(text, blk)
            if current is None:
                out.append((Drift(r, blk["target"], "block", "no-block"), want, text, blk))
            elif current != want:
                out.append((Drift(r, blk["target"], "block", "differs"), want, text, blk))
    return out


GENERATORS = {"codeowners": (owners.read, owners.render)}


def _generated_drift(ws, templates, repos):
    """(Drift, wanted text) for every [[generated]] target that is missing or differs."""
    doc = tomllib.loads((pathlib.Path(templates) / "sync.toml").read_text())
    out = []
    for g in doc.get("generated", []):
        if g.get("generator") not in GENERATORS:
            raise clikit.CliError("sync.invalid", f"unknown generator {g.get('generator')!r} in sync.toml",
                                  f"the generators are {', '.join(GENERATORS)}", f"{TOOL} --check --json")
        load, render = GENERATORS[g["generator"]]
        if not (ws / g["source"]).is_file():
            raise clikit.CliError("sync.source_missing", f"no {ws / g['source']} (the source of {g['target']})",
                                  "a [[generated]] entry renders from a file at the workspace root",
                                  f"git -C {ws} pull --ff-only", status=2)
        data = load(ws / g["source"])
        for r in owners.candidates(ws, repos):
            if not any(fnmatch.fnmatch(r, p) for p in g["repos"]) or any(fnmatch.fnmatch(r, p) for p in g.get("exclude", [])):
                continue
            want = render(data, r)
            path = ws / r / g["target"]
            if want is None:
                continue
            if not path.exists():
                out.append((Drift(r, g["target"], f"generated {g['source']}", "missing"), want))
            elif path.read_text() != want:
                out.append((Drift(r, g["target"], f"generated {g['source']}", "differs"), want))
    return out


def check(ws, templates):
    ws, templates = pathlib.Path(ws), pathlib.Path(templates)
    repos = [r for r in audit.repositories(ws) if (ws / r / ".git").exists()]
    out = []
    for e in _entries(templates):
        want = (templates / e["template"]).read_bytes()
        for r in repos:
            if not any(fnmatch.fnmatch(r, g) for g in e["repos"]) or any(fnmatch.fnmatch(r, g) for g in e.get("exclude", [])):
                continue
            path = ws / r / e["target"]
            if not path.exists():
                if e.get("when", "always") == "always":
                    out.append(Drift(r, e["target"], e["template"], "missing"))
            elif path.read_bytes() != want:
                out.append(Drift(r, e["target"], e["template"], "differs"))
    return (out + [d for d, *_ in _block_drift(ws, templates)] + [d for d, _ in _generated_drift(ws, templates, repos)]
            + _hook_drift(ws, repos))


HOOK = ".git/hooks/pre-commit"


def _hook_path(repo_dir):
    """The repository's pre-commit hook file, also for a linked worktree (whose .git is a file)."""
    r = subprocess.run(["git", "rev-parse", "--git-path", "hooks/pre-commit"], cwd=repo_dir,
                       capture_output=True, text=True, env=audit.git_env())
    p = pathlib.Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else pathlib.Path(".git/hooks/pre-commit")
    return p if p.is_absolute() else repo_dir / p


def _hook_drift(ws, repos):
    """A repository with a .pre-commit-config.yaml but no installed hook commits unchecked (a fresh clone
    carries no hooks)."""
    return [Drift(r, HOOK, "pre-commit install", "missing") for r in repos
            if (ws / r / ".pre-commit-config.yaml").is_file() and not _hook_path(ws / r).is_file()]


def apply(ws, templates):
    ws, templates = pathlib.Path(ws), pathlib.Path(templates)
    changed = []
    for d, want, text, blk in _block_drift(ws, templates):
        (ws / d.repo / d.target).write_text(_compose(blk, want, text))
        changed.append(d)
    for d, want in _generated_drift(ws, templates, audit.repositories(ws)):
        path = ws / d.repo / d.target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(want)
        changed.append(d)
    for d in check(ws, templates):
        if d.template == "block" or d.template.startswith("generated "):
            continue
        if d.template == "pre-commit install":
            if shutil.which("pre-commit"):
                subprocess.run(["pre-commit", "install"], cwd=ws / d.repo, capture_output=True)
                changed.append(d)
            continue
        path = ws / d.repo / d.target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((templates / d.template).read_bytes())
        changed.append(d)
    return changed


def check_result(found, verdict, verb):
    results = [{"id": f"{d.repo}/{d.target}", "rule": f"sync {d.template}", "verdict": verdict,
                "message": f"{verb}{d.state} ({d.template})", "location": {"path": f"{d.repo}/{d.target}"}}
               for d in found]
    return {"schema": "opendap.check-result/v1", "tool": {"name": TOOL}, "subject": {"repo": "workspace"},
            "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "results": results}


def main(argv=None):
    return clikit.main(TOOL, _main, argv)


def _main(argv):
    ap = clikit.Parser(prog=TOOL, summary=clikit.summary("sync"),
                       description="Shared boilerplate from the workspace templates.",
                       examples=["odapkit sync --check --json", "odapkit sync --apply --dry-run", "odapkit sync --apply"])
    ap.add_argument("--workspace")
    ap.add_argument("--templates")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="with --apply: name what it would write; write nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    ws = pathlib.Path(args.workspace) if args.workspace else workspace.root()
    if ws is None:
        raise clikit.no_workspace(TOOL)
    templates = pathlib.Path(args.templates) if args.templates else ws / "templates"
    if not (templates / "sync.toml").is_file():
        raise clikit.CliError("templates.not_found", f"no {templates / 'sync.toml'}",
                              "the templates folder holds sync.toml (the workspace's templates/)",
                              f"{TOOL} --check --templates DIR", status=2)
    dry = args.apply and args.dry_run
    found = apply(ws, templates) if args.apply and not dry else check(ws, templates)
    verb = "would write " if dry else "wrote " if args.apply else ""
    if args.json:
        print(json.dumps(check_result(found, "info" if args.apply else "fail", verb), indent=1))
    else:
        for d in found:
            print(f"{verb}{d.repo}/{d.target}: {d.state} ({d.template})")
    return 1 if found and args.check else 0


if __name__ == "__main__":
    sys.exit(main())
