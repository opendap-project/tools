"""odw evaluate: the evaluation-matrix engine behind every study and selection (opendap.matrix/v1).

A study is options × criteria. Each criterion is `hard` (gates the verdict), `soft` (weighs the rank) or `info`
(recorded only) and yields, per option, a state (pass, warning, fail, unverified, n/a, info) with a mandatory
reason: either through its own `evaluate(option) -> (state, why[, value[, score]])` (a soft criterion's
score in [0, 1] replaces its state's weight in the rank) or from a `value(option)` graded
against `thresholds` {ideal, warning, hard, direction}. Verdicts follow one aggregation rule; options that pass are
ranked by weighted score; the recommendation is always labelled as one and never stands for a decision.
The study's options, criteria and models belong to whoever owns the question; this engine is generic.
Spec: docs/specs/evaluation-matrix.md.
"""
import copy

STATE_SCORE = {"pass": 1.0, "warning": 0.5, "fail": 0.0, "unverified": 0.0, "n/a": 0.0, "info": 0.0}
AGGREGATIONS = ("all-hard-pass", "worst-state", "first-fail", "rule")
RECOMMENDATION_RULE = {
    "all-hard-pass": "best rank among options passing every hard criterion",
    "first-fail": "best rank among options passing every hard criterion",
    "worst-state": "best rank among options with every scored criterion passing",
    "rule": "best rank among options the study's rule passes",
}
SCHEMA_CRITERION_KEYS = ("id", "title", "kind", "weight", "requirements", "thresholds", "known_required")


def grade(value, thresholds):
    """pass up to `ideal`, warning up to `hard`, fail beyond (mirrored when direction is `min`); None is unverified."""
    if value is None:
        return "unverified"
    sign = -1 if thresholds.get("direction", "max") == "min" else 1
    v, ideal, hard = sign * value, sign * thresholds["ideal"], sign * thresholds["hard"]
    if v <= ideal:
        return "pass"
    return "warning" if v <= hard else "fail"


class Study:
    def __init__(self, id, question, options, criteria, aggregation="all-hard-pass", rule=None, sources=None,
                 requirements=None, status="study", recommend=True, decision=None):
        self.id, self.question, self.options, self.criteria = id, question, options, criteria
        self.aggregation, self.rule, self.sources, self.requirements, self.status = aggregation, rule, sources, requirements, status
        # A survey (recommend=False) or a cited decision gives verdicts and ranks and never a recommendation.
        self.recommend, self.decision = recommend, decision

    def _cell(self, option, c):
        unit = c.get("unit")
        score = None
        if "evaluate" in c:
            got = c["evaluate"](option)
            state, why = got[0], got[1]
            value = got[2] if len(got) > 2 else None
            if len(got) > 3:
                if not 0 <= got[3] <= 1:
                    raise ValueError(f"criterion {c['id']}: score {got[3]} for {option['id']} is outside [0, 1]")
                score = got[3]
        elif "value" in c:
            value = c["value"](option)
            if c["kind"] == "info" or "thresholds" not in c:
                state, why = "info", f"{value}{' ' + unit if unit else ''}"
            else:
                state = grade(value, c["thresholds"])
                t = c["thresholds"]
                why = (f"{value}{' ' + unit if unit else ''} against ideal {t['ideal']}, hard {t['hard']}"
                       if value is not None else "no value: unverified")
        else:
            raise ValueError(f"criterion {c['id']} needs evaluate or value")
        cell = {"option": option["id"], "criterion": c["id"], "state": state, "why": why}
        if value is not None:
            cell["value"] = value
        if unit:
            cell["unit"] = unit
        if score is not None:
            self._scores[(option["id"], c["id"])] = score
        return cell

    def _verdict(self, option, cells):
        by = {c["id"]: cells[(option["id"], c["id"])] for c in self.criteria}
        hard = [c for c in self.criteria if c["kind"] == "hard"]
        scored = [c for c in self.criteria if c["kind"] != "info"]
        if self.aggregation in ("all-hard-pass", "first-fail"):
            fails = [c["id"] for c in hard if by[c["id"]]["state"] == "fail"]
            unver = [c["id"] for c in hard if by[c["id"]]["state"] == "unverified"]
            unknown = [c["id"] for c in self.criteria if c["kind"] == "soft" and c.get("known_required") and by[c["id"]]["state"] == "unverified"]
            verdict = "fail" if fails else "incomplete" if unver or unknown else "pass"
            blockers = fails[:1] if self.aggregation == "first-fail" else fails
        elif self.aggregation == "worst-state":
            states = [by[c["id"]]["state"] for c in scored]
            verdict = "fail" if "fail" in states else "incomplete" if {"warning", "unverified"} & set(states) else "pass"
            blockers = [c["id"] for c in scored if by[c["id"]]["state"] == "fail"]
        elif self.aggregation == "rule":
            verdict, label = self.rule(option, by)
            out = {"option": option["id"], "verdict": verdict, "label": label}
            out["score"] = self._score(option, by)
            return out
        else:
            raise ValueError(f"unknown aggregation {self.aggregation}")
        out = {"option": option["id"], "verdict": verdict,
               "n_unverified": sum(by[c["id"]]["state"] == "unverified" for c in self.criteria)}
        if blockers:
            out["blockers"] = blockers
        out["score"] = self._score(option, by)
        return out

    def _score(self, option, by):
        return round(sum(c.get("weight", 1) * self._scores.get((option["id"], c["id"]), STATE_SCORE[by[c["id"]]["state"]])
                         for c in self.criteria if c["kind"] == "soft"), 6)

    def run(self):
        if self.aggregation not in AGGREGATIONS:
            raise ValueError(f"unknown aggregation {self.aggregation}")
        if (self.status == "decision-cited") != (self.decision is not None):
            raise ValueError("a decision is cited exactly when the status is decision-cited")
        self._scores = {}
        cells = {(o["id"], c["id"]): self._cell(o, c) for o in self.options for c in self.criteria}
        verdicts = [self._verdict(o, cells) for o in self.options]
        passing = [v for v in verdicts if v["verdict"] == "pass"]
        order = {o["id"]: i for i, o in enumerate(self.options)}
        # A matrix that recommends nothing ranks only on a soft criterion: option order alone is no ranking.
        recommends = self.recommend and self.decision is None
        if recommends or any(c["kind"] == "soft" for c in self.criteria):
            for rank, v in enumerate(sorted(passing, key=lambda v: (-v.get("score", 0), order[v["option"]])), 1):
                v["rank"] = rank
        m = {"schema": "opendap.matrix/v1", "id": self.id, "question": self.question, "status": self.status,
             "options": copy.deepcopy(self.options),
             "criteria": [{k: c[k] for k in SCHEMA_CRITERION_KEYS if k in c} for c in self.criteria],
             "results": [cells[(o["id"], c["id"])] for o in self.options for c in self.criteria],
             "aggregation": self.aggregation, "verdicts": verdicts}
        if self.requirements:
            m["requirements"] = self.requirements
        if self.sources:
            m["sources"] = self.sources
        if self.decision is not None:
            m["decision"] = dict(self.decision)
        elif not self.recommend:
            m["recommend"] = False
        else:
            top = [v for v in verdicts if v.get("rank") == 1]
            if top:
                m["recommendation"] = {"option": top[0]["option"], "rule": RECOMMENDATION_RULE[self.aggregation],
                                       "labelled": True}
        return m

    def sensitivity(self, criterion, field, values):
        """Re-run with criterion[field] set to each value; whether the top-ranked option changes."""
        base = self.run().get("recommendation", {}).get("option")
        tops = []
        for v in values:
            s = copy.copy(self)
            s.criteria = [dict(c, **{field: v}) if c["id"] == criterion else c for c in self.criteria]
            tops.append(s.run().get("recommendation", {}).get("option"))
        return {"parameter": f"{criterion}.{field}", "values": list(values), "top_changes": any(t != base for t in tops)}


def exit_status(matrix):
    """0 when a recommendation exists or some option passes, 1 when nothing passes."""
    return 0 if matrix.get("recommendation") or any(v["verdict"] == "pass" for v in matrix["verdicts"]) else 1


SYMBOL = {"pass": "pass", "warning": "warning", "fail": "**fail**", "unverified": "unverified", "n/a": "n/a", "info": "info"}


def render_markdown(m):
    crit = [c["id"] for c in m["criteria"]]
    cell = {(r["option"], r["criterion"]): r for r in m["results"]}
    verdict = {v["option"]: v for v in m["verdicts"]}
    lines = [f"<!-- @generated by odw evaluate from study {m['id']}. Do not edit. -->", "", f"# {m['question']}", "",
             "| Option | " + " | ".join(crit) + " | Verdict | Rank |", "|---|" + "---|" * (len(crit) + 2)]
    for o in m["options"]:
        v = verdict[o["id"]]
        row = [SYMBOL[cell[(o["id"], c)]["state"]] for c in crit]
        lines.append(f"| {o['name']} | " + " | ".join(row) + f" | {v['verdict']} | {v.get('rank', '')} |")
    lines += ["", "## Reasons", ""]
    lines += [f"- {cell[(o['id'], c)]['option']} · {c}: {cell[(o['id'], c)]['why']}" for o in m["options"] for c in crit]
    if m.get("recommendation"):
        r = m["recommendation"]
        lines += ["", f"**Recommendation (labelled, not a decision):** {r['option']} — {r['rule']}."]
    if m.get("decision"):
        d = m["decision"]
        lines += ["", f"**Decision ({d['by']}, {d['date']}): {d['summary']}.** The matrix records its consequences; it recommends nothing."]
    elif m.get("recommend") is False:
        lines += ["", "A survey: verdicts and ranks, no recommendation."]
    return "\n".join(lines) + "\n"
