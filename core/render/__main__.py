"""Run odw render's engine, render/cli.py: `python3 render ARGS...`, as bin/odw-render does."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "electronics", "render"))  # the KiCad adapter (HL-71)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "mechanical", "render"))  # the CAD adapters (HL-71)

from cli import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
