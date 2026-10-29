"""Run odw sim's engine, tools/sim/simchecks.py: `python3 tools/sim ARGS...`, as bin/odw-sim does."""
import sys

from simchecks import main

sys.exit(main(sys.argv[1:]))
