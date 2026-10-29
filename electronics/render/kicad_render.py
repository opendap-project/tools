"""odw render's KiCad adapters (RFC 0021 §3): `kicad-sch` and `kicad-pcb`, both through kicad-cli.

kicad-cli runs with a fresh KICAD_CONFIG_HOME that holds only the source's declared colour theme, so a
maintainer's own KiCad settings never reach an output (§5.1). kicad-cli is $KICAD_CLI, else KiCad.app's
($KICAD_APP, default /Applications/KiCad/KiCad.app), else the first on PATH.
"""
import json
import os
import re
import shutil
import subprocess

import normalize
from normalize import read_bytes

DEFAULT_LAYERS_EXTRA = ["F.Silkscreen", "B.Silkscreen", "F.Fab", "B.Fab"]
# view -> kicad-cli pcb render arguments; fixed, never the program's last session (§5.3)
VIEWS = {
    "top": ["--side", "top"],
    "bottom": ["--side", "bottom"],
    "iso-front": ["--side", "top", "--rotate", "-45,0,45"],
    "iso-back": ["--side", "bottom", "--rotate", "-45,0,45"],
}


class Unavailable(Exception):
    """The program an adapter needs is missing: `render.unavailable`."""


def kicad_cli():
    if os.environ.get("KICAD_CLI"):
        return os.environ["KICAD_CLI"]
    app = os.environ.get("KICAD_APP") or "/Applications/KiCad/KiCad.app"
    for c in (os.path.join(app, "Contents", "MacOS", "kicad-cli"), shutil.which("kicad-cli")):
        if c and os.access(c, os.X_OK):
            return c
    return None


def model_dir(cli):
    """KiCad's stock 3D models (KICAD10_3DMODEL_DIR): the environment's, else beside the app's kicad-cli."""
    if os.environ.get("KICAD10_3DMODEL_DIR"):
        return os.environ["KICAD10_3DMODEL_DIR"]
    contents = os.path.dirname(os.path.dirname(os.path.realpath(cli)))
    return os.path.join(contents, "SharedSupport", "3dmodels")


def version():
    cli = kicad_cli()
    if not cli:
        return None
    r = subprocess.run([cli, "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r.stdout.strip() or None


def _run(cmd, env):
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError("%s failed (exit %d): %s" % (" ".join(cmd[:4]), r.returncode, (r.stderr or r.stdout).strip()[-400:]))


def _env(work, theme):
    cfg = os.path.join(work, "kicad-config")
    colors = os.path.join(cfg, "10.0", "colors")
    os.makedirs(colors, exist_ok=True)
    if theme:
        shutil.copyfile(theme, os.path.join(colors, os.path.basename(theme)))
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["KICAD_CONFIG_HOME"] = cfg
    return env


def _theme_args(theme):
    return ["--theme", os.path.splitext(os.path.basename(theme))[0]] if theme else []


def _project(path):
    return os.path.splitext(path)[0] + ".kicad_pro"


def sheet_files(root):
    """The root sheet and every sheet it reaches (KiCad's `Sheetfile` property), relative to its folder."""
    folder, todo, seen = os.path.dirname(root), [os.path.basename(root)], []
    while todo:
        f = todo.pop()
        if f in seen or not os.path.isfile(os.path.join(folder, f)):
            continue
        seen.append(f)
        with open(os.path.join(folder, f), encoding="utf-8") as fh:
            todo += re.findall(r'\(property "Sheetfile" "([^"]+)"', fh.read())
    return sorted(seen)


def _text_vars(pro):
    try:
        with open(pro, encoding="utf-8") as fh:
            return json.load(fh).get("text_variables", {}) or {}
    except (OSError, ValueError):
        return {}


def drawing_sheet(path):
    """The project's drawing sheet (`page_layout_descr_file`), resolved; None for KiCad's default."""
    pro = _project(path)
    try:
        with open(pro, encoding="utf-8") as fh:
            f = (json.load(fh).get("schematic", {}) or {}).get("page_layout_descr_file", "")
    except (OSError, ValueError):
        return None
    if not f:
        return None
    folder = os.path.dirname(os.path.abspath(path))
    f = f.replace("${KIPRJMOD}", folder)
    return os.path.normpath(f if os.path.isabs(f) else os.path.join(folder, f))


def board_models(pcb, cli):
    """Every 3D model the board references: {variable path: resolved path}."""
    with open(pcb, encoding="utf-8") as fh:
        refs = sorted(set(re.findall(r'\(model "([^"]+)"', fh.read())))
    folder = os.path.dirname(os.path.abspath(pcb))
    names = dict(_text_vars(_project(pcb)), KIPRJMOD=folder, KICAD10_3DMODEL_DIR=model_dir(cli) if cli else "")
    out = {}
    for ref in refs:
        p = ref
        for _ in range(4):  # variables may name variables (OPENDAP_KICAD_3DMODELS uses KIPRJMOD)
            p = re.sub(r"\$\{([A-Za-z0-9_]+)\}", lambda m: names.get(m.group(1), m.group(0)), p)
        out[ref] = os.path.normpath(p if os.path.isabs(p) else os.path.join(folder, p))
    return out


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "root"


class Sch:
    type = "kicad-sch"
    defaults = {"kicad_theme": ""}

    def program(self):
        return ("kicad", version()) if kicad_cli() else None

    def inputs(self, comp, src):
        path = os.path.join(comp, src["path"])
        folder = os.path.dirname(src["path"])
        files = [os.path.join(folder, f) for f in sheet_files(path)] + [_project(src["path"])]
        if src["kicad_theme"]:
            files.append(src["kicad_theme"])
        out = {f: os.path.join(comp, f) for f in files}
        wks = drawing_sheet(path)
        if wks:
            rel = os.path.relpath(wks, comp)
            out[rel if not rel.startswith("..") else wks] = wks
        return out

    def generate(self, comp, src, work):
        cli = kicad_cli()
        if not cli:
            raise Unavailable("kicad-cli not found (install KiCad 10 or set KICAD_CLI)")
        theme = os.path.join(comp, src["kicad_theme"]) if src["kicad_theme"] else ""
        env, path = _env(work, theme), os.path.join(comp, src["path"])
        out = {}
        pdf = os.path.join(work, "schematic.pdf")
        _run([cli, "sch", "export", "pdf", *_theme_args(theme), "-o", pdf, path], env)
        out["schematic.pdf"] = normalize.pdf(read_bytes(pdf))
        svgdir = os.path.join(work, "svg")
        _run([cli, "sch", "export", "svg", *_theme_args(theme), "-o", svgdir, path], env)
        stem = os.path.splitext(os.path.basename(path))[0]
        for f in sorted(os.listdir(svgdir)):
            name = os.path.splitext(f)[0]
            name = name[len(stem):] if name.startswith(stem) else name
            out["schematic/%s.svg" % _slug(name)] = normalize.svg(read_bytes(os.path.join(svgdir, f)))
        return out


class Pcb:
    type = "kicad-pcb"
    defaults = {"kicad_theme": "", "layers": [], "views": list(VIEWS), "size": [1600, 1200], "glb": False}

    def program(self):
        return ("kicad", version()) if kicad_cli() else None

    def inputs(self, comp, src):
        files = [src["path"], _project(src["path"])] + ([src["kicad_theme"]] if src["kicad_theme"] else [])
        out = {f: os.path.join(comp, f) for f in files}
        out.update(board_models(os.path.join(comp, src["path"]), kicad_cli()))
        return out

    def layers(self, path, src):
        if src["layers"]:
            return list(src["layers"])
        with open(path, encoding="utf-8") as fh:
            head = fh.read(20000)
        copper = re.findall(r'\(\d+ "((?:F|B|In\d+)\.Cu)" (?:signal|power|mixed|jumper)', head)
        return copper + DEFAULT_LAYERS_EXTRA

    def generate(self, comp, src, work):
        cli = kicad_cli()
        if not cli:
            raise Unavailable("kicad-cli not found (install KiCad 10 or set KICAD_CLI)")
        theme = os.path.join(comp, src["kicad_theme"]) if src["kicad_theme"] else ""
        env, path = _env(work, theme), os.path.join(comp, src["path"])
        stem, out = os.path.splitext(os.path.basename(path))[0], {}
        svgdir = os.path.join(work, "pcb")
        _run([cli, "pcb", "export", "svg", "--mode-multi", "--layers", ",".join(self.layers(path, src)),
              "--cl", "Edge.Cuts", "--page-size-mode", "2", "--exclude-drawing-sheet", *_theme_args(theme),
              "-o", svgdir, path], env)
        for f in sorted(os.listdir(svgdir)):
            name = os.path.splitext(f)[0]
            name = name[len(stem) + 1:] if name.startswith(stem + "-") else name
            out["pcb/%s.svg" % name] = normalize.svg(read_bytes(os.path.join(svgdir, f)))
        w, h = src["size"]
        for view in src["views"]:
            png = os.path.join(work, view + ".png")
            _run([cli, "pcb", "render", *VIEWS[view], "--width", str(w), "--height", str(h), "--quality", "basic",
                  "--background", "opaque", "-o", png, path], env)
            out["render/%s.png" % view] = normalize.png(read_bytes(png))
        if src["glb"]:
            glb = os.path.join(work, stem + ".glb")
            _run([cli, "pcb", "export", "glb", "-o", glb, path], env)
            out[stem + ".glb"] = read_bytes(glb)
        return out

    def check_options(self, src):
        bad = [v for v in src["views"] if v not in VIEWS]
        return "unknown views %s (the views are %s)" % (", ".join(bad), ", ".join(VIEWS)) if bad else None
