"""Run odw motion's engine, motion/motioncli.py: `python3 motion ARGS...`, as bin/odw-motion does."""
import sys

from motioncli import main

sys.exit(main(sys.argv[1:]))
