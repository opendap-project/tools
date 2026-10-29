# Evaluation matrix

- **Status:** draft
- **Engine:** `odw.evaluate` (`core/odw/evaluate.py`); output `opendap.matrix/v1` ([workspace schemas](../../../schemas/README.md))

Every study, selection and trade-off is an evaluation matrix: options × criteria, a state and a reason in every cell, a verdict per option, a rank and a labelled recommendation. One engine computes them, so studies differ only in their options, criteria and models.

## Criteria

| Kind | Role |
|---|---|
| `hard` | Gates the verdict |
| `soft` | Weighs the rank (`weight`, default 1) |
| `info` | Recorded only |

A criterion yields a state per option through its own `evaluate(option) → (state, why[, value[, score]])`, or from `value(option)` graded against `thresholds`: pass up to `ideal`, warning up to `hard`, fail beyond (mirrored when `direction` is `min`); a missing value is `unverified`. States: `pass`, `warning`, `fail`, `unverified`, `n/a`, `info`. Every cell carries its reason.

## Verdicts, rank, recommendation

| Aggregation | Verdict |
|---|---|
| `all-hard-pass` | fail when a hard criterion fails; incomplete when one is unverified, or a soft criterion marked `known_required` is; pass otherwise |
| `first-fail` | as above, with only the first failing criterion as blocker |
| `worst-state` | over hard and soft criteria: any fail fails, any warning or unverified is incomplete |
| `rule` | the matrix's own rule returns a verdict and a label; the score is computed as for the others |

Passing options are ranked by weighted score over the soft criteria: a cell's own `score` in [0, 1] when its criterion gives one (a value, a judged mark, an argmax), otherwise its state's weight (pass 1, warning 0.5, otherwise 0). Equal scores keep the order of the options, so a tie-break is a second soft criterion with a small weight. The recommendation is the first-ranked passing option, always labelled as a recommendation: it never stands for a maintainers' decision. Two kinds of matrix recommend nothing. A survey (`recommend=False`, recorded as `"recommend": false`) gives verdicts and ranks only: a worst-case sweep, or a list of consequences. A matrix whose status is `decision-cited` carries the maintainers' `decision` (`summary`, `by`, `date`, optional `source`) in place of a recommendation, and records what the decision implies; the engine refuses one without the other. Such a matrix ranks its passing options only when it has a soft criterion; with hard criteria alone, option order would read as a preference, so it gives no rank. The recommendation's `rule` text names the aggregation that produced it. `sensitivity(criterion, field, values)` re-runs the matrix and reports whether the top option changes. A run exits 0 when something passes and 1 when nothing does.

## Output

The `opendap.matrix/v1` document, and a Markdown rendering with the `@generated` header: the matrix table, one reason per cell, and the labelled recommendation.
