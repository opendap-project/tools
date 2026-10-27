"""Build the craft-check fixtures: good.kicad_pcb passes every check, bad.kicad_pcb fails every one.

Run with KiCad's Python from any directory, then commit the regenerated boards:
  /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \
      tests/fixtures/craft/make_fixture.py

The two boards share one layout: a mirrored pair of channel blocks (groups CH-L and CH-R), a
battery connector, two mounting holes, the identity block, copper, a zone and the documentation
layers. bad.kicad_pcb changes each of them so that every check of tools/pcb-craft-check.py fails
for its own reason. Each board has a .kicad_dru with one tagged rule (FIX-01 [Q2]) to test the
DRC fold. Generation is reproducible.
"""

import json
import os
import re

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 60.0, 40.0
HOLDER = "The OpenDAP Project"


def mm(v):
    return pcbnew.FromMM(v)


def pt(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


class Fixture:
    def __init__(self, bad):
        pcbnew.KIID.SeedGenerator(7 if bad else 3)
        self.bad = bad
        self.b = pcbnew.BOARD()
        self.b.SetCopperLayerCount(2)
        self.nets = {}

    def net(self, name):
        if name not in self.nets:
            n = pcbnew.NETINFO_ITEM(self.b, name)
            self.b.Add(n)
            self.nets[name] = n
        return self.nets[name]

    def text(self, x, y, s, layer=pcbnew.F_SilkS, h=1.0, th=0.15, parent=None):
        t = pcbnew.PCB_TEXT(parent or self.b)
        t.SetText(s)
        t.SetLayer(layer)
        t.SetTextSize(pcbnew.VECTOR2I(mm(h), mm(h)))
        t.SetTextThickness(mm(th))
        t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT)
        t.SetPosition(pt(x, y))
        (parent or self.b).Add(t)
        return t

    def line(self, x1, y1, x2, y2, layer, w=0.15, parent=None):
        s = pcbnew.PCB_SHAPE(parent or self.b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pt(x1, y1))
        s.SetEnd(pt(x2, y2))
        s.SetWidth(mm(w))
        s.SetLayer(layer)
        (parent or self.b).Add(s)
        return s

    def part(self, ref, x, y, rot=0.0, pads=2, pitch=1.0, fpid="R_0402", ref_h=1.0,
             ref_visible=True, fab=True, mark=False, drills=None, ref_dy=None):
        fp = pcbnew.FOOTPRINT(self.b)
        fp.SetFPID(pcbnew.LIB_ID("Fixture", fpid))
        fp.SetReference(ref)
        fp.SetValue(fpid)
        fp.Value().SetVisible(False)
        fp.Value().SetLayer(pcbnew.F_Fab)
        n = pads if drills is None else len(drills)
        x0 = -pitch * (n - 1) / 2
        for i in range(n):
            p = pcbnew.PAD(fp)
            p.SetNumber(str(i + 1))
            if drills is None:
                p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
                p.SetLayerSet(p.SMDMask())
                p.SetShape(pcbnew.PAD_SHAPE_RECT)
                p.SetSize(pcbnew.VECTOR2I(mm(pitch * 0.6), mm(0.6)))
            else:
                p.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
                p.SetLayerSet(p.PTHMask())
                p.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
                p.SetSize(pcbnew.VECTOR2I(mm(drills[i] + 0.4), mm(drills[i] + 0.4)))
                p.SetDrillSize(pcbnew.VECTOR2I(mm(drills[i]), mm(drills[i])))
            p.SetPosition(pt(x0 + i * pitch, 0))
            fp.Add(p)
        half_w = pitch * (n - 1) / 2 + pitch * 0.3 + 0.25
        half_h = 0.3 + 0.25
        r = pcbnew.PCB_SHAPE(fp)
        r.SetShape(pcbnew.SHAPE_T_RECT)
        r.SetStart(pt(-half_w, -half_h))
        r.SetEnd(pt(half_w, half_h))
        r.SetWidth(mm(0.05))
        r.SetLayer(pcbnew.F_CrtYd)
        fp.Add(r)
        if fab:
            self.text(-0.5, 0, "${REFERENCE}", pcbnew.F_Fab, 0.5, 0.08, parent=fp)
        if mark:  # a short bar outside pad 1
            self.line(x0 - pitch * 0.3 - 0.3, -0.3, x0 - pitch * 0.3 - 0.3, 0.3, pcbnew.F_SilkS, parent=fp)
        rf = fp.Reference()
        rf.SetLayer(pcbnew.F_SilkS)
        rf.SetTextSize(pcbnew.VECTOR2I(mm(ref_h), mm(ref_h)))
        rf.SetTextThickness(mm(0.15))
        rf.SetPosition(pt(0, -(half_h + 0.8) if ref_dy is None else ref_dy))
        rf.SetVisible(ref_visible)
        fp.SetPosition(pt(x, y))
        fp.SetOrientationDegrees(rot)
        rf.SetTextAngleDegrees(0)
        self.b.Add(fp)
        return fp

    def group(self, name, fps):
        g = pcbnew.PCB_GROUP(self.b)
        g.SetName(name)
        self.b.Add(g)
        for fp in fps:
            g.AddItem(fp)

    def track(self, pts, w, net, arc_mid=None):
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            t = pcbnew.PCB_TRACK(self.b)
            t.SetStart(pt(x1, y1))
            t.SetEnd(pt(x2, y2))
            t.SetWidth(mm(w))
            t.SetLayer(pcbnew.F_Cu)
            t.SetNet(self.net(net))
            self.b.Add(t)

    def via(self, x, y, drill, land, net, teardrops=False):
        v = pcbnew.PCB_VIA(self.b)
        v.SetPosition(pt(x, y))
        v.SetDrill(mm(drill))
        v.SetWidth(mm(land))
        v.SetNet(self.net(net))
        v.SetTeardropsEnabled(teardrops)
        self.b.Add(v)

    def build(self):
        bad = self.bad
        tb = self.b.GetTitleBlock()
        tb.SetTitle("Craft fixture")
        tb.SetRevision("1.0")
        tb.SetDate("24/10/2026" if bad else "2026-10-24")
        tb.SetCompany("Example" if bad else HOLDER)

        r = pcbnew.PCB_SHAPE(self.b)
        r.SetShape(pcbnew.SHAPE_T_RECT)
        r.SetStart(pt(0, 0))
        r.SetEnd(pt(W, H))
        r.SetLayer(pcbnew.Edge_Cuts)
        r.SetWidth(mm(0.05))
        self.b.Add(r)

        # identity block (CRAFT-20 to -23)
        if not bad:
            self.text(3, 3, "${TITLE}", h=2.0, th=0.3)
            self.text(3, 5.5, "Rev ${REVISION}  ${ISSUE_DATE}")
            self.text(3, 7, HOLDER + "  CERN-OHL-S-2.0")
            self.text(3, 8.5, "github.com/opendap-project/toolkit-hardware")
            mark = pcbnew.FOOTPRINT(self.b)
            mark.SetFPID(pcbnew.LIB_ID("OpenDAP", "OpenDAP_Mark_3mm"))
            mark.SetReference("G1")
            mark.Reference().SetVisible(False)
            mark.Value().SetVisible(False)
            poly = pcbnew.PCB_SHAPE(mark)
            poly.SetShape(pcbnew.SHAPE_T_POLY)
            poly.SetPolyPoints([pt(0, 0), pt(3, 1.5), pt(0, 3)])
            poly.SetFilled(True)
            poly.SetWidth(0)
            poly.SetLayer(pcbnew.F_SilkS)
            mark.Add(poly)
            mark.SetPosition(pt(52, 3))
            self.b.Add(mark)
        else:
            self.text(3, 3, "OSHW")

        # channel blocks (CRAFT-10 to -13, -30 to -34)
        left = [
            self.part("R101", 8, 20, ref_h=0.8 if bad else 1.0),
            self.part("R102", 11, 20, rot=90 if bad else 0, ref_visible=not bad),
            self.part("C101", 14, 21.3 if bad else 20, fab=not bad),
            self.part("U101", 11, 26, pads=4, pitch=0.8, fpid="SOT-23-4", mark=not bad),
        ]
        right = [
            self.part("R201", W - 8 + (0.03 if bad else 0), 20),
            self.part("R202", W - 11, 20.5 if bad else 20, ref_dy=5.0 if bad else None),
            self.part("C201", W - 14, 20, rot=45 if bad else 0),
            self.part("U201", W - 11, 26, rot=180, pads=4, pitch=0.8, fpid="SOT-23-4", mark=True),
        ]
        self.group("CH-L", left)
        self.group("CH-R", right)

        # battery connector (CRAFT-13, -14)
        self.part("BT1", 30, 32, pads=2, pitch=2.0, fpid="JST_PH_2", mark=True)
        if not bad:
            self.text(27.5, 33.5, "+")
            self.text(31.8, 33.5, "-")

        # mounting holes (CRAFT-36)
        for ref, x, fpid in (("H1", 4, "MountingHole_2.2mm_M2"),
                             ("H2", 56, "MountingHole_3.2mm_M3" if bad else "MountingHole_2.2mm_M2")):
            fp = pcbnew.FOOTPRINT(self.b)
            fp.SetFPID(pcbnew.LIB_ID("Fixture", fpid))
            fp.SetReference(ref)
            fp.Reference().SetVisible(False)
            fp.Value().SetVisible(False)
            p = pcbnew.PAD(fp)
            p.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
            p.SetLayerSet(p.UnplatedHoleMask())
            p.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
            d = 3.2 if "3.2" in fpid else 2.2
            p.SetSize(pcbnew.VECTOR2I(mm(d), mm(d)))
            p.SetDrillSize(pcbnew.VECTOR2I(mm(d), mm(d)))
            fp.Add(p)
            fp.SetPosition(pt(x, 36))
            self.b.Add(fp)

        # silk near the edge (CRAFT-08)
        if bad:
            self.line(20, 0.2, 25, 0.2, pcbnew.F_SilkS)

        # copper (CRAFT-42, -44, -46, -48, -51)
        self.track([(20, 14), (24, 14), (26, 16), (26, 18)], 0.2, "SIG")
        self.track([(26, 18), (30, 18)], 0.3, "SIG")
        self.via(30, 18, 0.3, 0.6, "SIG")
        self.track([(34, 14), (36, 14)], 0.2, "SIG2")
        self.via(36, 14, 0.3, 0.6, "SIG2", teardrops=bad)
        if bad:
            self.track([(38, 14), (40, 14)], 0.4, "SIG3")
            a = pcbnew.PCB_ARC(self.b)
            a.SetStart(pt(40, 10))
            a.SetMid(pt(42, 8))
            a.SetEnd(pt(44, 10))
            a.SetWidth(mm(0.2))
            a.SetLayer(pcbnew.F_Cu)
            a.SetNet(self.net("SIG4"))
            self.b.Add(a)
            self.part("TP1", 30, 10, fpid="TestPoints",
                      drills=[0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3], pitch=2.0)
        for i, x in enumerate((20, 22, 24)):
            if bad and i == 2:
                self.via(x + 0.03, 30, 0.2, 0.46, "GND")
            else:
                self.via(x, 30, 0.3, 0.6, "GND")

        # zone (CRAFT-49, -50)
        z = pcbnew.ZONE(self.b)
        z.SetLayer(pcbnew.B_Cu)
        z.SetNet(self.net("GND"))
        z.SetZoneName("GND")
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_NEVER if bad else pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        z.SetMinThickness(mm(0.1 if bad else 0.2))
        ol = z.Outline()
        ol.NewOutline()
        corners = [(16, 28), (44, 28), (44, 38), (16, 38)] if not bad else \
            [(16, 28), (44, 28), (44, 38), (19, 38)]  # last edge not at 45-degree multiple
        for x, y in corners:
            ol.Append(mm(x), mm(y))
        self.b.Add(z)

        # documentation layers (CRAFT-60 to -62)
        if not bad:
            self.text(0, H + 3, "Fabrication notes: see the README", pcbnew.Cmts_User)
            g = pcbnew.PCB_GROUP(self.b)
            g.SetName("group-boardStackUp")
            self.b.Add(g)
            g.AddItem(self.text(0, H + 6, "Stackup: 2 layers, 1.6 mm", pcbnew.Dwgs_User))
            for (x1, y1), (x2, y2), off in (((0, H), (W, H), 2.0), ((W, 0), (W, H), -2.0)):
                d = pcbnew.PCB_DIM_ALIGNED(self.b)
                d.SetStart(pt(x1, y1))
                d.SetEnd(pt(x2, y2))
                d.SetHeight(mm(off))
                d.SetLayer(pcbnew.Dwgs_User)
                self.b.Add(d)

    def save(self, name):
        path = os.path.join(HERE, name + ".kicad_pcb")
        pcbnew.SaveBoard(path, self.b)
        stackup = STACKUP.format(fmask="Black", bmask="Green" if self.bad else "Black")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        text = re.sub(r"\(setup\n", "(setup\n" + stackup, text, count=1)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        with open(os.path.join(HERE, name + ".kicad_dru"), "w", encoding="utf-8") as fh:
            fh.write('(version 1)\n(rule "FIX-01 [Q2]: Track SIG3 at least 0.5 mm"\n'
                     '\t(severity warning)\n\t(condition "A.Type == \'Track\' && A.NetName == \'SIG3\'")\n'
                     '\t(constraint track_width (min 0.5mm))\n)\n')
        project = {"meta": {"filename": name + ".kicad_pro", "version": 3},
                   "net_settings": {"classes": [{"name": "Default", "priority": 2147483647}],
                                    "meta": {"version": 4}}}
        with open(os.path.join(HERE, name + ".kicad_pro"), "w", encoding="utf-8") as fh:
            json.dump(project, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(path)


STACKUP = """\t\t(stackup
\t\t\t(layer "F.SilkS"
\t\t\t\t(type "Top Silk Screen")
\t\t\t\t(color "White")
\t\t\t)
\t\t\t(layer "F.Paste"
\t\t\t\t(type "Top Solder Paste")
\t\t\t)
\t\t\t(layer "F.Mask"
\t\t\t\t(type "Top Solder Mask")
\t\t\t\t(color "{fmask}")
\t\t\t\t(thickness 0.01)
\t\t\t)
\t\t\t(layer "F.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.035)
\t\t\t)
\t\t\t(layer "dielectric 1"
\t\t\t\t(type "core")
\t\t\t\t(thickness 1.51)
\t\t\t\t(material "FR4")
\t\t\t\t(epsilon_r 4.5)
\t\t\t\t(loss_tangent 0.02)
\t\t\t)
\t\t\t(layer "B.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.035)
\t\t\t)
\t\t\t(layer "B.Mask"
\t\t\t\t(type "Bottom Solder Mask")
\t\t\t\t(color "{bmask}")
\t\t\t\t(thickness 0.01)
\t\t\t)
\t\t\t(layer "B.Paste"
\t\t\t\t(type "Bottom Solder Paste")
\t\t\t)
\t\t\t(layer "B.SilkS"
\t\t\t\t(type "Bottom Silk Screen")
\t\t\t\t(color "White")
\t\t\t)
\t\t\t(copper_finish "ENIG")
\t\t\t(dielectric_constraints no)
\t\t)
"""


def main():
    for bad, name in ((False, "good"), (True, "bad")):
        f = Fixture(bad)
        f.build()
        f.save(name)


if __name__ == "__main__":
    main()
