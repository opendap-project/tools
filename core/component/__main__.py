"""Run odw component's engine, component/validate.py: run by bin/odw-component in the hardware tools'
environment (hardware/tools/pyproject.toml, hardware/tools/uv.lock, until the one Python workspace)."""
import sys

from validate import main

sys.exit(main(sys.argv[1:]))
