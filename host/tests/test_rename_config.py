"""odw rename config: a cut root's shared configuration (RFC 0017 §7 step 4), on synthetic roots shaped like the
libs, hats and tools cuts (done by hand on 2026-10-27), never the real workspace."""
import json
import pathlib
import shutil
import subprocess

import pytest

from odw import audit, rename, rename_config, rename_precommit as rp, schemas

BLOCK = ("<!-- >>> odapkit sync: managed block from the workspace templates; edit them, not this block -->\n"
         "Part of [OpenDAP](https://github.com/opendap-project). This repository is Open Source.\n"
         "<!-- <<< odapkit sync -->\n")
SHARED = """# Local checks for every commit. Install once per clone: pre-commit install
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: check-yaml
      - id: check-added-large-files
        args: ["--maxkb=5120"]
  - repo: https://github.com/fsfe/reuse-tool
    rev: v6.2.0
    hooks:
      - id: reuse
"""
CONSUMER = """  - repo: https://github.com/opendap-project/toolkit-hooks
    rev: v0.4.1
    hooks:
      - id: no-stray-files
"""
REQS = """  # The requirements README is rendered from requirements.toml and checked.
  - repo: local
    hooks:
      - id: requirements-current
        name: requirements README rendered (odapkit req render --check)
        entry: odapkit req render docs/requirements/requirements.toml --check
        language: system
        pass_filenames: false
        files: ^docs/requirements/
"""
HOST = SHARED + CONSUMER + """  - repo: local
    hooks:
      - id: odw-tests
        name: odw tests (schemas, plan, audit)
        entry: uv run --quiet pytest -q
        language: system
        pass_filenames: false
        files: ^(src|schemas|tests)/|^pyproject\\.toml$
  # The odw dispatch crate: its unit and fixture-workspace tests, built into this checkout's target/.
  - repo: local
    hooks:
      - id: odw-dispatch-tests
        name: odw dispatch tests (cargo test)
        entry: sh -c 'CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-4}" cargo test --quiet --manifest-path crates/d/Cargo.toml'
        language: system
        pass_filenames: false
        files: ^crates/d/|^rust-toolchain\\.toml$
""" + REQS
IMAGE = SHARED + CONSUMER + """  # Shell scripts (found by their shebang): shellcheck, pinned through shellcheck-py.
  - repo: https://github.com/shellcheck-py/shellcheck-py
    rev: v0.11.0.1
    hooks:
      - id: shellcheck
        args: ["-x", "--source-path=SCRIPTDIR"]
""" + REQS + """  - repo: local
    hooks:
      - id: cli-contract
        name: command-line contract (tests/cli.sh)
        entry: tests/cli.sh
        language: system
        pass_filenames: false
        files: ^(bin|lib|tests)/|^tools\\.toml$
"""
HOOKS = SHARED + """  # This repository provides the shared hooks, so it runs them from its own tree.
  - repo: local
    hooks:
      - id: no-stray-files
        name: No stray files
        entry: hooks/no-stray-files.sh
        language: script
        files: \\.(pdf|html?)$
      - id: hooks-tests
        name: hook and tool tests (python3 -m unittest discover tests)
        entry: python3 -m unittest discover tests
        language: system
        pass_filenames: false
        files: ^(bin|hooks|lib|tests)/|^tools\\.toml$|^\\.pre-commit-hooks\\.yaml$
"""
HOOKS_MANIFEST = """# Shared pre-commit hooks for OpenDAP repositories.
- id: no-stray-files
  name: No stray files
  entry: hooks/no-stray-files.sh
  language: script
  files: \\.(pdf|html?)$
- id: kicad-erc
  name: KiCad ERC (changed schematics)
  entry: hooks/kicad-check.py erc
  language: script
  files: \\.kicad_sch$
"""
KICAD = SHARED + """  - repo: https://github.com/opendap-project/toolkit-hooks
    rev: v0.4.1
    hooks:
      - id: no-stray-files
      - id: kicad-erc
        exclude: ^design-rules/(vendor/|[^/]+/test/)  # rule-set test boards fail by design
  - repo: local
    hooks:
      - id: colour-theme
        name: Schematic colour theme (CRAFT-103)
        entry: python3 colour-themes/check_theme.py
        language: system
        files: ^colour-themes/
        pass_filenames: false
"""
PARTS = SHARED + CONSUMER
STANDARD = SHARED + CONSUMER + """  - repo: local
    hooks:
      # The interface v2 tables are generated from interfaces/hat-v2/ and checked; the generator is tested.
      - id: hat-interface-current
        name: interface v2 tables generated (tools/hat_interface.py render --check)
        entry: python tools/hat_interface.py render --check
        language: python
        additional_dependencies: ["jsonschema==4.23.0"]
        pass_filenames: false
        files: ^(interfaces/|tools/|docs/specs/interface-v2\\.md$)
""" + REQS
RADIO = SHARED + CONSUMER + REQS


def license_text(name):
    return f"{name} licence text\n"


def folder(readme, config, licences, claude_rules=(), extra=None):
    files = {
        "README.md": readme,
        "CLAUDE.md": f"# {readme.splitlines()[0][2:]}\n\nIntro line.\n\n{BLOCK}\n## Working here\n\n- Its rules.\n",
        "REUSE.toml": "version = 1\n",
        ".gitignore": "*.bak\n",
        ".pre-commit-config.yaml": config,
        ".markdownlint-cli2.jsonc": "{ \"config\": {} }\n",
        ".claude/settings.json": "{\"permissions\": {}}\n",
        ".github/CODEOWNERS": "* @opendap-project/maintainers\n",
        **{f"LICENSES/{n}.txt": license_text(n) for n in licences},
        **{f".claude/rules/{r}.md": f"# {r} rules\n" for r in claude_rules},
        **(extra or {}),
    }
    return files


CUTS = {
    "tools": ({"toolkit-host": "host", "toolkit-image": "image", "toolkit-hooks": "hooks"}, {
        "host": folder("# Host\n\nThe maintainers' workspace tools.\n", HOST, ["GPL-3.0-or-later", "MIT"],
                       extra={"docs/requirements/requirements.toml": "schema = \"x\"\n"}),
        "image": folder("# Image\n\nBuilds the Blackbird OS image. More text.\n", IMAGE, ["GPL-3.0-or-later", "MIT"],
                        extra={"docs/requirements/requirements.toml": "schema = \"x\"\n"}),
        "hooks": folder("# Hooks\n\nShared pre-commit hooks.\n", HOOKS, ["MIT", "CC-BY-SA-4.0"],
                        extra={".pre-commit-hooks.yaml": HOOKS_MANIFEST}),
    }),
    "libs": ({"kicad-lib": "kicad", "parts-lib": "parts"}, {
        "kicad": folder("# KiCad library\n\nSymbols and footprints.\n", KICAD, ["CERN-OHL-W-2.0", "CC-BY-SA-4.0"],
                        ["kicad"]),
        "parts": folder("# Parts\n\nThe manifest of external part files.\n", PARTS, ["CC-BY-SA-4.0", "MIT"]),
    }),
    "hats": ({"hat-standard": "standard", "hat-radio": "radio"}, {
        "standard": folder("# HAT Standard\n\nThe interface between the T1 and its HATs.\n", STANDARD,
                           ["MPL-2.0", "CC-BY-SA-4.0", "MIT"]),
        "radio": folder("# Radio HAT\n\nSoftware-defined radio.\n", RADIO, ["CC-BY-SA-4.0", "GPL-3.0-or-later"],
                        ["kicad", "freecad"]),
    }),
}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
                          env=audit.git_env()).stdout


def make_root(tmp_path, root):
    """The ws (repos.tsv and a map) and the merged root of one cut, each source whole in its folder."""
    landings, folders = CUTS[root]
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "repos.tsv").write_text("path\trepository\n" + "".join(
        f"public/{root}/{d}\topendap-project/{n}\n" for n, d in landings.items()))
    rows = [f"repo:opendap-project/{n}\trepo:opendap-project/{root} ({d}/)\trepo\tmerge\t\ttest"
            for n, d in landings.items()]
    (tmp_path / "map.tsv").write_text("old\tnew\tkind\taction\tnotes\tscope\n" + "\n".join(rows) + "\n")
    top = tmp_path / root
    top.mkdir()
    git(top, "init", "-q", "-b", "main")
    git(top, "config", "user.email", "t@example.org")
    git(top, "config", "user.name", "T")
    for d, files in folders.items():
        for name, text in files.items():
            (top / d / name).parent.mkdir(parents=True, exist_ok=True)
            (top / d / name).write_text(text)
    git(top, "add", "-A")
    git(top, "commit", "-q", "-m", f"{root}: join with history")
    return ws, top


def run(capsys, root, ws, top, *extra):
    code = rename.main(["config", root, "--workspace", str(ws), "--map", str(ws.parent / "map.tsv"),
                        "--root-dir", str(top), *extra])
    out, err = capsys.readouterr()
    return code, out, err


def tracked(top):
    return set(audit.tracked(top))


@pytest.mark.parametrize("root", sorted(CUTS))
def test_config_hoists_the_shared_files_and_commits_once(tmp_path, capsys, root):
    ws, top = make_root(tmp_path, root)
    head = git(top, "rev-parse", "HEAD").strip()
    code, out, err = run(capsys, root, ws, top, "--json")
    assert code == 0, err
    doc = json.loads(out)
    assert not schemas.errors(doc, "rename-config")
    assert doc["mode"] == "apply" and doc["base"] == head and len(doc["commits"]) == 1
    assert git(top, "log", "-1", "--format=%s").strip() == f"{root}: one shared configuration at the root"
    assert not git(top, "status", "--porcelain")
    files = tracked(top)
    landings = CUTS[root][1]
    for d in landings:
        for gone in (".pre-commit-config.yaml", ".markdownlint-cli2.jsonc", ".claude/settings.json",
                     ".github/CODEOWNERS"):
            assert f"{d}/{gone}" not in files
        assert not any(f.startswith(f"{d}/LICENSES/") or f.startswith(f"{d}/.claude/") for f in files)
        for kept in ("README.md", "CLAUDE.md", "REUSE.toml", ".gitignore"):
            assert f"{d}/{kept}" in files
        assert ">>> odapkit sync" not in (top / d / "CLAUDE.md").read_text()  # one block: the root's
        assert "## Working here" in (top / d / "CLAUDE.md").read_text()
    union = {pathlib.PurePosixPath(n).stem for f in landings.values() for n in f if n.startswith("LICENSES/")}
    assert {f[len("LICENSES/"):-4] for f in files if f.startswith("LICENSES/")} == union
    rules = {n for f in landings.values() for n in f if n.startswith(".claude/rules/")}
    assert rules <= files and {".claude/settings.json", ".markdownlint-cli2.jsonc", ".github/CODEOWNERS"} <= files
    claude = (top / "CLAUDE.md").read_text()
    assert claude.count(">>> odapkit sync") == 1 and claude.startswith(f"# {root}\n")
    readme = (top / "README.md").read_text()
    for d in landings:
        assert f"[`{d}/`]({d}/README.md)" in readme
    reuse = (top / "REUSE.toml").read_text()
    assert '".gitattributes"' in reuse and '"README.md"' in reuse and '"LICENSES' not in reuse
    # History follows the hoisted files: the first copy moves, the others leave.
    first = sorted(landings)[0]
    log = git(top, "log", "--follow", "--format=%s", "--", ".claude/settings.json").splitlines()
    assert log[-1] == f"{root}: join with history", first


def test_tools_hooks_are_one_config_scoped_by_folder(tmp_path, capsys):
    ws, top = make_root(tmp_path, "tools")
    assert run(capsys, "tools", ws, top)[0] == 0
    cfg = (top / ".pre-commit-config.yaml").read_text()
    # The shared remote hooks once, unscoped; the root's own source (toolkit-hooks) is never pinned remotely.
    assert cfg.count("repo: https://github.com/pre-commit/pre-commit-hooks") == 1 and cfg.count("id: reuse") == 1
    assert "toolkit-hooks" not in cfg
    # The provider's copy of a consumed hook runs from its folder for the whole root.
    hook = rp.parse(cfg).hook("no-stray-files")
    assert hook.get("entry") == "hooks/hooks/no-stray-files.sh" and hook.get("files") == r"\.(pdf|html?)$"
    # A hook only one folder had is scoped to it; local hooks run inside their folder, ids stay unique.
    shell = rp.parse(cfg).hook("shellcheck")
    assert shell.get("exclude") == "^(?!image/)" and shell.get("args") == '["-x", "--source-path=SCRIPTDIR"]'
    odw = rp.parse(cfg).hook("odw-tests")
    assert odw.get("entry") == "sh -c 'cd host && uv run --quiet pytest -q'"
    assert odw.get("files") == r"^host/(?:(src|schemas|tests)/|pyproject\.toml$)"
    cargo = rp.parse(cfg).hook("odw-dispatch-tests")
    assert cargo.get("entry").startswith("sh -c 'cd host && CARGO_BUILD_JOBS=")
    ids = [h.get("id") for h in rp.parse(cfg).hooks()]
    assert len(ids) == len(set(ids))
    assert {"requirements-current-host", "requirements-current-image", "cli-contract"} <= set(ids)
    assert rp.parse(cfg).hook("requirements-current-image").get("files") == "^image/docs/requirements/"
    # The hook manifest moves to the root, its script entries under the folder.
    manifest = (top / ".pre-commit-hooks.yaml").read_text()
    assert "entry: hooks/hooks/no-stray-files.sh" in manifest and "entry: hooks/hooks/kicad-check.py erc" in manifest
    assert "hooks/.pre-commit-hooks.yaml" not in tracked(top)
    assert '".pre-commit-hooks.yaml"' in (top / "REUSE.toml").read_text()


def test_libs_and_hats_hooks_match_the_hand_cuts(tmp_path, capsys):
    ws, top = make_root(tmp_path / "l", "libs")
    assert run(capsys, "libs", ws, top)[0] == 0
    cfg = rp.parse((top / ".pre-commit-config.yaml").read_text())
    # A remote hook keeps its manifest's `files` (\.kicad_sch$): it is scoped to its folders through `exclude`.
    assert cfg.hook("kicad-erc").get("exclude") == "^(?!kicad/)|^kicad/design-rules/(vendor/|[^/]+/test/)"
    assert cfg.hook("kicad-erc").get("files") is None
    assert cfg.hook("no-stray-files").get("files") is None  # every folder pins it: shared, unscoped
    assert cfg.hook("colour-theme").get("entry") == "sh -c 'cd kicad && python3 colour-themes/check_theme.py'"
    assert cfg.hook("colour-theme").get("files") == "^kicad/colour-themes/"
    ws, top = make_root(tmp_path / "h", "hats")
    assert run(capsys, "hats", ws, top)[0] == 0
    cfg = rp.parse((top / ".pre-commit-config.yaml").read_text())
    hat = cfg.hook("hat-interface-current")
    assert hat.get("entry") == "sh -c 'cd standard && python tools/hat_interface.py render --check'"
    assert hat.get("files") == r"^standard/(interfaces/|tools/|docs/specs/interface-v2\.md$)"
    assert hat.get("additional_dependencies") == '["jsonschema==4.23.0"]'
    assert hat.get("name") == "standard interface v2 tables generated (tools/hat_interface.py render --check)"
    assert "# The interface v2 tables are generated" in (top / ".pre-commit-config.yaml").read_text()
    assert {"requirements-current-standard", "requirements-current-radio"} <= {h.get("id") for h in cfg.hooks()}


@pytest.mark.skipif(shutil.which("pre-commit") is None, reason="pre-commit is not installed")
@pytest.mark.parametrize("root", sorted(CUTS))
def test_the_rendered_configs_are_valid_for_pre_commit(tmp_path, capsys, root):
    ws, top = make_root(tmp_path, root)
    assert run(capsys, root, ws, top)[0] == 0
    files = [str(top / ".pre-commit-config.yaml")]
    r = subprocess.run(["pre-commit", "validate-config", *files], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    if (top / ".pre-commit-hooks.yaml").exists():
        r = subprocess.run(["pre-commit", "validate-manifest", str(top / ".pre-commit-hooks.yaml")],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr


def test_names_with_a_colon_are_quoted():
    assert rp.scalar("odw: tests") == "'odw: tests'" and rp.scalar("it's 1") == "it's 1"
    assert rp.scalar("a #b") == "'a #b'" and rp.scalar("plain (x)") == "plain (x)"
    assert rp.scalar("'q'") == "'''q'''" and rp.scalar("*x") == "'*x'"


def test_scope_prefixes_every_alternative():
    assert rp.scope(None, "f") == "^f/"
    assert rp.scope("^docs/requirements/", "f") == "^f/docs/requirements/"
    assert rp.scope(r"^(a|b)/|^c\.toml$", "f") == r"^f/(?:(a|b)/|c\.toml$)"
    assert rp.scope(r"\.(pdf|html?)$", "f") == r"^f/.*(?:\.(pdf|html?)$)"
    assert rp.scope(r"^a/|[|]x", "f") == r"^f/(?:a/|.*(?:[|]x))"


def test_dry_run_changes_nothing_and_lists_the_actions(tmp_path, capsys):
    ws, top = make_root(tmp_path, "tools")
    head = git(top, "rev-parse", "HEAD")
    code, out, _ = run(capsys, "tools", ws, top, "--dry-run", "--json")
    doc = json.loads(out)
    assert code == 0 and doc["mode"] == "plan" and git(top, "rev-parse", "HEAD") == head
    assert not git(top, "status", "--porcelain") and not schemas.errors(doc, "rename-config")
    hoisted = {h["to"]: h["from"] for h in doc["hoisted"]}
    assert hoisted["LICENSES/MIT.txt"] == ["hooks/LICENSES/MIT.txt", "host/LICENSES/MIT.txt",
                                           "image/LICENSES/MIT.txt"]
    assert set(doc["written"]) == {".pre-commit-config.yaml", ".pre-commit-hooks.yaml", "README.md", "CLAUDE.md",
                                   "REUSE.toml"}
    assert set(doc["stripped"]) == {"host/CLAUDE.md", "image/CLAUDE.md", "hooks/CLAUDE.md"}
    assert doc["hooks"]["dropped"] == ["toolkit-hooks no-stray-files"]


def test_files_that_differ_are_refused(tmp_path, capsys):
    ws, top = make_root(tmp_path, "libs")
    (top / "parts/LICENSES/MIT.txt").unlink()
    (top / "kicad/LICENSES/MIT.txt").write_text("another MIT text\n")
    (top / "parts/LICENSES/MIT.txt").write_text("MIT licence text\n")
    git(top, "add", "-A")
    git(top, "commit", "-q", "-m", "two MIT texts")
    head = git(top, "rev-parse", "HEAD")
    code, out, _ = run(capsys, "libs", ws, top, "--json")
    assert code == 1 and json.loads(out)["error"]["code"] == "config.differs"
    assert "LICENSES/MIT.txt" in json.loads(out)["error"]["message"]
    assert git(top, "rev-parse", "HEAD") == head and not git(top, "status", "--porcelain")


def test_root_files_already_written_are_kept(tmp_path, capsys):
    ws, top = make_root(tmp_path, "hats")
    (top / "README.md").write_text("# HATs\n\nWritten by hand.\n")
    git(top, "add", "README.md")
    git(top, "commit", "-q", "-m", "readme")
    code, out, _ = run(capsys, "hats", ws, top, "--json")
    doc = json.loads(out)
    assert code == 0 and "README.md" in doc["kept"] and "README.md" not in doc["written"]
    assert (top / "README.md").read_text() == "# HATs\n\nWritten by hand.\n"


def test_a_second_run_finds_nothing_to_do(tmp_path, capsys):
    ws, top = make_root(tmp_path, "libs")
    assert run(capsys, "libs", ws, top)[0] == 0
    head = git(top, "rev-parse", "HEAD")
    code, out, _ = run(capsys, "libs", ws, top, "--json")
    doc = json.loads(out)
    assert code == 0 and doc["commits"] == [] and git(top, "rev-parse", "HEAD") == head


def test_refuses_a_dirty_or_missing_root(tmp_path, capsys):
    ws, top = make_root(tmp_path, "libs")
    (top / "notes.txt").write_text("someone else's\n")
    code, out, _ = run(capsys, "libs", ws, top, "--json")
    assert code == 1 and json.loads(out)["error"]["code"] == "config.dirty"
    code, out, _ = run(capsys, "libs", ws, tmp_path / "nowhere", "--json")
    assert code == 1 and json.loads(out)["error"]["code"] == "config.no-root"


def test_the_schema_is_valid():
    schemas.check_schema("rename-config")


def test_python_files_stay_within_budget():
    for mod in (rename_config, rp):
        assert pathlib.Path(mod.__file__).read_text().count("\n") <= 500, mod.__name__


def test_the_root_readme_rebases_relative_links_onto_each_folder(tmp_path):
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui/README.md").write_text("# UI\n\nThe design system ([spec](docs/specs/ds.md)), "
                                           "[site](https://opendap.cc) and [top](#license).\n")
    text = rename_config.readme("blackbird", tmp_path, ["ui"], ["blackbird-ui"])
    assert "([spec](ui/docs/specs/ds.md))" in text
    assert "[site](https://opendap.cc)" in text and "[top](#license)" in text


T1_KICAD = SHARED + """  - repo: https://github.com/opendap-project/tools
    rev: v0.1.0
    hooks:
      - id: no-stray-files
      - id: kicad-erc
      - id: kicad-drc
        exclude: ^hardware/rules/test/  # the rule test board fails by design
"""
GITMODULES = '[submodule "hardware/lib/opendap"]\n\tpath = hardware/lib/opendap\n\turl = https://github.com/opendap-project/libs.git\n'


T1_LANDING = ("--landing", "t1=product")


def make_t1(tmp_path):
    """A root shaped like the t1 cut: a nested landing, a submodule, no folder bringing MIT, a README opening on an
    image and a CLAUDE.md that ends with the managed block."""
    landings = {"board": "board", "faceplate-touch": "faceplates/touch", "t1": "product"}
    product = folder("# OpenDAP T1\n\n![Concept art of the T1](docs/art.png)\n\nThe OpenDAP T1 is a DAP.\n",
                     SHARED + CONSUMER, ["CC-BY-SA-4.0"])
    product["CLAUDE.md"] = f"# T1\n\nThe product.\n\n{BLOCK}"
    CUTS["t1"] = (landings, {
        "board": folder("# Board\n\nMain board.\n", T1_KICAD, ["CC-BY-SA-4.0", "GPL-3.0-or-later"],
                        extra={".gitmodules": GITMODULES}),
        "faceplates/touch": folder("# Touch\n\nTouch faceplate.\n", T1_KICAD, ["CC-BY-SA-4.0"]),
        "product": product,
    })
    try:
        ws, top = make_root(tmp_path, "t1")
    finally:
        del CUTS["t1"]
    (ws / "LICENSES").mkdir()
    (ws / "LICENSES/MIT.txt").write_text("MIT licence text (workspace)\n")
    return ws, top


def test_t1_submodules_move_to_the_root_gitmodules(tmp_path, capsys):
    ws, top = make_t1(tmp_path)
    code, out, err = run(capsys, "t1", ws, top, *T1_LANDING, "--json")
    assert code == 0, err
    files = tracked(top)
    assert "board/.gitmodules" not in files and ".gitmodules" in files
    assert (top / ".gitmodules").read_text() == ('[submodule "board/hardware/lib/opendap"]\n'
                                                 "\tpath = board/hardware/lib/opendap\n"
                                                 "\turl = https://github.com/opendap-project/libs.git\n")
    assert '".gitmodules"' in (top / "REUSE.toml").read_text()
    assert ".gitmodules" in json.loads(out)["written"]


def test_t1_a_licence_no_folder_brought_comes_from_the_workspace(tmp_path, capsys):
    ws, top = make_t1(tmp_path)
    code, out, err = run(capsys, "t1", ws, top, *T1_LANDING, "--json")
    doc = json.loads(out)
    assert code == 0, err
    assert (top / "LICENSES/MIT.txt").read_text() == "MIT licence text (workspace)\n"
    assert not [w for w in doc["warnings"] if w["code"] == "config.licence-missing"]
    assert "LICENSES/MIT.txt" in doc["written"]


def test_t1_readme_skips_images_and_claude_ends_cleanly(tmp_path, capsys):
    ws, top = make_t1(tmp_path)
    assert run(capsys, "t1", ws, top, *T1_LANDING)[0] == 0
    assert "| [`product/`](product/README.md) | The OpenDAP T1 is a DAP |" in (top / "README.md").read_text()
    assert (top / "product/CLAUDE.md").read_text() == "# T1\n\nThe product.\n"


def test_t1_remote_hooks_some_folders_pin_keep_their_manifest_files(tmp_path, capsys):
    ws, top = make_t1(tmp_path)
    assert run(capsys, "t1", ws, top, *T1_LANDING)[0] == 0
    cfg = rp.parse((top / ".pre-commit-config.yaml").read_text())
    erc = cfg.hook("kicad-erc")
    assert erc.get("files") is None and erc.get("exclude") == "^(?!(?:board|faceplates/touch)/)"
    drc = cfg.hook("kicad-drc")
    assert drc.get("files") is None
    assert drc.get("exclude") == "^(?!(?:board|faceplates/touch)/)|^(?:board|faceplates/touch)/hardware/rules/test/"
