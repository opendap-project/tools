"""odw render's engine (RFC 0021): find components, generate their outputs, write and check their stamps.

A component is a folder with an `outputs.toml` (`opendap.outputs/v1`); its outputs live in `outputs/` beside it,
with `outputs/stamp.json` (`opendap.outputs-stamp/v1`) and a generated `outputs/README.md`.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import tomllib

import cad
import kicad_render as kicad
import normalize
from normalize import read_bytes

MANIFEST, FOLDER, STAMP, INDEX = "outputs.toml", "outputs", "stamp.json", "README.md"
SCHEMA, STAMP_SCHEMA = "opendap.outputs/v1", "opendap.outputs-stamp/v1"
FORMAT = 1                      # bumped when a change in this tool changes its outputs: every stamp goes stale
MAX_FILE = 5 * 1024 * 1024      # per file (§8.1), check-added-large-files' limit
MAX_COMPONENT = 100 * 1024 * 1024
ADAPTERS = {a.type: a for a in (kicad.Sch(), kicad.Pcb(), cad.FreeCAD(), cad.Blender())}
SKIP_DIRS = {".git", "node_modules", "target", "__pycache__", FOLDER}


class Problem(Exception):
    def __init__(self, code, message, hint, fix, status=1):
        super().__init__(message)
        self.code, self.message, self.hint, self.fix, self.status = code, message, hint, fix, status


def sha(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return "missing"
    return h.hexdigest()


# ---------------------------------------------------------------- components

def _git_manifests(top):
    r = subprocess.run(["git", "-C", top, "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--",
                        ":(glob)**/" + MANIFEST, MANIFEST], capture_output=True, stdin=subprocess.DEVNULL,
                       env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    if r.returncode != 0:
        return None
    return sorted({os.path.dirname(os.path.join(top, p.decode())) for p in r.stdout.split(b"\0") if p})


def _walk_manifests(top):
    found = []
    for d, dirs, files in os.walk(top):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        if MANIFEST in files:
            found.append(d)
    return found


def components(path):
    """Component folders for PATH: the component that holds it, and every component at or below a folder. A
    PATH that no longer exists (a file a commit deletes) names only the component that held it."""
    path = os.path.abspath(path)
    up = path
    while not os.path.isdir(up):
        up = os.path.dirname(up)
    start, owner = up, None
    while True:
        if os.path.isfile(os.path.join(up, MANIFEST)):
            owner = up
            break
        # a repository's top ends the search; a submodule PATH itself (its .git a file) does not
        if (up != start and os.path.exists(os.path.join(up, ".git"))) or up == os.path.dirname(up):
            break
        up = os.path.dirname(up)
    below = []
    # a folder: the components below it too; but not a submodule's own (its .git a file), unless it is where
    # the tool runs (a linked worktree's top has a .git file as well)
    sub = os.path.isfile(os.path.join(path, ".git")) and path != os.path.abspath(os.getcwd())
    if os.path.isdir(path) and not sub:
        below = _git_manifests(path)
        below = _walk_manifests(path) if below is None else below
    return sorted({c for c in ([owner] if owner else []) + below})


def load(comp):
    """The component's sources, each with its adapter's defaults filled in."""
    path = os.path.join(comp, MANIFEST)
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise Problem("outputs.invalid", "%s: not valid TOML: %s" % (path, e), "outputs.toml is opendap.outputs/v1",
                      "odw render --help", 2) from None
    if data.get("schema") != SCHEMA or not isinstance(data.get("source"), list) or not data["source"]:
        raise Problem("outputs.invalid", "%s: needs schema = %r and at least one [[source]]" % (path, SCHEMA),
                      "the format is in odw render --help", "odw render --help", 2)
    sources, names = [], set()
    for i, s in enumerate(data["source"]):
        src = _source(path, i, s)
        if src["name"] in names:
            raise Problem("outputs.invalid", "%s: two sources named %r" % (path, src["name"]),
                          "each source's name is its folder under outputs/", "odw render --help", 2)
        names.add(src["name"])
        sources.append(src)
    return sources


def _source(path, i, s):
    where = "%s: source %d" % (path, i + 1)
    a = ADAPTERS.get(s.get("type"))
    if a is None or not isinstance(s.get("path"), str):
        raise Problem("outputs.invalid", "%s: type is one of %s, with a path" % (where, ", ".join(ADAPTERS)),
                      "the format is in odw render --help", "odw render --help", 2)
    allowed = set(a.defaults) | {"type", "path", "name"}
    extra = sorted(set(s) - allowed)
    if extra:
        raise Problem("outputs.invalid", "%s: unknown keys %s" % (where, ", ".join(extra)),
                      "a %s source takes %s" % (a.type, ", ".join(sorted(allowed))), "odw render --help", 2)
    p = s["path"]
    if os.path.isabs(p) or ".." in p.split("/") or not os.path.isfile(os.path.join(os.path.dirname(path), p)):
        raise Problem("outputs.invalid", "%s: no file %s in the component" % (where, p),
                      "path is relative to the folder of outputs.toml, inside it", "odw render --help", 2)
    src = dict(a.defaults, **s)
    src.setdefault("name", os.path.splitext(os.path.basename(p))[0])
    bad = a.check_options(src) if hasattr(a, "check_options") else None
    if bad:
        raise Problem("outputs.invalid", "%s: %s" % (where, bad), "the options are in odw render --help",
                      "odw render --help", 2)
    return src


def options_hash(src):
    return sha(json.dumps({"format": FORMAT, **src}, sort_keys=True).encode())


def inputs_of(comp, src):
    return {k: sha_file(v) for k, v in sorted(ADAPTERS[src["type"]].inputs(comp, src).items())}


# ---------------------------------------------------------------- update

def generate(comp, sources, log):
    """{output path under outputs/: bytes} and the stamp entries, from every source."""
    files, entries = {}, []
    for src in sources:
        a = ADAPTERS[src["type"]]
        prog = a.program()
        log("%s: %s %s" % (os.path.basename(comp), src["type"], src["path"]))
        with tempfile.TemporaryDirectory(prefix="odw-render-") as work:
            try:
                out = a.generate(comp, src, work)
            except kicad.Unavailable as e:
                raise Problem("render.unavailable", "%s: %s" % (src["path"], e), "odw render list shows each adapter",
                              "odw render list --json") from None
            except RuntimeError as e:
                raise Problem("render.failed", "%s: %s" % (src["path"], e), "the program's message is above",
                              "odw render update %s" % comp) from None
        out = {"%s/%s" % (src["name"], k): v for k, v in sorted(out.items())}
        files.update(out)
        entries.append({"name": src["name"], "type": src["type"], "path": src["path"], "options": options_hash(src),
                        "programs": {prog[0]: prog[1]} if prog else {}, "inputs": inputs_of(comp, src),
                        "outputs": {k: sha(v) for k, v in out.items()}})
    return files, entries


def keep_close_renders(comp, files):
    """A regenerated PNG within normalize.png_close of the committed one keeps the committed bytes (§5.4)."""
    for k, v in files.items():
        old = os.path.join(comp, FOLDER, k)
        if k.endswith(".png") and os.path.isfile(old):
            prev = read_bytes(old)
            if prev != v and normalize.png_close(prev, v):
                files[k] = prev


def index(comp, entries):
    """outputs/README.md: each source and its outputs, renders inline (markdownlint-clean)."""
    lines = ["# Outputs", "", "Generated by `odw render update` from `%s` (RFC 0021); never edited by hand." % MANIFEST, ""]
    for e in entries:
        lines += ["## %s" % e["name"], "", "`%s` (%s)" % (e["path"], e["type"]), ""]
        outs = sorted(e["outputs"])
        for k in [k for k in outs if k.endswith(".png")]:
            lines += ["![%s](%s)" % (k.split("/")[-1][:-4], _url(k)), ""]
        lines += ["- [`%s`](%s)" % (k[len(e["name"]) + 1:], _url(k)) for k in outs]
        lines.append("")
    return ("\n".join(lines).rstrip("\n") + "\n").encode()


def _url(path):
    return "".join(c if c.isalnum() or c in "/._-" else "%%%02X" % ord(c) for c in path)


def stamp_bytes(entries):
    return (json.dumps({"schema": STAMP_SCHEMA, "format": FORMAT, "sources": entries}, indent=1, sort_keys=True)
            + "\n").encode()


def update(comp, log, dry_run=False):
    """Regenerate the component's outputs; returns [(path under outputs/, action)] with action written,
    removed or unchanged."""
    sources = load(comp)
    files, entries = generate(comp, sources, log)
    keep_close_renders(comp, files)
    for e in entries:
        e["outputs"] = {k: sha(files[k]) for k in e["outputs"]}
    big = [(k, len(v)) for k, v in files.items() if len(v) > MAX_FILE]
    if big:
        raise Problem("render.too_large", ", ".join("%s is %.1f MB" % (k, n / 1e6) for k, n in big),
                      "a file stays within 5 MB (RFC 0021 §8.1)", "reduce layers, views or size in %s" % MANIFEST)
    if sum(len(v) for v in files.values()) > MAX_COMPONENT:
        raise Problem("render.too_large", "%s/outputs over 100 MB" % comp, "a component stays within 100 MB",
                      "reduce layers, views or size in %s" % MANIFEST)
    files[INDEX] = index(comp, entries)
    files[STAMP] = stamp_bytes(entries)
    return _write(os.path.join(comp, FOLDER), files, dry_run)


def _write(folder, files, dry_run):
    actions = []
    for d, _, names in os.walk(folder):
        for n in names:
            rel = os.path.relpath(os.path.join(d, n), folder)
            if rel not in files:
                actions.append((rel, "removed"))
                if not dry_run:
                    os.remove(os.path.join(d, n))
    for rel, data in sorted(files.items()):
        p = os.path.join(folder, rel)
        same = os.path.isfile(p) and read_bytes(p) == data
        actions.append((rel, "unchanged" if same else "written"))
        if not same and not dry_run:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(data)
    if not dry_run:
        for d, dirs, names in sorted(os.walk(folder, topdown=False)):
            if d != folder and not os.listdir(d):
                os.rmdir(d)
    return sorted(actions)


# ---------------------------------------------------------------- check

def check(comp):
    """[(id, verdict, message)] for one component: its stamp is current (RFC 0021 §4.3)."""
    sources, folder = load(comp), os.path.join(comp, FOLDER)
    stamp_path = os.path.join(folder, STAMP)
    try:
        with open(stamp_path, encoding="utf-8") as fh:
            stamp = json.load(fh)
    except (OSError, ValueError):
        return [("stamp", "fail", "no readable %s/%s: the outputs were never generated" % (FOLDER, STAMP))]
    if stamp.get("schema") != STAMP_SCHEMA:
        return [("stamp", "fail", "%s/%s is not %s" % (FOLDER, STAMP, STAMP_SCHEMA))]
    by_name, out = {e.get("name"): e for e in stamp.get("sources", [])}, []
    listed = {STAMP, INDEX}
    for src in sources:
        out += _check_source(comp, folder, src, by_name.get(src["name"]))
        listed |= set((by_name.get(src["name"]) or {}).get("outputs", {}))
    for name in sorted(set(by_name) - {s["name"] for s in sources}):
        out.append(("%s/stamp" % name, "fail", "the stamp lists source %s, which outputs.toml no longer does" % name))
    for d, _, names in os.walk(folder):
        for n in names:
            rel = os.path.relpath(os.path.join(d, n), folder)
            if rel not in listed:
                out.append((rel, "fail", "%s/%s is not a generated output (not in the stamp)" % (FOLDER, rel)))
    want = index(comp, [by_name[s["name"]] for s in sources if s["name"] in by_name])
    if not out and sha_file(os.path.join(folder, INDEX)) != sha(want):
        out.append((INDEX, "fail", "%s/%s differs from the one the stamp gives" % (FOLDER, INDEX)))
    return out or [("current", "pass", "outputs current")]


def _check_source(comp, folder, src, entry):
    n = src["name"]
    if entry is None:
        return [("%s/stamp" % n, "fail", "source %s is not in the stamp: never generated" % n)]
    out = []
    if entry.get("type") != src["type"] or entry.get("path") != src["path"] or entry.get("options") != options_hash(src):
        out.append(("%s/options" % n, "fail", "the source's type, path or options changed since the outputs were made"))
    old, new = entry.get("inputs", {}), inputs_of(comp, src)
    changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
    if changed:
        more = " and %d more" % (len(changed) - 3) if len(changed) > 3 else ""
        out.append(("%s/inputs" % n, "fail", "inputs changed: %s%s" % (", ".join(changed[:3]), more)))
    for k, h in sorted(entry.get("outputs", {}).items()):
        if sha_file(os.path.join(folder, k)) != h:
            out.append((k, "fail", "%s/%s is missing or edited" % (FOLDER, k)))
    return out


def programs():
    """[(type, program, version or None)] for `odw render list`."""
    rows = []
    for t, a in ADAPTERS.items():
        p = a.program()
        rows.append((t, p[0] if p else {"kicad-sch": "kicad", "kicad-pcb": "kicad"}.get(t, t), p[1] if p else None))
    return rows
