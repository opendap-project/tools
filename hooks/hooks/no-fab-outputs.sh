#!/bin/sh
# Reject generated fab outputs; they are produced locally and attached to releases.
[ $# -eq 0 ] && exit 0
echo "Generated fab outputs must not be committed (use /opendap:fab-package):" >&2
for f in "$@"; do echo "  $f" >&2; done
exit 1
