"""odw docs rfc-status: each RFC's affected repositories and whether each recorded the outcome in its docs/."""
import json

import pytest

from odw import docs, rfcstatus

RFC = """\
# RFC {n}: {title}

- **Status:** {status}
- **Date:** 2026-10-22
- **Affected repositories:** {affected}

## Summary

Text.
"""


def rfc(n, affected, status="Accepted (2026-10-22).", title="Thing"):
    return RFC.format(n=n, title=title, status=status, affected=affected)


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "repos.tsv").write_text(
        "path\trepository\n"
        "public/alpha\topendap-project/alpha\n"
        "public/beta\topendap-project/beta\n"
        "public/gamma\topendap-project/gamma\n"
        "public/rfcs\topendap-project/rfcs\n")
    for name in ("alpha", "beta", "rfcs"):  # gamma is listed but not cloned
        (tmp_path / "public" / name / ".git").mkdir(parents=True)
    (tmp_path / "public" / "rfcs" / "text").mkdir()
    return tmp_path


def write_rfc(ws, n, text):
    (ws / "public" / "rfcs" / "text" / f"{n}-thing.md").write_text(text)


def cite(ws, repo, rel, text):
    p = ws / "public" / repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


# ---- parsing the Affected repositories field ------------------------------------------------------


def test_inline_list_drops_notes_in_parentheses_and_the_final_stop():
    entries = rfcstatus.parse_affected(rfc("0001", "alpha (owner of the spec, BOM), beta, gamma (new)."))
    assert [e.name for e in entries] == ["alpha", "beta", "gamma"]


def test_bullet_list_with_semicolons_and_and():
    text = "# RFC 0002: T\n\n- **Status:** Accepted.\n- **Affected repositories:**\n  - alpha (new);\n  - beta and gamma;\n  - rfcs.\n\n## Summary\n"
    assert [e.name for e in rfcstatus.parse_affected(text)] == ["alpha", "beta", "gamma", "rfcs"]


def test_notes_with_links_inside_are_dropped_whole():
    text = rfc("0006", "alpha (the [spec](https://github.com/opendap-project/alpha/blob/main/docs/specs/x.md), `setup.sh`); beta")
    assert [e.name for e in rfcstatus.parse_affected(text)] == ["alpha", "beta"]


def test_lead_in_before_a_colon_is_dropped():
    entries = rfcstatus.parse_affected(rfc("0003", "every repository that names it: alpha, beta"))
    assert [e.name for e in entries] == ["alpha", "beta"]


def test_former_names_are_recorded_as_aliases():
    entries = rfcstatus.parse_affected(rfc("0004", "alpha (formerly old-alpha), beta (from old-beta), gamma (becomes gamma)"))
    assert [(e.name, e.renamed) for e in entries] == [
        ("alpha", ("old-alpha", "alpha")), ("beta", ("old-beta", "beta")), ("gamma", ("gamma", "gamma"))]


def test_no_field_gives_no_entries():
    assert rfcstatus.parse_affected("# RFC 0005: T\n\n- **Status:** Draft.\n\nText.\n") is None


# ---- resolving and recording --------------------------------------------------------------------------


def test_recorded_by_number_in_an_adr(ws):
    write_rfc(ws, "0001", rfc("0001", "alpha, beta"))
    cite(ws, "alpha", "docs/adr/0001-x.md", "# ADR 0001\n\nFollows RFC 0001.\n")
    cite(ws, "beta", "docs/requirements/requirements.toml", 'source = "RFC 0001"\n')
    report = rfcstatus.status(ws)
    rows = {r["repository"]: r["state"] for r in report["rfcs"][0]["rows"]}
    assert rows == {"alpha": "recorded", "beta": "recorded"}
    assert report["rfcs"][0]["complete"] is True


def test_recorded_by_github_url(ws):
    write_rfc(ws, "0001", rfc("0001", "alpha"))
    cite(ws, "alpha", "docs/specs/x.md", "See [the RFC](https://github.com/opendap-project/rfcs/blob/main/text/0001-thing.md).\n")
    assert rfcstatus.status(ws)["rfcs"][0]["rows"][0]["state"] == "recorded"


def test_another_number_or_a_file_outside_requirements_adr_specs_does_not_count(ws):
    write_rfc(ws, "0001", rfc("0001", "alpha"))
    cite(ws, "alpha", "docs/specs/x.md", "Follows RFC 0010 and RFC 00011.\n")
    cite(ws, "alpha", "README.md", "Follows RFC 0001.\n")
    cite(ws, "alpha", "docs/README.md", "Index: RFC 0001.\n")  # an index is not a requirement, ADR or spec
    row = rfcstatus.status(ws)["rfcs"][0]["rows"][0]
    assert row["state"] == "missing" and row["citations"] == []


def test_a_repository_without_docs_records_in_its_readme_or_contributing(ws):
    write_rfc(ws, "0001", rfc("0001", "alpha, beta"))
    cite(ws, "alpha", "README.md", "Follows RFC 0001.\n")
    cite(ws, "beta", "CONTRIBUTING.md", "See https://github.com/opendap-project/rfcs/blob/main/text/0001-thing.md\n")
    rows = {r["repository"]: (r["state"], r["citations"]) for r in rfcstatus.status(ws)["rfcs"][0]["rows"]}
    assert rows == {"alpha": ("recorded", ["README.md"]), "beta": ("recorded", ["CONTRIBUTING.md"])}


def test_a_repository_without_docs_and_no_citation_is_missing(ws):
    write_rfc(ws, "0001", rfc("0001", "alpha"))
    cite(ws, "alpha", "README.md", "Follows RFC 0010.\n")
    cite(ws, "alpha", "notes.md", "RFC 0001\n")  # only README.md and CONTRIBUTING.md count
    row = rfcstatus.status(ws)["rfcs"][0]["rows"][0]
    assert row["state"] == "missing" and row["citations"] == []


def test_a_generated_index_in_the_readme_is_not_a_record(ws):
    write_rfc(ws, "0001", rfc("0001", "rfcs"))
    cite(ws, "rfcs", "README.md", "# RFCs\n\n<!-- index:start -->\n| [RFC 0001: Thing](text/0001-thing.md) |\n<!-- index:end -->\n")
    assert rfcstatus.status(ws)["rfcs"][0]["rows"][0]["state"] == "missing"
    cite(ws, "rfcs", "README.md", "# RFCs\n\nThe process follows RFC 0001.\n\n<!-- index:start -->\n<!-- index:end -->\n")
    assert rfcstatus.status(ws)["rfcs"][0]["rows"][0]["citations"] == ["README.md"]


def test_the_workspace_root_without_docs_records_in_its_contributing(ws):
    write_rfc(ws, "0001", rfc("0001", "workspace"))
    cite(ws, "..", "CONTRIBUTING.md", "Cross-repository changes follow RFC 0001.\n")
    row = rfcstatus.status(ws)["rfcs"][0]["rows"][0]
    assert (row["state"], row["citations"]) == ("recorded", ["CONTRIBUTING.md"])


def test_citation_paths_are_reported(ws):
    write_rfc(ws, "0001", rfc("0001", "alpha"))
    cite(ws, "alpha", "docs/adr/0002-y.md", "RFC-0001\n")
    assert rfcstatus.status(ws)["rfcs"][0]["rows"][0]["citations"] == ["docs/adr/0002-y.md"]


def test_listed_but_not_cloned_is_absent_and_unknown_names_are_unresolved(ws):
    write_rfc(ws, "0001", rfc("0001", "gamma, the widgets"))
    rows = {r["repository"]: r["state"] for r in rfcstatus.status(ws)["rfcs"][0]["rows"]}
    assert rows == {"gamma": "absent", "the widgets": "unresolved"}


def test_workspace_is_the_root_folder(ws):
    write_rfc(ws, "0001", rfc("0001", "workspace (`repos.tsv`)"))
    cite(ws, "..", "docs/specs/w.md", "RFC 0001\n")  # public/../docs = the workspace root's docs/
    row = rfcstatus.status(ws)["rfcs"][0]["rows"][0]
    assert (row["repository"], row["path"], row["state"]) == ("workspace", ".", "recorded")


def test_a_former_name_declared_in_any_rfc_resolves(ws):
    write_rfc(ws, "0001", rfc("0001", "old-alpha"))
    write_rfc(ws, "0002", rfc("0002", "alpha (from old-alpha)"))
    row = rfcstatus.status(ws)["rfcs"][0]["rows"][0]
    assert (row["repository"], row["resolved_as"], row["path"]) == ("old-alpha", "alpha", "public/alpha")


def test_rfc_without_the_field_is_reported(ws):
    write_rfc(ws, "0001", "# RFC 0001: T\n\n- **Status:** Accepted.\n\nText.\n")
    r = rfcstatus.status(ws)["rfcs"][0]
    assert r["rows"] == [] and r["affected_declared"] is False and r["complete"] is False


# ---- outcome and CLI ------------------------------------------------------------------------------------


def test_failures_are_accepted_or_implemented_rfcs_with_unrecorded_rows(ws):
    write_rfc(ws, "0001", rfc("0001", "alpha"))
    write_rfc(ws, "0002", rfc("0002", "alpha", status="Draft."))
    write_rfc(ws, "0003", rfc("0003", "beta", status="Implemented (2026-10-25)."))
    cite(ws, "beta", "docs/adr/0001.md", "RFC 0003\n")
    report = rfcstatus.status(ws)
    assert report["failing"] == ["0001"]
    assert report["ready_to_implement"] == []


def test_accepted_and_complete_is_ready_to_implement(ws):
    write_rfc(ws, "0001", rfc("0001", "alpha"))
    cite(ws, "alpha", "docs/adr/0001.md", "RFC 0001\n")
    report = rfcstatus.status(ws)
    assert report["failing"] == [] and report["ready_to_implement"] == ["0001"]


def test_cli_json_and_exit_status(ws, capsys):
    write_rfc(ws, "0001", rfc("0001", "alpha"))
    assert docs.main(["rfc-status", "--workspace", str(ws), "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["rfcs"][0]["number"] == "0001" and out["failing"] == ["0001"]
    cite(ws, "alpha", "docs/adr/0001.md", "RFC 0001\n")
    assert docs.main(["rfc-status", "--workspace", str(ws)]) == 0
    assert "0001" in capsys.readouterr().out


def test_cli_usage_errors(tmp_path):
    assert docs.main(["rfc-status", "--bogus"]) == 2
    assert docs.main(["rfc-status", "--workspace", str(tmp_path)]) == 2  # no repos.tsv, no rfcs


def test_write_produces_a_generated_matrix(ws, tmp_path):
    write_rfc(ws, "0001", rfc("0001", "alpha, gamma"))
    out = tmp_path / "out"
    assert docs.main(["rfc-status", "--workspace", str(ws), "--write", str(out)]) == 1
    text = (out / "rfc-application.toml").read_text()
    assert text.startswith("# @generated")
    import tomllib
    data = tomllib.loads(text)
    assert data["rfc"][0]["number"] == "0001"
    assert [r["state"] for r in data["rfc"][0]["row"]] == ["missing", "absent"]
