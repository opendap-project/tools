# Stackup and trace geometry method

- **Status:** draft, v1, 2026-10-24. Generic, for every OpenDAP board.
- **Tool:** `odapkit stackup` ([`bin/odapkit-stackup`](../../bin/odapkit-stackup), [`tools/stackup/`](../../tools/stackup/)).

A board keeps its layer stack and the geometry of each impedance class in one machine-readable file, `hardware/stackup/stackup-<version>.toml`, explained by a spec in its `docs/specs/`. The file is the input to the fab's stack-up request and to the net classes; `odapkit stackup check` recomputes it after every change, so a width in the net classes is never older than the stack it came from. With `--json` every command prints one document with a `schema`: `opendap.stackup-stack/v1`, `-design/v1` and `-coupling/v1` (their results in `items`), `-check/v1`, `-validate/v1`, `-explore/v1` and `-pdn/v1`.

## 1. Steps

1. **Stack.** Layer count, build (1+N+1, 2+N+2), each layer's role, copper weights and dielectrics from the fab's published materials, with every value that is not published marked as an assumption. `odapkit stackup stack` prints the layer table, the total thickness and the board's `[[distance]]` and `[thickness]` checks.
2. **Design.** For each class, the target impedance, the layer and its reference plane(s), and the dimension held fixed (a pair's gap). `odapkit stackup design` searches the free dimension; the result is rounded to 0.5 µm and recorded in the file with the impedance it gives (`width_um`, `gap_um`, `z_ohm`).
3. **Check.** `odapkit stackup check` recomputes every class: the nominal within the design window (default ±2 % of the target), the recorded `z_ohm` not stale (0.5 Ω), and the fab corners reported.
4. **Fab.** The file goes to the fab with the stack-up request. The fab's answer (its stack, its Dk at frequency, its etch compensation) replaces the assumptions, and the widths are recomputed before the net classes freeze. The fab trims widths to its test coupon; the board's numbers are the design intent, not the fab's final artwork.

## 2. Solver

A 2D quasi-static (quasi-TEM) field solver, the same class of method as commercial PCB impedance calculators. Laplace's equation is solved over the cross-section with linear finite elements ([scikit-fem](https://github.com/kinnala/scikit-fem), BSD-3-Clause) on a graded tensor grid that has a line on every conductor and dielectric edge. The Maxwell capacitance matrix comes from the field energy, with dielectrics and in vacuum; L = C₀⁻¹ / c². A single line gives Z₀ = √(L/C); a symmetric pair gives the odd and even modes, Z_diff = 2 Z_odd and Z_common = Z_even / 2. Effective permittivity and delay per millimetre are reported with each class, for skew budgets across layers.

**Why this solver.** It is a maintained FEM library installed the canonical way: `tools/pyproject.toml`, the one Python project of this repository's tools, pins numpy, scipy and scikit-fem, and `tools/uv.lock` pins them with their hashes, so `uv run --locked` installs exactly the verified files. The problem setup is about 150 lines that can be read whole.

**Model.**

- Planes are the domain's top and bottom boundaries at 0 V. An open (microstrip) problem is enclosed at about 60 × the line's height, which costs under 0.2 %.
- A copper layer between a line and its plane is cut out under the line (`cutout_um`); outside the cut-out it is ground copper. A cut-out layer is filled with the mean Dk of the dielectrics around it.
- An inner trace is embedded in the dielectric named by its layer's `fills` (the prepreg pressed around it). An outer trace carries a conformal solder mask of the mask layer's thickness.
- Etch is trapezoidal; the solve uses the mean width (drawn width minus half the `[etch]` reduction).
- Dielectric thicknesses are pressed thicknesses, copper surface to copper surface.
- Not modelled: frequency dependence of Dk and loss, surface roughness, glass-weave skew, resin-rich areas over cut-outs. These come from the fab's coupon and the board's SI simulation.

**Validation** (`odapkit stackup validate`, also run by the tests):

| Reference | Agreement required | Typical result |
|---|---|---|
| Cohn's exact stripline (zero thickness, conformal mapping) | ±0.5 % | −0.14 to −0.26 % |
| Hammerstad and Jensen microstrip with thickness correction (Wadell §3.5) | ±1.5 % | −0.14 to +0.09 % |
| KiCad's calculator model, microstrip, thin copper (transcribed from its source) | ±1.0 % | −0.3 % |
| A pair 3 mm apart against twice the single line | ±0.5 % | −0.08 % |

IPC-2141A and KiCad's model with thick copper are listed for comparison only. IPC-2141A reads 4–14 % low for the HDI geometries the boards use. KiCad's microstrip model adds a thickness term to the filling factor and reads about 3 % high when the copper is a third of the dielectric height (35 µm on 90 µm); it agrees with the solver for thin copper.

## 3. Tolerances

`check` moves one parameter at a time to each end of its `[tolerance]` range (dielectric thickness, Dk, width, copper thickness) and reports each change, the arithmetic worst case (every parameter at its bad end) and the root sum of squares. The ranges are assumptions unless the fab publishes them. The fab's impedance tolerance (for example ±10 %) is held by the fab compensating the width against its coupon. A worst case outside it is therefore a warning, not a failure, and an RSS outside it means the geometry is too sensitive to rely on.

## 4. File format

```toml
version = 1
name = "…"
design_window_pct = 2.0            # nominal within target ± this

[etch]                              # trapezoid: top width = drawn − reduction
outer_reduction_um = 10.0
inner_reduction_um = 6.0

[tolerance]                         # fab corners for the sensitivity report
dielectric_pct = 10
er_pct = 5
width_um = 10
copper_pct = 15

[thickness]                         # nominal >= min_mm; nominal + tolerance_mm <= max_mm
min_mm = 1.0
max_mm = 1.2
tolerance_mm = 0.1
source = "…"

[[distance]]                        # copper surface to copper surface
from = "L1"
to = "L3"
min_um = 230
max_um = 240
source = "…"

[[layer]]                           # top to bottom
name = "L1"
type = "copper"                     # copper | dielectric | mask
thickness_um = 35
role = "signal"                     # free text
material = "…"                      # free text: source of the values
er = 4.45                           # dielectric and mask
fills = "above"                     # copper: the dielectric pressed around its traces

[[class]]
name = "D90-L1"                     # one geometry; several net classes may share it
netclasses = ["USB2_90D", "USB3_90D"]
type = "diff"                       # se | diff
target_ohm = 90
tolerance_pct = 10                  # the fab's window, for the corner report
layer = "L1"
reference = ["L2"]                  # one plane, or one above and one below
solve = "width"                     # width | gap: what `design` searches
gap_um = 100                        # pair edge-to-edge gap, drawn
width_um = 101.5                    # drawn width (recorded by design)
z_ohm = 89.99                       # recorded impedance (stale check)
cutout_um = 1600                    # optional: copper between line and plane cut out this wide
coplanar_gap_um = 600               # optional: same-layer ground this far from the line
constraints = "…"                   # free text: the constraint IDs it serves
```

A line referenced only from above (a bottom-side line) is solved on the mirrored stack.

## 5. Exploring the layer count (`odapkit stackup explore`)

Before a stack is written, a board whose floorplan is split into zones (each zone with its own layer roles: fast routing, power, ground, RF) enumerates its candidates. The input file holds the layer counts and builds to try, the thickness window, the copper, the fab's dielectric palette (each sheet or core with its pressed thickness and Dk, laser-drillable or not), the impedance classes to solve, the RF ground window and one `[[zone]]` per floorplan zone with its needs:

| Zone key | Need |
|---|---|
| `outer_fast` | `both`, `top`, `bottom` or `soc`: which outer layers carry fast routing (their neighbour must be ground) |
| `outer_power_ok` | outer sides that may carry power pours in this zone |
| `soc`, `soc_side_inner_fast` | the side of a fine-pitch BGA, and the inner fast layers its escape needs: the layer next to the BGA side is ground; with b ≥ 2 laser levels per side (2+N+2, 3+N+3) a fast layer 2 to b levels in, with any-layer any inner fast layer, with 1+N+1 any inner fast layer (a buried via stacked on the microvia); never a through-hole build |
| `inner_fast`, `stripline_gg` | inner fast layers (F, or D where `fast_dual_ok`), striplines between two grounds |
| `power_planes`, `paired_power`, `pour_layers` | power layers, one of them across `rules.pair_max_um` (or the zone's own `pair_max_um`) from a ground, inner pour layers |
| `escape_laser_only` | `true` when the BGA's escape sites only hold a laser land (a fine-pitch ring): the escape layer must be reached by stacked microvias, so 1+N+1 (a buried via under the microvia) does not reach it |
| `outer_power_counts` | `false` when the zone's outer layers hold parts (a BGA's decoupling): an outer pour then counts neither as a power plane nor as the paired plane |
| `rf` | an outer fast layer with a ground `[rf]` min–max µm below it, the layers between cut out |
| `low_speed_next_to_power`, `fast_dual_ok` | relaxations: low-speed signals may sit next to a power layer; two fast layers may share one reference (dual stripline) |

Builds: `through`, `1+N+1`, `2+N+2`, `3+N+3` and `any-layer` (every dielectric laser-drilled: laser prepregs and cores within `rules.laser_max_um`; the thickness a laser-only stack reaches is often the binding limit). The `cost_class` column orders them in that sequence (0 to 4). `rules.symmetric = false` enumerates asymmetric sequences (every position chosen on its own, bounded by `rules.max_sequences`); the fab's DFM review judges their warpage.

**Variants.** `[[variant]]` tables run several sets of constraints in one matrix: each has a `name`, a `set` table of dotted keys (as `--set`; a list of tables is indexed by entry name, `zone.core.power_planes`, `dielectric.c430.um`; new keys allowed) and an `unset` list of keys to remove. The CSV's first column is the variant, ranks are per variant, and the summary groups by variant, layer count and build. `--set` takes any TOML value. The `pair_um` column gives the thinnest power-to-ground dielectric of the chosen map of a zone with `paired_power`; among maps with the same grounds the thinner pairing is kept.

The enumeration is exhaustive within the palette and bounded: per layer count and build, every role vector per zone (depth-first, local rules checked as layers are assigned); every symmetric dielectric sequence (laser prepregs on the microvia levels, then core and prepreg alternating); thickness, plane pairing and RF distance per sequence; the narrowest class (`rules.width_gate_class`) solved on every fast layer a zone could use, cached per local cross-section, against `rules.min_trace_um`; then one map per zone chosen by dynamic programming for the fewest full-layer grounds and zone-local grounds. The CSV has one row per (layer count, build, dielectric sequence): its rank, or the first reason it failed. `--set table.key=value` runs a sensitivity without editing the file. Single-threaded; a ten-layer sweep over a fourteen-entry palette takes minutes, not hours.

## 6. Broadside coupling (`odapkit stackup coupling`)

A `[[coupling]]` case in a stackup file names two routing layers between the same two reference planes, a trace width and a lateral offset. The solver builds the cross-section with both traces and returns each trace's impedance with the other present and the near-end coefficient Kb = (C<sub>m</sub>/C + L<sub>m</sub>/L) / 4 from the Maxwell capacitance and inductance matrices. A dual stripline is acceptable for a class when Kb at the offset the routing can hold is inside the class's crosstalk budget; full overlap gives the worst case of a parallel run.

The same command checks edge coupling on one layer: `layers = ["L4", "L4"]` puts both traces on L4 at `offset_um` centre to centre (trace width plus spacing), between the references above and below.

## 7. Plane pairing and the PDN (`odapkit stackup pdn`)

How far a power plane may sit from its ground, and how deep in the stack, is a power-integrity question: a screening model answers it before a layout exists. The input file lists rails (nominal voltage, allowed ripple, the current step, the regulator as R + L, the decoupling capacitors with ESR, ESL, count, lateral distance to the load and vias per pad, the load's via count) and cases (the plane-to-ground dielectric d and its Dk, the plane area, the via length from the capacitors' side to the plane and from the load to the plane). Per rail and case:

Z(f) = Z<sub>load vias</sub> + 1 / (Y<sub>VRM</sub> + Σ Y<sub>capacitors</sub> + Y<sub>plane</sub> [+ Y<sub>package</sub>])

- a via pair of length h, radius a and pitch p has the loop inductance (μ₀ h / π) arcosh(p / 2a); a group of vias is taken in parallel without mutual inductance (optimistic);
- each capacitor adds ESL, its mounting via pair to the plane and the plane pair's radial inductance from its via to the load, (μ₀ d / 2π) ln(s / a): the only place the pairing distance d enters besides the plane capacitance ε₀ ε<sub>r</sub> A / d;
- the target is ripple / current step, judged up to `sweep.band_max_hz`.

The report gives the peak |Z| in the band, its margin to the target and `f_cross`, the first frequency where |Z| rises over the target: below it the board holds the rail alone, above it the package must. Compare cases by `f_cross` and the peak, never by the plane capacitance alone. It is a screening model: a layout's PDN simulation with the makers' capacitor models replaces it.
