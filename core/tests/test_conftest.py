import os
import subprocess


def test_fixtures_never_see_the_callers_git_environment(tmp_path):
    assert not [k for k in os.environ if k.startswith("GIT_")]
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=tmp_path, capture_output=True, text=True, check=True)
    assert os.path.realpath(out.stdout.strip()) == os.path.realpath(tmp_path)
