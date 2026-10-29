"""odw audit: workspace metrics measured from every repository's tracked files.

Usage: odw audit [--workspace DIR] [--rename-map TSV] [--write DIR [--dry-run]] [--json]

Repositories come from every repos.tsv at the workspace root or two levels below it (the public list and any
overlay). Metrics keep the keys of a programme's baseline so snapshots compare over time; --write appends a dated
snapshot to DIR/drift.toml and rewrites DIR/conformance.toml (one table per repository). With --rename-map, tracked
files that no row covers are listed; --dry-run with --write names the files and writes nothing. --json prints
opendap.workspace-audit/v1. Exit status: 0 measured, 2 usage (no workspace).
"""
import datetime as dt
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

from odw import clikit, workspace

TOOL = "odw audit"
SCHEMA = "opendap.workspace-audit/v1"

# The shared hooks: toolkit-hooks, or the merged tools root that serves them after the tools cut.
HOOKS_REPO = re.compile(r"repo:\s*https://github\.com/opendap-project/(?:toolkit-hooks|tools)\s*\n\s*rev:\s*(\S+)")
LOWER_OPEN_SOURCE = re.compile(r"\bopen source\b")
INLINE_HISTORY = re.compile(r"\(v\d+\.\d+[:,]|\bformerly\b|\bwas renamed\b|\bpreviously\b|\bsuperseded\b", re.I)
WORKING_NOTE = re.compile(r"\bfor now\b|\bnext step\b|\bnot started\b|\bTODO\b|\bwill take this over\b", re.I)
RDNS_ORG = re.compile(r"\borg\.opendap\.[A-Za-z]")
OPENDAP_ENV = re.compile(r"\bOPENDAP_[A-Z][A-Z0-9_]*")
BUDGETS = {".rs": ("rust_files_over_600_lines", 600), ".slint": ("slint_files_over_500_lines", 500),
           ".py": ("python_files_over_500_lines", 500)}
# HL-64: a test that runs git under a pre-commit hook inherits GIT_DIR, GIT_INDEX_FILE and others and acts on the
# repository being committed. Running git: subprocess (Python), Command::new (Rust), a `git` command (shell).
# Protected: the file, or for Python a conftest.py above it, removes every GIT_* variable from the environment
# (Rust: env_remove of each GIT_* variable, or env_clear).
RUNS_GIT = {".py": re.compile(r"\bsubprocess\b"), ".rs": re.compile(r"\bCommand::new\("),
            ".sh": re.compile(r"^[^#\n]*(?:^|[\s;&|(])git\s", re.M)}
STRIPS_GIT = {".py": re.compile(r"startswith\([\"']GIT_[\"']\).{0,40}?(?:del os\.environ|os\.environ\.pop|delenv)", re.S),
              ".rs": re.compile(r"\benv_clear\(\)|\"GIT_\".{0,80}?env_remove", re.S),
              ".sh": re.compile(r"GIT_.{0,60}?\bunset\b|\bunset\b.{0,60}?GIT_", re.S)}
TEXT = {".rs", ".py", ".sh", ".toml", ".md", ".slint", ".yaml", ".yml", ".json", ".conf", ".service", ".rules", ""}


def repositories(ws):
    lists = [ws / "repos.tsv", *sorted(ws.glob("*/*/repos.tsv"))]
    seen = []
    for f in lists:
        for line in f.read_text().splitlines()[1:]:
            if line.strip():
                path = line.split("\t")[0]
                if path not in seen:
                    seen.append(path)
    return seen


def git_env():
    """The environment without GIT_* variables, so a hook's GIT_INDEX_FILE or GIT_DIR never redirects git."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


# The tool's own runtime, which a hook of a commit it makes must not inherit (HL-109): uv's virtual environment (first
# on PATH, so the hook's python3 would be ours, with odw installed) and the folders the dispatcher exports.
RUNTIME = ("VIRTUAL_ENV", "UV_RUN_RECURSION_DEPTH", "ODAP_TOOLS_DIR")


def hook_env():
    """The environment a plain `git commit` from the maintainer's shell has: git_env() without the tool's runtime
    (RUNTIME, ODAP_TOOLKIT_*_DIR) and without the virtual environment's bin/ on PATH."""
    env = git_env()
    venv = env.get("VIRTUAL_ENV")
    for k in [k for k in env if k in RUNTIME or (k.startswith("ODAP_TOOLKIT_") and k.endswith("_DIR"))]:
        del env[k]
    if venv and "PATH" in env:
        drop = os.path.join(venv, "bin")
        env["PATH"] = os.pathsep.join(p for p in env["PATH"].split(os.pathsep) if p.rstrip("/") != drop)
    return env


def tracked(repo):
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True, env=git_env()).stdout
    return [p for p in out.decode().split("\0") if p]


def _read(path):
    try:
        return path.read_text(errors="replace")
    except (OSError, UnicodeDecodeError):
        return ""


def _covered(path, patterns, exact):
    if path in exact:
        return True
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def _test_code(f, text):
    """The part of a tracked file that is test code: a file under tests/ or named test_*, or a #[cfg(test)] module."""
    parts = f.split("/")
    if "tests" in parts[:-1] or parts[-1].startswith("test_") or parts[-1] == "conftest.py":
        return text
    if f.endswith(".rs") and "#[cfg(test)]" in text:
        return text[text.index("#[cfg(test)]"):]
    return None


def unprotected_git_tests(repo, files):
    """The test files that run git without removing the GIT_* variables a hook exports (HL-64)."""
    conftests = {os.path.dirname(f) for f in files if f.endswith("/conftest.py") or f == "conftest.py"
                 if STRIPS_GIT[".py"].search(_read(repo / f))}
    out = []
    for f in files:
        suffix = pathlib.PurePath(f).suffix
        if suffix not in RUNS_GIT:
            continue
        code = _test_code(f, _read(repo / f))
        if code is None or not RUNS_GIT[suffix].search(code) or STRIPS_GIT[suffix].search(code):
            continue
        dirs = ["/".join(f.split("/")[:i]) for i in range(len(f.split("/")))]
        if suffix == ".py" and any(d in conftests for d in dirs):
            continue
        out.append(f)
    return sorted(out)


def audit(ws, rename_map=None):
    ws = pathlib.Path(ws)
    paths = repositories(ws)
    present = [p for p in paths if (ws / p / ".git").exists()]
    r = {"repositories": {}, "software": {**{k: 0 for k, _ in BUDGETS.values()}, "test_files_running_git_unprotected": 0},
         "docs": {"docs_markdown_files": 0, "specs_with_inline_history": 0, "docs_with_working_notes": 0},
         "identifiers": {"rdns_org_opendap_uses": 0, "opendap_env_uses": 0}, "conformance": {}}
    pins, variants, no_pc, lower, rules = set(), set(), [], 0, 0
    all_files = []
    for p in present:
        repo = ws / p
        files = tracked(repo)
        all_files += [f"{p}/{f}" for f in files]
        pc = repo / ".pre-commit-config.yaml"
        pin = None
        if pc.is_file():
            text = pc.read_text()
            variants.add(hashlib.sha256(text.encode()).hexdigest())
            m = HOOKS_REPO.search(text)
            pin = m.group(1) if m else None
            if pin:
                pins.add(pin)
        else:
            no_pc.append(p)
        claude = _read(repo / "CLAUDE.md")
        low = bool(LOWER_OPEN_SOURCE.search(claude))
        lower += low
        n_rules = sum(1 for f in files if f.startswith(".claude/rules/"))
        rules += n_rules
        for f in files:
            path = repo / f
            suffix = path.suffix
            if suffix in BUDGETS:
                key, limit = BUDGETS[suffix]
                if _read(path).count("\n") > limit:
                    r["software"][key] += 1
            if f.startswith("docs/") and suffix == ".md":
                r["docs"]["docs_markdown_files"] += 1
                text = _read(path)
                if f.startswith("docs/specs/") and INLINE_HISTORY.search(text):
                    r["docs"]["specs_with_inline_history"] += 1
                if WORKING_NOTE.search(text):
                    r["docs"]["docs_with_working_notes"] += 1
            if suffix in TEXT and not f.startswith("docs/"):
                text = _read(path)
                r["identifiers"]["rdns_org_opendap_uses"] += len(RDNS_ORG.findall(text))
                r["identifiers"]["opendap_env_uses"] += len(OPENDAP_ENV.findall(text))
        leaky = unprotected_git_tests(repo, files)
        r["software"]["test_files_running_git_unprotected"] += len(leaky)
        r["conformance"][p] = {"pre_commit": pc.is_file(), "hooks_pin": pin or "", "claude_md_lowercase_open_source": low,
                               "claude_rules": n_rules, "tracked_files": len(files),
                               "tests_running_git_unprotected": leaky}
    r["repositories"] = {"total": len(present), "missing": [p for p in paths if p not in present],
                         "pre_commit_variants": len(variants), "hooks_pins": sorted(pins),
                         "claude_rules_copies": rules, "claude_md_lowercase_open_source": lower,
                         "repos_without_pre_commit": no_pc}
    if rename_map:
        exact, patterns = set(), []
        for line in pathlib.Path(rename_map).read_text().splitlines()[1:]:
            old = line.split("\t")[0]
            if "*" in old:
                patterns.append(old)
            else:
                exact.add(old)
        r["rename_map"] = {"unmapped": [f for f in all_files if not _covered(f, patterns, exact)]}
        r["rename_map"]["unmapped_count"] = len(r["rename_map"]["unmapped"])
    return r


def _toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return json.dumps(v, ensure_ascii=False)


def write(result, out, date=None, dry_run=False):
    out = pathlib.Path(out)
    if dry_run:
        for name in ("drift.toml", "conformance.toml"):
            print(f"{TOOL}: would write {out / name}", file=sys.stderr)
        return
    out.mkdir(parents=True, exist_ok=True)
    date = date or dt.date.today().isoformat()
    snap = [f"\n[[snapshot]]", f'date = "{date}"']
    for section in ("repositories", "software", "docs", "identifiers"):
        for k, v in result[section].items():
            snap.append(f"{section}.{k} = {_toml_value(v)}")
    if "rename_map" in result:
        snap.append(f"rename_map.unmapped_count = {result['rename_map']['unmapped_count']}")
    drift = out / "drift.toml"
    head = "" if drift.is_file() else "# @generated by odw audit: one snapshot per run. Do not edit.\n"
    with drift.open("a") as fh:
        fh.write(head + "\n".join(snap) + "\n")
    lines = ["# @generated by odw audit: one table per repository. Do not edit.", f'date = "{date}"']
    for repo, row in result["conformance"].items():
        lines.append(f'\n["{repo}"]')
        lines += [f"{k} = {_toml_value(v)}" for k, v in row.items()]
    (out / "conformance.toml").write_text("\n".join(lines) + "\n")


def main(argv=None):
    return clikit.main(TOOL, _main, argv)


def _main(argv):
    ap = clikit.Parser(prog=TOOL, summary=clikit.summary("audit"), description="Workspace metrics from tracked files.",
                       examples=["odw audit --json", "odw audit --write baseline --dry-run"])
    ap.add_argument("--workspace")
    ap.add_argument("--rename-map")
    ap.add_argument("--write", metavar="DIR")
    ap.add_argument("--dry-run", action="store_true", help="with --write: name the files and write nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    ws = pathlib.Path(args.workspace) if args.workspace else workspace.root()
    if ws is None:
        raise clikit.no_workspace(TOOL)
    result = audit(ws, rename_map=args.rename_map)
    if args.write:
        write(result, args.write, dry_run=args.dry_run)
    if args.json:
        print(json.dumps({"schema": SCHEMA, **result}, ensure_ascii=False, indent=1))
    else:
        for section in ("repositories", "software", "docs", "identifiers"):
            for k, v in result[section].items():
                print(f"{section}.{k} = {v}")
        if "rename_map" in result:
            print(f"rename_map.unmapped_count = {result['rename_map']['unmapped_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
