"""The shared command-line helpers (odw.clikit) for the tools root's Python engines (command standard §6).

This file lives in core/, beside the odw package that holds clikit (HL-71), so an engine of any domain folder
puts core/ on its path and calls `load()`: it returns odw.clikit, or stops with the error `repo.not_found`, its
hint and its fix when the helper is missing or not this user's. `summary(clikit, name)` is the tool's summary in
the tools.toml of the domain folder that declares it. Works on Python 3.9 (KiCad's).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(tool):
    helper = os.path.join(HERE, "odw", "clikit.py")
    if not os.path.isfile(helper) or os.stat(helper).st_uid != os.getuid():  # this user's code only
        sys.stderr.write(f"{tool}: error repo.not_found: no odw.clikit beside {HERE}\n"
                         "  hint: the tools root's core/odw/clikit.py runs this tool's command line\n"
                         "  fix: git -C <tools checkout> status core/odw\n")
        sys.exit(2)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from odw import clikit
    return clikit


def summary(clikit, name):
    return clikit.summary(name)
