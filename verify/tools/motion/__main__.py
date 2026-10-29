"""Run odw motion's engine, tools/motion/motioncli.py: `python3 tools/motion ARGS...`, as bin/odw-motion does."""
import sys

from motioncli import main

sys.exit(main(sys.argv[1:]))
