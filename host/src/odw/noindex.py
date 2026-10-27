"""Keep generated trees out of the macOS Spotlight index.

Spotlight skips any folder whose name ends in `.noindex`. Each generated tree `<name>` (a Cargo `target/`, a
Python `.venv/`, test caches) is renamed `<name>.noindex` and replaced by a relative symlink `<name>`, so every tool keeps
using the old path while builds stop feeding the indexer.

Usage: odapkit noindex [--apply [--force] | --check] [--node-modules] [--json] [PATH...]   (default PATH: the current folder)
Without --apply it lists what it would change; --check fails while a generated tree is not linked. --apply moves
nothing while a build runs (cargo, rustc, pytest), since moving a tree under a running build breaks it: it says
it skipped and exits 0, so a check stage that ends with it never fails for that; --force moves anyway.
Exit status: 0 done or skipped, 1 refused (a `<name>.noindex` already exists beside a real `<name>`) or, with
--check, a tree not linked, 2 usage.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys

DEFAULT = frozenset({"target", ".venv", ".pytest_cache", ".ruff_cache", ".mypy_cache"})
# mkosi output and cache trees are left out until a symlinked tree is verified inside the build VM.
SKIP = frozenset({".git", ".hg", ".svn"})
# A tree is linked only when it carries the marker its own tool writes, so a folder that merely has the name
# (a package's sources called `target`, an Xcode build cache) is left alone. CACHEDIR.TAG: Cargo, pytest, ruff,
# mypy; pyvenv.cfg: every Python virtual environment.
MARKERS = {"target": ("CACHEDIR.TAG",), ".pytest_cache": ("CACHEDIR.TAG",), ".ruff_cache": ("CACHEDIR.TAG",),
           ".mypy_cache": ("CACHEDIR.TAG",), ".venv": ("pyvenv.cfg",), "node_modules": ()}
# Dependency trees are never searched: their insides are other projects' files.
NEVER_DESCEND = frozenset({"node_modules"})


BUILDS = ("cargo", "rustc", "pytest")


def busy():
    """The build tools running now on this machine (any session's)."""
    out = []
    for name in BUILDS:
        r = subprocess.run(["pgrep", "-x", name], capture_output=True)
        if r.returncode == 0:
            out.append(name)
    return out


class NoindexError(Exception):
    pass


def candidates(root, names):
    """Real directories named in `names` under `root`, not inside each other, `.git` or a `.noindex` tree."""
    for dirpath, dirnames, _ in os.walk(root):
        keep = []
        for d in dirnames:
            path = pathlib.Path(dirpath) / d
            if d in SKIP or d.endswith(".noindex") or path.is_symlink():
                continue
            if d in names and all((path / m).exists() for m in MARKERS.get(d, ())):
                yield path
                continue
            if d in NEVER_DESCEND:
                continue
            keep.append(d)
        dirnames[:] = sorted(keep)


def apply(root, names, dry_run=True):
    changed = []
    for path in candidates(root, names):
        dest = path.with_name(path.name + ".noindex")
        if dest.exists():
            raise NoindexError(f"{dest} already exists beside a real {path.name}/: merge or remove one by hand")
        changed.append(path)
        if not dry_run:
            path.rename(dest)
            os.symlink(dest.name, path)
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser(prog="odapkit noindex", description="Keep generated trees out of the Spotlight index.")
    ap.add_argument("paths", nargs="*", default=["."])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="rename and link (default: only list)")
    mode.add_argument("--check", action="store_true", help="fail while a generated tree is not linked")
    ap.add_argument("--force", action="store_true", help="with --apply: move even while a build runs")
    ap.add_argument("--node-modules", action="store_true", help="include node_modules (off by default)")
    ap.add_argument("--json", action="store_true")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    names = DEFAULT | ({"node_modules"} if args.node_modules else set())
    if args.apply and not args.force and (running := busy()):
        print(f"odapkit noindex: skipped, a build is running ({', '.join(running)}); run it again afterwards")
        return 0
    out = []
    try:
        for p in args.paths:
            out += [str(c) for c in apply(pathlib.Path(p), names, dry_run=not args.apply)]
    except NoindexError as e:
        print(f"odapkit noindex: {e}", file=sys.stderr)
        return 1
    if args.check:
        for p in out:
            print(f"odapkit noindex: {p} is not linked: run odapkit noindex --apply", file=sys.stderr)
        if args.json:
            print(json.dumps({"applied": False, "paths": out}))
        return 1 if out else 0
    if args.json:
        print(json.dumps({"applied": args.apply, "paths": out}))
    else:
        verb = "linked" if args.apply else "would link"
        for p in out:
            print(f"{verb} {p} -> {pathlib.Path(p).name}.noindex")
    return 0


if __name__ == "__main__":
    sys.exit(main())
