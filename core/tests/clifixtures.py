"""Helpers the command-line tests of several domains share (HL-71): fixture repositories and the tools root's
folders. On the path through the root's pytest configuration (`pythonpath`)."""
import pathlib
import subprocess
import textwrap

from odw import audit

TOP = pathlib.Path(__file__).resolve().parents[2]  # the tools root
HOST = TOP / "host"  # the odapkit alias and the odw dispatch crate


def git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, env=audit.git_env(), capture_output=True)


def repo(root, path, files):
    """A fixture git repository at root/path with `files` ({name: (text, mode)}), committed."""
    d = root / path
    for name, (text, mode) in files.items():
        f = d / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(text))
        f.chmod(mode)
    git(["init", "-q"], d)
    git(["add", "-A"], d)
    git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], d)


def by_name(items):
    return {e["name"]: e for e in items}
