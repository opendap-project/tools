"""Git steps of `odapkit rename merge`: fresh clones, a history rewritten under a folder, the checks that every
commit and tree came through. Two engines rewrite: `git filter-repo --to-subdirectory-filter` when it is
installed, and a `git fast-export | fast-import` path rewrite this module implements. Spec: docs/specs/rename.md.
"""
import os
import pathlib
import re
import shutil
import subprocess

from odw import audit

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
LFS_POINTER = "version https://git-lfs.github.com/spec/v1"
LANDING = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*")
KEEP = ("GIT_AUTHOR_", "GIT_COMMITTER_")  # identity only; GIT_DIR and the like never leak in from a hook


class GitError(Exception):
    def __init__(self, args, stderr):
        super().__init__(f"git {' '.join(args[:3])} failed: {stderr.strip()[:400]}")


def env():
    """git's environment: no GIT_* from a hook, the caller's identity, and Git LFS pointers left as they are (the
    merge moves history, never LFS objects, and never downloads them)."""
    e = audit.git_env()
    e.update({k: v for k, v in os.environ.items() if k.startswith(KEEP)})
    e["GIT_LFS_SKIP_SMUDGE"] = "1"
    return e


def git(cwd, *args, inp=None, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, input=inp, env=env())
    if check and r.returncode:
        raise GitError(args, r.stderr)
    return r.stdout


def ok(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, env=env()).returncode == 0


def has_filter_repo():
    return shutil.which("git-filter-repo") is not None and ok(".", "filter-repo", "--version")


def is_worktree(path):
    return pathlib.Path(path).is_dir() and ok(path, "rev-parse", "--is-inside-work-tree")


def toplevel(path):
    return pathlib.Path(git(path, "rev-parse", "--show-toplevel").strip()).resolve()


def dirty(repo):
    """Uncommitted or untracked files; `--no-optional-locks` so status never refreshes the source's index."""
    return [line[3:] for line in git(repo, "--no-optional-locks", "status", "--porcelain=v1",
                                     "--untracked-files=all").splitlines()]


def unpushed(repo, branch):
    """(upstream, commits on `branch` not on its upstream as last fetched); upstream None when there is none."""
    up = git(repo, "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}", check=False).strip()
    if not up:
        return None, None
    return up, int(git(repo, "rev-list", "--count", f"{up}..refs/heads/{branch}").strip())


def clone(src, dst, branch):
    """A fresh, independent clone of one branch, without tags (`--no-local`: no object is shared with `src`)."""
    r = subprocess.run(["git", "clone", "-q", "--no-local", "--no-tags", "--single-branch", "--branch", branch,
                        str(src), str(dst)], capture_output=True, text=True, env=env())
    if r.returncode:
        raise GitError(["clone", str(src), branch], r.stderr)
    return git(dst, "rev-parse", "HEAD").strip()


def remote_head(src, branch):
    """The commit `branch` points to at a URL or bundle, without cloning it (None when it is not there)."""
    out = git(".", "ls-remote", str(src), f"refs/heads/{branch}", check=False).split()
    return out[0] if out else None


def count(repo, rev):
    return int(git(repo, "rev-list", "--count", rev).strip())


def pack_kib(repo):
    stats = dict(line.split(": ", 1) for line in git(repo, "count-objects", "-v").splitlines())
    return int(stats.get("size", 0)) + int(stats.get("size-pack", 0))


def lfs_pointers(repo, rev):
    out = git(repo, "grep", "-l", "-I", "--fixed-strings", "-e", LFS_POINTER, rev, check=False)
    return len([line for line in out.splitlines() if line])


def gitlinks(repo, rev):
    out = git(repo, "ls-tree", "-r", rev).splitlines()
    return [line.split("\t", 1)[1] for line in out if line.startswith("160000")]


# --- the fast-export engine -------------------------------------------------------------------------------------

def _prefix_path(path, prefix):
    """`path` as fast-export writes it (plain, or C-quoted when it starts with a quote) under `prefix`."""
    if path.startswith(b'"'):
        return b'"' + prefix + b"/" + path[1:]
    return prefix + b"/" + path


def prefix_stream(stream, landing):
    """The fast-export `stream` (a binary file) with every file path under `landing`; data blocks pass verbatim."""
    prefix, out = landing.encode(), []
    while True:
        line = stream.readline()
        if not line:
            return b"".join(out)
        if line.startswith(b"data "):
            n = int(line[5:].strip())
            data = stream.read(n)
            if len(data) != n:
                raise ValueError("fast-export stream ended inside a data block")
            out += [line, data]
            continue
        if line.startswith(b"M "):
            mode, ref, path = line[2:].rstrip(b"\n").split(b" ", 2)
            line = b"M " + mode + b" " + ref + b" " + _prefix_path(path, prefix) + b"\n"
        elif line.startswith(b"D "):
            line = b"D " + _prefix_path(line[2:].rstrip(b"\n"), prefix) + b"\n"
        elif line[:2] in (b"C ", b"R ", b"N "):
            raise ValueError(f"unexpected fast-export command {line[:2]!r}: copies, renames and notes are not "
                             "exported without -C, -M or notes refs")
        out.append(line)


def rewrite_fast_export(clone_dir, out_dir, landing, branch, marks_dir):
    """Rewrite `branch` of `clone_dir` into a new repository `out_dir`; return its head and the commit map."""
    subprocess.run(["git", "init", "-q", "--bare", str(out_dir)], check=True, capture_output=True, env=env())
    old_marks, new_marks = marks_dir / "old.marks", marks_dir / "new.marks"
    exp = subprocess.Popen(["git", "-C", str(clone_dir), "fast-export", "--signed-tags=strip",
                            "--tag-of-filtered-object=drop", "--reencode=no", f"--export-marks={old_marks}",
                            f"refs/heads/{branch}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env())
    stream = prefix_stream(exp.stdout, landing)
    _, err = exp.communicate()
    if exp.returncode:
        raise GitError(["fast-export"], err.decode(errors="replace"))
    imp = subprocess.run(["git", "-C", str(out_dir), "fast-import", "--quiet", f"--export-marks={new_marks}"],
                         input=stream, capture_output=True, env=env())
    if imp.returncode:
        raise GitError(["fast-import"], imp.stderr.decode(errors="replace"))
    old = dict(line.split() for line in old_marks.read_text().splitlines())
    new = dict(line.split() for line in new_marks.read_text().splitlines())
    commits = set(git(clone_dir, "rev-list", f"refs/heads/{branch}").split())
    cmap = [(old[k], new[k]) for k in old if old[k] in commits and k in new]
    return git(out_dir, "rev-parse", f"refs/heads/{branch}").strip(), cmap


# --- the filter-repo engine -------------------------------------------------------------------------------------

def rewrite_filter_repo(clone_dir, landing, branch):
    """Rewrite `clone_dir` in place (a work clone of its own); return its head and the commit map."""
    git(clone_dir, "filter-repo", "--quiet", "--force", "--to-subdirectory-filter", landing,
        "--refs", f"refs/heads/{branch}")
    cmap_file = pathlib.Path(clone_dir) / ".git/filter-repo/commit-map"
    cmap = [tuple(line.split()) for line in cmap_file.read_text().splitlines()[1:] if line.strip()]
    cmap = [(a, b) for a, b in cmap if set(b) != {"0"}]  # a zero id is a commit the rewrite pruned
    return git(clone_dir, "rev-parse", f"refs/heads/{branch}").strip(), cmap


# --- checks -----------------------------------------------------------------------------------------------------

def _trees(repo, specs):
    out = git(repo, "cat-file", "--batch-check=%(objectname) %(objecttype)", inp="".join(f"{s}\n" for s in specs))
    trees = []
    for line in out.splitlines():
        parts = line.split()
        trees.append(parts[0] if len(parts) == 2 and parts[1] == "tree" else EMPTY_TREE)
    return trees


def trees_match(original, rewritten, cmap, landing):
    """Every original commit's tree is the rewritten commit's tree at `landing` (an empty tree when absent)."""
    if not cmap:
        return False
    before = _trees(original, [f"{a}^{{tree}}" for a, _ in cmap])
    after = _trees(rewritten, [f"{b}:{landing}" if landing else f"{b}^{{tree}}" for _, b in cmap])
    return before == after


def write_commit_map(path, cmap):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("old new\n" + "".join(f"{a} {b}\n" for a, b in cmap))
