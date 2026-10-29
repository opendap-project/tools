"""odw cli probes: run an executable's safe probes (--help, an unknown flag, a read-only --json call).

A shell tool's --json call runs traced (`sh -x`, marked lines kept out of its stderr), and the probe names the
scripts of its root that call reached (`reached`): those are verified through it.

Every probe runs with stdin at end of file and no terminal, a timeout, an empty temporary working folder, and,
on macOS, inside a sandbox that denies the network and every file write outside that folder, the uv cache and
Python virtual environments: a probe never changes state even when the executable ignores its arguments.
Spec: docs/specs/cli-conformance.md.
"""
import concurrent.futures
import os
import pathlib
import re
import shlex
import shutil
import signal
import subprocess
import tempfile

UNKNOWN_FLAG = "--odw-no-such-flag"
# The umbrella's own read-only --json call (command standard §4). Every other tool declares its call in its
# tools.toml (`probe`); an executable that declares --json without one is unverified.
UMBRELLA_PROBE = ["help", "--json"]
# A shell parent's --json probe runs under `sh -x` with this mark, so the scripts it runs are known (HL-100): a script
# reached by its parent's tools.toml probe is verified through it. The marked lines are taken out of stderr.
TRACE_MARK = "odw-probe-trace "
TRACED = re.compile(r"^\++" + re.escape(TRACE_MARK))
SHELLS = {"sh", "bash", "dash", "zsh"}


def json_args(entry, ws):
    """The arguments of an entry's read-only --json probe, {root} and {workspace} resolved; None when it has none."""
    if entry.get("kind") == "umbrella":
        return list(UMBRELLA_PROBE)
    args = entry.get("json_probe")
    if not args:
        return None
    root = str(pathlib.Path(ws) / entry["declared_in"])
    return [a.replace("{root}", root).replace("{workspace}", str(ws)) for a in args]


def _uv_cache():
    env = os.environ.get("UV_CACHE_DIR")
    return pathlib.Path(env) if env else pathlib.Path.home() / ".cache" / "uv"


def sandbox_profile(tmp):
    """A macOS sandbox profile: everything allowed except the network and writes outside the listed places."""
    allowed = [pathlib.Path(tmp).resolve(), _uv_cache().resolve()]
    writable = " ".join(f'(subpath "{p}")' for p in allowed)
    return ("(version 1)(allow default)(deny network*)(deny file-write*)"
            f'(allow file-write* {writable} (subpath "/dev") (regex #"/\\.venv/"))')


def command(path, mode):
    """How to start a file: directly when executable, else through its interpreter."""
    p = str(path)
    if mode == "100755":
        return [p]
    return ["python3", p] if p.endswith(".py") else ["sh", p]


def shell_of(path):
    """The shell a script's shebang names (sh, bash, dash, zsh), or None for anything else."""
    try:
        with open(path, "rb") as f:
            first = f.readline().decode(errors="replace").strip()
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    words = first[2:].split()
    name = pathlib.PurePath(words[0]).name if words else ""
    if name == "env" and len(words) > 1:
        name = words[1]
    return name if name in SHELLS else None


def split_trace(stderr):
    """(stderr without the trace, the trace lines)."""
    kept, trace = [], []
    for line in stderr.splitlines(keepends=True):
        (trace if TRACED.match(line) else kept).append(line)
    return "".join(kept), trace


def reached(trace, ws, entry):
    """The scripts of the entry's root that a traced run started: workspace paths of the .py, .sh and extensionless
    files the trace names, other than the entry itself (paths are compared resolved, so links do not matter)."""
    root = (pathlib.Path(ws) / entry["root"]).resolve()
    own = (pathlib.Path(ws) / entry["path"]).resolve()
    out = []
    for line in trace:
        try:
            words = shlex.split(TRACED.sub("", line, count=1))
        except ValueError:
            words = line.split()
        for w in words:
            if not w.startswith("/"):
                continue
            f = pathlib.Path(w)
            if f.suffix not in (".py", ".sh", "") or not f.is_file():
                continue
            f = f.resolve()
            if f == own or root not in f.parents:
                continue
            rel = f"{entry['root']}/{f.relative_to(root).as_posix()}"
            if rel not in out:
                out.append(rel)
    return out


def run(argv, timeout=20.0, workspace=None, env_extra=None):
    """One probe: {argv, exit, stdout, stderr, timed_out, sandboxed}."""
    with tempfile.TemporaryDirectory(prefix="odw-cli-probe-") as tmp:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(TMPDIR=tmp, NO_COLOR="1", **(env_extra or {}))
        if workspace:
            env["ODAP_WORKSPACE_DIR"] = str(workspace)
        sandboxed = bool(shutil.which("sandbox-exec"))
        full = ["sandbox-exec", "-p", sandbox_profile(tmp), *argv] if sandboxed else list(argv)
        try:
            proc = subprocess.Popen(full, cwd=tmp, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, start_new_session=True)
        except OSError as e:
            return {"argv": list(argv), "exit": None, "stdout": "", "stderr": str(e), "timed_out": False,
                    "sandboxed": sandboxed}
        try:
            so, se = proc.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            # The whole process group goes, so a child holding the pipes cannot keep the probe waiting.
            os.killpg(proc.pid, signal.SIGKILL)
            so, se = proc.communicate()
            timed_out = True
        out = {"exit": None if timed_out else proc.returncode, "stdout": so.decode(errors="replace"),
               "stderr": se.decode(errors="replace"), "timed_out": timed_out}
    out.update(argv=list(argv), sandboxed=sandboxed)
    return out


def probe(entry, ws, timeout=20.0):
    """The probes of one inventory entry, or None when it may not be run."""
    if not entry["probe"]:
        return None
    # A built Cargo umbrella runs as its binary; its entry path is the source it was built from.
    base = [entry["exec"]] if entry.get("exec") else command(pathlib.Path(ws) / entry["path"], entry["mode"])
    res = {"help": run(base + ["--help"], timeout, ws), "unknown_flag": run(base + [UNKNOWN_FLAG], timeout, ws)}
    args = json_args(entry, ws)
    if not args:
        return res
    shell = None if entry.get("exec") or entry.get("kind") == "umbrella" else shell_of(base[-1])
    if not shell:
        res["json"] = run(base + args, timeout, ws)
        return res
    j = run([shell, "-x", base[-1], *args], timeout, ws, {"PS4": "+" + TRACE_MARK})
    j["argv"] = base + args
    j["stderr"], trace = split_trace(j["stderr"])
    j["reached"] = reached(trace, ws, entry)
    res["json"] = j
    return res


def probe_all(entries, ws, timeout=20.0, jobs=4):
    """{path: probes} for every entry, `jobs` at a time."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {e["path"]: pool.submit(probe, e, ws, timeout) for e in entries}
        return {path: f.result() for path, f in futures.items()}
