# Variant tool

- **Status:** Draft v0 (2026-10-27). Implements the toolkit-hardware row of [RFC 0011](https://github.com/opendap-project/rfcs/blob/main/text/0011-feature-blocks-and-variants.md) §6 for the schematic-to-PCB part of rule 7 (variants are data) and the `opendap.board-variant/v1` record of [RFC 0018](https://github.com/opendap-project/rfcs/blob/main/text/0018-digital-thread.md) §3, the absent-state check of RFC 0011 rule 2 for unfitted blocks (§6), and the software outputs of rule 7 and rule 8: a capabilities fragment for the device description and a devicetree overlay fragment (§7). The verification plan is not generated yet.
- **Tool:** `odapkit variant` ([`bin/odapkit-variant`](../../bin/odapkit-variant), an entry point to [`tools/variant/`](../../tools/variant/): `cli.py` the command line, `catalog.py`, `variantfile.py`, `resolve.py`, `kicad.py`, `absent.py`, `software.py`, `build.py`); Python 3.11 standard library and KiCad 10's `kicad-cli`.
- **Schemas:** [`schemas/board-variant.schema.json`](../../schemas/board-variant.schema.json), [`schemas/block.schema.json`](../../schemas/block.schema.json), [`schemas/variant-absent-state.schema.json`](../../schemas/variant-absent-state.schema.json), [`schemas/variant-capabilities.schema.json`](../../schemas/variant-capabilities.schema.json), [`schemas/variant-devicetree.schema.json`](../../schemas/variant-devicetree.schema.json).

## 1. What it does

A variant is a named selection of feature blocks (`fit`, `dnp`, `alternate`) and parameters over one board design. `odapkit variant` reads a component's variant files, the block catalog each one names and the board's KiCad project, and gives each variant its own pipeline from schematic to PCB: a validated block selection, a per-part resolution, a BOM, KiCad 10 variant data, plots, placement, and a variant-specific ERC and DRC, in an output tree of its own. The base design is never written.

It is generic: nothing in it names a board. A component is found by its folder layout (`variants/` or `hardware/variants/`), its catalog by the variant file's `catalog` path, and its KiCad project by the variant's `board` name (`<board>.kicad_pro` under the component root) or `--project`.

| Command | Reads | Result |
|---|---|---|
| `list` | variant files, catalogs | Each variant's kind, status, target level, block counts and whether it is valid |
| `get <variant>` | one variant file, its catalog | Every catalog block's resolved state (fit, dnp, alternate and its name, parameters, open decision) and the findings |
| `diff <a> <b>` | two variants, the schematic | Block-level differences; part-level differences (fitted, value, MPN); BOM quantity changes per part. `--blocks-only` skips KiCad |
| `bom <variant>` | variant, catalog, schematic | The BOM with DNP and alternates applied; `--out FILE` writes `.csv` or `.json` |
| `apply <variant> --out DIR` | variant, catalog, KiCad project | The variant build (§5), with the absent-state report (§6) and the software outputs (§7) |
| `check` | every variant | Every variant valid and consistent with the schematic; its absent-state check (§6); each one built twice and compared file by file. `--no-build` skips the builds, `--no-schematic` also skips KiCad (and the absent-state check) |

The verbs are the command standard's closed set; `show`, the name `get` had before, still runs it and prints a one-line hint to use `get`, and is not listed in `--help`.

Every command takes `--json` (documents `opendap.variant-list/v1`, `opendap.variant-get/v1`, `opendap.variant-diff/v1`, `opendap.variant-bom/v1`, `opendap.variant-build/v1`, `opendap.variant-check/v1`, each with a `findings` list) and `--dir` (the variants folder). Exit status: 0 no finding at error level (warnings allowed), 1 at least one error, 2 a usage, file or tool error (`input.invalid`, `variant.not_found`, `catalog.not_found`, `project.not_found`, `tool.missing`, `kicad.failed`, `variant.build_failed`), reported with a code, a hint and a fix as the [command standard](../../../host/docs/specs/command-standard.md) asks.

## 2. Variant file: `opendap.board-variant/v1`

One TOML file per variant, `variants/<name>.toml`. The format was defined in the T1 board's feature-block spec (§4.1); this section is its definition from now on.

| Key | Type | Rule |
|---|---|---|
| `schema` | string | `"opendap.board-variant/v1"` |
| `board` | string | Board identity; the KiCad project is `<board>.kicad_pro` |
| `name` | string | Lowercase, digits, hyphens; equals the file name; also the KiCad variant name |
| `kind` | string | `full`, `production` or an application name (RFC 0011 §3) |
| `status` | string | `proposed` or `accepted` (accepted only by the maintainers' decision) |
| `description` | string | Optional; written as the KiCad variant's description |
| `target_level` | string | RFC 0010 level, `Q0`–`Q9` |
| `catalog` | string | Path of the block catalog, relative to the component root (§3) |
| `promise` | array | Requirement IDs (`PREFIX-NNN`) the variant is declared against |
| `[instrumentation]` | table | `links`: `"shunt"` or `"zero-ohm"`; `test_points`: `"fit"` |
| `[blocks]` | table | One entry per catalog block: `{ state = "fit" \| "dnp" \| "alternate", alternate = "<name>", params = { … }, open = "<pending decision>", note = "<reason>" }` |

Checks (code, level):

| Code | Level | Rule |
|---|---|---|
| `variant.field-unknown`, `variant.field-missing`, `variant.field-type`, `variant.schema`, `variant.name`, `variant.kind`, `variant.status`, `variant.target-level`, `variant.promise`, `variant.instrumentation` | error | The table above; unknown keys are rejected at every level |
| `block.unknown` | error | A block that is not in the catalog |
| `block.removed` | error | A block the catalog marks removed |
| `block.missing` | error | A catalog block with no entry: every block appears exactly once |
| `block.state` | error | `state` is `fit`, `dnp` or `alternate` |
| `alternate.not-allowed`, `alternate.unknown`, `alternate.missing`, `alternate.unexpected` | error | `alternate` names one of the block's catalog alternates, only with `state = "alternate"`, and only where the catalog lists alternates |
| `param.undeclared` | error | Every `params` key is declared by the block |
| `open.accepted` | error | An `accepted` variant has no `open` entry |
| `promise.unmet` | error | A `dnp` block bound by a must requirement of the variant's `promise` |
| `variant.full-dnp` | warning | A `dnp` block in a `full` variant (RFC 0011 rule 1) |
| `catalog.table-state` | warning | A Markdown catalog's summary column named as the variant disagrees with the file (§3.2) |

## 3. Block catalog

### 3.1 Block records: `opendap.block/v1`

`catalog = "blocks"` names a folder of records, `blocks/<block>.toml`. The tool reads these fields and accepts the other RFC 0011 §1 fields (`interface`, `power_domain`, `measure`, `sheet`, `layout_block`, `level`, `note`) without reading them; any other key is `catalog.field-unknown`.

| Key | Type | Meaning |
|---|---|---|
| `schema` | string | `"opendap.block/v1"` |
| `id` | string | `blk.<name>` |
| `function` | string | What the block does |
| `status` | string | `active` (default) or `removed`: a removed block keeps its record and is never in a variant |
| `requirements` | array | `{ id, priority = "must" \| "should" }`; must requirements in a variant's `promise` keep the block fitted |
| `alternates` | array | `{ name, note }`: the names a variant may select |
| `params` | array | Parameter names a variant may set |
| `sub_blocks` | array | `Block` field values of nested sheets that belong to this block |
| `absent_state` | string or array | The block-level absent state (a string), or `[{ nets = ["NET", …], state = "…" }]`: what each group of interface nets does when the block is not fitted (§6). Another shape is `catalog.absent-state` (error) |
| `software` | string or table | `{ devicetree = "<label>" \| "</path>", compatible = [...], provides = ["org.opendap.hw.…"], description = ["<section>"], note }`, or a string read as a Markdown `Software` cell (§3.2). Another shape is `catalog.software` (error) |

### 3.2 Markdown catalog (transitional)

`catalog = "docs/specs/<file>.md"` reads a catalog laid out as RFC 0011 asks, until it becomes block records:

- the summary table is the first table with an `ID` column; each row with one backticked `blk.*` ID is a block; a `Function` cell starting with "Removed" removes it; the `Bound by …` column gives the requirements, each with the priority of the parenthesis that follows it (`A-1, A-2 (must), B-3 (should)`), or the requirements of another block (``as `blk.x` ``); columns named like a variant are compared with that variant's file;
- a block record is a heading naming one or more blocks in backticks (a heading ending in ": removed" removes them); its `Sub-blocks` row gives the `Block` field values of its nested sheets (backticked `blk.*` names that are not catalog IDs: `sub_blocks`; in a record of several blocks each goes to the block its name starts with); its `Alternates` row gives the alternates (backticked names) and, after the word "Parameter" or "Parameters", the parameters; its `Absent state` row is the block-level absent state; its `Software` row gives the devicetree node (a backticked name followed by the word "node": `` `sdhci` node ``), the compatibles (backticked `vendor,device`), the `org.opendap.hw.*` interfaces (a backticked `.Name` continues the previous one's namespace) and the device-description sections (backticked after the word "description").

## 4. From blocks to parts

The schematic is read through `kicad-cli sch export netlist --format kicadxml`, which carries every symbol's fields, its DNP and BOM flags and the overrides of every KiCad 10 variant.

- **Block of a part:** its `Block` field. A value that is not a catalog ID belongs to the block that lists it in `sub_blocks`; otherwise to the longest catalog ID it starts with (`blk.soc-power` → `blk.soc`), reported as `sch.block-undeclared` (warning); no match is `sch.block-unknown` (error). A part without the field is `sch.block-field-missing` (warning) and stays fitted; a catalog block with no part is `sch.block-empty` (warning).
- **DNP:** every part of a `dnp` block. Otherwise the variant's data decides, else the base design's DNP flag stays.
- **Variant data, two sources:**
  - *kicad* mode, when the schematic already holds KiCad 10 variant data under the variant's name: its field overrides and DNP flags are used, and checked: a part of a `dnp` block that KiCad fits, or a DNP flip in a `fit` block, is `kicad.dnp-mismatch`; an override that disagrees with an alternate field is `kicad.field-mismatch` (errors; the variant file wins in the build).
  - *generated* mode otherwise: per-variant field sets on the symbols, `Alt.<selector>.<Field>`, where the selectors are the block's chosen alternate and the variant's `instrumentation.links` value (`Alt.mem-16.MPN`, `Alt.zero-ohm.Value`). `Alt.<selector>.DNP = yes|no` fits or removes a part: an alternate on another land is a DNP footprint pair in the block's sheet, fitted by its alternate.
- An alternate that changes no part is `alternate.no-data`; an override of `Footprint` is `alternate.footprint` (errors): every variant shares the copper.
- When the schematic has KiCad data for the variant and the board does not, `pcb.variant-missing` (warning): the board's own `--variant` fab outputs are the base population until it is updated from the schematic.

The BOM groups the parts in the BOM by value, footprint, MPN and manufacturer; fitted lines first, then the DNP lines.

## 5. The variant build: `apply`

`odapkit variant apply <variant> --out DIR` refuses an invalid variant file (exit 1, nothing written), an output folder inside the KiCad project, and a non-empty folder that is not a previous build (exit 2). It writes:

| Path | Content |
|---|---|
| `design/` | Copy of the project's design files with the variant's KiCad 10 data written from the resolution of §4: `(variant (name …) (dnp …) (field …))` in each symbol instance and footprint, the board's variant list with its description, and `VARIANT` in the project's text variables. `${KIPRJMOD}` in the project file and library tables is replaced by the project's folder, so the copy finds the same libraries |
| `sch/<project>.pdf`, `sch/svg/` | Schematic plots with `--variant` and `-D VARIANT=<name>`: DNP marked, alternates' values, the variant in the title block |
| `pcb/fab-front.pdf`, `pcb/fab-back.pdf`, `.svg` | Fab layers and the outline, DNP footprints crossed out, with the title block |
| `pcb/pos.csv` | Placement, both sides, DNP excluded |
| `bom.json`, `bom.csv` | The BOM (`opendap.variant-bom/v1`) |
| `bom-kicad.csv` | `kicad-cli sch export bom --variant` per reference: compared part by part with the resolution (`bom.parity`, error on a difference) |
| `checks/erc-base.json`, `checks/erc.json` | ERC of the copy, then of an ERC copy without the variant's DNP symbols |
| `checks/drc-base.json`, `checks/drc.json` | DRC of the copy, then of a DRC copy whose footprints carry the variant's DNP attribute |
| `absent-state.json` | `opendap.variant-absent-state/v1` (§6) |
| `software/capabilities.json` | `opendap.variant-capabilities/v1` (§7) |
| `software/devicetree.json`, `software/variant.dtso` | `opendap.variant-devicetree/v1` and the overlay fragment (§7) |
| `manifest.json` | `opendap.variant-build/v1` |

**Per-variant ERC and DRC.** KiCad 10's ERC and DRC take no variant, so the tool runs them on variant copies and reports what the variant adds to the base results (`erc.new`, `drc.new`: an error for new errors, a warning for new warnings). Removing a symbol leaves its power symbols, flags, No Connect markers, labels and wires behind; violations whose items are only those are taken out of `checks/erc.json`, counted in its `removal_artifacts`, and not reported (KiCad flags one of several orphaned power symbols of a net, and which one changes from run to run). A real pin left alone (`pin_not_connected` on a part) and a label left with one pin (`isolated_pin_label`) are reported: they are where an absent block leaves a net undefined.

**Title block.** The variant name reaches plots through the text variable `VARIANT`. A design whose title blocks and drawing sheet never use `${VARIANT}` gets `titleblock.no-variant` (warning).

**Manifest.** The variant, its board, kind, status and variant-data mode; KiCad's version; the sha256 of the tool's own sources; the sha256 of every input (the variant file, the catalog, every design file of the project, paths relative to the component root: the nearest folder above the variant file holding `component.toml`, up to the repository root, else the folder that holds `variants/` or `hardware/variants/`, so the paths are the same in a repository of its own and in a merged monorepo); the BOM counts and parity; the ERC and DRC summaries; the absent-state counts (`absent_state`) and the software summary (`software`: blocks present and absent, devicetree counts); the findings; the sha256 of every output. It holds no time and no absolute path.

**Reproducibility.** KiCad writes the time into PDF metadata, SVG titles and check reports; the build zeroes those dates, makes report paths relative and drops what KiCad leaves in `design/` while plotting (`.kicad_prl`), so two builds of the same inputs are identical file by file. `check` copies the project's design files once and builds each variant twice from that snapshot, so an edit to the design during the check cannot mix two states; it reports `build.not-reproducible` (error) for any file that differs, and `build.inputs-changed` (warning, nothing established) when the variant file or its catalog changed between the two builds. DRC runs in threads and lists its findings in a different order each run, so the reports are written sorted; for a missing connection KiCad names one representative pair of items, and the pair changes from run to run, so `unconnected_items` are kept per net with their count.

## 6. Absent-state check

RFC 0011 rule 2: a block left off leaves every net it touches in a declared state. `apply` and `check` run it per variant on the XML netlist (nets with their nodes and pin types) and the variant's resolution (§4).

**Declarations.** A block's declared absent states come from two places, read the same way:

- the **interface tables** of its schematic sheets: a table whose header has a `Net` column and a column starting with `Absent state` (the RFC 0011 block sheets carry one: `Net | Direction | Absent state (block not fitted)`). A sheet belongs to the block named by its file (`<stem>.kicad_sch` → `blk.<stem>`, resolved as a `Block` field is, §4), else to the block most of its parts belong to;
- its record's `absent_state` list (§3.1).

A net-name cell lists names separated by commas; text in parentheses and after an item's first word is ignored; `_RXD` after `BT_UART_TXD` is `BT_UART_RXD`; `SD_D0-D3` is `SD_D0` to `SD_D3`; `*` and a lowercase `x` in an otherwise uppercase name stand for any characters (`DDR_*_A`, `DSD_x`). A name matches a net's last path element. The state text is classified by its words: `not-applicable`, `pull` (a pull-up or pull-down on the board), `internal-pull` (the SoC's or a ball's own pull; block IDs such as `blk.soc-pmuio` name a place and do not count), `tied`, `floating` (open, floating, no load) or `described`. A record's block-level `absent_state` or Markdown `Absent state` row that says "not applicable" makes a `dnp` of that block `absent.not-applicable` (error).

**Classes.** Every net with a node on a part the variant leaves unfitted is looked at with the fitted nodes only:

| Class | Rule |
|---|---|
| removed | No fitted node is left: counted, not listed |
| rail | A node of pin type `power_in` or `power_out`, or a net a power symbol names (a symbol whose library symbol has the `power` flag): counted, not listed |
| rail-unsourced | A rail whose only `power_out` was on removed parts, with fitted `power_in` loads left |
| single-pin | One fitted part left on a net that had two or more |
| floating | An `input`, `bidirectional` or `tri_state` pin left with no driver (`output`, `power_out`) and no pull: a fitted two-pin resistor (`R<n>`) whose other pin is on a rail |
| defined | Anything else |

**Scope.** A net touched by a part of a `dnp` block has scope `block`: it needs a declaration of that block. A net touched only by other unfitted parts (option lands, alternate twins, base-design DNP) has scope `part` and is listed only when it becomes single-pin; it is an expected option when every remaining part is in the block of the removed ones.

**Findings** (one per code and block, naming up to eight nets):

| Code | Level | When |
|---|---|---|
| `absent.floating` | error | A floating net with no declaration |
| `absent.single-pin` | error | A single-pin net with no declaration whose remaining pin is an undriven input |
| `absent.single-pin` | warning | Any other undeclared single-pin net (an output or a passive left alone) |
| `absent.rail-unsourced` | warning | An undeclared rail-unsourced net |
| `absent.undeclared` | warning | A net of scope `block` that is defined but has no declaration |
| `absent.declared-mismatch` | warning | Declared `pull`, but the netlist finds the net floating (the pull sits inside the absent block or is missing) |
| `absent.not-applicable` | error | Declared not applicable (never omitted), yet the variant leaves the block off |

**Report** (`absent-state.json`, `opendap.variant-absent-state/v1`): the `dnp` blocks with their block-level state and number of declarations; counts per class and expected/unexpected; one entry per listed net with its class, scope, the blocks of its removed parts, the removed and remaining references, the remaining pins with their types, the declaration it matched (block, names, text, kind, source sheet or record) and its finding. `check` prints the counts per variant; its JSON carries them in each variant's `absent_state`.

The check sees the schematic, not firmware ordering or the unpowered scenario of the board's catalog (a switched block that is fitted but off): those stay review items (§8).

## 7. Software outputs

Rule 7 and rule 8 (SYS-040): software enables what the variant fits and treats what it leaves off as absent. Both outputs are generated from the variant file and its catalog alone, so they are the same bytes for the same inputs.

**Capabilities** (`software/capabilities.json`, `opendap.variant-capabilities/v1`): the board, variant, kind and status; `present` (blocks `fit` or `alternate`) and `absent` (`dnp`); per block its state, alternate, parameters, open decision, requirements, devicetree node and compatibles, `org.opendap.hw.*` interfaces and device-description sections; the union of `provides` and of `description_sections` over the present blocks; the open entries. It is a fragment for the board's device description: `opendap.device-description/v1` (system hardware support spec §3) has a `provides` section but no component records carrying the block identity yet (RFC 0011 §6, system row), and its reader rejects unknown sections, so the fragment is not merged into it. **The consumer side is open.**

**Devicetree** (`software/devicetree.json`, `opendap.variant-devicetree/v1`, and `software/variant.dtso`): for each block whose catalog record names its devicetree node, `status = "okay"` when the variant fits the block and `"disabled"` when it does not; a node is a label (`&sdhci`) or a path (`&{/memory@0}`). A block that names no node, or names something that is neither (`usb-c-connector`), is listed as skipped with the reason, in the JSON and at the end of the fragment. A node named by blocks of both states is written `okay` and reported as `software.node-conflict` (warning). The fragment is the input for the board's overlay: `/dts-v1/; /plugin/;`, a header naming the variant file relative to the component, no date.

## 8. Limits

- The netlist is the same for every variant (shared copper): the per-variant ERC sees a removed symbol, not an unpowered one. The absent-state check (§6) covers unfitted blocks: floating and single-pin nets, unsourced rails and their declarations. Back-powering, held buses, straps, enable defaults and the unpowered scenario (a fitted block switched off) are not checked yet.
- A pull is recognised by the reference `R<n>` of a two-pin part; pins typed `passive` on an IC hide drivers and inputs from the check.
- The verification plan per variant (RFC 0011 rule 7) is not generated yet. The capabilities fragment has no consumer yet (§7).
- The Markdown catalog reader depends on the layout of §3.2; it is replaced by block records.

## 9. Tests

`tests/test_variant.py`: the validation rules one by one on mutated copies of the fixture variants; the Markdown reader on `tests/fixtures/variant/md/` (with its `Absent state` and `Software` rows); the absent-state classes, scopes, declarations, name patterns and findings on synthetic nets; the capabilities and devicetree outputs; part resolution in both modes, its mismatches and the BOM and diffs on synthetic parts; the S-expression edits (idempotent, only the named variant touched); the JSON Schemas on the fixtures (with uv); and, with kicad-cli, every command on `tests/fixtures/variant/board/`, a three-block KiCad 10 project written by `make_fixture.py`, whose sensor sheet carries an interface table: the build's outputs, placement and parity, the variant ERC, the absent-state report (a declared single-pin net and a declared defined net; an undeclared one on a mutated copy), the software outputs and their JSON Schemas, an unchanged base design, the refusals, reproducibility and the exit codes.
