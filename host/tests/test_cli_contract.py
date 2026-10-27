"""Every odapkit tool of this root holds to the plugin contract (command standard §6), through odw.clikit."""
import json
import pathlib
import subprocess
import tomllib

import pytest

from odw import (audit, cli_conformance, clikit, docs, noindex, plan, rename, requirements, sync,
                 verify)

HOST = pathlib.Path(__file__).resolve().parents[1]
TOOLS = {t["name"]: t for t in tomllib.loads((HOST / "tools.toml").read_text())["tool"]}
PYTHON = {"audit": audit, "cli": cli_conformance, "docs": docs, "noindex": noindex, "plan": plan,
          "rename": rename, "req": requirements, "sync": sync, "verify": verify}


def test_every_plugin_in_bin_is_declared():
    on_disk = {p.name.removeprefix("odapkit-") for p in (HOST / "bin").glob("odapkit-*")}
    assert on_disk == set(TOOLS)
    assert all((HOST / t["entry"]).is_file() for t in TOOLS.values())


@pytest.mark.parametrize("name", sorted(PYTHON))
def test_help_first_line_is_front_tool_and_the_declared_summary(name, capsys):
    assert PYTHON[name].main(["--help"]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first == f"odapkit {name}: {TOOLS[name]['summary']}"


@pytest.mark.parametrize("name", sorted(PYTHON))
def test_an_unknown_flag_exits_2_with_a_code_a_hint_and_a_fix(name, capsys):
    assert PYTHON[name].main(["--odw-no-such-flag"]) == 2
    err = capsys.readouterr().err
    assert "error input.invalid:" in err and "\n  hint: " in err and "\n  fix: " in err


@pytest.mark.parametrize("name", sorted(PYTHON))
def test_a_usage_error_with_json_is_an_error_document(name, capsys):
    PYTHON[name].main(["--json", "--odw-no-such-flag"])
    out = capsys.readouterr().out.strip().splitlines()
    assert json.loads(out[-1])["schema"] == clikit.ERROR_SCHEMA


@pytest.mark.parametrize("name", ["qemu", "vmnet"])
def test_shell_plugins_help_first_line(name):
    r = subprocess.run([str(HOST / "bin" / f"odapkit-{name}"), "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    assert r.stdout.splitlines()[0] == f"odapkit {name}: {TOOLS[name]['summary']}"


@pytest.mark.parametrize("name", ["qemu", "vmnet"])
def test_shell_plugins_unknown_flag_exits_2_with_a_code(name):
    r = subprocess.run([str(HOST / "bin" / f"odapkit-{name}"), "--odw-no-such-flag"], capture_output=True,
                       text=True)
    assert r.returncode == 2 and "error input.invalid:" in r.stderr and "hint: " in r.stderr


def test_qemu_install_dry_run_names_its_steps_and_changes_nothing(tmp_path):
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    r = subprocess.run([str(HOST / "bin" / "odapkit-qemu"), "install", "--dry-run"], capture_output=True,
                       text=True, env=env)
    # Without the build tools on PATH the dry run says the install would fail, and exits 1.
    assert r.returncode == 1 and "would fail: missing" in r.stderr
    assert "would download https://download.qemu.org/qemu-" in r.stderr and "would link" in r.stderr
    assert list(tmp_path.iterdir()) == []


def test_qemu_status_json_has_a_schema(tmp_path):
    r = subprocess.run([str(HOST / "bin" / "odapkit-qemu"), "status", "--json"], capture_output=True, text=True,
                       env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 1
    doc = json.loads(r.stdout)
    assert doc["schema"] == "opendap.qemu-status/v1" and doc["installed"] is False


@pytest.mark.skipif(subprocess.run(["uname", "-s"], capture_output=True, text=True).stdout.strip() != "Darwin",
                    reason="odapkit vmnet is macOS only")
@pytest.mark.parametrize("verb", ["install", "uninstall"])
def test_vmnet_dry_run_names_its_steps_and_runs_nothing_as_root(verb):
    r = subprocess.run([str(HOST / "bin" / "odapkit-vmnet"), verb, "--dry-run"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                                                      "ODAP_LAN_INTERFACE": "en9"})
    assert r.returncode == 0, r.stderr
    assert "would" in r.stderr and "sudo" not in r.stderr.split("would")[0]


@pytest.mark.skipif(subprocess.run(["uname", "-s"], capture_output=True, text=True).stdout.strip() != "Darwin",
                    reason="odapkit vmnet is macOS only")
def test_vmnet_status_json_has_a_schema():
    r = subprocess.run([str(HOST / "bin" / "odapkit-vmnet"), "status", "--json"], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "ODAP_LAN_INTERFACE": "en9"})
    doc = json.loads(r.stdout)
    assert doc["schema"] == "opendap.vmnet-status/v1" and doc["running"] is False and r.returncode == 1


def _umbrella(*args):
    return subprocess.run([str(HOST / "bin" / "odapkit"), *args], capture_output=True, text=True)


def test_umbrella_help_json_lists_the_tools_with_a_schema():
    r = _umbrella("help", "--json")
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    assert doc["schema"] == "opendap.toolkit-tools/v1"
    by_name = {t["name"]: t for t in doc["tools"]}
    assert by_name["plan"]["summary"] and by_name["plan"]["path"].endswith("/bin/odapkit-plan")
    for t in doc["tools"]:  # a tool of this checkout carries the summary tools.toml declares
        if t["path"].startswith(f"{HOST}/bin/"):
            assert t["summary"] == TOOLS[t["name"]]["summary"]


def test_umbrella_unknown_tool_is_an_error_with_a_code_and_a_fix():
    r = _umbrella("no-such-tool")
    assert r.returncode == 2
    assert "error tool.not_found:" in r.stderr and "fix: odapkit help" in r.stderr
