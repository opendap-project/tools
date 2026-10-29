"""odw render's FreeCAD and Blender adapters (RFC 0021 §3).

- `freecad`: the model as glTF (binary), through FreeCAD's exporter in `freecadcmd` ($ODAP_FREECADCMD, else
  FreeCAD.app's, else the first `freecadcmd` on PATH). PNG views need FreeCAD's 3D view, which freecadcmd has
  not: a `views` list on a FreeCAD source is `render.unavailable`.
- `blender`: PNG views with the Workbench engine from fixed orthographic cameras framed on the scene's meshes,
  and glTF (binary), through `blender --background --factory-startup` ($ODAP_BLENDER, else Blender.app's, else
  the first `blender` on PATH).
"""
import os
import shutil
import subprocess

import normalize
from normalize import read_bytes
from kicad import Unavailable, _run

FREECAD_SCRIPT = r'''
import sys, FreeCAD, Import
src, out = sys.argv[-2], sys.argv[-1]
doc = FreeCAD.openDocument(src)
objs = [o for o in doc.RootObjects if hasattr(o, "Shape")]
Import.export(objs, out)
'''

BLENDER_SCRIPT = r'''
import sys, math, bpy, mathutils
args = sys.argv[sys.argv.index("--") + 1:]
out, glb, w, h, views = args[0], args[1], int(args[2]), int(args[3]), [v for v in args[4].split(",") if v]
scene = bpy.context.scene
meshes = [o for o in scene.objects if o.type == "MESH"]
pts = [o.matrix_world @ mathutils.Vector(c) for o in meshes for c in o.bound_box] or [mathutils.Vector((0, 0, 0))]
lo = mathutils.Vector([min(p[i] for p in pts) for i in range(3)])
hi = mathutils.Vector([max(p[i] for p in pts) for i in range(3)])
centre, size = (lo + hi) / 2, max((hi - lo).length, 1e-3)
DIRS = {"front": (0, -1, 0), "back": (0, 1, 0), "top": (0, 0, 1), "bottom": (0, 0, -1),
        "left": (-1, 0, 0), "right": (1, 0, 0), "iso": (1, -1, 1)}
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = w, h, 100
scene.render.image_settings.file_format = "PNG"
scene.render.film_transparent = False
cam_data = bpy.data.cameras.new("odw-render")
cam_data.type, cam_data.ortho_scale = "ORTHO", size * 1.1
cam = bpy.data.objects.new("odw-render", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
for v in views:
    d = mathutils.Vector(DIRS[v]).normalized()
    cam.location = centre + d * size * 2
    up = mathutils.Vector((0, 1, 0)) if abs(d.z) > 0.99 else mathutils.Vector((0, 0, 1))
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y" if up.y else "Z").to_euler()
    cam_data.clip_end = size * 10
    scene.render.filepath = "%s/%s.png" % (out, v)
    bpy.ops.render.render(write_still=True)
bpy.data.objects.remove(cam)
if glb != "-":
    bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", use_selection=False, export_cameras=False,
                              export_lights=False)
'''

BLENDER_VIEWS = ("front", "back", "top", "bottom", "left", "right", "iso")


def _find(env, app_path, names):
    if os.environ.get(env):
        return os.environ[env]
    for c in [app_path] + [shutil.which(n) for n in names]:
        if c and os.access(c, os.X_OK):
            return c
    return None


def freecadcmd():
    return _find("ODAP_FREECADCMD", "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
                 ("freecadcmd", "FreeCADCmd"))


def blender():
    return _find("ODAP_BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender", ("blender",))


def _version(cmd, arg="--version"):
    r = subprocess.run([cmd, arg], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    lines = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip()]
    return lines[-1].strip() if cmd.endswith(("freecadcmd", "FreeCADCmd")) and lines else (lines[0].strip() if lines else None)


def _env():
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


class FreeCAD:
    type = "freecad"
    defaults = {"views": [], "glb": True}

    def program(self):
        cmd = freecadcmd()
        return ("freecad", _version(cmd)) if cmd else None

    def inputs(self, comp, src):
        return {src["path"]: os.path.join(comp, src["path"])}

    def check_options(self, src):
        if src["views"]:
            return "PNG views need FreeCAD's 3D view, which freecadcmd has not (RFC 0021 §3); leave views empty"
        return None

    def generate(self, comp, src, work):
        cmd = freecadcmd()
        if not cmd:
            raise Unavailable("freecadcmd not found (install FreeCAD or set ODAP_FREECADCMD)")
        out = {}
        if src["glb"]:
            script, glb = os.path.join(work, "export.py"), os.path.join(work, "model.glb")
            with open(script, "w") as fh:
                fh.write(FREECAD_SCRIPT)
            _run([cmd, script, os.path.join(comp, src["path"]), glb], _env())
            name = os.path.splitext(os.path.basename(src["path"]))[0]
            out[name + ".glb"] = read_bytes(glb)
        return out


class Blender:
    type = "blender"
    defaults = {"views": ["front", "top", "iso"], "glb": True, "size": [1600, 1200]}

    def program(self):
        cmd = blender()
        return ("blender", _version(cmd)) if cmd else None

    def inputs(self, comp, src):
        return {src["path"]: os.path.join(comp, src["path"])}

    def check_options(self, src):
        bad = [v for v in src["views"] if v not in BLENDER_VIEWS]
        return "unknown views %s (the views are %s)" % (", ".join(bad), ", ".join(BLENDER_VIEWS)) if bad else None

    def generate(self, comp, src, work):
        cmd = blender()
        if not cmd:
            raise Unavailable("blender not found (install Blender or set ODAP_BLENDER)")
        name = os.path.splitext(os.path.basename(src["path"]))[0]
        script, pngs = os.path.join(work, "render.py"), os.path.join(work, "views")
        glb = os.path.join(work, "model.glb") if src["glb"] else "-"
        os.makedirs(pngs, exist_ok=True)
        with open(script, "w") as fh:
            fh.write(BLENDER_SCRIPT)
        w, h = src["size"]
        _run([cmd, "--background", "--factory-startup", os.path.join(comp, src["path"]), "--python", script, "--",
              pngs, glb, str(w), str(h), ",".join(src["views"])], _env())
        out = {"render/%s.png" % v: normalize.png(read_bytes(os.path.join(pngs, v + ".png")))
               for v in src["views"]}
        if src["glb"]:
            out[name + ".glb"] = read_bytes(glb)
        return out
