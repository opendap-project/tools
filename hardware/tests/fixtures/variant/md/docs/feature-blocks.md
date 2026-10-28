# Fixture block catalog

A Markdown catalog laid out as RFC 0011 asks: a summary table with an ID column, then one record per block.

## Catalog overview

| # | ID | Function | Bound by (priority) | full | prod |
|---|---|---|---|---|---|
| 1 | `blk.core` | Core | FIX-001, FIX-004 (must) | fit | fit |
| 2 | `blk.sensor` | Sensor | FIX-002 (should), FIX-005 | fit | open (DNP option) |
| 3 | `blk.mem` | Memory | FIX-003 (must: 32 GB on prototypes) | fit 32 GB | fit |
| 4 | `blk.mem-supply` | Memory supply | as `blk.mem` | fit | fit |
| 5 | `blk.radio` | Removed (fixture) | — | — | — |

## Block records

### 1. `blk.core`: core

| Field | Content |
|---|---|
| Alternates | None same-land |
| Absent state | Not applicable: never omitted |
| Software | Board devicetree (H1) |
| Sub-blocks | `blk.core-power` and `blk.core-io` (sheets of this block) |

### 2. `blk.sensor`: sensor

| Field | Content |
|---|---|
| Alternates | `sensor-b` (second source); mute shunt as `fit` option |
| Absent state | `SENSOR_EN` pulled down on `blk.core`; 0x48 free |
| Software | `acme,tiny-sensor` (`acme-sensor`), `sensor0` node; `org.opendap.hw.Fixture`, `.Probe`; description `sensors` |

### 3–4. `blk.mem`, `blk.mem-supply`: memory and its supply

| Field | Content |
|---|---|
| Alternates | `mem-16` (16 GB). Parameter: `capacity_gb` |
| Software | `/memory@0` node; description `identity` and `power` sections |
| Sub-blocks | `blk.mem-supply-ldo`, `blk.mem-bank` |

### 5. `blk.radio`: removed

Removed from the catalog.
