#!/bin/sh
# Runs a test suite the way a pre-commit hook does, with GIT_DIR and GIT_INDEX_FILE pointing at a scratch
# repository, and fails when the suite changed that repository (its config, refs or index): a test that let
# git inherit the hook's GIT_* variables would have written into the clone being committed.
# Usage: tests/git-leak.sh DIR COMMAND...   (exit 0 clean, 1 leaked, 2 usage; the suite's own status is printed)
set -u
[ $# -ge 2 ] || { echo "usage: git-leak.sh DIR COMMAND..." >&2; exit 2; }
dir=$1; shift
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
clean() { (for v in $(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$v"; done; "$@"); }
clean git init -q "$tmp/scratch"
clean git -C "$tmp/scratch" -c user.name=t -c user.email=t@t commit -q --allow-empty -m scratch
g="$tmp/scratch/.git"
state() { cat "$g/config"; clean git -C "$tmp/scratch" for-each-ref; cksum < "$g/index" 2>/dev/null || echo "no index"; }
state > "$tmp/before"
(cd "$dir" && GIT_DIR="$g" GIT_INDEX_FILE="$g/index" "$@") > "$tmp/out" 2>&1
echo "git-leak: suite exit $? in $dir"
state > "$tmp/after"
if ! diff -u "$tmp/before" "$tmp/after" > "$tmp/diff"; then
    echo "git-leak: LEAK: the suite changed the scratch repository:" >&2
    cat "$tmp/diff" >&2
    exit 1
fi
echo "git-leak: clean"
