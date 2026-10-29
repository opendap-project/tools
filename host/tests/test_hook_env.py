"""HL-109 (9): a commit the rename tool makes runs its hooks in the environment a plain `git commit` has, not the
tool's own runtime (uv's virtual environment first on PATH, the dispatcher's folder variables)."""
import os
import subprocess

from odw import audit, rename, rename_config

LEAKS = {"VIRTUAL_ENV": "/elsewhere/.venv", "UV_RUN_RECURSION_DEPTH": "1", "ODAP_TOOLS_DIR": "/elsewhere",
         "ODAP_TOOLKIT_HOST_DIR": "/elsewhere/host", "ODAP_TOOLKIT_HARDWARE_DIR": "/elsewhere/hardware",
         "GIT_INDEX_FILE": "/elsewhere/index"}


def test_the_hook_environment_drops_the_tools_runtime(monkeypatch):
    for k, v in LEAKS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PATH", f"/elsewhere/.venv/bin{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setenv("ODAP_KEEP_ME", "1")  # another ODAP_ setting is the maintainer's own
    env = audit.hook_env()
    assert not set(LEAKS) & set(env)
    assert env["PATH"].split(os.pathsep) == ["/usr/bin", "/bin"] and env["ODAP_KEEP_ME"] == "1"


def test_the_rename_tools_commits_run_their_hooks_without_it(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=audit.git_env())
    out = tmp_path / "env.txt"
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text(f'#!/bin/sh\nenv > "{out}"\n')
    hook.chmod(0o755)
    (repo / "a").write_text("a\n")
    for k, v in LEAKS.items():
        if not k.startswith("GIT_"):
            monkeypatch.setenv(k, v)
    monkeypatch.setenv("PATH", f"/elsewhere/.venv/bin{os.pathsep}{os.environ['PATH']}")
    for git in (rename._git, rename_config._git):
        git(repo, "add", "a")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
        seen = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
        assert not {k for k in LEAKS if not k.startswith("GIT_")} & set(seen), git
        assert "/elsewhere/.venv/bin" not in seen["PATH"].split(os.pathsep)
