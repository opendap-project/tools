"""The OpenDAP PCB craft rules as data: load tools/craft/rules.toml and render the standard's tables.

rules.toml (schema opendap.craft-rules/v1, schemas/craft-rules.schema.json) holds every CRAFT rule
of the craft standard with its level, text, value, reason, source and enforcement, and the limits the
checks read. The craft checks import this module; `odapkit craft rules render` writes the standard's
rule tables from it, between the markers

    <!-- >>> odapkit craft rules §<section>: ... -->
    <!-- <<< odapkit craft rules -->

Runs on Python 3.9 (KiCad's bundled Python, which has no tomllib): without tomllib the file is read
through $ODAPKIT_PYTHON3 (default python3), as the checks read their configuration.
"""
import json
import os
import re
import subprocess
import sys

RULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.toml")
SCHEMA = "opendap.craft-rules/v1"
COLUMNS = ("ID", "Level", "Rule", "Value", "Why", "Source", "Enforcement")
FIELDS = ("id", "level", "rule", "value", "why", "source", "enforcement")
BEGIN = "<!-- >>> odapkit craft rules §{section}: rendered from toolkit-hardware tools/craft/rules.toml; edit it, not this table -->"
END = "<!-- <<< odapkit craft rules -->"
BLOCK = re.compile(r"<!-- >>> odapkit craft rules §(?P<section>[0-9.]+):[^\n]*-->\n(?P<body>.*?)" + re.escape(END), re.S)


def _read_toml(path):
    try:
        import tomllib  # Python 3.11+
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except ImportError:
        py = os.environ.get("ODAPKIT_PYTHON3", "python3")
        res = subprocess.run([py, "-c", "import json,sys,tomllib;print(json.dumps(tomllib.load(open(sys.argv[1],'rb'))))", path],
                             capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"cannot read {path} (needs Python 3.11+ as $ODAPKIT_PYTHON3): {res.stderr.strip()}")
        return json.loads(res.stdout)


class Rules:
    """The rule set: levels, the standard's URL, and each rule by ID."""

    def __init__(self, data):
        if data.get("schema") != SCHEMA:
            raise ValueError(f"rules: schema {data.get('schema')!r}, expected {SCHEMA!r}")
        self.spec = data["spec"]
        self.levels = tuple(data["levels"])
        self.rules = list(data["rule"])
        self.by_id = {}
        for r in self.rules:
            if r["id"] in self.by_id:
                raise ValueError(f"rules: {r['id']} twice")
            if r["level"] not in self.levels:
                raise ValueError(f"rules: {r['id']} level {r['level']!r} not in {self.levels}")
            self.by_id[r["id"]] = r

    def level(self, rule_id):
        return self.by_id[rule_id]["level"]

    def limit(self, rule_id, key):
        """A limit stated in the rule's row; KeyError if the data does not carry it."""
        return self.by_id[rule_id]["limits"][key]

    def series(self, rule_id, lo, step, hi):
        """The values from limit lo to limit hi in steps of limit step, both ends included."""
        a, d, b = (self.limit(rule_id, k) for k in (lo, step, hi))
        return tuple(round(a + k * d, 4) for k in range(int(round((b - a) / d)) + 1))

    def sections(self):
        out = []
        for r in self.rules:
            if r["section"] not in out:
                out.append(r["section"])
        return out

    def table(self, section):
        """The Markdown table of one section of the standard."""
        rows = [r for r in self.rules if r["section"] == section]
        if not rows:
            raise KeyError(f"no rules in section {section}")
        lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
        for r in rows:
            cells = [r[f] for f in FIELDS]
            for c in cells:
                if "|" in c or "\n" in c:
                    raise ValueError(f"rules: {r['id']}: a cell holds '|' or a newline")
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines) + "\n"


def load(path=RULES):
    return Rules(_read_toml(path))


def render(text, rules):
    """The standard with every marked table rendered from the rules. Each section with rules is
    marked exactly once."""
    found = []

    def block(m):
        sec = m.group("section")
        found.append(sec)
        return BEGIN.format(section=sec) + "\n\n" + rules.table(sec) + "\n" + END

    out = BLOCK.sub(block, text)
    missing = [s for s in rules.sections() if s not in found]
    twice = sorted({s for s in found if found.count(s) > 1})
    if missing or twice:
        raise ValueError("marked tables: " + "; ".join(
            ([f"missing §{', §'.join(missing)}"] if missing else []) + ([f"twice §{', §'.join(twice)}"] if twice else [])))
    return out


USAGE = """usage: odapkit craft rules render SPEC.md [--check] [--rules FILE] [--json]

Writes the standard's rule tables in SPEC.md from the rules data (default tools/craft/rules.toml), between
their `odapkit craft rules` markers; --check only reports a stale table (exit 1). --json prints
opendap.craft-rules-render/v1: spec, current, written.

Example: odapkit craft rules render docs/specs/pcb-craft-standard.md --check --json
Exit status: 0 current or written, 1 stale or invalid rules, 2 usage error."""


def _usage_error(message):
    print(f"odapkit craft rules: error input.invalid: {message}\n  hint: {USAGE.splitlines()[0]}\n"
          "  fix: odapkit craft rules --help", file=sys.stderr)
    return 2


def main(argv):
    args = list(argv)
    if any(a in ("-h", "--help") for a in args):
        print(USAGE)
        return 0
    check, as_json = "--check" in args, "--json" in args
    args = [a for a in args if a not in ("--check", "--json")]
    path = RULES
    if "--rules" in args:
        i = args.index("--rules")
        if i + 1 >= len(args):
            return _usage_error("--rules needs a FILE")
        path = args[i + 1]
        del args[i:i + 2]
    flags = [a for a in args if a.startswith("-")]
    if flags:
        return _usage_error(f"unknown flag {flags[0]}")
    if len(args) != 2 or args[0] != "render":
        return _usage_error("render SPEC.md is the one command")
    spec = args[1]
    with open(spec, encoding="utf-8") as fh:
        text = fh.read()
    try:
        out = render(text, load(path))
    except (ValueError, KeyError) as e:
        print(f"odapkit craft rules: {spec}: {e}", file=sys.stderr)
        return 1
    written = not check and out != text
    if as_json:
        print(json.dumps({"schema": "opendap.craft-rules-render/v1", "spec": spec, "current": out == text,
                          "written": written}))
    if check:
        if out != text:
            print(f"odapkit craft rules: {spec} is not current (run: odapkit craft rules render {spec})", file=sys.stderr)
            return 1
        return 0
    if written:
        with open(spec, "w", encoding="utf-8") as fh:
            fh.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
