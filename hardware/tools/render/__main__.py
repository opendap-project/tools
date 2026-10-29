"""Run odw render's engine, tools/render/cli.py: `python3 tools/render ARGS...`, as bin/odw-render does."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
