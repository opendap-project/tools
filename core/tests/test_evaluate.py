"""odw evaluate: the evaluation-matrix engine (opendap.matrix/v1)."""
import pytest

from odw import evaluate, schemas

OPTIONS = [{"id": "a", "name": "A", "attributes": {"height_mm": 12, "cost": 3}},
           {"id": "b", "name": "B", "attributes": {"height_mm": 16, "cost": 1}},
           {"id": "c", "name": "C", "attributes": {"height_mm": 13.5, "cost": 2}}]


def fits(o):
    h = o["attributes"]["height_mm"]
    return ("pass", f"{h} mm under 14 mm") if h <= 14 else ("fail", f"{h} mm over 14 mm")


CRITERIA = [
    {"id": "G1", "title": "Fits the envelope", "kind": "hard", "evaluate": fits},
    {"id": "S1", "title": "Height margin", "kind": "soft", "weight": 2,
     "thresholds": {"ideal": 12, "warning": 13, "hard": 14, "direction": "max"}, "value": lambda o: o["attributes"]["height_mm"], "unit": "mm"},
    {"id": "I1", "title": "Cost class", "kind": "info", "value": lambda o: o["attributes"]["cost"]},
]


def study(**kw):
    return evaluate.Study(id="t", question="Which part?", options=OPTIONS, criteria=CRITERIA, **kw)


def test_grade_thresholds():
    t = {"ideal": 12, "warning": 13, "hard": 14, "direction": "max"}
    assert evaluate.grade(11, t) == "pass"
    assert evaluate.grade(12.5, t) == "warning"
    assert evaluate.grade(13.5, t) == "warning"
    assert evaluate.grade(15, t) == "fail"
    assert evaluate.grade(None, t) == "unverified"


def test_all_hard_pass_verdicts_and_blockers():
    m = study(aggregation="all-hard-pass").run()
    v = {x["option"]: x for x in m["verdicts"]}
    assert v["a"]["verdict"] == "pass" and v["c"]["verdict"] == "pass"
    assert v["b"]["verdict"] == "fail" and v["b"]["blockers"] == ["G1"]


def test_worst_state_counts_warnings_as_incomplete():
    m = study(aggregation="worst-state").run()
    v = {x["option"]: x["verdict"] for x in m["verdicts"]}
    assert v == {"a": "pass", "b": "fail", "c": "incomplete"}


def test_rank_by_weighted_score_among_passing():
    m = study(aggregation="all-hard-pass").run()
    ranked = sorted((x for x in m["verdicts"] if "rank" in x), key=lambda x: x["rank"])
    assert [x["option"] for x in ranked] == ["a", "c"]


def test_recommendation_is_labelled_and_never_absent_when_something_passes():
    m = study(aggregation="all-hard-pass").run()
    assert m["recommendation"] == {"option": "a", "rule": "best rank among options passing every hard criterion", "labelled": True}


def test_every_result_has_a_reason():
    m = study().run()
    assert all(r["why"] for r in m["results"])
    assert {(r["option"], r["criterion"]) for r in m["results"]} == {(o["id"], c["id"]) for o in OPTIONS for c in CRITERIA}


def test_output_validates_against_the_schema():
    assert schemas.errors(study().run(), "matrix") == []


def test_sensitivity_reports_whether_the_top_changes():
    s = study(aggregation="all-hard-pass")
    sens = s.sensitivity("S1", "weight", [0, 1, 10])
    assert sens == {"parameter": "S1.weight", "values": [0, 1, 10], "top_changes": False}


def test_no_passing_option_gives_no_recommendation_and_exit_1():
    tight = [{**OPTIONS[1]}]
    m = evaluate.Study(id="t", question="q", options=tight, criteria=CRITERIA[:1]).run()
    assert "recommendation" not in m
    assert evaluate.exit_status(m) == 1


def test_markdown_render_has_header_and_a_cell_per_pair():
    md = evaluate.render_markdown(study().run())
    assert md.startswith("<!-- @generated")
    assert "| A |" in md and "G1" in md


def test_unknown_aggregation_is_an_error():
    with pytest.raises(ValueError):
        study(aggregation="vibes").run()


def test_soft_criterion_may_score_numerically():
    crit = [{"id": "G1", "title": "Fits", "kind": "hard", "evaluate": fits},
            {"id": "V", "title": "Value", "kind": "soft", "weight": 1,
             "evaluate": lambda o: ("pass", f"value {o['attributes']['cost']}", o["attributes"]["cost"], o["attributes"]["cost"] / 3)}]
    m = evaluate.Study(id="t", question="q", options=OPTIONS, criteria=crit).run()
    ranked = sorted((v for v in m["verdicts"] if "rank" in v), key=lambda v: v["rank"])
    assert [v["option"] for v in ranked] == ["a", "c"]
    assert schemas.errors(m, "matrix") == []


def test_numeric_score_outside_unit_interval_is_an_error():
    crit = [{"id": "V", "title": "Value", "kind": "soft", "evaluate": lambda o: ("pass", "x", 1, 2.0)}]
    with pytest.raises(ValueError):
        evaluate.Study(id="t", question="q", options=OPTIONS, criteria=crit).run()


def test_rule_aggregation_also_ranks_by_score():
    rule = lambda o, by: ("pass", "use now") if by["G1"]["state"] == "pass" else ("fail", "reserve")
    m = study(aggregation="rule", rule=rule).run()
    ranked = sorted((v for v in m["verdicts"] if "rank" in v), key=lambda v: v["rank"])
    assert [v["option"] for v in ranked] == ["a", "c"]
    assert all("score" in v for v in m["verdicts"]) and ranked[0]["label"] == "use now"


def test_soft_criterion_that_must_be_known_makes_the_verdict_incomplete():
    crit = [CRITERIA[0], {"id": "P", "title": "Low power", "kind": "soft", "known_required": True,
                          "evaluate": lambda o: ("unverified", "no datasheet figure") if o["id"] == "a" else ("pass", "1 mA")}]
    m = evaluate.Study(id="t", question="q", options=OPTIONS, criteria=crit).run()
    v = {x["option"]: x["verdict"] for x in m["verdicts"]}
    assert v == {"a": "incomplete", "b": "fail", "c": "pass"}
    assert schemas.errors(m, "matrix") == []


def test_a_survey_gives_verdicts_and_ranks_but_no_recommendation():
    m = study(recommend=False).run()
    assert "recommendation" not in m and m["recommend"] is False
    assert [v["verdict"] for v in m["verdicts"]] == ["pass", "fail", "pass"]
    assert schemas.errors(m, "matrix") == []
    assert "Recommendation" not in evaluate.render_markdown(m)
    assert evaluate.exit_status(m) == 0


def test_a_cited_decision_replaces_the_recommendation():
    d = {"summary": "USB only", "by": "maintainers", "date": "2026-10-25"}
    m = study(status="decision-cited", decision=d).run()
    assert "recommendation" not in m and m["decision"] == d
    assert schemas.errors(m, "matrix") == []
    assert "Decision (maintainers, 2026-10-25): USB only" in evaluate.render_markdown(m)
    with pytest.raises(ValueError):
        study(status="decision-cited").run()


def test_the_recommendation_rule_names_the_aggregation():
    rule = lambda o, by: ("pass", "ok") if by["G1"]["state"] == "pass" else ("fail", "no")
    assert "rule" in study(aggregation="rule", rule=rule).run()["recommendation"]["rule"]
    assert "hard criterion" in study().run()["recommendation"]["rule"]


def test_a_matrix_that_recommends_nothing_ranks_only_by_soft_criteria():
    hard_only = [CRITERIA[0]]
    m = evaluate.Study(id="t", question="q", options=OPTIONS, criteria=hard_only, recommend=False).run()
    assert all("rank" not in v for v in m["verdicts"])
    d = {"summary": "s", "by": "maintainers", "date": "2026-10-25"}
    m = evaluate.Study(id="t", question="q", options=OPTIONS, criteria=hard_only, status="decision-cited", decision=d).run()
    assert all("rank" not in v for v in m["verdicts"])
    assert any("rank" in v for v in study(recommend=False).run()["verdicts"])
    assert schemas.errors(m, "matrix") == []
