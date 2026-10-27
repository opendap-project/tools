"""Run odapkit parts's engine, tools/sourcing/partscli.py: `python3 tools/sourcing ARGS...`, as bin/odapkit-parts does."""
import sys

from partscli import main

sys.exit(main(sys.argv[1:]))
