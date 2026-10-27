"""odw cli probes: run an executable's safe probes (--help, an unknown flag, a read-only --json call).

Every probe runs with stdin at end of file and no terminal, a timeout, an empty temporary working folder, and,
on macOS, inside a sandbox that denies the network and every file write outside that folder, the uv cache and
Python virtual environments: a probe never changes state even when the executable ignores its arguments.
Spec: docs/specs/cli-conformance.md.
"""
import concurrent.futures
import os
import pathlib
import shutil
import signal
import subprocess
import tempfile

UNKNOWN_FLAG = "--odw-no-such-flag"
# Read-only invocations whose --json output the conformance test parses, by executable name ({workspace} is
# replaced by the workspace folder). An executable that declares --json and is not listed here is unverified.
HOST_FIX = "{workspace}/public/toolkit/host/tests/fixtures"
JSON_PROBES = {
    "odapkit": ["help", "--json"],
    "odapkit-audit": ["--workspace", "{workspace}", "--json"],
    "odapkit-cli": ["list", "--workspace", "{workspace}", "--root", "public/toolkit/host", "--json"],
    "odapkit-docs": ["check", "--json", "{workspace}/public/toolkit/host/README.md"],
    "odapkit-noindex": ["--dry-run", "--json", "{workspace}/public/toolkit/host"],
    "odapkit-plan": ["--programme", f"{HOST_FIX}/programme", "--json", "list"],
    # No public rename map exists to plan from: the probe reads the error document of a refused call.
    "odapkit-rename": ["plan", "no-such-root", "--workspace", "{workspace}", "--map", "/dev/null", "--json"],
    "odapkit-req": ["index", "--workspace", "{workspace}", "--json"],
    "odapkit-sync": ["--workspace", "{workspace}", "--check", "--json"],
    "odapkit-verify": ["coverage", f"{HOST_FIX}/verification/matrix.toml", "--workspace", "{workspace}", "--json"],
}


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


def run(argv, timeout=20.0, workspace=None):
    """One probe: {argv, exit, stdout, stderr, timed_out, sandboxed}."""
    with tempfile.TemporaryDirectory(prefix="odw-cli-probe-") as tmp:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(TMPDIR=tmp, NO_COLOR="1")
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
    base = command(pathlib.Path(ws) / entry["path"], entry["mode"])
    res = {"help": run(base + ["--help"], timeout, ws), "unknown_flag": run(base + [UNKNOWN_FLAG], timeout, ws)}
    args = JSON_PROBES.get(entry["name"])
    if args:
        res["json"] = run(base + [a.replace("{workspace}", str(ws)) for a in args], timeout, ws)
    return res


def probe_all(entries, ws, timeout=20.0, jobs=4):
    """{path: probes} for every entry, `jobs` at a time."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {e["path"]: pool.submit(probe, e, ws, timeout) for e in entries}
        return {path: f.result() for path, f in futures.items()}
