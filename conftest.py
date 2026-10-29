"""Every test runs without the caller's GIT_* variables.

Under a pre-commit hook, git exports GIT_DIR, GIT_INDEX_FILE and others; a fixture's own `git init` or
`git config` would then act on the repository being committed (it once wrote a placeholder identity and
core.bare into this clone's config). Clearing them here makes every fixture repository its own.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def no_inherited_git_environment(monkeypatch):
    for k in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(k)
