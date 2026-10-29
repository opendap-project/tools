"""Run a board's simulation regression checks (ngspice) against limits from its constraints and ADRs.

Usage:
    odw sim [run] [PATH] [--only GLOB]... [--json] [--report FILE] [--fetch]
                [--allow-missing-models] [--dry-run]
    odw sim list [PATH] [--json]
    odw sim fetch [PATH] [--dry-run]

PATH is a repository (its sim/*/checks.toml are run), a folder of studies (*/checks.toml) or one
study (checks.toml). Default: the current folder.

run    runs every check, prints a pass/fail table and writes a JSON report (default
       <sim folder>/results/regression.json; results/ is never committed). --json prints the
       report instead of the table. --only keeps the checks whose "study/id" matches GLOB.
       --fetch first fetches missing vendor models. A check whose model is missing is "blocked",
       which fails the run unless --allow-missing-models.
list   prints the checks without running them (--json: opendap.sim-checks/v1, its items).
fetch  fetches the vendor models the checks declare: parts-sync (parts-lib manifest.toml) into
       the parts cache, then each model file is extracted into the study, verified by sha256 and
       required to be ignored by git (vendor models are never committed).

checks.toml (one per study, next to its netlists):

    version = 1

    [[model]]                        # optional: a vendor file a check needs
    id = "tps62136"
    manufacturer = "Texas Instruments"
    mpn = "TPS62136"                 # its entry in parts-lib manifest.toml
    file = "SLVMC09.zip"             # the file in the parts cache
    member = "TPS62136_PSPICE_TRANS/TPS62136_TRANS.lib"   # inside the archive (optional)
    dest = "models/TPS62136_TRANS.lib"                    # relative to the study, git-ignored
    sha256 = "…"                     # of dest

    [[check]]
    id = "inverter-peak-current"
    description = "…"                # optional
    netlist = "tps65131-dc-load.cir" # run as ngspice -b in the study folder; or
    # command = ["uv", "run", "run.py", "step903"]   # any program, run in the study folder
    params = { vin = 2.9 }           # netlist only: overrides of existing .param values
    measure = "inv_pct"              # value of an output line "inv_pct = <number>" (.meas, print)
    # pattern = '^SUP=1 +\\S+ +(\\S+)'   # or: a regex whose first group is the value
    reduce = "max"                   # first (default) | last | min | max | all
    min = 0.0                        # at least one of min, max
    max = 80.0
    unit = "%"                       # optional, for the report
    source = "BRD-049"               # required: the constraint, ADR or requirement the limit is from
    models = ["tps62136"]            # optional: [[model]] ids it needs
    timeout = 600                    # optional, seconds (default 1800)

--dry-run (run, fetch) names the checks, report and models it would run, write and fetch, and does none of
it (example: odw sim --dry-run). Errors carry a dotted code, a hint and a fix (toolkit-host's odw.clikit).

Environment: JOBS (parallel runs, default 2), NGSPICE (the ngspice binary, default ngspice).
Exit status: 0 every check passed, 1 a check failed, errored or is blocked, 2 usage error.
Standard library only (Python 3.11 or later).
"""
import concurrent.futures as cf
import fnmatch
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import zipfile

if sys.version_info < (3, 11):
    sys.exit("odw sim: Python 3.11 or later is needed (tomllib)")
import tomllib

sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "core"))  # core/: clihost (HL-71); last: core/kicad never shadows
from clihost import load, summary  # noqa: E402

TOOL = "odw sim"
clikit = load(TOOL)

HERE = os.path.dirname(os.path.realpath(__file__))
PARTS_SYNC = os.path.join(HERE, "..", "hardware", "tools", "parts-sync.py")  # until the parts domain moves (HL-71)
NGSPICE = os.environ.get("NGSPICE", "ngspice")
NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
REDUCE = ("first", "last", "min", "max", "all")
CHECK_KEYS = {"id", "description", "netlist", "command", "params", "measure", "pattern", "reduce",
              "min", "max", "unit", "source", "models", "timeout"}
MODEL_KEYS = {"id", "manufacturer", "mpn", "file", "member", "dest", "sha256"}


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------- loading


def discover(path):
    path = os.path.abspath(path)
    if os.path.isfile(os.path.join(path, "checks.toml")):
        return os.path.dirname(path), [os.path.join(path, "checks.toml")]
    base = os.path.join(path, "sim") if os.path.isdir(os.path.join(path, "sim")) else path
    found = sorted(os.path.join(base, d, "checks.toml") for d in os.listdir(base)
                   if os.path.isfile(os.path.join(base, d, "checks.toml")))
    return base, found


def load_study(path):
    study = os.path.basename(os.path.dirname(path))
    where = os.path.relpath(path)
    try:
        data = tomllib.load(open(path, "rb"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{where}: {e}")
    if data.get("version") != 1:
        raise ConfigError(f"{where}: 'version = 1' is required")
    extra = set(data) - {"version", "model", "check"}
    if extra:
        raise ConfigError(f"{where}: unknown keys {sorted(extra)}")
    models = {}
    for m in data.get("model", []):
        if set(m) - MODEL_KEYS:
            raise ConfigError(f"{where}: model {m.get('id')}: unknown keys {sorted(set(m) - MODEL_KEYS)}")
        for k in ("id", "manufacturer", "mpn", "file", "dest", "sha256"):
            if not isinstance(m.get(k), str) or not m[k]:
                raise ConfigError(f"{where}: model {m.get('id', '?')}: '{k}' is required")
        if os.path.isabs(m["dest"]) or ".." in m["dest"].split("/"):
            raise ConfigError(f"{where}: model {m['id']}: dest must be inside the study")
        if m["id"] in models:
            raise ConfigError(f"{where}: model id {m['id']} is used twice")
        models[m["id"]] = m
    checks, ids = [], set()
    for c in data.get("check", []):
        cid = c.get("id")
        if not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", cid):
            raise ConfigError(f"{where}: every check needs an id ([A-Za-z0-9._-]+)")
        if cid in ids:
            raise ConfigError(f"{where}: check id {cid} is used twice")
        ids.add(cid)
        bad = lambda msg: ConfigError(f"{where}: check {cid}: {msg}")
        if set(c) - CHECK_KEYS:
            raise bad(f"unknown keys {sorted(set(c) - CHECK_KEYS)}")
        if ("netlist" in c) == ("command" in c):
            raise bad("needs exactly one of netlist, command")
        if "command" in c and not (isinstance(c["command"], list) and c["command"]
                                   and all(isinstance(a, str) for a in c["command"])):
            raise bad("command must be a list of strings")
        if "params" in c and "netlist" not in c:
            raise bad("params apply to a netlist only")
        if ("measure" in c) == ("pattern" in c):
            raise bad("needs exactly one of measure, pattern")
        if "pattern" in c:
            try:
                if re.compile(c["pattern"], re.M).groups < 1:
                    raise bad("pattern needs a capturing group for the value")
            except re.error as e:
                raise bad(f"pattern: {e}")
        if c.get("reduce", "first") not in REDUCE:
            raise bad(f"reduce must be one of {', '.join(REDUCE)}")
        if "min" not in c and "max" not in c:
            raise bad("needs a min or a max limit")
        for k in ("min", "max"):
            if k in c and not isinstance(c[k], (int, float)):
                raise bad(f"{k} must be a number")
        if not isinstance(c.get("source"), str) or not c["source"].strip():
            raise bad("needs the source of its limit (constraint, ADR or requirement ID)")
        for mid in c.get("models", []):
            if mid not in models:
                raise bad(f"model {mid} is not declared in a [[model]] table")
        c = dict(c, study=study, dir=os.path.dirname(path))
        checks.append(c)
    return models, checks


# ---------------------------------------------------------------- models


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def parts_cache(m):
    slug = lambda s: re.sub(r"[^A-Za-z0-9._-]+", "-", s)
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "opendap", "parts")
    return os.path.join(cache, slug(m["manufacturer"]), slug(m["mpn"]), m["file"])


def git_ignored(study_dir, rel):
    r = subprocess.run(["git", "-C", study_dir, "check-ignore", "-q", "--no-index", rel],
                       capture_output=True)
    return r.returncode == 0


def model_state(study_dir, m):
    """'ok', or why the model is not usable."""
    dest = os.path.join(study_dir, m["dest"])
    if not os.path.isfile(dest):
        return f"missing {m['dest']} (odw sim fetch)"
    if sha256(dest) != m["sha256"]:
        return f"{m['dest']} does not match its sha256"
    return "ok"


def fetch_model(study_dir, m, log):
    if not git_ignored(study_dir, m["dest"]):
        return f"{m['dest']} is not ignored by git: vendor models are never committed"
    if model_state(study_dir, m) == "ok":
        return "ok"
    src = parts_cache(m)
    if not os.path.isfile(src):
        r = subprocess.run([sys.executable, PARTS_SYNC, "--mpn", m["mpn"]], capture_output=True, text=True)
        log(r.stdout.rstrip())
        if not os.path.isfile(src):
            return f"{m['file']} is not in the parts cache: add it to parts-lib manifest.toml (mpn {m['mpn']}) or place it at {src}"
    dest = os.path.join(study_dir, m["dest"])
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    root = os.path.realpath(study_dir)
    if os.path.islink(dest) or os.path.commonpath([os.path.realpath(dest), root]) != root:
        return f"{m['dest']} is a link or resolves outside the study; refusing to write it"
    if m.get("member"):
        try:
            with zipfile.ZipFile(src) as z, z.open(m["member"]) as f:
                data = f.read()
        except (zipfile.BadZipFile, KeyError) as e:
            return f"{m['file']}: cannot extract {m['member']}: {e}"
    else:
        with open(src, "rb") as f:
            data = f.read()
    if hashlib.sha256(data).hexdigest() != m["sha256"]:
        return f"{m['file']}: {m.get('member', m['file'])} does not match its sha256 (a new model revision?)"
    with os.fdopen(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644), "wb") as f:
        f.write(data)
    return "ok"


# ---------------------------------------------------------------- running


def render_netlist(text, params):
    for key, value in params.items():
        pat = re.compile(r"(?im)^(\.param\b[^\n]*?\b" + re.escape(key) + r"\s*=\s*)(\{[^}]*\}|[^\s;]+)")
        if not pat.search(text):
            raise ConfigError(f"no .param {key} in the netlist")
        text = pat.sub(lambda mm: mm.group(1) + str(value), text)
    return text


def values_of(check, out):
    if "measure" in check:
        pat = re.compile(r"(?im)^\s*" + re.escape(check["measure"]) + r"\s*=\s*(" + NUMBER + ")")
    else:
        pat = re.compile(check["pattern"], re.M)
    vals = []
    for mm in pat.finditer(out):
        try:
            vals.append(float(mm.group(1)))
        except (TypeError, ValueError):
            pass
    return vals


def within(c, v):
    return not math.isnan(v) and ("min" not in c or v >= c["min"]) and ("max" not in c or v <= c["max"])


RUNS, RUNS_LOCK = {}, threading.Lock()


def execute(key, timeout):
    """Run one netlist or command in its study folder: (exit code, output), or (None, error)."""
    d, kind, what = key
    tmp = None
    try:
        if kind == "netlist":
            tmp = os.path.join(d, f".odapkit-sim-{hashlib.sha256(what.encode()).hexdigest()[:12]}.cir")
            with open(tmp, "w") as f:
                f.write(what)
            argv = [NGSPICE, "-b", os.path.basename(tmp)]
        else:
            argv = list(what)
        p = subprocess.run(argv, cwd=d, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + "\n" + p.stderr
    except subprocess.TimeoutExpired:
        return None, f"timeout after {timeout} s"
    except OSError as e:
        return None, str(e)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def shared_run(key, timeout):
    """Identical runs (same netlist text, or same command, in the same study) execute once; the
    checks that read them share the output, so a command never races with itself."""
    with RUNS_LOCK:
        fut = RUNS.get(key)
        owner = fut is None
        if owner:
            fut = RUNS[key] = cf.Future()
    if owner:
        fut.set_result(execute(key, timeout))
    return fut.result()


def run_check(c, models):
    res = {k: c[k] for k in ("study", "id", "description", "netlist", "command", "params", "measure",
                             "pattern", "reduce", "min", "max", "unit", "source", "models") if k in c}
    for mid in c.get("models", []):
        state = model_state(c["dir"], models[mid])
        if state != "ok":
            return dict(res, status="blocked", detail=f"model {mid}: {state}")
    t0 = time.monotonic()
    try:
        if "netlist" in c:
            with open(os.path.join(c["dir"], c["netlist"])) as f:
                text = render_netlist(f.read(), c.get("params", {}))
            key = (c["dir"], "netlist", text)
        else:
            key = (c["dir"], "command", tuple(c["command"]))
    except (OSError, ConfigError) as e:
        return dict(res, status="error", detail=str(e))
    rc, out = shared_run(key, c.get("timeout", 1800))
    if rc is None:
        return dict(res, status="error", detail=out)
    res["seconds"] = round(time.monotonic() - t0, 2)
    vals = values_of(c, out)
    if not vals:
        tail = "\n".join(out.strip().splitlines()[-8:])
        return dict(res, status="error", detail=f"no value found (exit {rc}); output ends:\n{tail}")
    how = c.get("reduce", "first")
    if how == "all":
        res["values"] = vals
        bad = [v for v in vals if not within(c, v)]
        res["value"] = bad[0] if bad else (min(vals) if "min" in c and "max" not in c else max(vals))
        res["status"] = "fail" if bad else "pass"
    else:
        v = {"first": vals[0], "last": vals[-1], "min": min(vals), "max": max(vals)}[how]
        res["value"] = v
        res["status"] = "pass" if within(c, v) else "fail"
    return res


# ---------------------------------------------------------------- output


def fmt(v):
    return "" if v is None else f"{v:.4g}"


def table(results):
    rows = [("study", "check", "value", "min", "max", "unit", "source", "status")]
    for r in results:
        val = fmt(r.get("value"))
        if r.get("values"):
            val = f"{fmt(min(r['values']))}..{fmt(max(r['values']))}"
        rows.append((r["study"], r["id"], val, fmt(r.get("min")), fmt(r.get("max")),
                     r.get("unit", ""), r["source"], r["status"].upper()))
    w = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(cell.ljust(w[i]) for i, cell in enumerate(row)).rstrip() for row in rows]
    lines.insert(1, "  ".join("-" * x for x in w))
    for r in results:
        if r.get("detail"):
            lines.append(f"{r['study']}/{r['id']}: {r['detail']}")
    return "\n".join(lines)


def ngspice_version():
    try:
        out = subprocess.run([NGSPICE, "--version"], capture_output=True, text=True, timeout=30).stdout
        m = re.search(r"ngspice-(\S+)", out)
        return m.group(1) if m else None
    except (OSError, subprocess.TimeoutExpired):
        return None


# ---------------------------------------------------------------- commands


def parse(argv):
    opts = {"cmd": "run", "path": ".", "only": [], "json": False, "report": None, "fetch": False,
            "allow_missing": False, "dry_run": False}
    args = list(argv)
    if args and args[0] in ("run", "list", "fetch"):
        opts["cmd"] = args.pop(0)
    paths = []
    while args:
        a = args.pop(0)
        if a == "--only" and args:
            opts["only"].append(args.pop(0))
        elif a == "--report" and args:
            opts["report"] = args.pop(0)
        elif a == "--json":
            opts["json"] = True
        elif a == "--fetch":
            opts["fetch"] = True
        elif a == "--allow-missing-models":
            opts["allow_missing"] = True
        elif a == "--dry-run":
            opts["dry_run"] = True
        elif a.startswith("-"):
            return None
        else:
            paths.append(a)
    if len(paths) > 1:
        return None
    if paths:
        opts["path"] = paths[0]
    return opts


def main(argv):
    if argv and argv[0] in ("-h", "--help", "help"):
        print(f"{TOOL}: {summary(clikit, 'sim')}\n" + __doc__.split("\n", 1)[1])
        return 0
    opts = parse(argv)
    fail = lambda code, msg, hint, fix, status=1: clikit.report(  # noqa: E731
        TOOL, clikit.CliError(code, msg, hint, fix, status), "--json" in argv)
    if opts is None or not os.path.isdir(opts["path"]):
        return fail("input.invalid", "bad arguments" if opts is None else f"no folder {opts['path']}",
                    "one PATH at most, and only the flags --help lists", f"{TOOL} --help", 2)
    base, files = discover(opts["path"])
    if not files:
        return fail("checks.not_found", f"no checks.toml under {opts['path']}",
                    "a study keeps its checks in sim/<study>/checks.toml", f"{TOOL} list PATH")
    models, checks = {}, []
    try:
        for f in files:
            m, c = load_study(f)
            models[os.path.dirname(f)] = m
            checks += c
    except ConfigError as e:
        return fail("checks.invalid", str(e), "the checks.toml format is in odw sim --help", f"{TOOL} --help")
    if opts["only"]:
        checks = [c for c in checks if any(fnmatch.fnmatch(f"{c['study']}/{c['id']}", g) for g in opts["only"])]
    log = lambda s: s and print(s, file=sys.stderr)

    if opts["cmd"] == "list":
        if opts["json"]:
            print(json.dumps({"schema": "opendap.sim-checks/v1",
                              "items": [{k: v for k, v in c.items() if k != "dir"} for c in checks]}, indent=2))
        else:
            for c in checks:
                lim = " ".join(f"{k} {c[k]:g}" for k in ("min", "max") if k in c)
                print(f"{c['study']}/{c['id']}: {c.get('measure') or 'pattern'} {lim} {c.get('unit', '')} [{c['source']}]")
        return 0

    if opts["cmd"] == "fetch" or opts["fetch"]:
        needed = {(c["dir"], mid) for c in checks for mid in c.get("models", [])}
        if opts["cmd"] == "fetch":
            needed = {(d, mid) for d, ms in models.items() for mid in ms}
        if opts["dry_run"]:
            for d, mid in sorted(needed):
                print(f"{TOOL}: would fetch {os.path.basename(d)}/{mid}", file=sys.stderr)
            if opts["cmd"] == "fetch":
                return 0
        ok = True
        for d, mid in sorted(needed):
            state = fetch_model(d, models[d][mid], log)
            print(f"{os.path.basename(d)}/{mid}: {state}", file=sys.stderr)
            ok = ok and state == "ok"
        if opts["cmd"] == "fetch":
            return 0 if ok else 1

    if opts["dry_run"]:
        for c in checks:
            print(f"{TOOL}: would run {c['study']}/{c['id']}", file=sys.stderr)
        report = opts["report"] or os.path.join(base, "results", "regression.json")
        print(f"{TOOL}: would write {report}", file=sys.stderr)
        return 0
    jobs = max(1, int(os.environ.get("JOBS", "2")))
    with cf.ThreadPoolExecutor(jobs) as ex:
        results = list(ex.map(lambda c: run_check(c, models[c["dir"]]), checks))
    failed = [r for r in results if r["status"] in ("fail", "error")
              or (r["status"] == "blocked" and not opts["allow_missing"])]
    report = {
        "schema": "opendap.sim-report/v1",
        "ok": not failed,
        "ngspice": ngspice_version(),
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "counts": {s: sum(r["status"] == s for r in results) for s in ("pass", "fail", "error", "blocked")},
        "checks": results,
    }
    path = opts["report"] or os.path.join(base, "results", "regression.json")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(report, f, indent=2)
    if opts["json"]:
        print(json.dumps(report, indent=2))
    else:
        print(table(results))
        c = report["counts"]
        print(f"\n{c['pass']} passed, {c['fail']} failed, {c['error']} errors, {c['blocked']} blocked"
              f" — report: {path if os.path.relpath(path).startswith('..') else os.path.relpath(path)}")
    return 0 if report["ok"] else 1

