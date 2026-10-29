"""The workspace: the folder that holds repos.tsv. One implementation for every maintainer tool."""
import os
import pathlib


def root(start=None):
    """The nearest folder at or above `start` (default: the current folder) that holds repos.tsv, or None.
    ODAP_WORKSPACE_DIR overrides the search."""
    env = os.environ.get("ODAP_WORKSPACE_DIR")
    if env:
        return pathlib.Path(env)
    d = pathlib.Path(start or os.getcwd()).resolve()
    for p in (d, *d.parents):
        if (p / "repos.tsv").is_file():
            return p
    return None


def state_dir(start=None):
    """<workspace>/.state, where runtime coordination (claims) lives outside git; None outside a workspace."""
    r = root(start)
    return r / ".state" if r else None
