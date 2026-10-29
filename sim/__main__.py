"""Run odw sim's engine, simchecks.py: `python3 sim/__main__.py ARGS...`, as bin/odw-sim does."""
import sys

from simchecks import main

sys.exit(main(sys.argv[1:]))
