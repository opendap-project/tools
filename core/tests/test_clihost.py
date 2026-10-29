"""core/clihost.py (HL-71): an engine of any domain folder gets odw.clikit from core/, beside it, and its --help
summary from the tools.toml of the folder that declares the tool."""
import os
import subprocess
import sys
import tomllib

CORE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(code):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ODAP_", "GIT_"))}
    return subprocess.run([sys.executable, "-c", f"import sys; sys.path.append({CORE!r}); {code}"],
                          capture_output=True, text=True, env=env, cwd="/")


def test_load_returns_clikit_from_core_beside_it():
    r = run("import clihost; c = clihost.load('t'); print(c.__file__)")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == os.path.join(CORE, "odw", "clikit.py")


def test_summary_reads_the_declaring_folders_tools_toml():
    r = run("import clihost; c = clihost.load('t'); print(clihost.summary(c, 'sim')); print(clihost.summary(c, 'component'))")
    assert r.returncode == 0, r.stderr
    declared = {t["name"]: t["summary"] for d in ("hardware", "core")
                for t in tomllib.loads(open(os.path.join(CORE, "..", d, "tools.toml")).read())["tool"]}
    assert r.stdout.splitlines() == [declared["sim"], declared["component"]]
