"""toolkit-host's command-line helpers (odw.clikit) for this repository's Python tools (command standard §6).

`load()` returns odw.clikit from toolkit-host, found through ODAP_TOOLKIT_HOST_DIR (odapkit sets it) or the
workspace's repos.tsv (a folder inside the workspace only); without it, the tool stops with the error
`repo.not_found`, its hint and its fix. `summary(name)` is the tool's summary in this root's tools.toml.
Works on Python 3.9 (KiCad's).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_TOML = os.path.join(ROOT, "tools.toml")


def host_dir():
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
