"""Run odapkit component's engine, tools/component/validate.py: run by bin/odapkit-component
in the tools' environment (tools/pyproject.toml, tools/uv.lock)."""
import sys

from validate import main

sys.exit(main(sys.argv[1:]))
