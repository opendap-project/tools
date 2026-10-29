# Simulation regression checks

A board's ngspice studies live in `sim/<study>/` of its repository. Each study that has pass/fail criteria lists them in `sim/<study>/checks.toml`, and `odw sim` runs them all after every design change: one table of results, one JSON report, exit status 1 when a check fails. Every limit names its source, so a failing check points at the constraint, ADR or requirement it protects.

## Running

```sh
odw sim list  [PATH] [--json]   # the checks, without running them (opendap.sim-checks/v1)
odw sim fetch [PATH] [--dry-run] # vendor models into the studies (never committed)
odw sim [run] [PATH] [--only 'output-stage/*'] [--json] [--report FILE] [--fetch] [--allow-missing-models] [--dry-run]
```

`PATH` is a repository (its `sim/*/checks.toml`), a folder of studies, or one study; the default is the current folder. `JOBS` sets how many checks run at once (default 2), `NGSPICE` the ngspice binary. The report goes to `<sim folder>/results/regression.json` (`results/` is git-ignored in board repositories). `--dry-run` names the checks it would run, the report it would write and the models it would fetch, and does none of it.

| Status | Meaning |
|---|---|
| `pass` | The value is within the limits |
| `fail` | The value is outside them |
| `error` | The run gave no value (simulation error, timeout, an unknown `.param`) |
| `blocked` | A vendor model the check needs is missing; fails the run unless `--allow-missing-models` |

## `checks.toml`

```toml
version = 1

[[model]]                         # a vendor file a check needs
id = "tps62136"
manufacturer = "Texas Instruments"
mpn = "TPS62136"                  # its entry in parts-lib manifest.toml
file = "SLVMC09.zip"              # the file parts-sync puts in the parts cache
member = "TPS62136_PSPICE_TRANS/TPS62136_TRANS.lib"   # inside the archive (optional)
dest = "models/TPS62136_TRANS.lib"                    # relative to the study
sha256 = "252dce79…"              # of dest

[[check]]
id = "inverter-peak-current"
description = "…"                 # optional
netlist = "tps65131-dc-load.cir"  # or: command = ["uv", "run", "run.py", "step903"]
params = { vin = 2.9 }            # netlist only: new values for existing .param entries
measure = "inv_pct"               # or: pattern = '^SUP=1 +\S+ +(\S+)'
reduce = "max"                    # first (default) | last | min | max | all
min = 0.0                         # at least one of min, max
max = 80.0
unit = "%"                        # optional
source = "BRD-049"                # required
models = ["tps62136"]             # optional
timeout = 600                     # optional, seconds (default 1800)
```

- **What runs.** A `netlist` is copied with its `params` applied and run as `ngspice -b` in the study folder, so `.spiceinit` and relative includes resolve. A `command` runs any program there (for example a study's own `run.py`). Identical runs (the same rendered netlist, or the same command, in the same study) execute once and their output is shared by every check that reads it, so a command never runs twice at the same time over the same result files.
- **What is measured.** `measure` reads the output lines `name = value` that ngspice prints for `.meas` and `print`. `pattern` is a regular expression whose first group is the value, for tables printed with `echo`. When a quantity appears several times, `reduce` picks one value, or `all` requires every value to be within the limits.
- **Limits** are in the unit ngspice prints (SI unless the netlist scales it); `unit` only labels the report.
- **Vendor models** are declared, never committed: `odw sim fetch` (or `run --fetch`) runs `parts-sync --mpn` for the part, extracts `member` from the cached archive, checks its sha256 and refuses a `dest` that git does not ignore.

## Report

`opendap.sim-report/v1`: `ok`, the ngspice version, the time, counts per status, and for every check its definition, `status`, `value` (and `values` for `reduce = "all"`), the run time in seconds and, when not passing, a `detail`.
