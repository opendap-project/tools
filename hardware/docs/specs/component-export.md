# Component export contract

Every OpenDAP component (board, case core, faceplates, HATs, the OpenDAP HAT Standard, …) keeps its own internal sources: KiCad, FreeCAD, Blender, simulations, code. Towards everyone else it publishes itself **as a single unit**, through its `export/` folder.

Composition is hierarchical, and not only at the top: any component may consume another component's export wherever that makes sense (a HAT consumes the device-side piece from `hat-standard`; a faceplate consumes the faceplate interface from `case`), and the product integration (`t1`) consumes them all. At every level, an imported component behaves as one part, not as its internal pieces.

## Rules

1. Consumers read only `export/`, never the sources of another repository.
2. Each export is pinned to a tagged version of the producing repository.
3. An export declares the interfaces it implements and their versions.
4. Exports are generated locally from the sources and committed with their tag.

## `export/` contents

`component.yaml` describes the export: name, version, implemented interfaces with their versions, and the list of exported files. The exported files depend on the component:

| Domain | Internal sources | Exported as one unit | Consumed for |
|---|---|---|---|
| Mechanics | FreeCAD | STEP of the whole component as a single body, plus a simplified envelope; mass and center of gravity in `component.yaml` | Fit, interference, drop and thermal studies of the assembly |
| Renders | Blender | A Blender collection (linked, not appended), plus glTF as a neutral format | Renders of the assembly |
| Electronics | KiCad | STEP of the assembled PCB, connector netlist, interface definition | System-level connectivity checks across connectors |
| Signal paths | Simulations | Touchstone (`.sNp`) model of the component's segment of each high-speed channel | Cascading segments into a full channel model |
| Power and thermal | Simulations | Dissipated power and simplified thermal geometry | Thermal simulation of the assembly |
| Firmware | Rust | Versioned binaries | Blackbird OS image |
| Software and OS | Rust, overlays | Crates, Blackbird OS packages, device tree overlays | Blackbird OS image composition |

## `component.yaml` schema

The schema is [`schemas/component.schema.json`](../../schemas/component.schema.json) (JSON Schema 2020-12). It covers what the rules and the table above list, and nothing else: unknown keys are rejected, so a new field is added here first. Check a component with `odw component validate [PATH]` (a `component.yaml`, an `export/` folder or a component repository).

| Key | Required | Type | From |
|---|---|---|---|
| `name` | yes | string | "name" |
| `version` | yes | string: the tag of the producing repository the export is committed with | "version"; rules 2 and 4 |
| `interfaces` | yes | list of `{name, version}`, both strings; empty when the component implements none | "implemented interfaces with their versions"; rule 3 |
| `files` | yes | list of `{path, domain}`; `path` relative to `export/`; `domain` one of `mechanics`, `renders`, `electronics`, `signal-paths`, `power-thermal`, `firmware`, `software` (the rows of the table above) | "the list of exported files" |
| `mechanics` | when a file has domain `mechanics` | `{mass_g, center_of_gravity_mm: [x, y, z]}` | "mass and center of gravity in `component.yaml`" |

Besides the schema, the validator checks that interface names and file paths are listed once, that every listed file exists inside `export/`, and warns about files in `export/` that are not listed (`README.md` is not an exported file).

Minimal example, a board that exports electronics and renders (illustrative: the names, versions and files are placeholders, not the T1 board's export; it is the test fixture `tests/fixtures/component/board/export/component.yaml`):

```yaml
name: board
version: v0.1.0
interfaces:
  - name: hat-interface
    version: "1"
files:
  - path: board.step
    domain: electronics
  - path: board.glb
    domain: renders
  - path: board-connectors.net
    domain: electronics
```

### Open points

The contract is silent on the following, so the schema leaves them out or open:

- **Units and frame of the mass properties.** The schema uses grams and millimetres (`freecad-export.py` reports millimetres) and the coordinate frame of the exported STEP. To confirm, together with where the frame's origin is defined.
- **Body and envelope.** Mechanics exports a single-body STEP plus a simplified envelope; nothing in a file entry tells them apart yet (likewise the Blender collection and the glTF, or the per-channel Touchstone files).
- **Formats of `name`, `version` and interface identifiers.** Whether `name` is the repository name, the tag format (for example `v0.1.0`), and how an interface is named (by the repository that defines it, for example `hat-standard`) and versioned.
- **What a component consumes.** Rule 2 pins every consumed export to a tag, but the contract does not say whether `component.yaml` lists the exports it consumes.
- **Integrity.** No checksums of exported files: rule 4 commits them with the tag, so git already pins their content.
- **Schema version.** `component.yaml` carries no schema version; one is needed before the first incompatible change.
- **Firmware and software** exports (binaries, crates, packages, overlays) may live outside `export/` (registries, package archives); how they are referenced is not defined.
