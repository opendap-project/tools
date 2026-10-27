"""odw cli inventory: every command-line surface of the public roots, classed by its command-standard §5 kind.

An executable is a tracked file in a `bin/`, `scripts/`, `tools/` or `hooks/` folder of a root (mode 755, a
shebang or a Python `__main__`; tests, fixtures, vendored code and library modules are not executables), or a
binary target of a Cargo package (`[[bin]]`, `src/main.rs`, `src/bin/*.rs`). Kinds: `umbrella`, `plugin`,
`device-binary`, `host-binary`, `script`, `hook`. Each entry carries the facts read from its source (and the
sources it runs, one level down) that the conformance test needs, and whether it may be probed by running it.
Spec: docs/specs/cli-conformance.md.
"""
import pathlib
import re
import subprocess
import tomllib

from odw import audit

SCHEMA = "opendap.cli-inventory/v1"
KINDS = ("umbrella", "plugin", "device-binary", "host-binary", "script", "hook")
FOLDERS = ("bin", "scripts", "tools", "hooks")
UMBRELLAS = {"odapkit", "odw", "opendap"}
PLUGIN = re.compile(r"^(odapkit|odw|opendap)-[a-z][a-z0-9-]*$")
SKIP = re.compile(r"(^|/)(tests?|fixtures|vendor|__pycache__)/|(^|/)test_[^/]*$|(^|/)__init__\.py$")
SCRIPT_SUFFIXES = {"", ".py", ".sh"}
# Roots whose Cargo binaries run on the device; every other Cargo binary runs on the host.
DEVICE_ROOTS = re.compile(r"^public/system(/|$)|^public/hats/[^/]+/software(/|$)")

FACTS = {
    "help": re.compile(r"argparse|\bclap\b|--help|\b-h\)|\b-h\|"),
    "json": re.compile(r"--json\b"),
    "dry_run": re.compile(r"--dry-run\b"),
    "check": re.compile(r"--check\b"),
    "force": re.compile(r"--force\b"),
    "hint": re.compile(r"\bhint\b"),
    "error_code": re.compile(r"""["'`][a-z][a-z0-9_]*\.[a-z][a-z0-9_]*["'`]"""),
    "stderr": re.compile(r">&2|sys\.stderr|eprintln!|file=sys\.stderr"),
}
# A bracketed letter keeps a pattern from matching its own source, which the closure of odapkit cli reads.
PROMPT = re.compile(r"\binput\(|getpass\.getpass\(|\bread\s+-[a-z]*p\b|<[/]dev/tty|stdin\(\)\s*\.\s*(read_line|lines|lock)"
                    r"|dialogue[r]|inquir[e]::|rpasswor[d]")
WRITES = {
    "py": re.compile(r"write_text\(|write_bytes\(|open\([^)\n]*[\"'][wax]b?\+?[\"']|shutil\.(copy|move|rmtree)|os\.(remove|unlink"
                     r"|rename|replace|makedirs)|\.mkdir\(|\.unlink\(|[\"']git[\"'],\s*[\"'](commit|push|add|mv)[\"']"),
    "rs": re.compile(r"fs::(write|create_dir|remove_file|rename|copy)|File::create|OpenOptions"),
    "sh": re.compile(r"(?:^|[\s;|&(])(rm|mv|cp|mkdir|ln|tee|install)\s+-?[\w\"$]|\bgit\s+(commit|push|add|mv)\b"
                     r"|(?:^|[\s;|&(])>{1,2}\s*[\"$~/.A-Za-z_]", re.M),
}
NO_WRITE_TARGETS = re.compile(r">\s*/dev/null|>&2|2>&1")
NETWORK = re.compile(r"\blimactl\b|qemu-syste[m]|\bssh\b|\bscp\b|\brsync\b|urllib\.request|\brequests\b|\bcurl\b|\bwget\b"
                     r"|\bsudo\b|socket_vmne[t]|launchct[l]|\bodapkit-sysroot\b"
                     r"|\bhdiutil\b|\bdiskutil\b|\bosascript\b|\bpkill\b|\bkillall\b")
BAD_EXIT = {
    "sh": re.compile(r"\bexit\s+([3-9]|[1-9]\d+)\b|\bdie\s+\"[^\"]*\"\s+([3-9]|[1-9]\d+)\b"),
    "py": re.compile(r"sys\.exit\(\s*([3-9]|[1-9]\d+)\s*\)|SystemExit\(\s*([3-9]|[1-9]\d+)\s*\)|\breturn\s+([3-9])\s*$",
                     re.M),
    "rs": re.compile(r"process::exit\(\s*([3-9]|[1-9]\d+)\s*\)|ExitCode::from\(\s*([3-9]|[1-9]\d+)"),
}
FORBIDDEN_VERBS = ("info", "show", "ls", "rm", "remove")
VERB_SOURCE = re.compile(r"""add_parser\(\s*["'](%s)["']|^\s*"?(%s)"?\)\s|^\s*(%s)\|""" % (("|".join(FORBIDDEN_VERBS),) * 3), re.M)
RUST_VERB = re.compile(r"#\[derive\([^)]*Subcommand[^)]*\)\][^}]*?\b(Info|Show|Ls|Rm|Remove)\b", re.S)
RUNS = re.compile(r"python3?\"?\s+(?:-m\s+([\w.]+)|\"?\$\w+/([\w./-]+\.py))|exec\s+\"\$\w+/([\w./-]+)\""
                  r"|^from\s+([\w.]+)\s+import|\$hooks/hooks/([\w.-]+)", re.M)


def roots_in(repos_tsv):
    """The roots a repos.tsv lists, in its order (the public list; overlays are not read)."""
    lines = pathlib.Path(repos_tsv).read_text().splitlines()[1:]
    return [line.split("\t")[0] for line in lines if line.strip()]


def tracked_modes(repo):
    """{path: git mode} for every tracked file of a repository."""
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-s", "-z"], capture_output=True, check=True,
                         env=audit.git_env()).stdout.decode()
    modes = {}
    for rec in filter(None, out.split("\0")):
        meta, path = rec.split("\t", 1)
        modes[path] = meta.split()[0]
    return modes


def _lang(path, text):
    first = text.split("\n", 1)[0]
    if path.suffix == ".rs":
        return "rs"
    if path.suffix == ".py" or "python" in first:
        return "py"
    return "sh"


# `from pkg import a, b`: the modules pkg/a.py and pkg/b.py, when they exist, are run too.
FROM_IMPORT = re.compile(r"^from\s+([\w.]+)\s+import\s+\(?([\w ,]+)", re.M)
SOURCED = re.compile(r'^\s*\.\s+.*?/([\w.-]+/[\w.-]+\.sh)"?\s*$', re.M)


def _resolve(repo, files, token):
    """The tracked file a `python -m X`, `$here/x.py` or `from X import` names, if this repository has it."""
    if token.endswith(".py") or "/" in token:
        tail = token.lstrip("./")
        hits = [f for f in files if f.endswith(tail)]
    else:
        tail = token.replace(".", "/")
        hits = [f for f in files if f.endswith(f"{tail}.py") or f.endswith(f"{tail}/__init__.py")]
    return hits[0] if hits else None


def _closure(repo, files, rel, depth=2):
    """The executable's own source, then the repository sources it runs or imports, `depth` levels down."""
    seen, out, queue = set(), [], [(rel, 0)]
    while queue:
        f, d = queue.pop(0)
        if f in seen:
            continue
        seen.add(f)
        text = audit._read(repo / f)
        # A sourced shell library runs in the same shell: its text is part of the file's own.
        for m in SOURCED.finditer(text):
            hit = _resolve(repo, files, m.group(1))
            if hit:
                text += "\n" + audit._read(repo / hit)
        out.append((f, text))
        if d >= depth:
            continue
        for m in RUNS.finditer(text):
            token = next(g for g in m.groups() if g)
            hit = _resolve(repo, files, token)
            if hit:
                queue.append((hit, d + 1))
        for m in FROM_IMPORT.finditer(text):
            for name in (n.split(" as ")[0].strip() for n in m.group(2).split(",")):
                hit = _resolve(repo, files, f"{m.group(1)}.{name}") if name else None
                if hit:
                    queue.append((hit, d + 1))
    return out


def _bad_exits(sources):
    out = []
    for f, text in sources:
        lang = _lang(pathlib.Path(f), text)
        for m in BAD_EXIT[lang].finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            out.append({"path": f, "line": line, "code": int(next(g for g in m.groups() if g))})
    return out


def facts(sources):
    """What the conformance test reads from source: flags named, prompts, writes, network, exit codes, verbs."""
    own = sources[0][1]
    every = "\n".join(t for _, t in sources)
    lang = _lang(pathlib.Path(sources[0][0]), own)
    # A wrapper that only hands its arguments to another program handles --help where that program does.
    delegates = lang == "sh" and bool(re.search(r'^\s*exec\s.*"\$@"', own, re.M)) and len(sources) > 1
    f = {k: bool(rx.search(every)) for k, rx in FACTS.items()}
    f["help"] = bool(FACTS["help"].search(own)) or (delegates and f["help"])
    # The argument parser that guarantees --help (exit 0) and an unknown flag (exit 2) for this executable.
    parsing = every if delegates or lang == "rs" else own
    f["parser"] = ("clap" if re.search(r"\bclap::|derive\([^)]*\bParser\b", parsing) else
                   "argparse" if "argparse" in parsing else "")
    f["lang"] = lang
    f["prompt"] = bool(PROMPT.search(every))
    writes = [m.group(0) for p, t in sources for m in WRITES[_lang(pathlib.Path(p), t)].finditer(t)
              if not NO_WRITE_TARGETS.match(t[m.start():m.end() + 12].strip())]
    f["writes"] = sorted(set(w.strip() for w in writes))[:5]
    net = NETWORK.search(every)
    f["network"] = net.group(0) if net else ""
    f["bad_exits"] = _bad_exits(sources)
    verbs = [g for m in VERB_SOURCE.finditer(every) for g in m.groups() if g]
    verbs += [g.lower() for m in RUST_VERB.finditer(every) for g in m.groups() if g]
    f["forbidden_verbs"] = sorted(set(verbs))
    f["sources"] = [p for p, _ in sources]
    return f


def _is_executable(rel, mode, text):
    path = pathlib.Path(rel)
    if SKIP.search(rel) or path.suffix not in SCRIPT_SUFFIXES and not path.name.endswith(".chroot"):
        return False
    return mode == "100755" or text.startswith("#!") or "__main__" in text


def _hook_names(repo, files):
    """Names of the files a repository's hook declarations run (pre-commit, plugin hooks)."""
    names = set()
    for f in files:
        if f.endswith((".pre-commit-hooks.yaml", "hooks.json")):
            names |= set(re.findall(r"[\w.-]+\.(?:sh|py)\b", audit._read(repo / f)))
    return names


def kind_of(root, rel, hooks):
    name = pathlib.PurePath(rel).name
    if name in UMBRELLAS:
        return "umbrella"
    if PLUGIN.match(name):
        return "plugin"
    if name in hooks or "/hooks/" in f"/{rel}" or name.endswith(".chroot"):
        return "hook"
    if "mkosi.extra/" in rel:
        return "device-binary"
    if rel.startswith("bin/") or "/bin/" in rel:
        return "host-binary"
    return "script"


def _crates(repo, files):
    """{package name: crate folder} for every Cargo package of a repository."""
    out = {}
    for f in files:
        if f.endswith("Cargo.toml"):
            m = re.search(r'^\[package\][^\[]*?^name\s*=\s*"([^"]+)"', audit._read(repo / f), re.M | re.S)
            if m:
                out[m.group(1)] = "" if f == "Cargo.toml" else str(pathlib.PurePath(f).parent)
    return out


def _cargo_bins(repo, files):
    """(binary name, crate folder, entry file) for every binary target of every Cargo package."""
    out = []
    for f in files:
        if not f.endswith("Cargo.toml"):
            continue
        try:
            data = tomllib.loads(audit._read(repo / f))
        except tomllib.TOMLDecodeError:
            continue
        pkg = data.get("package", {}).get("name")
        if not pkg:
            continue
        crate = "" if f == "Cargo.toml" else str(pathlib.PurePath(f).parent)
        prefix = f"{crate}/" if crate else ""
        bins = {b.get("name", pkg): b.get("path", f"{prefix}src/main.rs") for b in data.get("bin", [])}
        if not bins and f"{prefix}src/main.rs" in files:
            bins[pkg] = f"{prefix}src/main.rs"
        for b in files:
            m = re.fullmatch(re.escape(prefix) + r"src/bin/([\w-]+)\.rs", b)
            if m:
                bins.setdefault(m.group(1), b)
        deps = sorted(data.get("dependencies", {}))
        for name, entry in sorted(bins.items()):
            entry = entry if entry.startswith(prefix) else f"{prefix}{entry}"
            out.append((name, crate, entry, deps))
    return out


def _entry(root, path, name, kind, mode, f):
    probe, why = True, ""
    if kind in ("device-binary", "hook"):
        probe, why = False, f"{kind}: classified from source, never run"
    elif f.get("cargo"):
        probe, why = False, "Cargo binary: classified from source, not built"
    elif f["network"]:
        probe, why = False, f"network or VM ({f['network']}): classified from source, never run"
    elif not f["help"]:
        probe, why = False, "no --help handling in source: running it would run its action"
    return {"path": f"{root}/{path}", "root": root, "name": name, "kind": kind, "mode": mode, "probe": probe,
            "probe_skipped": why, "facts": f}


def inventory(ws, roots=None):
    """Every executable of the public roots (the workspace's repos.tsv), sorted by path."""
    ws = pathlib.Path(ws)
    items = []
    for root in roots_in(ws / "repos.tsv"):
        repo = ws / root
        if roots and root not in roots or not (repo / ".git").exists():
            continue
        modes = tracked_modes(repo)
        files = list(modes)
        hooks = _hook_names(repo, files)
        for rel, mode in modes.items():
            parts = pathlib.PurePath(rel).parts
            if not any(p in FOLDERS for p in parts[:-1]):
                continue
            text = audit._read(repo / rel)
            if not _is_executable(rel, mode, text):
                continue
            kind = kind_of(root, rel, hooks)
            items.append(_entry(root, rel, pathlib.PurePath(rel).name, kind, mode, facts(_closure(repo, files, rel))))
        crates = _crates(repo, files)
        for name, crate, entry, deps in _cargo_bins(repo, files):
            # The binary's crate and the crates of this repository it depends on (its errors may live there).
            dirs = [crate] + [crates[d] for d in deps if d in crates]
            srcs = [f for f in files for d in dirs if f.startswith(f"{d}/src/" if d else "src/") and f.endswith(".rs")]
            sources = [(entry, audit._read(repo / entry))] + [(s, audit._read(repo / s)) for s in srcs if s != entry]
            f = facts(sources)
            f["cargo"] = True
            full = f"{root}/{crate}" if crate else root
            kind = "umbrella" if name in UMBRELLAS else "device-binary" if DEVICE_ROOTS.match(full) else "host-binary"
            items.append(_entry(root, entry, name, kind, "cargo", f))
    return sorted(items, key=lambda e: e["path"])


def document(items):
    by_kind = {k: sum(1 for e in items if e["kind"] == k) for k in KINDS}
    return {"schema": SCHEMA, "items": items, "counts": {"total": len(items), **by_kind}}
