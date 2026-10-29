#!/bin/sh
# Reject files that should not be committed: manufacturer documents (copyright) and saved web pages.
# HTML is only allowed as standalone 3D previews under renders/web/. A PDF is allowed only as a generated visual
# output (RFC 0021 §7): inside an outputs/ folder with an outputs.toml beside it whose stamp.json lists it.
own_output() {
  case "$1" in
    outputs/*) top=. ;;
    */outputs/*) top="${1%%/outputs/*}" ;;
    *) return 1 ;;
  esac
  rel="${1#"$top"/outputs/}"
  [ -f "$top/outputs.toml" ] && [ -f "$top/outputs/stamp.json" ] && grep -qF "\"$rel\"" "$top/outputs/stamp.json"
}
status=0
for f in "$@"; do
  case "$f" in
    */renders/web/*.html|renders/web/*.html) ;;
    *.html|*.htm) echo "Stray web page (HTML is only allowed under renders/web/): $f" >&2; status=1 ;;
    *.pdf)
      own_output "$f" ||
        { echo "PDF documents are not committed (datasheets live in the local parts cache, see parts-lib; a generated schematic belongs in a component's outputs/, odw render update): $f" >&2; status=1; } ;;
  esac
done
exit $status
