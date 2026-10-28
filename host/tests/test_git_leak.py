"""tests/git-leak.sh, the HL-64 reproduction: a suite run under a hook's GIT_DIR and GIT_INDEX_FILE must leave the
scratch repository they point at unchanged."""
import pathlib
import subprocess

import pytest

LEAK = pathlib.Path(__file__).with_name("git-leak.sh")
LEAKY = """\
import os, subprocess, tempfile, unittest
class T(unittest.TestCase):
    def test_fixture_repository(self):
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", d], check=True)
        subprocess.run(["git", "-C", d, "config", "user.name", "placeholder"], check=True)
"""
STANZA = """\
import os
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]
"""


@pytest.mark.parametrize("protected", [False, True])
def test_a_suite_that_lets_git_inherit_the_hook_variables_is_caught(tmp_path, protected):
    (tmp_path / "test_fixture.py").write_text((STANZA if protected else "") + LEAKY)
    r = subprocess.run(["sh", str(LEAK), str(tmp_path), "python3", "-m", "unittest", "discover", "."],
                       capture_output=True, text=True)
    assert "suite exit 0" in r.stdout
    if protected:
        assert r.returncode == 0, r.stderr
    else:
        assert r.returncode == 1
        assert "name = placeholder" in r.stderr


def test_usage():
    assert subprocess.run(["sh", str(LEAK)], capture_output=True).returncode == 2
