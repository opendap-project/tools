"""Run odw variant's engine, tools/variant/cli.py: `python3 tools/variant ARGS...`, as bin/odw-variant does."""
import sys

from cli import main

sys.exit(main(sys.argv[1:]))
