# Export a FreeCAD document as one STEP solid (the component as a single body).
# Run with FreeCAD's headless interpreter:
#   freecadcmd mechanical/freecad_export.py -- INPUT.FCStd OUTPUT.step
# Also prints mass properties (volume, center of mass) of the exported body.
import sys
import FreeCAD
import Part

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[-2:]
src, dst = args[0], args[1]
doc = FreeCAD.openDocument(src)
shapes = [o.Shape for o in doc.Objects
          if hasattr(o, "Shape") and not o.Shape.isNull() and o.Shape.Solids
          and not o.InList]  # top-level objects only
if not shapes:
    raise SystemExit(f"no solid top-level objects in {src}")
body = Part.makeCompound(shapes)
body.exportStep(dst)
com = body.Solids[0].CenterOfMass if len(body.Solids) == 1 else sum((s.CenterOfMass * s.Volume for s in body.Solids), FreeCAD.Vector()) / body.Volume
print(f"exported {dst}: solids={len(body.Solids)} volume_mm3={body.Volume:.3f} center_of_mass_mm=({com.x:.3f}, {com.y:.3f}, {com.z:.3f})")
