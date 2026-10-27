"""toolkit-host's command-line helpers (odw.clikit) for this repository's Python tools (command standard §6).

`load()` returns odw.clikit from toolkit-host: host/ of this repository when this checkout is one of its folders
(the merged tools root), else ODAP_TOOLKIT_HOST_DIR (odapkit sets it), else the workspace's repos.tsv (a folder
inside the workspace only); without it, the tool stops with the error
`repo.not_found`, its hint and its fix. `summary(name)` is the tool's summary in this root's tools.toml.
Works on Python 3.9 (KiCad's).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_TOML = os.path.join(ROOT, "tools.toml")


def sibling_host(start):
    """host/ of the same repository when `start` is one of its folders (the merged tools root): the nearest
    folder at or above `start` with a .git is the repository's top, and its host/ holds odw.clikit."""
    d = start = os.path.realpath(start)
    while not os.path.exists(os.path.join(d, ".git")):
        if d == os.path.dirname(d):
            return None
        d = os.path.dirname(d)
    helper = os.path.join(d, "host", "src", "odw", "clikit.py")
    mine = d != start and os.lstat(os.path.join(d, ".git")).st_uid == os.getuid()  # this user's repository only
    if mine and os.path.isfile(helper) and os.stat(helper).st_uid == os.getuid():
        return os.path.join(d, "host")
    return None


def host_dir():
    sibling = sibling_host(ROOT)
    if sibling:
        return sibling
    if os.environ.get("ODAP_TOOLKIT_HOST_DIR"):
        return os.environ["ODAP_TOOLKIT_HOST_DIR"]
    d = ROOT
    while d != os.path.dirname(d):
        tsv = os.path.join(d, "repos.tsv")
        # Only a workspace this user owns: a repos.tsv someone else wrote never names code to import.
        if os.path.isfile(tsv) and os.stat(tsv).st_uid == os.getuid():
            for line in open(tsv).read().splitlines()[1:]:
                path, _, slug = line.partition("\t")
                full = os.path.realpath(os.path.join(d, path))
                if slug.split("/")[-1] == "toolkit-host" and full.startswith(os.path.realpath(d) + os.sep) \
                        and os.path.isdir(full):
                    return full
            return None
        d = os.path.dirname(d)
    return None


def load(tool):
    host = host_dir()
    helper = os.path.join(host or "", "src", "odw", "clikit.py")
    if not host or not os.path.isfile(helper) or os.stat(helper).st_uid != os.getuid():
        sys.stderr.write(f"{tool}: error repo.not_found: no toolkit-host checkout\n"
                         "  hint: toolkit-host's odw.clikit runs this tool's command line\n"
                         "  fix: export ODAP_TOOLKIT_HOST_DIR=<toolkit-host checkout>\n")
        sys.exit(2)
    sys.path.insert(0, os.path.join(host, "src"))
    from odw import clikit
    return clikit


def summary(clikit, name):
    return clikit.summary(name, TOOLS_TOML)
