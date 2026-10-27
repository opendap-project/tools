# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6", "jsonschema>=4.18"]
# ///
"""Run odapkit component's engine, tools/component/validate.py: run as a uv script by bin/odapkit-component
(uv installs the dependencies above)."""
import sys

from validate import main

sys.exit(main(sys.argv[1:]))
