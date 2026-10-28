#!/bin/sh
# Reject files that should not be committed: manufacturer documents (copyright) and saved web pages.
# HTML is only allowed as standalone 3D previews under renders/web/.
status=0
for f in "$@"; do
  case "$f" in
    */renders/web/*.html|renders/web/*.html) ;;
    *.html|*.htm) echo "Stray web page (HTML is only allowed under renders/web/): $f" >&2; status=1 ;;
    *.pdf) echo "PDF documents are not committed (datasheets live in the local parts cache, see parts-lib): $f" >&2; status=1 ;;
  esac
done
exit $status
