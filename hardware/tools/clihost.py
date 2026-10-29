"""toolkit-host's command-line helpers (odw.clikit) for this repository's Python tools (command standard §6).

`load()` returns odw.clikit from toolkit-host: host/ of this repository when this checkout is one of its folders
(the merged tools root), else $ODAP_TOOLS_DIR/core (odw sets ODAP_TOOLS_DIR), else ODAP_TOOLKIT_HOST_DIR
(deprecated; odapkit sets it), else the workspace's repos.tsv (a folder
inside the workspace only); without it, the tool stops with the error
`repo.not_found`, its hint and its fix. `summary(name)` is the tool's summary in this root's tools.toml.
Works on Python 3.9 (KiCad's).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_TOML = os.path.join(ROOT, "tools.toml")


def alias_home(host):
    """The helpers' folder for the deprecated ODAP_TOOLKIT_HOST_DIR: its sibling core/ when it is host/ of the tools
    root (HL-71), else the toolkit-host checkout it names."""
    core = os.path.normpath(os.path.join(host, "..", "core"))
    return core if os.path.isfile(os.path.join(core, "odw", "clikit.py")) else host


def import_root(home):
    """The folder to put on sys.path so `odw` imports from `home`: core/ holds odw/, a toolkit-host checkout src/odw/."""
    return home if os.path.isfile(os.path.join(home, "odw", "clikit.py")) else os.path.join(home, "src")


def sibling_host(start):
    """core/ of the same repository when `start` is one of its folders (the merged tools root): the nearest
    folder at or above `start` with a .git is the repository's top, and its core/ holds odw.clikit (HL-71)."""
    d = start = os.path.realpath(start)
    while not os.path.exists(os.path.join(d, ".git")):
        if d == os.path.dirname(d):
            return None
        d = os.path.dirname(d)
    helper = os.path.join(d, "core", "odw", "clikit.py")
    mine = d != start and os.lstat(os.path.join(d, ".git")).st_uid == os.getuid()  # this user's repository only
    if mine and os.path.isfile(helper) and os.stat(helper).st_uid == os.getuid():
        return os.path.join(d, "core")
    return None


def host_dir():
    sibling = sibling_host(ROOT)
    if sibling:
        return sibling
    tools = os.environ.get("ODAP_TOOLS_DIR")  # the tools root, which odw exports: its core/ holds the helpers (HL-71)
    if tools and os.path.isfile(os.path.join(tools, "core", "odw", "clikit.py")):
        return os.path.join(tools, "core")
    if os.environ.get("ODAP_TOOLKIT_HOST_DIR"):  # deprecated alias, until HL-71 phase 4
        return alias_home(os.environ["ODAP_TOOLKIT_HOST_DIR"])
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
    helper = os.path.join(import_root(host or ""), "odw", "clikit.py")
    if not host or not os.path.isfile(helper) or os.stat(helper).st_uid != os.getuid():
        sys.stderr.write(f"{tool}: error repo.not_found: no toolkit-host checkout\n"
                         "  hint: toolkit-host's odw.clikit runs this tool's command line\n"
                         "  fix: export ODAP_TOOLS_DIR=<tools checkout>\n")
        sys.exit(2)
    sys.path.insert(0, import_root(host))
    from odw import clikit
    return clikit


def summary(clikit, name):
    return clikit.summary(name, TOOLS_TOML)
