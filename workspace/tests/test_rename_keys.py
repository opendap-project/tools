"""odw rename: maker keys (`mfr:` rows) are rewritten where they are used as keys, never in prose."""
import json

import pytest

from odw import rename, rename_keys, schemas
from test_rename import git, make_repo, run

ROWS = [  # (old, new, action, root)
    ("mfr:Texas_Instruments", "mfr:texas-instruments", "rename", "libs"),
    ("mfr:Texas-Instruments", "mfr:texas-instruments", "rename", "libs"),
    ("mfr:ECS", "mfr:ecs", "rename", "libs"),
    ("mfr:Microchip", "mfr:microchip", "rename", "libs"),
    ("mfr:NXP", "mfr:nxp", "rename", "libs"),
    ("mfr:Nexperia", "mfr:nexperia", "rename", "libs"),
    ("mfr:Diodes-Inc.", "mfr:diodes-inc", "rename", "libs"),
    ("mfr:OpenDAP_Device", "mfr:opendap-generic", "rename", "libs"),
    ("mfr:Rockchip", "mfr:rockchip", "rename", "libs"),
    ("mfr:Rockchip_PMIC", "mfr:rockchip", "merge", "libs"),
    ("mfr:Murata", "mfr:murata", "rename", ""),  # no root and no source repository in the notes
    ("env:ODAP_KICAD_LIB_DIR", "env:ODAP_LIBS_DIR", "rename", "libs"),
]
FILES = {
    "kicad/symbols/Texas_Instruments.kicad_sym":
        '(property "Footprint" "Texas_Instruments:SOT-23")\n(property "Manufacturer" "Texas Instruments")\n',
    "kicad/symbols/ECS.kicad_sym": '(property "Footprint" "ECS:XO")\n',
    "kicad/symbols/OpenDAP_Device.kicad_sym": "(kicad_symbol_lib)\n",
    "kicad/symbols/Rockchip.kicad_sym": '(symbol "RK3566")\n',
    "kicad/symbols/Rockchip_PMIC.kicad_sym": '(symbol "RK809-5")\n',
    "kicad/footprints/Rockchip.pretty/A.kicad_mod": "(footprint A)\n",
    "kicad/footprints/Rockchip_PMIC.pretty/B.kicad_mod": "(footprint B)\n",
    "kicad/footprints/Texas_Instruments.pretty/X.kicad_mod":
        '(model "${OPENDAP_KICAD_3DMODELS}/Texas_Instruments.3dshapes/X.step")\n',
    "kicad/3dmodels/Texas_Instruments.3dshapes/X.step": "ISO-10303-21;\n",
    "kicad/generator/parts/Texas_Instruments/BQ.toml":
        'manufacturer = "Texas Instruments"\nlibrary = "Texas_Instruments"\nsheet = "Texas-Instruments/BQ.md"\n',
    "kicad/generator/generic.py": 'LIBRARY = "OpenDAP_Device"\nMODEL = {"library": "Rockchip_PMIC"}\n',
    "kicad/sym-lib-table": '(lib (name "Texas_Instruments")(type "KiCad")(uri "${L}/Texas_Instruments.kicad_sym"))\n',
    "parts/Texas-Instruments/BQ.md":
        "# BQ\n\nTexas-Instruments makes it. Second sources: Microchip/Atmel and NXP/Nexperia.\n"
        "Folder `parts/Texas-Instruments/`, key `Texas_Instruments`, prose Texas_Instruments: the old key.\n"
        "Datasheet: https://example.org/Texas-Instruments%20BQ.pdf; Diodes-Inc. parts in `Diodes-Inc./`.\n"
        "Library ECS:XO and NXP:Y, ODAP_KICAD_LIB_DIR, Murata:L_1 and Murata parts.\n",
    "parts/Diodes-Inc./D.md": "# D\n",
}


@pytest.fixture
def libs(tmp_path):
    """A workspace whose libs root is already cut (its own repository), and the map's maker rows."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "repos.tsv").write_text("path\trepository\npublic/libs\topendap-project/libs\n")
    make_repo(ws / "public/libs", FILES)
    lines = ["old\tnew\tkind\taction\tnotes\tscope\troot"]
    lines += ["\t".join([o, n, "identifier", a, "library key", "test", r]) for o, n, a, r in ROWS]
    (tmp_path / "map.tsv").write_text("\n".join(lines) + "\n")
    return ws


def plan(capsys, ws, *extra):
    code, out, err = run(capsys, "plan", "libs", "--workspace", str(ws), "--map", str(ws.parent / "map.tsv"),
                         "--json", *extra)
    assert code == 0, err
    doc = json.loads(out)
    assert not schemas.errors(doc, "rename-plan")
    return doc


def test_key_contexts_are_rewritten_and_prose_stays():
    table = {"Texas_Instruments": "texas-instruments", "Texas-Instruments": "texas-instruments", "NXP": "nxp",
             "Microchip": "microchip", "Nexperia": "nexperia", "Diodes-Inc.": "diodes-inc", "ECS": "ecs"}
    pats = rename_keys.patterns(table)
    cases = {
        '"Texas_Instruments:SOT-23"': '"texas-instruments:SOT-23"',  # lib id
        "Texas_Instruments.kicad_sym, ECS.pretty": "texas-instruments.kicad_sym, ecs.pretty",
        "${M}/Texas_Instruments.3dshapes/X.step": "${M}/texas-instruments.3dshapes/X.step",
        "parts/Texas-Instruments/BQ.md": "parts/texas-instruments/BQ.md",  # segment, then a file
        "parts/Texas-Instruments/sub/": "parts/texas-instruments/sub/",  # segment, then a folder
        "`parts/Texas-Instruments/`": "`parts/texas-instruments/`",  # segment, then a closing backtick
        '"Diodes-Inc./D.md"': '"diodes-inc/D.md"',
        'library = "Texas_Instruments"': 'library = "texas-instruments"',
        '"library": "NXP"': '"library": "nxp"',
        "LIBRARY = 'ECS'": "LIBRARY = 'ecs'",
        '(lib (name "Texas_Instruments")': '(lib (name "texas-instruments")',
        "key `Texas_Instruments`": "key `texas-instruments`",
    }
    for old, new in cases.items():
        assert rename_keys.rewrite(old, pats, table)[0] == new, old
    prose = ["Texas Instruments", "Texas-Instruments makes it.", "Microchip/Atmel and NXP/Nexperia.",
             "Texas_Instruments: the old key", "https://example.org/Texas-Instruments%20BQ.pdf",
             "Diodes-Inc. parts", "manufacturer = \"Texas Instruments\"", "MyTexas_Instruments.kicad_sym",
             "NXP:", "Texas_Instruments_PMIC.pretty"]
    for text in prose:
        assert rename_keys.rewrite(text, pats, table)[0] == text, text


def test_longest_key_wins():
    table = {"Rockchip": "rockchip", "Rockchip_PMIC": "rockchip"}
    pats = rename_keys.patterns(table)
    assert rename_keys.rewrite("Rockchip_PMIC:RK809 Rockchip:RK3566", pats, table)[0] == "rockchip:RK809 rockchip:RK3566"


def test_paths_move_on_key_segments_only():
    table = {"Texas_Instruments": "texas-instruments", "Texas-Instruments": "texas-instruments", "ECS": "ecs"}
    assert rename_keys.new_path("kicad/footprints/Texas_Instruments.pretty/X.kicad_mod", table) == \
        "kicad/footprints/texas-instruments.pretty/X.kicad_mod"
    assert rename_keys.new_path("parts/Texas-Instruments/BQ.md", table) == "parts/texas-instruments/BQ.md"
    assert rename_keys.new_path("kicad/symbols/ECS.kicad_sym", table) == "kicad/symbols/ecs.kicad_sym"
    assert rename_keys.new_path("docs/ECS", table) == "docs/ECS"  # a file named like a key is not a folder key
    assert rename_keys.new_path("docs/ECS.md", table) == "docs/ECS.md"


def test_plan_moves_keys_rewrites_contexts_and_reports_the_merge_clash(libs, capsys):
    doc = plan(capsys, libs)
    moves = {m["from"]: m["to"] for m in doc["moves"]}
    assert moves["kicad/footprints/Texas_Instruments.pretty/X.kicad_mod"] == \
        "kicad/footprints/texas-instruments.pretty/X.kicad_mod"
    assert moves["kicad/3dmodels/Texas_Instruments.3dshapes/X.step"] == \
        "kicad/3dmodels/texas-instruments.3dshapes/X.step"
    assert moves["parts/Texas-Instruments/BQ.md"] == "parts/texas-instruments/BQ.md"
    assert moves["parts/Diodes-Inc./D.md"] == "parts/diodes-inc/D.md"
    assert moves["kicad/symbols/OpenDAP_Device.kicad_sym"] == "kicad/symbols/opendap-generic.kicad_sym"
    assert moves["kicad/footprints/Rockchip_PMIC.pretty/B.kicad_mod"] == "kicad/footprints/rockchip.pretty/B.kicad_mod"
    clash = [c for c in doc["conflicts"] if c["path"] == "kicad/symbols/rockchip.kicad_sym"]
    assert len(clash) == 1 and "kicad/symbols/Rockchip.kicad_sym" in clash[0]["reason"] \
        and "kicad/symbols/Rockchip_PMIC.kicad_sym" in clash[0]["reason"]
    assert len(doc["conflicts"]) == 1  # the folders merge file by file
    ids = {i["old"]: (i["status"], i.get("reason")) for i in doc["identifiers"]}
    assert ids["mfr:Texas_Instruments"] == ("apply", None) and ids["mfr:Rockchip_PMIC"] == ("apply", None)
    assert ids["mfr:Murata"] == ("manual", "owner-unknown")  # a context-only row still needs its owner
    edits = {(e["path"], e["old"], e["new"]) for e in doc["edits"]}
    assert ("parts/texas-instruments/BQ.md", "Texas_Instruments", "texas-instruments") in edits
    assert ("kicad/generator/generic.py", "Rockchip_PMIC", "rockchip") in edits
    assert ("parts/texas-instruments/BQ.md", "ODAP_KICAD_LIB_DIR", "ODAP_LIBS_DIR") in edits


def test_kind_filter_limits_the_identifier_rows(libs, capsys):
    doc = plan(capsys, libs, "--kind", "mfr")
    assert {i["old"].split(":")[0] for i in doc["identifiers"]} == {"mfr"}
    assert not any(e["old"] == "ODAP_KICAD_LIB_DIR" for e in doc["edits"])
    assert doc["moves"]
    doc = plan(capsys, libs, "--kind", "env")
    assert not doc["moves"] and {e["old"] for e in doc["edits"]} == {"ODAP_KICAD_LIB_DIR"}


def test_apply_refuses_the_merge_clash_then_moves_keys_with_history(libs, capsys):
    root = libs / "public/libs"
    argv = ["apply", "libs", "--workspace", str(libs), "--map", str(libs.parent / "map.tsv"), "--kind", "mfr", "--json"]
    head = git(root, "rev-parse", "HEAD")
    code, out, _ = run(capsys, *argv)
    assert code == 1 and json.loads(out)["error"]["code"] == "rename.conflict"
    assert git(root, "rev-parse", "HEAD") == head
    git(root, "rm", "-q", "kicad/symbols/Rockchip_PMIC.kicad_sym")
    git(root, "commit", "-q", "-m", "the generators rebuild rockchip.kicad_sym")
    code, out, err = run(capsys, *argv)
    assert code == 0, err
    files = set(git(root, "ls-files").split())
    assert {"kicad/symbols/ecs.kicad_sym", "kicad/footprints/rockchip.pretty/A.kicad_mod",
            "kicad/footprints/rockchip.pretty/B.kicad_mod", "parts/diodes-inc/D.md"} <= files
    assert not any("Texas" in f or "ECS" in f or "Rockchip" in f for f in files)
    log = git(root, "log", "--follow", "--format=%s", "--", "kicad/symbols/ecs.kicad_sym").splitlines()
    assert log[-1] == "init"  # the case-only rename kept its history
    text = (root / "parts/texas-instruments/BQ.md").read_text()
    assert "Microchip/Atmel and NXP/Nexperia" in text and "Texas-Instruments makes it" in text
    assert "`texas-instruments`" in text and "ODAP_KICAD_LIB_DIR" in text  # --kind mfr left the env row
    assert not git(root, "status", "--porcelain")
    code, out, _ = run(capsys, "check", *argv[1:])
    assert code == 0


def test_python_files_stay_within_budget():
    import pathlib
    assert pathlib.Path(rename_keys.__file__).read_text().count("\n") <= 500
    assert pathlib.Path(rename.__file__).read_text().count("\n") <= 500
