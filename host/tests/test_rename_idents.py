"""odw rename identifiers by kind and context (HL-109): the cases the Blackbird identifier cut (HL-86 to HL-91) fixed by
hand, each as a fixture, so the next root's identifier rows need no hand edits."""
import json

from test_rename import MAP, args, git, run, ws  # noqa: F401 (the fixture)

from odw import rename_idents as ri, rename_map

ROWS = [
    ("crate", "odap-composer", "blackbird-composer"),
    ("crate", "opendap-input", "blackbird-ui-input"),
    ("crate", "odap-boot", "blackbird-apps-boot"),
    ("binary", "odap-boot", "/usr/lib/blackbird/boot"),
    ("binary", "odap-vdev-view", "blackbird-emulator-viewer"),
    ("path", "/run/opendap", "/run/blackbird"),
    ("path", "/run/opendap/composer", "/run/blackbird/composer"),
    ("path", "/run/opendap/debug-ui", "/run/blackbird/debug-ui"),
    ("identifier:user", "odap", "blackbird"),
]


def rw(path, text):
    return ri.Rewriter(ROWS).rewrite(path, text)[0]


def test_cargo_keys_of_a_renamed_crate_are_rewritten():  # case 1
    text = ('[workspace.dependencies]\nodap-composer = { path = "crates/odap-composer" }\n'
            '[dependencies]\nodap-composer.workspace = true\nopendap-input = { workspace = true }\n')
    assert rw("Cargo.toml", text) == (
        '[workspace.dependencies]\nblackbird-composer = { path = "crates/odap-composer" }\n'
        '[dependencies]\nblackbird-composer.workspace = true\nblackbird-ui-input = { workspace = true }\n')


def test_rust_paths_of_a_renamed_crate_are_rewritten():  # case 2
    text = "use opendap_input::Event;\nfn f() { odap_composer::run(); }\nlet opendap_input_x = 1;\n"
    assert rw("src/main.rs", text) == (
        "use blackbird_ui_input::Event;\nfn f() { blackbird_composer::run(); }\nlet opendap_input_x = 1;\n")
    assert rw("README.md", "opendap_input::Event\n") == "opendap_input::Event\n"  # Rust paths in Rust only


def test_a_binary_at_the_end_of_a_path_is_rewritten():  # case 3
    assert rw("run.py", 'VIEW = "viewer/target/release/odap-vdev-view"\n') == \
        'VIEW = "viewer/target/release/blackbird-emulator-viewer"\n'


def test_a_runtime_directory_is_rewritten_without_its_prefix():  # case 5
    text = "[Service]\nRuntimeDirectory=opendap/composer opendap\nExecStart=/usr/bin/x\n"
    assert rw("x.service", text) == "[Service]\nRuntimeDirectory=blackbird/composer blackbird\nExecStart=/usr/bin/x\n"
    assert rw("notes.md", "the opendap/composer folder\n") == "the opendap/composer folder\n"


def test_a_token_that_is_a_crate_and_a_binary_follows_its_context():  # case 8
    assert rw("README.md", "Run odap-boot handoff, then odap-boot cover.\n") == \
        "Run /usr/lib/blackbird/boot handoff, then /usr/lib/blackbird/boot cover.\n"
    assert rw("check.toml", 'run = ["cargo", "clippy", "-p", "odap-boot"]\n') == \
        'run = ["cargo", "clippy", "-p", "blackbird-apps-boot"]\n'
    assert rw("x.sh", "cargo test --package odap-boot\n") == "cargo test --package blackbird-apps-boot\n"
    assert rw("crates/boot/Cargo.toml", '[package]\nname = "odap-boot"\n') == '[package]\nname = "blackbird-apps-boot"\n'
    assert rw("Cargo.lock", 'name = "odap-boot"\n') == 'name = "blackbird-apps-boot"\n'


def test_a_user_is_rewritten_where_it_names_the_user_only():  # case 11
    unit = "[Service]\nUser=odap\nGroup=odap\nSupplementaryGroups=audio odap\n[Socket]\nSocketGroup=odap\n"
    assert rw("x.service", unit) == ("[Service]\nUser=blackbird\nGroup=blackbird\nSupplementaryGroups=audio blackbird\n"
                                     "[Socket]\nSocketGroup=blackbird\n")
    assert rw("sysusers.d/x.conf", 'u odap - "OpenDAP session" /home/odap\nm odap audio\n') == \
        'u blackbird - "OpenDAP session" /home/blackbird\nm blackbird audio\n'
    assert rw("tmpfiles.d/x.conf", "d /run/x 0755 odap odap -\n") == "d /run/x 0755 blackbird blackbird -\n"
    assert rw("x.sh", "cp a ~odap/b && chown odap:odap c\n") == "cp a ~blackbird/b && chown blackbird:blackbird c\n"
    assert rw("README.md", "odap is the old prefix; see odap-x.\n") == "odap is the old prefix; see odap-x.\n"


def test_every_rewrite_is_listed_with_its_line():
    _, edits = ri.Rewriter(ROWS).rewrite("x.service", "a\nUser=odap\n")
    assert [(e.line, e.old, e.new) for e in edits] == [(2, "odap", "blackbird")]


def test_the_plan_applies_each_row_by_its_kind(ws, capsys):
    """Wiring: a crate row reaches the plan as a crate, so a Cargo key before `.workspace` is rewritten."""
    (ws / "public/libs/kicad/Cargo.toml").write_text("[dependencies]\nodap-composer.workspace = true\n")
    git(ws / "public/libs/kicad", "add", "Cargo.toml")
    git(ws / "public/libs/kicad", "commit", "-qm", "cargo")
    lines = ["old\tnew\tkind\taction\tnotes\tscope\troot"]
    lines += ["\t".join([*r, "test", "libs" if r[0] == "crate:odap-composer" else ""]) for r in MAP]
    (ws.parent / "map.tsv").write_text("\n".join(lines) + "\n")  # the crate row names its owner
    m = rename_map.load(ws, ws.parent / "map.tsv")
    rows, _, _ = ri.collect(m, {"libs"})
    assert ("crate", "odap-composer", "blackbird-composer") in rows
    code, out, _ = run(capsys, "plan", "libs", *args(ws), "--json")
    edits = {(e["path"], e["old"], e["new"]) for e in json.loads(out)["edits"] if e["kind"] == "identifier"}
    assert ("kicad/Cargo.toml", "odap-composer", "blackbird-composer") in edits


def test_a_user_row_is_not_a_plain_word(ws):
    """Its contexts make it safe (case 11): it applies once it has an owner, like a maker key."""
    lines = ["old\tnew\tkind\taction\tnotes\tscope\troot", "identifier:user:odap\tidentifier:user:blackbird\t"
             "identifier\trename\tsession user\ttest\tlibs"]
    (ws.parent / "map.tsv").write_text("\n".join(lines) + "\n")
    m = rename_map.load(ws, ws.parent / "map.tsv")
    i = rename_map.identifier(m, rename_map.identifier_rows(m)[0])
    assert (i.reason, i.owner) == (None, "libs")
    lines[1] = lines[1].rsplit("\t", 1)[0] + "\t"  # no owner: still needs one
    (ws.parent / "map.tsv").write_text("\n".join(lines) + "\n")
    m = rename_map.load(ws, ws.parent / "map.tsv")
    assert rename_map.identifier(m, rename_map.identifier_rows(m)[0]).reason != "plain-word"


def _plan_edits(ws, capsys, files):
    """The identifier edits of a libs plan after adding `files` to kicad-lib, with the crate row owned by libs."""
    lines = ["old\tnew\tkind\taction\tnotes\tscope\troot"]
    lines += ["\t".join([*r, "test", "libs" if r[0] == "crate:odap-composer" else ""]) for r in MAP]
    (ws.parent / "map.tsv").write_text("\n".join(lines) + "\n")
    top = ws / "public/libs/kicad"
    for name, text in files.items():
        (top / name).parent.mkdir(parents=True, exist_ok=True)
        (top / name).write_text(text)
    git(top, "add", "-A")
    git(top, "commit", "-qm", "files")
    code, out, _ = run(capsys, "plan", "libs", *args(ws), "--json")
    return {e["path"] for e in json.loads(out)["edits"] if e["kind"] == "identifier"}


def test_tests_that_drive_the_renamed_thing_are_rewritten_their_fixtures_are_not(ws, capsys):  # case 4
    paths = _plan_edits(ws, capsys, {"tests/e2e/scenarios.py": 'BIN = "odap-composer"\n',
                                     "tests/fixtures/golden.txt": "odap-composer\n",
                                     "tests/sound-soak/run.sh": "cargo run -p odap-composer\n"})
    assert {"kicad/tests/e2e/scenarios.py", "kicad/tests/sound-soak/run.sh"} <= paths
    assert "kicad/tests/fixtures/golden.txt" not in paths


def test_an_accepted_adr_is_never_rewritten(ws, capsys):  # case 7
    paths = _plan_edits(ws, capsys, {
        "docs/adr/0001-composer.md": "# 1. Composer\n\n- **Status:** Accepted\n\nThe odap-composer crate.\n",
        "docs/adr/0002-next.md": "# 2. Next\n\n- **Status:** Proposed\n\nThe odap-composer crate.\n"})
    assert "kicad/docs/adr/0002-next.md" in paths and "kicad/docs/adr/0001-composer.md" not in paths


def _identifier(ws, old, new):
    lines = ["old\tnew\tkind\taction\tnotes\tscope\troot", f"{old}\t{new}\tidentifier\trename\t\ttest\tlibs"]
    (ws.parent / "map.tsv").write_text("\n".join(lines) + "\n")
    m = rename_map.load(ws, ws.parent / "map.tsv")
    return rename_map.identifier(m, rename_map.identifier_rows(m)[0])


def test_a_qualified_path_matches_its_value(ws):  # case 6
    i = _identifier(ws, "path:fw_cfg:opt/org.opendap/device-description", "path:fw_cfg:opt/cc.opendap/device-description")
    assert (i.reason, i.old, i.new) == (None, ["opt/org.opendap/device-description"], ["opt/cc.opendap/device-description"])
    assert _identifier(ws, "path:/run/opendap", "path:/run/blackbird").old == ["/run/opendap"]


def test_a_new_value_without_the_kind_takes_the_old_ones(ws):  # case 10
    i = _identifier(ws, "crate:odap-composer", "blackbird-composer")
    assert (i.reason, i.new) == (None, ["blackbird-composer"])
    assert _identifier(ws, "crate:odap-composer", "binary:blackbird-composer").reason == "kind-mismatch"
