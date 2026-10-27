# OpenDAP PCB craft standard

The craft standard for OpenDAP boards: layout (§3–§8) and, since 2026-10-24, schematics and symbols (§9).

- **Status:** Draft. Requirement KLIB-003 (must, confirmed by the maintainers on 2026-10-24): every OpenDAP board follows this standard. The rules and their values follow the sources cited; the proposals in [§8](#8-colour-and-finish) and the items in [§12](#12-open-items) wait for the maintainers. §9 (schematics and symbols) was extended on 2026-10-24 at the maintainers' request for visual quality and technical communication in KiCad.
- **Date:** 2026-10-25
- **Applies to:** every OpenDAP board: the T1 main board, faceplates, HATs and their reference designs, and any later device.
- **Levels:** each rule carries the lowest quality level at which it is mandatory ([RFC 0010](https://github.com/opendap-project/rfcs/blob/main/text/0010-quality-levels.md)): Q0 bench-safe, Q1 prototype, Q2 pre-compliance, Q3 compliant product, Q4 reference. A build passes every rule at or below its target level; the rest is debt, listed, never dropped.
- **Enforcement:** **DRC**, the rule set [`design-rules/opendap-craft/`](../../design-rules/opendap-craft/); **CHK**, `odapkit craft check` in [toolkit-hardware](https://github.com/opendap-project/toolkit-hardware) (`tools/pcb-craft-check.py` for boards, `tools/sch-craft-check.py` for schematics and symbol libraries); **ERC**, the project's KiCad ERC with the severities of CRAFT-158, folded into the same report; **Review**, the design review, by a person, against this document.

## Contents

1. [Why](#1-why)
2. [How the standard is applied](#2-how-the-standard-is-applied)
3. [Silkscreen and footprints](#3-silkscreen-and-footprints)
4. [Board markings](#4-board-markings)
5. [Placement](#5-placement)
6. [Routing and copper](#6-routing-and-copper)
7. [Documentation layers](#7-documentation-layers)
8. [Colour and finish](#8-colour-and-finish)
9. [Schematics and symbols](#9-schematics-and-symbols)
10. [Decisions taken](#10-decisions-taken)
11. [Enforcement summary](#11-enforcement-summary)
12. [Open items](#12-open-items)
13. [Sources](#13-sources)

## 1. Why

The maintainers asked (2026-10-24) that OpenDAP's boards be made with visible care for engineering, quality and appearance, beyond function. An OpenDAP board is also documentation: the boards are open, their files are published, and owners of the DIY edition handle them. A board that reads clearly is easier to assemble, inspect, probe, repair and modify.

The standard turns that into rules a tool or a reviewer can check: one typeface and a closed set of sizes, fixed orientations, a placement grid, closed sets of track widths and vias, required markings and documentation layers. Most of it follows published conventions (KLC, IPC-7351 through KLC, fab capability pages); where a value is OpenDAP's own choice, the rule says so.

Andy Hertzfeld records Steve Jobs, in July 1981, rejecting a Macintosh board layout because the memory chips' lines were "too close together", and saying that "a great carpenter isn't going to use lousy wood for the back of a cabinet, even though nobody's going to see it" [FOLK-PCB]. The point taken here is narrower and practical: an open board's inside is seen, by its users and by anyone who builds on it.

## 2. How the standard is applied

- **Composition.** A board's `<project>.kicad_dru` is composed by `odapkit dru compose` from its `<project>.dru.toml` manifest, in this order: the fab rule set (for example [`pcbway-hdi`](../../design-rules/pcbway-hdi/)), then [`opendap-craft`](../../design-rules/opendap-craft/), then the product's own rules. The craft rules use only constraints the fab sets do not (assertions, `silk_clearance`, `track_angle`, `track_segment_length`), so they never relax a fab limit; KiCad applies every matching assertion, so product rules cannot relax them either.
- **Exemptions** are built into the rules and are the only ones:
  - impedance-controlled net classes, named `<name>_<ohms>D` (differential, as the T1 board's `USB3_90D`) or `<name>_<ohms>SE` (single-ended), take their widths and geometry from the stackup: exempt from CRAFT-40, CRAFT-42 and CRAFT-43;
  - rule areas named `CRAFT-EXEMPT*` or containing `ESCAPE` (a BGA escape, where the fab floors apply) exempt the copper inside them from CRAFT-40, CRAFT-41, CRAFT-43 and CRAFT-47;
  - a footprint field `Craft_Exempt` = `"CRAFT-30 CRAFT-31: <reason>"` exempts that footprint from the CHK rules it names; the report lists every exemption with its reason. Use it for parts placed from a mechanical datum (connectors, buttons, mounting holes).
- **The check.** `odapkit craft check <board>.kicad_pcb` runs the CHK rules, runs the board's DRC and folds every rule tagged `[Qn]` into the same report, groups results by level, and prints the level the board reaches. A `<project>.craft.toml` next to the board names its target level and its mirrored blocks (CRAFT-34).
- **Ratchet.** A level reached is kept (RFC 0010). A change that makes a passing rule fail is a regression, whatever the rule's level.

## 3. Silkscreen and footprints

The legend is set like a technical drawing: one typeface, four sizes, two reading directions, one stroke weight.

<!-- >>> odapkit craft rules §3: rendered from toolkit-hardware tools/craft/rules.toml; edit it, not this table -->

| ID | Level | Rule | Value | Why | Source | Enforcement |
|---|---|---|---|---|---|---|
| CRAFT-01 | Q2 | One typeface: KiCad's stroke font, regular, upright. Logos and the brand wordmark are graphics, never text. | `KiCad Font`, not bold, not italic | A stroke font plots as lines of one width, which the fab reproduces evenly; KLC sizes are given in it; SparkFun requires a vector font for the same reason | [KLC] F5.1; [SFE] | DRC |
| CRAFT-02 | Q2 | Text height and stroke from a closed set; width equals height. | 0.8 × 0.15 mm (dense areas only); **1.0 × 0.15 mm** (reference designators); 1.6 × 0.24 mm (labels); 2.0 × 0.30 mm (board name) | Fewer sizes read as one hierarchy. 1.0 × 0.15 is KLC's reference size; 0.8 mm and 0.15 mm are the prototype fab's minimums, and it gives about 1 : 5 stroke to height as the best ratio; the larger sizes keep 15 % (SparkFun's ratio) | [KLC] F5.1; [FAB]; [JLC]; [SFE] | DRC |
| CRAFT-03 | Q2 | Two reading directions: from the bottom edge (0°) and from the right edge (90°). A flipped footprint keeps its text angle, so the same two values hold on the bottom side. | 0° or 90° | A board read from two sides only, never upside down. No primary source was found for the two-direction convention: OpenDAP's choice | — | DRC |
| CRAFT-04 | Q1 | Text reads from its own side: unmirrored on top, mirrored on the bottom. | — | A reversed legend is not legible; KiCad also flags mirrored text on the front | [KICAD-DRC] | DRC |
| CRAFT-05 | Q2 | One silk stroke for outlines and marks. Filled marks (pin-1 triangles, dots, the brand mark) have no stroke. | 0.15 mm | KLC allows 0.10–0.15 mm (0.12 nominal); the prototype fab's minimum line is 0.15 mm, so OpenDAP uses KLC's upper value | [KLC] F5.1; [FAB] | DRC |
| CRAFT-06 | Q1 | No silk inside a solder-mask opening (pads, exposed copper). Set the Board Setup check `silk_over_copper` to error. | — | Silk on a pad prevents soldering; fabs clip it | [KLC] F5.1; [EC-LEG]; [JLC] | DRC (Board Setup) |
| CRAFT-07 | Q2 | Silk clear of pads and via lands. | 0.15 mm from pads; 0.15 mm from via lands (DRC: 0.30 mm from the via hole, see the rule set's README) | JLCPCB's minimum to pads is 0.15 mm; KLC recommends 0.2 mm | [JLC]; [KLC] F5.1 | DRC |
| CRAFT-08 | Q2 | Silk clear of the board edge. | 0.5 mm | KLC's value; Eurocircuits clips legend within 0.25 mm of a routed edge | [KLC] F5.1; [EC-LEG] | CHK (KiCad 10.0.7's DRC does not measure this reliably) |
| CRAFT-09 | Q2 | Text clear of any other silk, so every character stands alone. | 0.15 mm | Legibility; one stroke width of air | — | DRC |
| CRAFT-10 | Q2 | Every part with electrical pads has a visible reference designator on the silk of its side. Where density forbids it, the designator moves to the fabrication layer and the part takes a `Craft_Exempt` with the reason. | — | The open board is a repair document; see [§10](#10-decisions-taken) for why OpenDAP does not minimise its legend | MNT Reform [MNT]; [SFE] | CHK |
| CRAFT-11 | Q2 | Every part carries `${REFERENCE}` on its fabrication layer. | — | The assembly drawing, which is published, then names every part even where the silk cannot | [KLC] F5.2 | CHK |
| CRAFT-12 | Q3 | Reference designators in one size per side, beside their part (within 2 mm of its courtyard and nearer to it than to any other part), never under an SMD body. | 2 mm | One size reads as one system; a designator nearer another part names the wrong one; KLC forbids silk under SMD bodies | [KLC] F5.1 | CHK |
| CRAFT-13 | Q1 | Every polarised part (ICs, diodes, LEDs, transistors, connectors, batteries, crystals) has a pin-1 or polarity mark next to pin 1, outside the body, visible after assembly and after mating. Forms: a filled triangle for ICs and connectors, a bar at the cathode for diodes, `+` for polarised capacitors. | — | Orientation errors are found at a glance; KLC's mark forms | [KLC] F5.1 | CHK (mark next to pin 1); Review (form) |
| CRAFT-14 | Q1 | Battery and power-input connectors are marked `+` and `−` in silk, within 3 mm. | 3 mm | The battery is user-replaceable (T1-055): its polarity must read without a schematic | T1-055 | CHK |
| CRAFT-15 | Q1 | Footprints follow KLC orientation and anchors: pin 1 top left (IPC-7351 "Level A"), two-terminal parts with pin 1 on the left; SMD anchor at the body centroid, THT anchor at pin 1. The fab package states the convention, as fabs differ (Eurocircuits uses IEC 61188-7, pin 1 bottom left). | — | Pick-and-place rotations come out right without per-part fixes | [KLC] F4.2, F6.2, F7.2; [EC-ROT] | Review (library) |

<!-- <<< odapkit craft rules -->

## 4. Board markings

Every board identifies itself without its documentation. The identity block is one group, on one side, left-aligned, in CRAFT-02 sizes: the board name at 2.0 mm, the rest at 1.0 mm.

<!-- >>> odapkit craft rules §4: rendered from toolkit-hardware tools/craft/rules.toml; edit it, not this table -->

| ID | Level | Rule | Value | Why | Source | Enforcement |
|---|---|---|---|---|---|---|
| CRAFT-20 | Q1 | Board name and revision on the silk, from the title block (`${TITLE}`, `${REVISION}`), so the copper and the files cannot disagree. | — | Revision mix-ups are the commonest bring-up error; the MNT Reform motherboard carries both | [MNT] | CHK |
| CRAFT-21 | Q2 | Issue date (`${ISSUE_DATE}`, ISO 8601), the copyright holder "The OpenDAP Project", and the licence's SPDX identifier on the silk: `CERN-OHL-S-2.0` for product hardware, `CERN-OHL-W-2.0` for HAT reference designs and building blocks. | — | The board carries its own terms; the MNT Reform motherboard prints a dated identification line | [MNT]; workspace licensing rule | CHK |
| CRAFT-22 | Q3 | The source location (`github.com/opendap-project/<repository>`) on the silk, and the OpenDAP mark as a footprint built from the approved files (mark ≥ 5 mm high, wordmark ≥ 4 mm, the print minimums; the mark's gap, 6.9 % of its height, stays above the silk's 0.15 mm line). | 5 mm / 4 mm | CERN-OHL-S §4 requires recipients to be told the source location, and lets a notice require it on the product; OpenDAP puts it on the board. The mark's minimums are the brand's | [CERN-S] §1.9, §4; [BRAND] | CHK (text, `OpenDAP_Mark*` footprint); Review (size) |
| CRAFT-23 | Q1 | The OSHW mark only after OSHWA certifies the board, with its UID next to it. No OpenDAP board is certified today, so no board carries it (**UNVERIFIED** whether certification will be sought). | UID: two letters and up to six digits | OSHWA allows the mark only after certification | [OSHWA] | CHK |
| CRAFT-24 | Q2 | Title block complete: title, revision, ISO 8601 date, company "The OpenDAP Project". | — | Every output (fab drawing, assembly drawing, schematic PDF) inherits it | — | CHK |
| CRAFT-25 | Q1 | Only names the maintainers confirmed: OpenDAP, OpenDAP T1, Blackbird OS, OpenDAP HATs; never an unconfirmed product name. | — | Workspace brand rules | workspace rules | Review |
| CRAFT-26 | Q3 | A silk-free area on each production board for a serial-number or 2D-code label, flat and away from probe points. | Size per board | Traceability in production | — | Review |

<!-- <<< odapkit craft rules -->

## 5. Placement

Placement carries most of a board's visible order: a grid, rows, shared orientations, and mirrored channels laid out as mirrors.

<!-- >>> odapkit craft rules §5: rendered from toolkit-hardware tools/craft/rules.toml; edit it, not this table -->

| ID | Level | Rule | Value | Why | Source | Enforcement |
|---|---|---|---|---|---|---|
| CRAFT-30 | Q2 | Footprint anchors on the placement grid, from the board's grid origin. Parts placed from a mechanical datum take a `Craft_Exempt`. | 0.05 mm | A common grid makes rows and alignments exact; 0.05 mm is fine enough for 0.4 mm-pitch HDI placement. OpenDAP's choice (SparkFun uses 0.1 in for the outline) | [SFE] | CHK |
| CRAFT-31 | Q2 | Footprint rotations in multiples of 90°. | — | Pick-and-place and inspection; diagonal parts break rows | — | CHK |
| CRAFT-32 | Q3 | Passives in one placement group share one orientation (0°/180° or 90°/270°). A group is a KiCad group, else a schematic sheet instance. | — | Uniform blocks read as one function and inspect faster | — | CHK |
| CRAFT-33 | Q3 | Passives in a group sit in rows or columns: each shares an x or y centre with another. | 0.01 mm | — | — | CHK |
| CRAFT-34 | Q2 | Mirrored channels are laid out as mirrors: left and right audio channels, and the two halves of a DAC or amplifier, symmetric about an axis (or translated copies), part for part, with the same footprints and mirrored rotations. Declared in `<project>.craft.toml`. | 0.05 mm | Matched channels: TI's OPA1622 EVM keeps the left and right traces "as balanced as possible"; mirrored artwork for matched THD+N is reported in AN-1490 (**UNVERIFIED** verbatim) | [TI-OPA1622] §5.1; [AN-1490] | CHK |
| CRAFT-35 | Q2 | The board outline, mounting holes, connectors and controls come from the enclosure's export (case `export/`, component export contract), never drawn by eye; corners take the case's radii. | — | Hierarchical composition; the board fits its enclosure by construction | Component export contract; [IFX-MB] | Review |
| CRAFT-36 | Q3 | One mounting-hole (screw) size per board, and one screw family across the device, driven by common tools. | — | T1-055: the battery is replaced with common tools | T1-055 | CHK (size); Review (family) |
| CRAFT-37 | Q3 | Shield cans are catalogue parts (T1-038), two-piece (frame and removable lid) where the inside must be probed or reworked, aligned to the placement grid and to the board edges. | — | Off-the-shelf parts; access for repair | T1-038 | Review |
| CRAFT-38 | Q2 | Connectors at the board edge facing their enclosure opening, one family per function, the same mating direction, with a latch or a bracket on every flat-cable and board-to-board connector. | — | A flat-cable or board-to-board connector held only by its mating force can unseat in a drop; one mating direction toward the opening keeps assembly and repair to one motion | — | Review |
| CRAFT-39 | Q1 | The battery connector is reachable with the back cover removed and nothing else (T1-055), and labelled (CRAFT-14). | — | User-replaceable battery | T1-055 | Review |

<!-- <<< odapkit craft rules -->

## 6. Routing and copper

<!-- >>> odapkit craft rules §6: rendered from toolkit-hardware tools/craft/rules.toml; edit it, not this table -->

| ID | Level | Rule | Value | Why | Source | Enforcement |
|---|---|---|---|---|---|---|
| CRAFT-40 | Q2 | Straight segments at multiples of 45°. | 0.001 mm tolerance | One geometry for the whole board | — | DRC |
| CRAFT-41 | Q3 | Corners of 135° or more between connected segments: no right-angle or acute corners; T-junctions are made at a pad or a via, or with 45° entries. | 135° | Even routing; acute corners trap etchant and narrow the copper | — | DRC |
| CRAFT-42 | Q3 | Arcs only where signal integrity justifies them: on impedance-controlled classes. | — | Arcs are a tool for controlled impedance, not a style | — | CHK (KiCad DRC gives arcs no property of their own) |
| CRAFT-43 | Q2 | Track widths from the OpenDAP series; impedance classes take their widths from the stackup. | 0.08, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0 mm; wider copper is a pour | A closed series keeps neighbouring tracks visibly related; every value is at or above the fab sets' minimums (3 mil HDI, 4 mil multilayer) | [RES] | DRC |
| CRAFT-44 | Q3 | At most two widths per net class: the class width and one neck-down. | 2 | Width follows function, not the router's mood | — | CHK |
| CRAFT-45 | Q2 | Vias from the OpenDAP set (drill / land). | 0.10 / 0.25 mm laser microvia; 0.20 / 0.46 mm; 0.30 / 0.60 mm | Each meets the prototype fab's HDI and multilayer minimums (0.075 mm microvia ring, 5 mil mechanical ring); fewer sizes, fewer tools | [RES] | DRC |
| CRAFT-46 | Q3 | At most eight mechanical drill sizes per board (vias, plated and unplated holes, slots); laser drills not counted. | 8 | Each size is a tool change; consistency | — | CHK |
| CRAFT-47 | Q4 | No jogs: no straight segment shorter than 0.1 mm. | 0.1 mm | Short kinks are router residue | — | DRC |
| CRAFT-48 | Q3 | One teardrop policy for the board: on at every via and pad junction, or off at all of them. Product rules may require them (the T1 board does at Q2). | — | Consistent geometry | — | CHK |
| CRAFT-49 | Q2 | Copper zones remove islands; set the Board Setup check `isolated_copper` to error. | — | No floating copper | — | CHK |
| CRAFT-50 | Q3 | Zone fill minimum width ≥ 0.2 mm and zone outlines at 45° multiples. | 0.2 mm | No slivers; pour edges follow the routing geometry. KiCad's `connection_width` did not report a 1 mm neck against a 2 mm minimum in kicad-cli 10.0.7 tests, so slivers are controlled by the fill's minimum width | — | CHK |
| CRAFT-51 | Q3 | Stitching vias (on no track) in one size, on the placement grid, on a regular pitch; the TI OPA1622 EVM rings its jack ground with stitching vias. | Pitch per board | Even return paths and an even appearance | [TI-OPA1622] §5.1 | CHK (size, grid); Review (pitch) |
| CRAFT-52 | Q4 | Matched visual density: no needless vias, no stray copper, similar fill across a block. | — | — | — | Review |

<!-- <<< odapkit craft rules -->

## 7. Documentation layers

The board file carries its own fabrication drawing, so the published files are complete without a separate PDF.

<!-- >>> odapkit craft rules §7: rendered from toolkit-hardware tools/craft/rules.toml; edit it, not this table -->

| ID | Level | Rule | Value | Why | Source | Enforcement |
|---|---|---|---|---|---|---|
| CRAFT-60 | Q2 | Fabrication notes on User.Comments or User.Drawings, titled "Fabrication notes": material, finish, mask and silk colours, impedance and test requirements, the pick-and-place rotation convention (CRAFT-15). | — | The order cannot drift from the design | IPC-2614 covers board fabrication documentation (public summary; the standard itself was not read) | CHK (present); Review (content) |
| CRAFT-61 | Q2 | The stackup table (KiCad: Place > Stackup Table) on a User layer, with the layer legend. | — | One source for the stackup | — | CHK |
| CRAFT-62 | Q2 | Board dimensions on User.Drawings: at least the outline's width and height, and the mounting holes and connector datums. | ≥ 2 | IPC-2615 covers dimensioning (public summary only) | — | CHK (count); Review (datums) |
| CRAFT-63 | Q2 | Layer roles fixed: Edge.Cuts outline only; User.Drawings dimensions; User.Comments notes; F/B.Fab body outlines and `${REFERENCE}` (KLC F5.2); F/B.Courtyard courtyards (KLC F5.3). | — | Every OpenDAP board reads the same | [KLC] F5.2, F5.3 | Review |

<!-- <<< odapkit craft rules -->

## 8. Colour and finish

The design system's palette is graphite, white and one orange accent, with "matte finishes" as an attribute, and orange reserved for the Action button [DS] §2.1, §3, §7. Its default logo colour is white on graphite [DS] §2.3. A board follows that palette; the specific finish is a cost and inspection decision, so it is a proposal.

<!-- >>> odapkit craft rules §8: rendered from toolkit-hardware tools/craft/rules.toml; edit it, not this table -->

| ID | Level | Rule | Value | Why | Source | Enforcement |
|---|---|---|---|---|---|---|
| CRAFT-70 | Q3 | **PROPOSAL, for the maintainers:** production boards in matte black solder mask with white silkscreen and ENIG finish; prototypes (Q1) in any standard colour of the prototype fab, green preferred. | Matte black / white / ENIG | Matte black is the closest offered colour to graphite; white silk is the logo's default colour on graphite. The prototype fab offers matte black as a premium colour; its black mask needs a wider web (4 mil for black against 3 mil for green at the advanced minimum; 5 mil for colours other than green on the HDI page), and black hides traces from visual inspection, which is why prototypes stay green. ENIG is the starting point for the main board | [FAB]; [RES] | Review |
| CRAFT-71 | Q2 | Mask and silk colours recorded in the KiCad stackup (Board Setup), the same on both sides, so renders and the fab notes match the order. | — | — | — | CHK |
| CRAFT-72 | Q2 | No accent colours on boards: no orange or coloured silk, no coloured mask other than the chosen one. | — | Orange belongs to the Action button | [DS] §3.3, §7 | Review |

<!-- <<< odapkit craft rules -->

## 9. Schematics and symbols

A schematic is the board's primary technical document: reviewers, HAT developers and repairers read it before the copper, and RFC 0011 makes each sheet a feature block that other boards reuse. The rules below set how an OpenDAP schematic communicates: one drawing sheet, one block per sheet, symbols drawn the same way as KiCad's own library, fields a tool can read, net names with one meaning, and sheets laid out in the reading order of the signal.

Sources: the KiCad Library Conventions as text [KLC-S], KiCad 10's schematic editor, drawing-sheet editor and CLI manuals [KICAD-SCH] [KICAD-WKS] [KICAD-CLI], tested on kicad-cli 10.0.7 [K10]; IEC 61082-1 for document structure and grid references, IEEE 315 and IEC 60617 for graphic symbols, IEEE 315 / ASME Y14.44 for reference designations [IEC-61082] [IEEE-315] [IEC-60617]; and nine published KiCad designs measured for this standard [BENCH]. The IEC and IEEE standards themselves were not read; where a rule rests on them, it rests on the summary cited, and the rule says so.

Templates in this repository: the drawing sheet [`drawing-sheets/opendap.kicad_wks`](../../drawing-sheets/), the colour theme [`colour-themes/opendap.json`](../../colour-themes/), the block-sheet template [`templates/feature-block/`](../../templates/feature-block/) and an example project that passes every check, [`examples/load-switch/`](../../examples/load-switch/), all generated by [`templates/generate.py`](../../templates/generate.py).

### 9.1 Drawing sheet and title block

One drawing sheet serves every OpenDAP schematic. Its items are anchored to the page corners and its zone marks repeat to the page edge, so the same file draws an A4 or an A3 frame (tested at both sizes with kicad-cli 10.0.7).

Title block, field by field. Per-sheet values live in each sheet's title block (KiCad stores a title block per sheet file [K10]); project-wide values are project text variables, written once in the `.kicad_pro` and shown on every sheet by the drawing sheet.

| Shown as | Source | Content | Example |
|---|---|---|---|
| Title | `${TITLE}` (sheet) | The sheet's function, in sentence case | Peripheral supply switch |
| Block | `${COMMENT1}` (sheet) | The block ID (RFC 0011), or `root`, `power`, `interconnect` | `blk.periph-switch` |
| Purpose | `${COMMENT2}` (sheet) | One sentence: what the sheet does and for whom | Switches VIN onto VOUT … |
| Project, board | `${OPENDAP_PROJECT}`, `${OPENDAP_BOARD}` (project) | The product and the board | OpenDAP T1, main board |
| Variant | `${VARIANT}` (KiCad 10), or the project's `${OPENDAP_BASE_VARIANT}` when it is empty | The design variant a plot was made for; for the base design (KiCad's default variant, every part fitted) the name the project gives it in `OPENDAP_BASE_VARIANT`, `full` (CRAFT-147). The drawing sheet picks between the two with a KiCad 10 text expression; a project text variable named `VARIANT` would not show, KiCad resolves its own first [K10] | full, production |
| Rev | `${REVISION}` (sheet) | The board revision, the same on every sheet | A |
| Date | `${ISSUE_DATE}` (sheet) | ISO 8601 date of the sheet's last change | 2026-10-24 |
| Sheet | `${#} / ${##}` | Sheet number and count | 5 / 12 |
| Terms | `${COMPANY}`, `${OPENDAP_LICENCE}`, `${OPENDAP_SOURCE}` | "The OpenDAP Project", the SPDX identifier, the source repository | CERN-OHL-S-2.0 · github.com/opendap-project/board |
| Path | `${SHEETPATH}` | Where the sheet sits in the hierarchy | /Audio/DAC left/ |

<!-- >>> odapkit craft rules §9.1: rendered from toolkit-hardware tools/craft/rules.toml; edit it, not this table -->

| ID | Level | Rule | Value | Why | Source | Enforcement |
|---|---|---|---|---|---|---|
| CRAFT-80 | Q1 | Every sheet's title block is filled per the field map: title, ISO 8601 date, revision (identical on every sheet), company "The OpenDAP Project", comment 1 the block ID or role, comment 2 the purpose. | — | The sheet identifies itself when printed alone; one revision per board | [IEC-61082]; [BENCH] (8 of 9 projects fill title, date, revision and company) | CHK |
| CRAFT-81 | Q2 | A4 landscape for block sheets; A3 landscape for the root, the overviews and a block that cannot be read at A4 (an SoC's power unit). No other size, no portrait. | A4 / A3 | Readable when printed; poster-size single sheets hide the architecture (Pocket Reform, A0) | [BENCH] | CHK |
| CRAFT-100 | Q1 | The project uses the OpenDAP drawing sheet from kicad-lib through the pinned submodule: `${KIPRJMOD}/../lib/opendap/drawing-sheets/opendap.kicad_wks`, and the file exists. | — | kicad-cli silently falls back to KiCad's default sheet when the path is wrong [K10]; Pocket Reform's sheet points outside its repository [BENCH] | [KICAD-WKS] | CHK |
| CRAFT-101 | Q2 | Project text variables `OPENDAP_PROJECT`, `OPENDAP_BOARD`, `OPENDAP_LICENCE` (`CERN-OHL-S-2.0` or `CERN-OHL-W-2.0`) and `OPENDAP_SOURCE` (`github.com/opendap-project/<repository>`) are set; ERC `unresolved_variable` at error. | — | One source for every sheet; Great Scott Gadgets fill their title blocks the same way | [BENCH]; workspace licensing rule | CHK, ERC |
| CRAFT-102 | Q2 | One typeface: KiCad's stroke font, in the drawing sheet and on every sheet. Text sizes in the drawing sheet 1.0, 1.5 and 2.5 mm; lines 0.15 mm, frame 0.25 mm. | Stroke font | See below | [K10]; [BENCH] (no project sets a face) | CHK (sheets); Review (drawing sheet) |
| CRAFT-103 | Q2 | No hand-set colours on items: colour comes from the theme. Plots and reviews use the OpenDAP theme: graphite ink on white for parts, local labels and the sheet; functional accents, one per drawing role, for what a reader must tell apart at a glance: wires and junctions `#2563A8`, buses `#173866` (the same hue, darker, on the wider bus line), hierarchical labels and sheet pins `#007068` (a block's interface, one colour on both sides of the sheet boundary), global labels `#962668` (rails only, CRAFT-150), DNP and excluded-from-simulation markers `#6C40B8`; secondary text (fields, pin numbers, notes, net-class flags) in `graphite.700`, no-connect flags in `graphite.500`; the error and warning hues only for ERC markers, darkened for white (`#C62832`, `#966200`); the orange accent only for the editor's hover and selection. Every ink colour reaches 4.5:1 on white for text and 3:1 for lines, and the functional groups are at least ΔE76 20 apart (`colour-themes/check_theme.py`). Meaning is never carried by colour alone: label kinds keep their shapes (CRAFT-151), rails their power symbols (CRAFT-85). | [`colour-themes/opendap.json`](../../colour-themes/opendap.json) | Restrained palette with functional colour accents, orange reserved for interaction [DS] §1, §3; a monochrome theme made wires, labels, symbol bodies and rails hard to tell apart in the 2026-10-25 visual review (§12); prints in black and white without loss of meaning | [DS] §3.1–3.3; WCAG 2.2 1.4.3, 1.4.11 | CHK (no colour on items; `check_theme.py` on the theme); Review (theme) |
| CRAFT-104 | Q3 | Notes refer to places by sheet and zone (`5:B3`), from the frame's zone marks (columns 1–6 or 1–8, rows A–D or A–F, 50 mm fields). | — | IEC 61082-1 grid reference system (summary) | [IEC-61082] | Review |

<!-- <<< odapkit craft rules -->

**Why the stroke font, not IBM Plex.** KiCad 10 can embed outline fonts in a schematic (`(embedded_fonts yes)` and `embedded_files`, Schematic Setup → Embedded Files) [KICAD-SCH], but a `.kicad_wks` cannot embed a font: it names a face that fontconfig resolves. A font embedded in the schematic did reach the drawing sheet in a test, but that is observed behaviour, not documented [K10]; without the font, KiCad falls back to the stroke font with only a console message, and `kicad-cli sch upgrade` drops embedded fonts [K10]. Every symbol in KiCad's library and in kicad-lib is set in the stroke font at 1.27 mm (KLC S3.2), so the sheet stays one face. IBM Plex Sans in the title block is a proposal for Q4 once KiCad documents the behaviour (§12).

### 9.2 Sheet structure

<!-- >>> odapkit craft rules §9.2: rendered from toolkit-hardware tools/craft/rules.toml; edit it, not this table -->

| ID | Level | Rule | Value | Why | Source | Enforcement |
|---|---|---|---|---|---|---|
| CRAFT-82 | Q1 | One feature block per sheet (RFC 0011). The sheet symbol carries a `Block` field with the block ID, its title block comment 1 says the same, and its file is named after the block without `blk.` (`periph-switch.kicad_sch`). | — | Tools resolve blocks without parsing sheet names; one sheet is one layout block and one variant unit | RFC 0011 §1; board feature-blocks spec §1 | CHK |
| CRAFT-110 | Q2 | Three overview sheets: the **root** (a block diagram: sheet symbols, buses, notes, no parts), the **power** overview (power tree and rail table: rail, voltage, source, switch, consumers, net class, measurement) and the **interconnect** overview (one pin-map table per external connector). Parts live only in block sheets; mounting holes, fiducials and logos in a `blk.mechanical` sheet. | — | The architecture first, depth after; Cynthion's and Kria's roots are wired block diagrams [BENCH] | [BENCH]; CM4 IO board (connector index per sheet) | CHK |
| CRAFT-111 | Q3 | The root reads in signal-flow order: overviews in the top row, then blocks left to right, top to bottom, from inputs (power and connectors) through processing to outputs; page numbers follow that reading order. | — | The PDF reads in the same order as the diagram | [IEC-61082] (signal flow, summary) | CHK (page order); Review (flow) |
| CRAFT-112 | Q1 | Sheet pins are named exactly as the block's interface (the catalog's `interface` list, mirrored in `<project>.craft.toml` or a `blocks.toml`) and equal the sheet's hierarchical labels; ERC `hier_label_mismatch` at error. | — | RFC 0011 rule 4: a block refers only to its interface | RFC 0011 | CHK, ERC |
| CRAFT-113 | Q2 | Sheet pins by direction: inputs on the left edge, outputs on the right, supplies entering at the top, a separate ground return at the bottom, bidirectional buses on the side of their source; a rail the block produces leaves on the right. | — | The block symbol reads like an IC symbol (KLC S4.2) | [KLC-S] S4.2 | CHK |
| CRAFT-114 | Q3 | Inside the block sheet, hierarchical labels sit at the sheet's edges on the same side as their sheet pins. | — | The sheet and its symbol read the same way | — | CHK |
| CRAFT-115 | Q2 | Every block sheet has a text box beginning "Notes", above the title block: function, **absent state** (RFC 0011 rule 2), the constraint IDs the sheet implements, the decisions it applies by ADR number, and the "unless noted" defaults (tolerance, dielectric, voltage rating). | — | The reasons travel with the circuit; Glasgow's notes carry numbers [BENCH] | RFC 0011; [BENCH] | CHK (present, absent state stated); Review (content) |
| CRAFT-116 | Q3 | References numbered by sheet: one hundred-block per sheet (KiCad's "sheet number × 100" annotation), × 1000 for a sheet with more than 99 parts of a prefix. | 100 | Stable designators per block; the mirrored pairs of CRAFT-34 and CRAFT-87 follow by construction | — | CHK |
| CRAFT-87 | Q3 | Mirrored channels drawn once, as one hierarchical sheet used twice, annotated by sheet so that CRAFT-34's pairs are R1xx / R2xx by construction. | offset 100 | Symmetry starts in the schematic; Cynthion and Glasgow reuse sheets for repeated channels [BENCH] | [BENCH] | Review |

<!-- <<< odapkit craft rules -->

### 9.3 Symbols

kicad-lib's symbols, and any project symbol, follow KLC; where OpenDAP differs, the rule says so.

<!-- >>> odapkit craft rules §9.3: rendered from toolkit-hardware tools/craft/rules.toml; edit it, not this table -->

| ID | Level | Rule | Value | Why | Source | Enforcement |
|---|---|---|---|---|---|---|
| CRAFT-120 | Q1 | Symbols come from kicad-lib or KiCad's library; never drawn inside a project schematic. Project-only symbols live in the project's `hardware/lib/` and follow this section. | — | One drawing per part, checked once | [KLC-S] | Review |
| CRAFT-121 | Q2 | Pin length 2.54 mm, longer in 1.27 mm steps when the pin number needs it: 1.27 mm per character of the symbol's longest pin number beyond the first (two characters: 3.81 mm; a ball name such as `AA38`: 6.35 mm), at most 7.62 mm, one length per symbol, so the number, centred on its pin, clears the pin name and a no-connect flag on the pin end. Two-pin discretes keep KiCad's short pins. | 2.54–7.62 mm | KLC S4.1; at 2.54 mm a two-digit number met the no-connect flag in the 2026-10-26 visual review (a stroke-font character is about 1.1 mm at 1.27 mm, the flag 1.22 mm across) | [KLC-S] S4.1 | CHK |
| CRAFT-122 | Q1 | Pin connection points on the 2.54 mm grid; two-pin discretes on 1.27 mm, as KiCad's own. | 2.54 mm | Symbols land on the schematic's connection grid; KiCad's library is 98 % on grid, Cynthion 16 % [BENCH] | [KLC-S] S4.1; [IEC-60617] (via KLC) | CHK |
| CRAFT-123 | Q2 | Supply inputs at the top, ground and negative supplies at the bottom; a converter's input on the left and output on the right (KLC's exception). | — | KLC S4.2; supply flows down the page | [KLC-S] S4.2 | CHK |
| CRAFT-124 | Q1 | Electrical pin types set for ERC: `power_in` / `power_out` for supplies, `bidirectional` for programmable IO, `open_collector` for open-drain, `no_connect` for NC pads, `passive` for every pin of passives and connectors; never `unspecified`. Active-low pin names use the overbar `~{RESET}` (KLC S4.7). | — | ERC is only as good as the pin types; KLC S4.4 | [KLC-S] S4.4, S4.7 | CHK; ERC |
| CRAFT-125 | Q1 | No hidden pins, NC pins included (stricter than KLC S4.6). | — | Every pad is visible and takes a wire or a no-connect flag; a hidden power pin makes an implicit global connection [KICAD-SCH] | [KLC-S] S4.6 | CHK |
| CRAFT-126 | Q2 | Large parts are split into units by function: an SoC by IO domain and function (one unit per IO bank, one per high-speed interface, separate power and ground units); a PMIC by rail (one unit per regulator with its pins, one for control); connectors above 64 contacts by group. Units are not interchangeable. | ≤ 64 pins per unit | A unit fits an A4 sheet at 2.54 mm pitch and lands on the sheet of its block; KLC S3.8 separates power units | [KLC-S] S3.8 | CHK (pin count); Review (split) |
| CRAFT-127 | Q3 | Inputs on the left, outputs on the right; bus pins in KiCad's bus order; pins grouped by function with a one-grid gap between groups, ports ordered top to bottom. | — | KLC S4.2 | [KLC-S] S4.2 | CHK (sides, heuristic); Review |
| CRAFT-128 | Q2 | Black-box bodies (ICs, modules, connectors): rectangle, 0.254 mm line, background fill; simple discretes unfilled; no De Morgan body style. | 0.254 mm | KLC S3.3, S3.9 | [KLC-S] S3.3 | CHK |
| CRAFT-129 | Q2 | Symbol text 1.27 mm; pin names and numbers down to 0.508 mm only on small symbols; pin-name offset 0.508 mm. | 1.27 mm | KLC S3.2, S3.6 | [KLC-S] S3.2, S3.6 | CHK |
| CRAFT-130 | Q1 | Reference prefixes from KLC's designator table (IEEE 315 / ASME Y14.44 lineage): R, C, L, FB, D, Q, U, J, P, SW, Y (crystals and oscillators), F, BT, H, JP, K, T, M, LS, MK, AE, RN, RT, RV, TP, FL, DS, A, Z; plus NT (net tie) and FID (the boards' fiducials). | — | One meaning per letter across every board | [KLC-S] S6.1; [IEEE-315] | CHK |
| CRAFT-131 | Q3 | IEC 60617 graphic forms (rectangular resistor, as KiCad's `Device:R`), never the `_US` forms; logic gates in one style per board. | — | KiCad's default library and international readers | [IEC-60617]; [KLC-S] | CHK (`_US`); Review |
| CRAFT-132 | Q2 | Alternate pin functions for multiplexed pads (KLC S4.1): the pin's primary name is its default function (a strap's function first); every other function is an alternate, and each placed unit selects the function the board uses, so the sheet names what the pad does. | — | The SoC's pin plan reads on the sheet; KiCad 10 supports `(alternate …)` per pin | [KLC-S] S4.1; [KICAD-SCH] | Review |
| CRAFT-133 | Q2 | Placed IC symbols upright: rotation 0°, never mirrored; passives at 0° or 90°. | — | Pin text reads left to right | — | CHK |

<!-- <<< odapkit craft rules -->

### 9.4 Fields

| Field | Content | Shown |
|---|---|---|
| Reference | Designator (CRAFT-130); a unit of a multi-unit part shows it with a dot and the unit's name or letter (`U3501.System`, `U1.A`): the project's unit notation `.A` (`schematic.subpart_id_separator` 46 in the `.kicad_pro`), since KiCad 10 otherwise runs the unit name into the designator (`U3501System`) [K10] | Yes |
| Value | What a reader needs to identify the part: resistors, capacitors, inductors and crystals as a value with its unit (CRAFT-141: `22 Ω`, `4.7 kΩ`, `100 nF`, `2.2 µH`, `24.576 MHz`); ICs the base part number (`TPS22919DCK`); connectors the series | Yes |
| Spec | Secondary ratings when they differ from the sheet's "unless noted" line: voltage, dielectric, tolerance, power (`16 V X7R`, `1 % 0603`) | Yes, under Value |
| Footprint | `<library>:<footprint>` | No |
| Datasheet | https URL of the manufacturer's data sheet | No |
| Description | One line: function, key ratings, package | No |
| Manufacturer, MPN | The primary orderable part (turn-key assembly) | No |
| Manufacturer_2, MPN_2 | A second source, as a pair | No |
| Block | Block ID (RFC 0011) | No |
| Constraints | Constraint IDs the part is placed or routed under (`PCB-A-07 PCB-P-02`) | No |
| Alternate | On a DNP footprint of an alternate pair: the alternate's name (RFC 0011 rule 7) | No |
| Sim.* | KiCad's simulation fields, models referenced inside the project | No |

<!-- >>> odapkit craft rules §9.4: rendered from toolkit-hardware tools/craft/rules.toml; edit it, not this table -->

| ID | Level | Rule | Value | Why | Source | Enforcement |
|---|---|---|---|---|---|---|
| CRAFT-140 | Q1 | Every placed part carries Reference, Value, Footprint (on-board parts), Datasheet (https), Description, Manufacturer and MPN (BOM parts) and Block, filled; library symbols carry Description and, fully specified, Footprint, Datasheet, Manufacturer and MPN. | — | BOM, assembly and the variant tool read them; MPN on 77–100 % in 7 of 9 benchmarked projects | [KLC-S] S6.2; [BENCH] | CHK |
| CRAFT-141 | Q2 | Component values always carry their unit: the number, one space, the SI prefix and the unit symbol. Unicode `Ω` (U+03A9) and `µ` (U+00B5), never `R`, `u`, the ohm sign U+2126 or the Greek mu U+03BC; the decimal point is `.`; no bare numbers, no `4k7` notation. Checked on resistors (`Ω`), capacitors (`F`), inductors (`H`) and crystals (`Hz`); any other part whose Value is a quantity follows the same form. Ratings go to Spec, never into Value. | `22 Ω`, `4.7 kΩ`, `5 mΩ`, `0 Ω`, `100 nF`, `2.2 µH`, `24.576 MHz` | A bare number is ambiguous: in a visual review in KiCad (2026-10-25) a 1 Ω shunt read only "1". KiCad renders `Ω` and `µ`, and KiCad 10.0.7's SPICE netlist export still reads the form (`100 kΩ` → `100k`, `1 µF` → `1u`), so the simulator and BOM grouping keep working; Olimex packs ratings into Value, which no tool can read [BENCH] | [BENCH]; maintainers, 2026-10-25 | CHK |
| CRAFT-142 | Q2 | Spec shown only where it differs from the sheet's "unless noted" defaults; capacitors on rails above 5 V and every polarised part always show their voltage. | — | Reader's information without clutter | IEC 61082 "unless otherwise specified" practice (summary) | Review |
| CRAFT-143 | Q2 | Only Reference, Value and Spec are shown; every other field hidden. Field names exactly as the table; no unnamed, placeholder or vendor-leftover fields. | — | KLC S6.2; CM4 IO's `Field4`–`Field8` and SnapEDA leftovers are what this prevents [BENCH] | [KLC-S] S6.2; [BENCH] | CHK |
| CRAFT-144 | Q3 | A second source, where one exists, in `Manufacturer_2` and `MPN_2` together. | — | Sourcing resilience (workspace parts rule); GSG `Substitution`, Glasgow `MPN_Alt` | [BENCH] | CHK (pairs); Review |
| CRAFT-145 | Q1 | Block equals the block of the sheet the part sits on. | — | RFC 0011: the variant tool resolves parts by block | RFC 0011 | CHK |
| CRAFT-146 | Q3 | Constraints holds only constraint IDs, space-separated. | — | The layout check finds the parts a constraint binds | Board PCB design constraints | CHK |
| CRAFT-147 | Q1 | Variants are data (RFC 0011 rule 7): the base design fits every part (the full variant); DNP in the base design only on the unused footprint of an alternate pair, which carries `Alternate`. Other variants are KiCad 10 design variants written by `odapkit variant sync` from the variant files, never edited by hand. | — | KiCad 10 stores per-variant DNP and field overrides per instance [K10]; hand edits would drift from the variant files | RFC 0011; [KICAD-SCH] | CHK |
| CRAFT-148 | Q3 | Simulation fields reference models inside the project (`${KIPRJMOD}`), never an absolute path; parts without a model on a simulated sheet are excluded from simulation. | — | `odapkit sim` runs anywhere | — | CHK |

<!-- <<< odapkit craft rules -->

### 9.5 Nets, labels and connections

<!-- >>> odapkit craft rules §9.5: rendered from toolkit-hardware tools/craft/rules.toml; edit it, not this table -->

| ID | Level | Rule | Value | Why | Source | Enforcement |
|---|---|---|---|---|---|---|
| CRAFT-84 | Q1 | Net names: signals in `UPPER_SNAKE_CASE`, named by function from the host's side, connector-style (`I2C2_SCL`, `FP_SPI_CS_N`), never by SoC ball; rails exactly as the board's power tree names them; active low with the suffix `_N`, never an overbar, `n` prefix, `#` or `!` in a net name; differential pairs `_P` / `_N` (a net ending `_N` is a pair member only if its `_P` exists); buses as vectors `D[0..7]` or groups `I2C2{SCL SDA}` with project bus aliases; direction suffixes `_IN` / `_OUT` only where the host's view is ambiguous. ERC `similar_labels`, `multiple_net_names` at error. | — | Names feed net classes, DRC rules, the pin plan, devicetree and the variant tool; the T1 pinout v1 uses `_N` | [BENCH]; board pinout v1 | CHK, ERC |
| CRAFT-85 | Q1 | Rails through power symbols whose Value is the rail's name in the power tree; one `GND` unless a separate return is designed (joined by a net tie, NT); ground symbols point down, positive rails up; `PWR_FLAG` only where a rail is driven from off the sheet's parts (a connector, a measurement link). Rails internal to one block may use KiCad 10 local power symbols. | — | Power symbols are global by nature; a rail named twice is two nets | [KICAD-SCH] | CHK, ERC |
| CRAFT-150 | Q1 | Label kinds carry meaning: local labels within a sheet; hierarchical labels for a block's interface; global labels never for signals, only for a rail that cannot be a power symbol. | — | Flat designs through global labels hide the data flow (Reform: 396 global, 0 hierarchical) [BENCH] | [BENCH] | CHK |
| CRAFT-151 | Q2 | Label and sheet-pin shapes state direction as seen from the block: `input`, `output`, `bidirectional` (I2C SDA, SDIO data), `tri_state` (shared buses with an output enable), `passive` (analogue and supplies where direction does not apply); a sheet pin and its hierarchical label share the shape. KiCad treats shapes as visual only [KICAD-SCH], so they are checked here. | — | Shape is the semiotics of direction | [KICAD-SCH] | CHK |
| CRAFT-152 | Q1 | A no-connect flag on every unused pin; ERC `pin_not_connected`, `no_connect_connected`, `no_connect_dangling` at error. | — | Every pad accounted for | — | ERC |
| CRAFT-153 | Q2 | No four-way junctions: a crossing is two T-junctions one grid step apart; a dot on every T, none elsewhere; ERC `four_way_junction` at error (KiCad's default ignores it). | — | A four-way dot is lost when printed small; a crossing without a dot is ambiguous | IEEE 315 / IEC 60617 practice (summary) | CHK, ERC |
| CRAFT-154 | Q2 | Wires and buses orthogonal, at KiCad's default widths; no wire through a symbol body or text; buses for three or more related nets that travel together. | — | — | — | CHK (orthogonal, widths); Review |
| CRAFT-155 | Q2 | Net classes in the project match the board's `hardware/rules/netclasses.toml` both ways: every class there exists in the project, and every class the project uses (classes, patterns, `Netclass` fields of directive labels) is declared there; ERC `undefined_netclass` at error. Classes are assigned by patterns generated from the file, or by a directive label at the net's source. | — | The schematic and the product rules share one contract | board `netclasses.toml` | CHK, ERC |
| CRAFT-156 | Q2 | Interface nets cross the hierarchy only through sheet pins; a block's internal nets never leave it. | — | RFC 0011 rule 4 | RFC 0011 | Review (variant tool check, RFC 0011 §6.3) |
| CRAFT-157 | Q3 | A net joining two or more ICs is named. | — | A reader can refer to it; auto names change with annotation | — | CHK (netlist) |
| CRAFT-158 | Q1 | The project's ERC severities hold the craft checks at error: `endpoint_off_grid`, `four_way_junction`, `pin_not_connected`, `label_dangling`, `hier_label_mismatch`, `power_pin_not_driven`, `pin_not_driven`, `unresolved_variable`, `unannotated`, `duplicate_reference`, `missing_power_pin`, `multiple_net_names`, `similar_labels`, `similar_power`, `similar_label_and_power`, `same_local_global_label`, `same_local_global_power`, `no_connect_connected`, `no_connect_dangling`, `wire_dangling`, `unconnected_wire_endpoint`, `undefined_netclass`, `ground_pin_not_ground`. No rule class is switched off; an exception is an ERC exclusion with its reason. | — | Antmicro switches `pin_to_pin` off; no benchmarked board runs ERC in CI [BENCH] | [K10] (KiCad 10 key names) | CHK |

<!-- <<< odapkit craft rules -->

### 9.6 Sheet layout

<!-- >>> odapkit craft rules §9.6: rendered from toolkit-hardware tools/craft/rules.toml; edit it, not this table -->

| ID | Level | Rule | Value | Why | Source | Enforcement |
|---|---|---|---|---|---|---|
| CRAFT-86 | Q2 | Everything on KiCad's 1.27 mm (50 mil) connection grid: symbols, wires, junctions, labels, sheets and sheet pins; rotations in multiples of 90°; ERC `endpoint_off_grid` at error. | 1.27 mm | Off-grid ends do not connect | [KICAD-SCH] (default grid 50 mil) | CHK, ERC |
| CRAFT-83 | Q2 | Signal flow left to right; supplies at the top, ground symbols on one baseline at the bottom of the circuit. | — | IEC 61082-1 signal-flow layout (summary) | [IEC-61082] | Review |
| CRAFT-170 | Q2 | Sheet zones: the circuit in the upper band; interface and rail tables at the lower left; notes above the title block. Interface labels inside the frame, at least 25 mm from it. | — | Every block sheet reads the same way (template) | — | Review |
| CRAFT-171 | Q2 | Decoupling next to the pins it serves, grouped by rail, in the order it is placed on the board (smallest value nearest the pin), each group under its rail's name. | — | The schematic predicts the layout | — | Review |
| CRAFT-172 | Q3 | A sheet with decoupling carries a rail table (KiCad table headed "Rail": rail, capacitors, the data sheet's requirement with its section). | — | The requirement is checked where the capacitors are chosen | — | CHK |
| CRAFT-173 | Q2 | Text sizes from a closed set: 1.27 mm for everything (labels, fields, notes, tables), 2.54 mm for group titles only. | 1.27 / 2.54 mm | Two sizes read as one hierarchy | [KLC-S] S3.2 | CHK |
| CRAFT-174 | Q2 | Functional groups inside a sheet in a titled rectangle: dashed default line, no fill, title at 2.54 mm above its top-left corner, one grid step between groups. | — | Grouping without colour | — | Review |
| CRAFT-175 | Q2 | Whitespace: fields and text never overlap a symbol, wire or other text; parts in a group aligned on rows or columns. | — | Legibility | — | Review |
| CRAFT-176 | Q2 | Pin maps, strap options, address maps, option and variant tables are KiCad tables, never text drawn with vertical-bar characters. | — | Glasgow keeps its I²C map as a KiCad table [BENCH] | [BENCH]; [KICAD-SCH] (tables) | CHK |

<!-- <<< odapkit craft rules -->

### 9.7 What goes on each sheet

| Sheet | Holds | Never |
|---|---|---|
| Root | Overview sheet symbols in the top row; block sheet symbols in signal-flow order, wired through sheet pins and buses; rails as power symbols at the sheet pins; notes on the reading order | Parts |
| Power | The power tree as a diagram; the rail table (rail, voltage, source block, switch or enable, consumers, net class, measurement point) | Parts; a rail name not in the power tree |
| Interconnect | One pin-map table per external connector (pin, net, direction, block, notes) | Parts |
| Block | One block's parts; interface labels at the edges; titled groups; interface table with each net's absent state; rail table; notes | Parts of another block; absent-state parts of its own interface (they live on the host side) |

### 9.8 How the schematic check runs

`odapkit craft check <project>.kicad_sch` (the root sheet) reads the hierarchy's S-expressions and the `.kicad_pro`, runs the CHK rules of §9, checks every embedded library symbol against §9.3, runs the project's ERC (kicad-cli) and the netlist, and reports by level as for boards: ERC violations of a type tied to a CRAFT rule count against that rule; other ERC errors count at Q0. `odapkit craft check <library>.kicad_sym` runs the symbol rules on a library. Configuration in `<project>.craft.toml`, `[schematic]` table: the power tree's rail names, the path of `netclasses.toml`, and each block's interface. KiCad 10 has no schematic Python API outside the GUI [K10], so the check parses the files itself.

## 10. Decisions taken

- **Full legend.** An open board keeps a full legend (CRAFT-10, CRAFT-11): the published files and the DIY edition make it a repair document. Discipline (CRAFT-01 to -12) keeps it quiet.
- **Single HDI main board.** No stacked boards through a perimeter interposer: the interposer is a custom part (T1-038), prototype assembly does not cover it, and rework becomes impractical.
- **Common tools.** One standard screw family (CRAFT-36, T1-055); the battery stays replaceable with common tools, and the board keeps its connector reachable and marked (CRAFT-14, CRAFT-39).
- **No rule** rests on recycled plating and solder, or on underfill; underfill is a reliability decision per BGA, taken with drop testing (§12).

## 11. Enforcement summary

Boards:

| Level | DRC (`opendap-craft.kicad_dru`) | CHK (`odapkit craft check`) | Review |
|---|---|---|---|
| Q1 | CRAFT-04, CRAFT-06 (Board Setup) | CRAFT-13, -14, -20, -23 | CRAFT-15, -25, -39 |
| Q2 | CRAFT-01, -02, -03, -05, -07, -09, -40, -43, -45 | CRAFT-08, -10, -11, -21, -24, -30, -31, -34, -49, -60, -61, -62, -71 | CRAFT-35, -38, -63, -72 |
| Q3 | CRAFT-41 | CRAFT-12, -22, -32, -33, -36, -42, -44, -46, -48, -50, -51 | CRAFT-26, -37, -70 |
| Q4 | CRAFT-47 | — | CRAFT-52 |

Schematics and symbols (§9):

| Level | ERC (project severities) | CHK (`odapkit craft check`) | Review |
|---|---|---|---|
| Q1 | CRAFT-152; also -84, -85, -101, -112, -155 | CRAFT-80, -82, -84, -85, -100, -112, -122, -124, -125, -130, -140, -145, -147, -150, -158 | CRAFT-120 |
| Q2 | CRAFT-86, -153 | CRAFT-81, -86, -101, -102, -103, -110, -113, -115, -121, -123, -126, -128, -129, -133, -141, -143, -151, -153, -154, -155, -173, -176 | CRAFT-83, -132, -142, -156, -170, -171, -174, -175 |
| Q3 | — | CRAFT-111, -114, -116, -127, -131, -144, -146, -148, -157, -172 | CRAFT-87, -104 |

Rules checked partly by a tool and partly by review are listed under the tool.

## 12. Open items

- **CRAFT-70:** the production mask, silk and finish: the maintainers' decision.
- **CRAFT-23:** whether to seek OSHWA certification.
- **The OpenDAP mark as a footprint:** kicad-lib has no `OpenDAP_Mark` footprint yet; it is to be generated from the brand's flat SVGs (blackbird-ui `brand/`), with the print minimum sizes, which the brand marks **UNVERIFIED** until a first print run.
- **Underfill** under the BGAs: a reliability decision after drop testing.
- **Pick-and-place convention per fab** (CRAFT-15): the prototype fab's convention is not published on its capability pages (**UNVERIFIED**).
- **Title-block typeface** (CRAFT-102): **decided (maintainers, 2026-10-24):** KiCad's stroke font now; IBM Plex Sans at Q4, once KiCad documents that fonts embedded in a schematic serve its drawing sheet.
- **Active-low net names** (CRAFT-84): **decided (maintainers, 2026-10-24):** net names use the suffix `_N`, as the T1 pinout v1; pin names on symbols keep the overbar `~{}`.
- **Colour theme** (CRAFT-103): **decided (maintainers, 2026-10-24):** every published plot (fab package, documentation, review PDFs) uses `colour-themes/opendap.json`, so the documents look the same everywhere; developers install it into KiCad's `colors` folder for daily work. Applying it in the fab-package tool is open.
- **Functional accents in the colour theme** (CRAFT-103): **Accepted (2026-10-25).** Basis: a visual review in KiCad found wires, labels, symbol bodies and rails hard to tell apart in the monochrome graphite theme (`check_theme.py` measures ΔE76 0 between those groups in the previous theme). The design system's palette is graphite, white and orange, with semantic colours that keep one meaning [DS] §3; its tokens are tuned for dark surfaces (`state.warning` `#F3C66B` is 1.6:1 on white), so the schematic accents are darker shades chosen for a white sheet, one per drawing role, and none of them is red, amber or orange, which stay with ERC and interaction. Why each:
  - **Wires and junctions, blue `#2563A8` (6.1:1):** connectivity is what a reader follows first; a hue apart from the graphite parts separates a wire from a symbol outline or pin where they meet.
  - **Buses, dark blue `#173866` (11.7:1):** a bus is a bundle of wires, so the same hue; darker and on KiCad's wider bus line, so a bus never reads as a wire (ΔE76 22 from wires, plus the width).
  - **Hierarchical labels and sheet pins, teal `#007068` (6.0:1):** they are one thing, a block's interface (CRAFT-112, -151), seen from the two sides of the sheet boundary; one colour ties them.
  - **Global labels, magenta `#962668` (7.6:1):** CRAFT-150 keeps global labels for rails that cannot be a power symbol, so the colour marks a rail. KiCad 10 has no theme colour for power symbols (they draw on the symbol layers; its theme keys are listed in `common/settings/color_settings.cpp`), so power symbols stay graphite and are recognised by their shape (CRAFT-85). Net-class colours were rejected for rails: they reach only the rails that have a class in `netclasses.toml`, so an uncoloured rail would read as a signal.
  - **DNP and excluded-from-simulation markers, violet `#6C40B8` (6.9:1):** an assembly state of a part, which a variant review must find at a glance (CRAFT-147); not red, because DNP is a decision, not an error.
  - **Local labels graphite, no-connect flags `graphite.500` (4.2:1), secondary text `graphite.700`:** local labels are the most numerous text and stay ink; a no-connect flag marks the absence of a net, visible but quieter than nets.
  - **ERC error `#C62832` (5.6:1) and warning `#966200` (5.2:1):** the design system's error and warning hues, darkened to read on white; ERC markers only.
  - The accents together stay a small share of the sheet's ink, the way [DS] §3.3 keeps accents to a few percent.
- **Units on component values** (CRAFT-141): **decided (maintainers, 2026-10-25),** after a visual review in KiCad where a 1 Ω shunt read only "1": component values always carry their unit, with an SI prefix and a space (`22 Ω`, `4.7 kΩ`, `5 mΩ`, `100 nF`, `2.2 µH`, `24.576 MHz`), in Unicode `Ω` and `µ`; the decimal point stays `.` in repository text. The previous short form (`4.7k`, `100n`, `1`) is retired.
- **Per-part review tick** (Reform's `Checked` field): **adopted (maintainers, 2026-10-24) at Q2:** every symbol instance carries a `Checked` field filled with the reviewer and date once the part is checked against its datasheet (application circuit, values, ratings); empty is allowed below Q2, and the schematic craft check reports it by level.
- **Library symbols:** a first run of the symbol checks on kicad-lib's own libraries (2026-10-24) found pins off the 2.54 mm grid (CRAFT-122), oscillators with prefix `X` (CRAFT-130, KLC uses `Y`) and connectors and SoC units above 64 pins (CRAFT-126). **Fixed (2026-10-25)** in the generators: top and bottom pins land on the 2.54 mm grid (an even count leaves the centre empty), the NDK oscillators take `Y`, the DF40C connectors split into two units of 50 contacts, the RK3566 ground units are rebalanced to 64 pins and the eMMC NC balls split by ball row; `generator/test_library.py` now checks CRAFT-122, -125, -126 and -130 on every symbol library. KiCad's stock `Device:Crystal` and `Device:Crystal_GND24` fail CRAFT-122 (and the latter CRAFT-125); boards use `OpenDAP_Device:Crystal` and `OpenDAP_Device:Crystal_GND24` from this library instead. CRAFT-127 findings (inputs on the right, outputs on the left) remain on CS43198-CNZR, RK809-5, BQ25798RQMR and BQ28Z610DRZR-R1; they are Q3 and still open.

## 13. Sources

Pages fetched on 2026-10-24 unless stated.

| Key | Source |
|---|---|
| KLC | KiCad Library Conventions, footprint rules F4.2, F5.1, F5.2, F5.3, F6.2, F6.3, F7.2: <https://klc.kicad.org> (read from the source repository, <https://gitlab.com/kicad/libraries/klc>, `content/footprint/`) |
| KICAD-DRC | KiCad 10 custom design rules: <https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#custom_design_rules>; behaviour measured with kicad-cli 10.0.7 ([rule set README](../../design-rules/opendap-craft/README.md)) |
| EC-ROT | Eurocircuits, pin 1 and zero-point rotation (IPC-7351 and IEC 61188-7): <https://www.eurocircuits.com/technical-guidelines/pcb-assembly-guidelines/defining-pin-1-zero-point-rotation/> |
| EC-LEG | Eurocircuits, legend print guidelines (0.10 mm line, 1.00 mm height, 0.100 mm to exposed copper, 0.250 mm to a routed edge): <https://www.eurocircuits.com/pcb-design-guidelines-legend-print/> |
| FAB | Prototype fab capabilities, [P1] in [`design-rules/pcbway-capabilities.md`](../../design-rules/pcbway-capabilities.md) (silkscreen line ≥ 0.15 mm, height ≥ 0.8 mm, width to height 1 : 5; mask colours: green, red, yellow, blue; premium white, black, matt green, matte black, purple; silk white, black, yellow) |
| JLC | JLCPCB, character design specifications (≥ 0.15 mm line and ≥ 1.0 mm height standard; ≥ 0.15 mm to pads): <https://jlcpcb.com/blog/character-design-specifications> |
| RES | [Prototype fab published capabilities](../../design-rules/pcbway-capabilities.md) and the fab rule sets in [`design-rules/`](../../design-rules/) |
| SFE | SparkFun, designing for SparkFun manufacture: <https://learn.sparkfun.com/tutorials/how-to-sell-your-widget-on-sparkfun/designing-for-sparkfun-manufacture> |
| MNT | MNT Reform sources and licences: <https://source.mnt.re/reform/reform>; motherboard photograph: <https://mntre.com/media/reform_v2_images/reform_v2_motherboard.jpg> |
| TI-OPA1622 | TI OPA1622EVM user's guide, SBOU172, §5.1: <https://www.ti.com/lit/pdf/sbou172> |
| AN-1490 | TI (National) AN-1490, LM4702 layout, mirrored channels: the English text was not read, **UNVERIFIED** |
| FOLK-PCB | Andy Hertzfeld, "PC Board Esthetics", July 1981: <https://www.folklore.org/PC_Board_Esthetics.html> |
| IFX-MB | iFixit, Retina MacBook 2015 teardown: <https://www.ifixit.com/Teardown/Retina+Macbook+2015+Teardown/39841> |
| OSHWA | OSHWA certification requirements and mark usage: <https://certification.oshwa.org/requirements.html>, <https://certification.oshwa.org/mark-usage.html> |
| CERN-S | CERN-OHL-S-2.0, §1.9 and §4 (SPDX copy): <https://spdx.org/licenses/CERN-OHL-S-2.0.html> |
| DS | Engineered Matter design system: <https://github.com/opendap-project/blackbird-ui/blob/main/docs/specs/design-system.md> |
| BRAND | OpenDAP mark construction and minimum sizes: <https://github.com/opendap-project/blackbird-ui/blob/main/brand/README.md> |
| KLC-S | KiCad Library Conventions, symbol rules S3.1–S3.9, S4.1–S4.7, S6.1, S6.2, S7.1, read from the source repository at commit `6e8cbad9556359879d2b91f904d6df82517d97d1`: <https://gitlab.com/kicad/libraries/klc> (`content/symbol/`); site <https://klc.kicad.org/symbol/> |
| KICAD-SCH | KiCad 10 Schematic Editor manual (design variants, embedding files, net classes, tables, labels, power symbols, ERC checks): <https://docs.kicad.org/10.0/en/eeschema/eeschema.html> |
| KICAD-WKS | KiCad 10 Drawing Sheet Editor manual (text variables): <https://docs.kicad.org/10.0/en/pl_editor/pl_editor.html> |
| KICAD-CLI | KiCad 10 command-line interface, schematic ERC and exports: <https://docs.kicad.org/10.0/en/cli/cli.html> |
| K10 | Behaviour measured with kicad-cli 10.0.7 on 2026-10-24 (drawing-sheet fallback, font embedding, `sch upgrade`, variant storage, ERC keys) and the KiCad 10.0 sources (`eeschema/erc/erc_item.cpp`, `common/settings/color_settings.cpp`) |
| IEC-61082 | IEC 61082-1:2014, Preparation of documents used in electrotechnology, Part 1: Rules: the standard was not read; its scope (document structure, signal flow, grid reference systems) from the IEC catalogue: <https://webstore.iec.ch/en/publication/4436> |
| IEEE-315 | IEEE 315-1975 (graphic symbols, including reference designation letters) and ASME Y14.44 (reference designations): not read; summarised at <https://en.wikipedia.org/wiki/Reference_designator> |
| IEC-60617 | IEC 60617, graphical symbols for diagrams: not read; KLC S4.1 cites it for the 100 mil grid |
| BENCH | Published KiCad designs measured for this standard, each at a recorded commit: MNT Reform and Pocket Reform, Great Scott Gadgets Cynthion and HackRF, Glasgow, Antmicro, the Compute Module 4 IO board, Olimex, the Framework mainboard, and the KiCad and SparkFun libraries |
