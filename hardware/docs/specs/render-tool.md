# Render tool

- **Status:** Implemented (2026-10-29). It implements [RFC 0021](https://github.com/opendap-project/rfcs/blob/main/text/0021-committed-visual-outputs.md) (committed visual outputs): §2 to §8 in the tool, §6 in the hooks.
- **Tool:** `odw render` ([`bin/odw-render`](../../bin/odw-render), an entry point to [`tools/render/`](../../tools/render/): `cli.py` the command line, `core.py` the engine, `kicad_render.py` the KiCad adapters, `cad.py` the FreeCAD and Blender adapters, `normalize.py` the determinism rewrites); Python 3.11 standard library, and the program of each declared source.
- **Tests:** [`tests/test_render.py`](../../tests/test_render.py), fixtures in [`tests/fixtures/render/`](../../tests/fixtures/render/). The KiCad, FreeCAD and Blender tests skip when their program is missing.

## 1. What it does

A component is a folder with an `outputs.toml` (`opendap.outputs/v1`, RFC 0021 §3). `odw render` generates its `outputs/` folder from the declared sources, writes `outputs/README.md` (an index) and `outputs/stamp.json` (`opendap.outputs-stamp/v1`, the source-hash stamp of RFC 0021 §4), and checks the stamp without running any program.

| Command | Reads | Result |
|---|---|---|
| `list [PATH...]` | `outputs.toml` files | The components under PATH, their sources, and each adapter's program and version on this machine (`opendap.render-list/v1` with `--json`) |
| `update [PATH...]` | sources, `outputs/` | Regenerates each component's outputs; writes a file only when its bytes change; `--dry-run` names what it would write |
| `check [PATH...]` | stamp, sources, `outputs/` | Every component current (RFC 0021 §4.3), by hashing only; `--regenerate` updates first and fails when `outputs/` then differs from git's index |

PATH is a component, a folder holding components, or a file inside one; the default is the current folder. Exit status: 0 current or written, 1 stale, failed or a program missing (`render.unavailable`), 2 usage error. Errors carry a stable code, a hint and a fix ([command standard](../../../core/docs/specs/command-standard.md)).

## 2. Adapters

| `type` | Program | Outputs |
|---|---|---|
| `kicad-sch` | `kicad-cli` (`KICAD_CLI`) | `schematic.pdf`, `schematic/<sheet>.svg` |
| `kicad-pcb` | `kicad-cli` | `pcb/<layer>.svg`, `render/<view>.png`, `<name>.glb` when `glb = true` |
| `freecad` | `freecadcmd` (`ODAP_FREECADCMD`) | `<name>.glb` |
| `blender` | `blender --background --factory-startup` (`ODAP_BLENDER`) | `render/<view>.png`, `<name>.glb` |

KiCad runs with a fresh `KICAD_CONFIG_HOME` that holds only the declared `kicad_theme`; KiCad's stock 3D models come from `KICAD10_3DMODEL_DIR`, else from beside the app's `kicad-cli`.

## 3. Determinism

`normalize.py` rewrites what the programs stamp (RFC 0021 §5.2): the PDF `CreationDate` and `ModDate` to a fixed date of the same length; the date in each SVG title; PNG ancillary chunks `tIME`, `tEXt`, `zTXt` and `iTXt` dropped. It merges the paths of each stroke-font glyph group of an SVG into one path (§5.5).

3D renders are compared with a tolerance (§5.4): a regenerated PNG of the same size that differs from the committed one in at most 0.1 % of its pixels (`PNG_PIXELS`), by at most 32 in a channel (`PNG_CHANNEL`), counts as unchanged, and `update` keeps the committed bytes. Only 8-bit grey, RGB and RGBA PNGs are compared; any other PNG that differs counts as changed.

## 4. Limits

`update` fails on a file over 5 MB or a component whose `outputs/` exceeds 100 MB (RFC 0021 §8.1), naming the file.

## 5. Hooks

`outputs-current` (pre-commit, `check`) and `outputs-regenerate` (pre-push, `check --regenerate`) run this tool from the pinned tools checkout (RFC 0021 §6). `no-stray-files` allows a PDF in an `outputs/` folder only when its `stamp.json` lists it (§7).
