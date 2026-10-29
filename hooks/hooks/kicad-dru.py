#!/usr/bin/env python3
"""Compose a KiCad project's .kicad_dru from its rule sources, or check that it is up to date.

A KiCad project reads one <project>.kicad_dru. OpenDAP keeps the fab rule set (kicad-lib) and
the product rules (next to each board) apart, and composes them. Each project lists its sources
in a manifest next to it, <project>.dru.toml:

    version = 1
    sources = [
      "hardware/lib/opendap/design-rules/pcbway-hdi/pcbway-hdi.kicad_dru",
      "hardware/rules/product.kicad_dru",
    ]

Paths are relative to the component's folder, in composition order: the repository root, or in a
root that merged several components (the t1 root's board/) the nearest folder above the manifest
from which every source resolves. The output, <project>.kicad_dru
next to the manifest, is "(version 1)" once, then each source's rules, every block preceded by a
comment naming the source and its sha256. Two rules with the same name fail the composition.

Usage:
    kicad-dru.py compose MANIFEST...        write each manifest's .kicad_dru
    kicad-dru.py compose --stdout MANIFEST  print the composition instead
    kicad-dru.py compose --dry-run MANIFEST...
                                            compose and name each file it would write; write nothing
    kicad-dru.py check [--json] [MANIFEST...]
                                            verify each .kicad_dru equals its composition;
                                            with no manifest, every *.dru.toml in the git repository

Exit status: 0 success, 1 a stale, missing or invalid composition, 2 usage error.
Standard library only (Python 3.11 or later).
"""
import difflib
import hashlib
import json
import os
import subprocess
import sys

if sys.version_info < (3, 11):
    sys.exit("kicad-dru: Python 3.11 or later is needed (tomllib)")
import tomllib

MANIFEST_SUFFIX = ".dru.toml"
TOOL = "odw dru"
# The composed files' header keeps the old name until the boards' roots are cut, so a header the hook
# compares stays current (HL-72).
HEADER_TOOL = "odapkit dru"


class DruError(Exception):
    pass


# ---------------------------------------------------------------- parsing


def top_level_forms(text, origin):
    """Return the top-level forms of a .kicad_dru text: (head, name, start, end).

    Follows KiCad's rules lexer: '#' starts a comment when it is the first non-blank character of
    a line; strings are double-quoted with backslash escapes."""
    forms, i, n = [], 0, len(text)
    depth, start, head, name, tokens = 0, None, None, None, 0
    line_start = True
    while i < n:
        c = text[i]
        if c == "\n":
            line_start = True
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue
        if c == "#" and line_start:
            while i < n and text[i] != "\n":
                i += 1
            continue
        line_start = False
        if c == "(":
            if depth == 0:
                start, head, name, tokens = i, None, None, 0
            depth += 1
            i += 1
            continue
        if c == ")":
            if depth == 0:
                raise DruError(f"{origin}: unbalanced ')' at offset {i}")
            depth -= 1
            i += 1
            if depth == 0:
                forms.append((head, name, start, i))
            continue
        if depth == 0:
            raise DruError(f"{origin}: text outside a form at offset {i}")
        # A token: a quoted string or a bare symbol.
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                raise DruError(f"{origin}: unterminated string at offset {i}")
            tok = text[i + 1:j]
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()"':
                j += 1
            tok = text[i:j]
            i = j
        if depth == 1:
            tokens += 1
            if tokens == 1:
                head = tok
            elif tokens == 2:
                name = tok
    if depth != 0:
        raise DruError(f"{origin}: unbalanced '(' (missing ')' at end of file)")
    return forms


def rules_body(text, origin):
    """The source text without its (version N) header, and the names of its rules."""
    forms = top_level_forms(text, origin)
    names, cut = [], []
    for head, name, start, end in forms:
        if head == "version":
            if cut:
                raise DruError(f"{origin}: a second (version …) header at offset {start}")
            if name != "1":
                raise DruError(f"{origin}: (version {name}) is not supported, only (version 1)")
            cut.append((start, end))
        elif head == "rule":
            if not name:
                raise DruError(f"{origin}: a rule without a name at offset {start}")
            names.append(name)
        else:
            raise DruError(f"{origin}: unexpected top-level form ({head} …) at offset {start}")
    for start, end in reversed(cut):
        text = text[:start] + text[end:]
    return text.strip("\n").rstrip() + "\n", names


# ---------------------------------------------------------------- manifests


# Variables git exports to hooks that pin the repository instead of discovering it from a path.
# In a linked worktree, git runs hooks with GIT_DIR set to .git/worktrees/<name> and no
# GIT_WORK_TREE, so `git -C <subdir> rev-parse --show-toplevel` answers <subdir> itself.
# GIT_INDEX_FILE is kept: it names the index of the commit being checked.
PINNING_GIT_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_PREFIX")


def git_env():
    """The environment for git commands that discover the repository from a path."""
    return {k: v for k, v in os.environ.items() if k not in PINNING_GIT_VARS}


def repo_root(path):
    d = os.path.dirname(os.path.abspath(path))
    try:
        out = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True, env=git_env()).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.CalledProcessError):
        pass
    while d != os.path.dirname(d):
        if os.path.exists(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    raise DruError(f"{path}: not inside a git repository (source paths are relative to its root)")


def load_manifest(path):
    if not path.endswith(MANIFEST_SUFFIX):
        raise DruError(f"{path}: a manifest is named <project>{MANIFEST_SUFFIX}")
    try:
        data = tomllib.load(open(path, "rb"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise DruError(f"{path}: {e}")
    unknown = set(data) - {"version", "sources"}
    if unknown:
        raise DruError(f"{path}: unknown keys {sorted(unknown)} (allowed: version, sources)")
    if data.get("version") != 1:
        raise DruError(f"{path}: 'version = 1' is required")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources or not all(isinstance(s, str) and s for s in sources):
        raise DruError(f"{path}: 'sources' must be a non-empty list of paths")
    if len(set(sources)) != len(sources):
        raise DruError(f"{path}: a source is listed twice")
    return sources


def target_of(manifest):
    return manifest[: -len(MANIFEST_SUFFIX)] + ".kicad_dru"


def inside(path, root):
    """True when path, with every link resolved, is inside root (itself resolved)."""
    real = os.path.realpath(path)
    return os.path.commonpath([real, root]) == root


def checked_target(manifest, root):
    """The manifest's .kicad_dru, refused when it is a link or resolves outside the repository."""
    if not inside(manifest, root):
        raise DruError(f"{manifest}: the manifest resolves outside the repository")
    target = target_of(manifest)
    if os.path.islink(target):
        raise DruError(f"{target}: the composed file is a symbolic link; refusing to read or write it")
    if not inside(target, root):
        raise DruError(f"{target}: the composed file resolves outside the repository")
    return os.path.realpath(target)


def anchor(manifest, root, sources):
    """The folder the sources are relative to: the nearest folder from the manifest's up to the repository root
    from which every source is a file; the repository root when none is (its errors name the missing source)."""
    d = os.path.dirname(os.path.realpath(manifest))
    while inside(d, root):
        if all(os.path.isfile(os.path.join(d, s)) for s in sources):
            return d
        if d == root:
            break
        d = os.path.dirname(d)
    return root


def compose(manifest):
    """The composed .kicad_dru text for a manifest. Every path it reads or names for writing
    resolves inside the repository (the kicad-lib submodule is inside it)."""
    root = os.path.realpath(repo_root(manifest))
    target = checked_target(manifest, root)
    sources = load_manifest(manifest)
    base = anchor(manifest, root, [s for s in sources if not os.path.isabs(s)])
    rel_manifest = os.path.relpath(os.path.realpath(manifest), base)
    out = [
        "(version 1)\n",
        f"# Composed by `{HEADER_TOOL}` from {rel_manifest}. Do not edit by hand:\n",
        f"# edit the sources and run `{HEADER_TOOL} compose {rel_manifest}`.\n",
    ]
    seen = {}
    for src in sources:
        if os.path.isabs(src) or ".." in src.replace("\\", "/").split("/"):
            raise DruError(f"{manifest}: source {src} must be a path inside the repository, relative to its root")
        if not src.endswith(".kicad_dru"):
            raise DruError(f"{manifest}: source {src} is not a .kicad_dru file")
        path = os.path.join(base, src)
        if os.path.realpath(path) == target:
            raise DruError(f"{manifest}: the composed file {src} cannot be one of its own sources")
        if os.path.lexists(path) and not inside(path, root):
            raise DruError(f"{manifest}: source {src} resolves outside the repository")
        if not os.path.isfile(path):
            hint = " (is the kicad-lib submodule checked out? git submodule update --init)" if "/lib/" in src else ""
            raise DruError(f"{manifest}: source {src} not found{hint}")
        raw = open(path, "rb").read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise DruError(f"{src}: not UTF-8 text")
        body, names = rules_body(text, src)
        for name in names:
            if name in seen:
                where = "earlier in the same file" if seen[name] == src else f"in {seen[name]}"
                raise DruError(f"duplicate rule name \"{name}\": in {src} and {where}")
            seen[name] = src
        out += ["\n", f"# Source: {src}\n", f"# sha256: {hashlib.sha256(raw).hexdigest()}\n", "\n", body]
    return "".join(out)


def tracked_manifests():
    try:
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                              text=True, check=True, env=git_env()).stdout.strip()
        files = subprocess.run(["git", "-C", root, "ls-files", "--cached", "--others", "--exclude-standard",
                                "-z", "--", f"*{MANIFEST_SUFFIX}"],
                               capture_output=True, text=True, check=True, env=git_env()).stdout.split("\0")
    except (OSError, subprocess.CalledProcessError):
        raise DruError("not inside a git repository: name the manifests to check")
    return sorted(os.path.relpath(os.path.join(root, f)) for f in files if f and os.path.isfile(os.path.join(root, f)))


# ---------------------------------------------------------------- commands


def cmd_compose(args):
    to_stdout, dry_run = "--stdout" in args, "--dry-run" in args
    manifests = [a for a in args if a not in ("--stdout", "--dry-run")]
    if not manifests or any(a.startswith("-") for a in manifests) or (to_stdout and len(manifests) != 1) \
            or (to_stdout and dry_run):
        return usage()
    for m in manifests:
        try:
            text = compose(m)
        except DruError as e:
            print(f"kicad-dru: {e}", file=sys.stderr)
            return 1
        if to_stdout:
            sys.stdout.write(text)
        elif dry_run:
            print(f"would compose {target_of(m)} ({len(text.encode())} bytes)")
        else:
            # compose() confined the target to the repository and refused a link; O_NOFOLLOW
            # also refuses a link created since.
            fd = os.open(target_of(m), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, "w", newline="\n") as f:
                f.write(text)
            print(f"composed {target_of(m)}")
    return 0


def cmd_check(args):
    as_json = "--json" in args
    manifests = [a for a in args if a != "--json"]
    if any(a.startswith("-") for a in manifests):
        return usage()
    try:
        manifests = manifests or tracked_manifests()
    except DruError as e:
        print(f"kicad-dru: {e}", file=sys.stderr)
        return 1
    items, ok = [], True
    for m in manifests:
        item = {"manifest": m, "target": target_of(m)}
        try:
            want = compose(m)
            if not os.path.isfile(item["target"]):
                item["status"] = "missing"
            else:
                # Reached only after compose() confined the target; O_NOFOLLOW refuses a late link.
                fd = os.open(item["target"], os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, encoding="utf-8", errors="replace", newline="") as f:
                    have = f.read()
                item["status"] = "ok" if have == want else "stale"
                if have != want:
                    item["diff"] = "".join(list(difflib.unified_diff(
                        have.splitlines(True), want.splitlines(True),
                        item["target"], "composition"))[:40])
        except DruError as e:
            item["status"], item["error"] = "error", str(e)
        ok = ok and item["status"] == "ok"
        items.append(item)
    if as_json:
        print(json.dumps({"schema": "opendap.dru-check/v1", "ok": ok, "items": items}, indent=2))
    else:
        for it in items:
            if it["status"] == "ok":
                continue
            if it["status"] == "error":
                print(f"kicad-dru: {it['error']}", file=sys.stderr)
            else:
                print(f"kicad-dru: {it['target']} is {it['status']}: run `{TOOL} compose {it['manifest']}`",
                      file=sys.stderr)
                if it.get("diff"):
                    sys.stderr.write(it["diff"])
        if ok and items:
            print(f"{len(items)} composed rule file(s) up to date")
    return 0 if ok else 1


def usage():
    print(__doc__.split("Usage:")[1].split("Exit status")[0].rstrip(), file=sys.stderr)
    return 2


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0 if argv else 2
    cmd, args = argv[0], argv[1:]
    if cmd == "compose":
        return cmd_compose(args)
    if cmd == "check":
        return cmd_check(args)
    return usage()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
