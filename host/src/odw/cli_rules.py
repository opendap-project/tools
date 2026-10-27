"""odw cli rules: the command standard's §5 rules as criteria, one state and reason per executable.

Each criterion applies to the surface kinds whose row of the §5 table names it and is `n/a` elsewhere. A cell
comes from the probes when the executable was run, from its source otherwise, and says which.
Spec: docs/specs/cli-conformance.md.
"""
import json
import re

ALL = {"umbrella", "plugin", "device-binary", "host-binary", "script", "hook"}
RUNNABLE = ALL - {"hook"}
BINARIES = {"umbrella", "plugin", "device-binary", "host-binary"}
PARSERS = re.compile(r"argparse|\bclap\b")
ERROR_CODE = re.compile(r"\b[a-z][a-z0-9_]*\.[a-z][a-z0-9_]+\b")
# opendap and its plugins may also use the CLI spec's exit codes 3-11 and 130 (§6).
OPENDAP_CODES = set(range(3, 12)) | {130}


def _tail(text, n=160):
    text = " ".join(text.split())
    return text[-n:] if text else "(nothing)"


def _probe_word(p):
    return "timed out" if p["timed_out"] else f"exit {p['exit']}"


def _src(e):
    return "\n".join(e["facts"].get("sources", []))


def help_(e, p, ctx):
    f = e["facts"]
    if p:
        h = p["help"]
        if h["exit"] == 0 and h["stdout"].strip():
            return "pass", "--help: exit 0, usage on stdout"
        return "fail", f"--help: {_probe_word(h)}, stdout {'empty' if not h['stdout'].strip() else 'given'}; stderr: {_tail(h['stderr'])}"
    if not f["help"]:
        return "fail", f"from source: no --help handling ({e['probe_skipped']})"
    if f.get("parser"):
        return "pass", f"from source: {f['parser']} handles --help (not run: {e['probe_skipped']})"
    return "unverified", f"--help named in source; not run ({e['probe_skipped']})"


def help_first_line(e, p, ctx):
    front, tool = e["name"].split("-", 1)
    want = f"{front} {tool}: "
    if not p:
        return "unverified", f"not run ({e['probe_skipped']})"
    first = (p["help"]["stdout"].strip().splitlines() or [""])[0]
    summary = ctx["summaries"].get(e["path"])
    if not first.startswith(want):
        return "fail", f"first line is {first[:80]!r}, not '{want}<summary>'"
    if summary and summary not in first:
        return "fail", f"first line does not carry the tools.toml summary {summary!r}"
    return "pass", f"first line {first[:80]!r}"


def unknown_flag(e, p, ctx):
    if p:
        u = p["unknown_flag"]
        if u["exit"] == 2:
            return "pass", "an unknown flag exits 2"
        return "fail", f"an unknown flag: {_probe_word(u)} (want 2); stderr: {_tail(u['stderr'], 100)}"
    if e["facts"].get("parser"):
        return "pass", f"from source: {e['facts']['parser']} exits 2 on an unknown flag (not run)"
    return "unverified", f"not run ({e['probe_skipped']})"


def _document(stdout):
    """One JSON document, or the last line of an NDJSON stream; None when neither parses."""
    for text in (stdout, (stdout.strip().splitlines() or [""])[-1]):
        try:
            return json.loads(text)
        except ValueError:
            continue
    return None


def json_(e, p, ctx):
    f = e["facts"]
    declared = f["json"] or bool(p and "--json" in p["help"]["stdout"])
    if not declared:
        if p and re.search(r"no machine output|no --json", p["help"]["stdout"], re.I):
            return "pass", "--help says the tool has no machine output"
        if e["kind"] in ("plugin", "umbrella"):
            return "fail", "no --json, and --help does not say the tool has no machine output"
        return "warning", "no --json (required where it reports state or results)"
    if not p or "json" not in p:
        return "unverified", "--json declared; no read-only --json invocation is listed to probe it"
    j = p["json"]
    doc = _document(j["stdout"])
    if doc is None:
        return "fail", f"--json ({_probe_word(j)}): stdout is not JSON; stderr: {_tail(j['stderr'], 100)}"
    if not isinstance(doc, dict) or "schema" not in doc:
        return "fail", "--json document has no schema field"
    return "pass", f"--json parses, schema {doc['schema']}"


def no_prompt(e, p, ctx):
    if e["facts"]["prompt"]:
        return "fail", "a prompt read from the terminal or stdin is in source"
    if p:
        late = [k for k in ("help", "unknown_flag") if p[k]["timed_out"]]
        if late:
            return "fail", f"with stdin at end of file and no TTY, {', '.join(late)} did not return"
        return "pass", "every probe returned with stdin at end of file and no TTY"
    return "pass", "from source: no prompt (not run)"


def exit_status(e, p, ctx):
    allowed = {0, 1, 2} | (OPENDAP_CODES if e["name"].startswith("opendap") else set())
    bad = [b for b in e["facts"]["bad_exits"] if b["code"] not in allowed]
    seen = sorted({v["exit"] for v in (p or {}).values() if v["exit"] is not None and v["exit"] not in allowed})
    if bad or seen:
        where = ", ".join(f"{b['path']}:{b['line']} exits {b['code']}" for b in bad[:3])
        return "fail", "; ".join(x for x in (where, f"probes exited {seen}" if seen else "") if x)
    return "pass", "exit status only 0, 1, 2" + ("" if p else " (from source)")


def error_code(e, p, ctx):
    f = e["facts"]
    if f["error_code"] and f["hint"]:
        return "pass", "errors carry a dotted code and a hint (from source)"
    if p and ERROR_CODE.search(p["unknown_flag"]["stderr"]) and "hint" in p["unknown_flag"]["stderr"]:
        return "pass", "a usage error carries a dotted code and a hint"
    return "fail", "errors carry no stable dotted code with a hint and a fix"


def dry_run(e, p, ctx):
    f = e["facts"]
    if not f["writes"]:
        return "n/a", "no state change found in source"
    if f["dry_run"]:
        return "pass", "changes state and takes --dry-run"
    return "fail", f"changes state ({', '.join(f['writes'][:3])}) and takes no --dry-run"


def generator_check(e, p, ctx):
    f = e["facts"]
    if not f["writes"]:
        return "n/a", "writes no files (from source)"
    if f["check"]:
        return "pass", "writes files and takes --check"
    return "fail", f"writes files ({', '.join(f['writes'][:3])}) and takes no --check"


def verbs(e, p, ctx):
    bad = list(e["facts"]["forbidden_verbs"])
    if p:
        bad += [v for v in ("info", "show", "ls", "rm", "remove")
                if re.search(rf"^\s+{v}\b", p["help"]["stdout"], re.M) and v not in bad]
    if bad:
        return "fail", f"subcommands outside the closed verb set: {', '.join(sorted(set(bad)))}"
    return "pass", "no subcommand outside the closed verb set"


def tools_toml(e, p, ctx):
    if e["path"] in ctx["summaries"]:
        return "pass", "declared in its root's tools.toml"
    return "fail", "not declared in a tools.toml"


def hook_message(e, p, ctx):
    if e["facts"]["stderr"]:
        return "pass", "reports on stderr (from source)"
    return "fail", "no message on stderr in source"


CRITERIA = [
    ("help", "--help exits 0 with usage on stdout", RUNNABLE, help_),
    ("help-first-line", "--help starts '<front> <tool>: <summary>' (§6)", {"plugin"}, help_first_line),
    ("unknown-flag", "an unknown flag exits 2", RUNNABLE, unknown_flag),
    ("json", "--json where it reports; one document with a schema", RUNNABLE, json_),
    ("no-prompt", "no prompt without a TTY", ALL, no_prompt),
    ("exit-status", "exit status 0, 1, 2", ALL, exit_status),
    ("error-code", "errors with a stable code, a hint and a fix", BINARIES, error_code),
    ("dry-run", "--dry-run for every command that changes state", BINARIES, dry_run),
    ("generator-check", "--check for generators", {"script"}, generator_check),
    ("verbs", "one verb vocabulary (get, list, … never info, show, ls)", BINARIES, verbs),
    ("tools-toml", "declared in its root's tools.toml", {"plugin"}, tools_toml),
    ("hook-message", "a hook reports what to run on stderr", {"hook"}, hook_message),
]


def cell(criterion, entry, probes, ctx):
    cid, _, kinds, fn = next(c for c in CRITERIA if c[0] == criterion)
    if entry["kind"] not in kinds:
        return "n/a", f"not in the {entry['kind']} row of §5"
    return fn(entry, probes, ctx)

