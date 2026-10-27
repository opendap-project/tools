"""odw sync: shared boilerplate rendered from the workspace templates.

Usage: odapkit sync [--workspace DIR] [--templates DIR] (--check | --apply) [--json]

`<templates>/sync.toml` (opendap.sync/v1) lists [[file]] entries: a `template` path, the `target` path inside each
repository, the repository globs it applies to (`repos`, over the paths in every repos.tsv) and `when`: `always`
(the file must exist and match) or `present` (it must match where a repository has it). --check reports every
target that is missing or differs (exit 1); --apply writes them. Default templates: <workspace>/templates.
Spec: docs/specs/workspace-sync.md.
"""
import argparse
import dataclasses
import fnmatch
import json
import pathlib
import shutil
import subprocess
import sys
import tomllib

from odw import audit, workspace


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
    return out + [d for d, *_ in _block_drift(ws, templates)] + _hook_drift(ws, repos)


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
    for d in check(ws, templates):
        if d.template == "block":
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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="odapkit sync", description="Shared boilerplate from the workspace templates.")
    ap.add_argument("--workspace")
    ap.add_argument("--templates")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    ws = pathlib.Path(args.workspace) if args.workspace else workspace.root()
    if ws is None:
        print("odapkit sync: not inside a workspace", file=sys.stderr)
        return 2
    templates = pathlib.Path(args.templates) if args.templates else ws / "templates"
    found = apply(ws, templates) if args.apply else check(ws, templates)
    if args.json:
        print(json.dumps([dataclasses.asdict(d) for d in found], indent=1))
    else:
        verb = "wrote" if args.apply else ""
        for d in found:
            print(f"{verb + ' ' if verb else ''}{d.repo}/{d.target}: {d.state} ({d.template})")
    return 1 if found and args.check else 0


if __name__ == "__main__":
    sys.exit(main())
