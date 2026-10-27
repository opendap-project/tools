//! The `odw` binary on fixture workspaces: discovery, help, `--json`, exit codes, and the conformance
//! cases of the command standard (§8) that apply to the dispatcher itself.

use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};

use serde_json::Value;

const ODW: &str = env!("CARGO_BIN_EXE_odw");

/// A throwaway folder under the target directory.
struct Fixture {
    dir: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        static N: AtomicUsize = AtomicUsize::new(0);
        let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join(format!(
            "odw-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        Fixture { dir }
    }

    fn write(&self, rel: &str, text: &str, exec: bool) -> PathBuf {
        let p = self.dir.join(rel);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(&p, text).unwrap();
        std::fs::set_permissions(
            &p,
            std::fs::Permissions::from_mode(if exec { 0o755 } else { 0o644 }),
        )
        .unwrap();
        p
    }

    /// A plugin that prints its name, its arguments and ODAP_TOOLKIT_HOST_DIR, and exits with $EXIT_WITH.
    fn plugin(&self, rel: &str, label: &str) -> PathBuf {
        let body = format!(
            "#!/bin/sh\n# odapkit {label}: the {label} fixture\necho \"{label} $*\"\necho \"host=${{ODAP_TOOLKIT_HOST_DIR:-}}\"\nexit \"${{EXIT_WITH:-0}}\"\n"
        );
        self.write(rel, &body, true)
    }

    fn odw(&self, args: &[&str], env: &[(&str, &str)]) -> Output {
        let mut c = Command::new(ODW);
        c.args(args)
            .env_clear()
            .env("ODAP_WORKSPACE_DIR", self.dir.join("ws"))
            .env(
                "PATH",
                format!("{}:/usr/bin:/bin", self.dir.join("path").display()),
            )
            .stdin(Stdio::null());
        for (k, v) in env {
            c.env(k, v);
        }
        c.output().unwrap()
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn tools_toml(entries: &[(&str, &str)]) -> String {
    let mut s = String::from("schema = \"opendap.tools/v1\"\nfront = \"odw\"\n");
    for (name, stability) in entries {
        s += &format!(
            "\n[[tool]]\nname = \"{name}\"\nsummary = \"The {name} tool\"\nentry = \"bin/odapkit-{name}\"\naudience = \"maintainer\"\nstability = \"{stability}\"\n"
        );
    }
    s
}

/// Two roots: `public/toolkit/host` (plan, internal secret) and `public/other` (sim, plan again).
fn workspace() -> Fixture {
    let f = Fixture::new();
    f.write(
        "ws/repos.tsv",
        "path\trepository\npublic/toolkit/host\topendap-project/toolkit-host\npublic/other\topendap-project/other\n",
        false,
    );
    f.write(
        "ws/public/toolkit/host/tools.toml",
        &tools_toml(&[("plan", "preview"), ("secret", "internal")]),
        false,
    );
    f.plugin("ws/public/toolkit/host/bin/odapkit-plan", "plan");
    f.plugin("ws/public/toolkit/host/bin/odapkit-secret", "secret");
    f.write(
        "ws/public/other/tools.toml",
        &tools_toml(&[("sim", "stable"), ("plan", "preview")]),
        false,
    );
    f.plugin("ws/public/other/bin/odapkit-sim", "sim");
    f.plugin("ws/public/other/bin/odapkit-plan", "other-plan");
    f
}

fn stdout(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).into_owned()
}

fn stderr(o: &Output) -> String {
    String::from_utf8_lossy(&o.stderr).into_owned()
}

#[test]
fn runs_the_declared_entry_with_its_arguments_and_exit_status() {
    let f = workspace();
    let o = f.odw(&["plan", "list", "--json"], &[]);
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    assert_eq!(stdout(&o).lines().next(), Some("plan list --json"));
    let o = f.odw(&["sim"], &[("EXIT_WITH", "1")]);
    assert_eq!(
        (o.status.code(), stdout(&o).lines().next()),
        (Some(1), Some("sim "))
    );
    let o = f.odw(&["sim"], &[("EXIT_WITH", "7")]);
    assert_eq!(o.status.code(), Some(7));
}

#[test]
fn the_first_root_in_manifest_order_wins_and_shadows_path() {
    let f = workspace();
    f.plugin("path/odw-plan", "path-plan");
    let o = f.odw(&["plan"], &[]);
    assert_eq!(stdout(&o).lines().next(), Some("plan "));
}

#[test]
fn path_plugins_run_when_no_root_declares_them() {
    let f = workspace();
    f.plugin("path/odapkit-legacy", "legacy");
    f.plugin("path/odw-fresh", "fresh");
    assert_eq!(
        stdout(&f.odw(&["legacy", "x"], &[])).lines().next(),
        Some("legacy x")
    );
    assert_eq!(
        stdout(&f.odw(&["fresh"], &[])).lines().next(),
        Some("fresh ")
    );
}

#[test]
fn a_root_override_moves_the_root_and_is_exported_to_tools() {
    let f = workspace();
    let wt = f.dir.join("worktree");
    f.write(
        "worktree/tools.toml",
        &tools_toml(&[("plan", "preview")]),
        false,
    );
    f.plugin("worktree/bin/odapkit-plan", "worktree-plan");
    let o = f.odw(
        &["plan"],
        &[("ODAP_TOOLKIT_HOST_DIR", wt.to_str().unwrap())],
    );
    let out = stdout(&o);
    assert_eq!(out.lines().next(), Some("worktree-plan "));
    assert_eq!(
        out.lines().nth(1).unwrap(),
        format!("host={}", wt.display())
    );
    // Unset: the tool gets the manifest's toolkit-host root.
    let out = stdout(&f.odw(&["sim"], &[]));
    assert_eq!(
        out.lines().nth(1).unwrap(),
        format!("host={}", f.dir.join("ws/public/toolkit/host").display())
    );
}

#[test]
fn no_repository_name_matching() {
    let f = Fixture::new();
    f.write(
        "ws/repos.tsv",
        "path\trepository\npublic/toolkit/x\topendap-project/toolkit-x\n",
        false,
    );
    f.plugin("ws/public/toolkit/x/bin/odapkit-orphan", "orphan");
    let o = f.odw(&["orphan"], &[]);
    assert_eq!(o.status.code(), Some(2));
    assert!(stderr(&o).contains("error tool.not_found: no tool orphan (odw help lists them)"));
}

#[test]
fn unknown_and_bad_names_exit_2_with_a_code_a_hint_and_a_fix() {
    let f = workspace();
    for name in [
        "nope",
        "a/b",
        ".hidden",
        "Plan",
        "../public/other/bin/odapkit-sim",
    ] {
        let o = f.odw(&[name], &[]);
        assert_eq!(o.status.code(), Some(2), "{name}");
        let err = stderr(&o);
        assert!(
            err.starts_with(&format!(
                "odw: error tool.not_found: no tool {name} (odw help lists them)\n"
            )),
            "{err}"
        );
        assert!(err.contains("\n  hint: ") && err.contains("\n  fix: odw help\n"));
        assert!(o.stdout.is_empty());
    }
    let o = f.odw(&["help", "nope"], &[]);
    assert_eq!(o.status.code(), Some(2));
}

#[test]
fn unknown_flags_exit_2_and_json_adds_an_error_document() {
    let f = workspace();
    let o = f.odw(&["--odw-no-such-flag"], &[]);
    assert_eq!(o.status.code(), Some(2));
    assert!(stderr(&o).contains("error input.invalid: unknown flag --odw-no-such-flag"));
    let o = f.odw(&["help", "--json", "--odw-no-such-flag"], &[]);
    assert_eq!(o.status.code(), Some(2));
    let doc: Value = serde_json::from_str(stdout(&o).trim()).unwrap();
    assert_eq!(
        (doc["schema"].as_str(), doc["error"]["code"].as_str()),
        (Some("opendap.error/v1"), Some("input.invalid"))
    );
}

#[test]
fn help_lists_name_summary_audience_and_stability_grouped() {
    let f = workspace();
    f.plugin("path/odw-loose", "loose");
    for args in [&[][..], &["help"], &["--help"]] {
        let o = f.odw(args, &[]);
        assert_eq!(o.status.code(), Some(0));
        let out = stdout(&o);
        assert!(out.starts_with("Usage: odw TOOL"));
        let (stable, preview) = (
            out.find("\nstable:\n").unwrap(),
            out.find("\npreview:\n").unwrap(),
        );
        assert!(stable < preview);
        assert!(out.contains("  sim              The sim tool  (maintainer, stable)\n"));
        assert!(out.contains("  plan             The plan tool  (maintainer, preview)\n"));
        assert!(out.contains("found on PATH (audience and stability unknown):\n  loose            the loose fixture\n"));
        assert!(!out.contains("secret"));
    }
    assert!(stdout(&f.odw(&["help", "--all"], &[])).contains("\ninternal:\n  secret"));
}

#[test]
fn help_json_is_a_tools_list() {
    let f = workspace();
    f.plugin("path/odw-loose", "loose");
    let o = f.odw(&["help", "--json"], &[]);
    assert_eq!(o.status.code(), Some(0));
    let doc: Value = serde_json::from_str(&stdout(&o)).unwrap();
    assert_eq!(doc["schema"], "opendap.tools-list/v1");
    let names: Vec<&str> = doc["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|i| i["name"].as_str().unwrap())
        .collect();
    assert_eq!(names, ["plan", "sim", "loose"]);
    let plan = &doc["items"][0];
    assert_eq!(plan["root"], "public/toolkit/host");
    assert_eq!(plan["audience"], "maintainer");
    assert_eq!(plan["replaces"], Value::Null);
    assert!(
        plan["entry"]
            .as_str()
            .unwrap()
            .ends_with("/ws/public/toolkit/host/bin/odapkit-plan")
    );
    let loose = &doc["items"][2];
    assert_eq!(
        (loose["root"].as_str(), loose["stability"].as_str()),
        (Some(""), Some("unknown"))
    );
    assert_eq!(stdout(&f.odw(&["--json"], &[])), stdout(&o));
    let all: Value =
        serde_json::from_str(&stdout(&f.odw(&["help", "--json", "--all"], &[]))).unwrap();
    assert_eq!(all["items"].as_array().unwrap().len(), 4);
}

#[test]
fn help_tool_runs_the_tools_help() {
    let f = workspace();
    let o = f.odw(&["help", "sim"], &[]);
    assert_eq!(
        (o.status.code(), stdout(&o).lines().next()),
        (Some(0), Some("sim --help"))
    );
}

#[test]
fn a_builtin_is_never_shadowed() {
    let f = workspace();
    f.write(
        "ws/public/other/tools.toml",
        &tools_toml(&[("help", "stable"), ("check", "stable"), ("sim", "stable")]),
        false,
    );
    f.plugin("ws/public/other/bin/odapkit-help", "evil-help");
    f.plugin("ws/public/other/bin/odapkit-check", "evil-check");
    f.plugin("path/odw-help", "path-help");
    let o = f.odw(&["help"], &[]);
    assert_eq!(o.status.code(), Some(0));
    assert!(stdout(&o).starts_with("Usage: odw TOOL"));
    assert!(stderr(&o).contains("would shadow the built-in command odw help"));
    let o = f.odw(&["check"], &[]);
    assert_eq!(o.status.code(), Some(1));
    assert!(stderr(&o).contains("error command.unavailable:") && o.stdout.is_empty());
}

#[test]
fn a_broken_root_warns_in_help_and_the_others_still_work() {
    let f = workspace();
    f.write(
        "ws/public/other/tools.toml",
        "schema = \"opendap.tools/v1\"\nfront = \"odw\"\n[[tool]]\nname = 1\n",
        false,
    );
    let o = f.odw(&["help"], &[]);
    assert_eq!(o.status.code(), Some(0));
    assert!(stderr(&o).contains("public/other/tools.toml: not a valid opendap.tools/v1"));
    assert_eq!(f.odw(&["plan"], &[]).status.code(), Some(0));
    let o = f.odw(&["plan"], &[]);
    assert!(o.stderr.is_empty(), "dispatch stays quiet: {}", stderr(&o));
}

#[test]
fn outside_a_workspace_only_path_is_read() {
    let f = Fixture::new();
    f.plugin("path/odw-solo", "solo");
    let nows = f.dir.join("nows");
    let o = f.odw(&["solo"], &[("ODAP_WORKSPACE_DIR", nows.to_str().unwrap())]);
    assert_eq!(stdout(&o).lines().next(), Some("solo "));
    assert_eq!(stdout(&o).lines().nth(1), Some("host="));
}

#[test]
fn it_never_waits_for_input() {
    // §8 item 6: stdin closed, no TTY; the fixture runs with stdin from /dev/null and must return.
    let f = workspace();
    let start = std::time::Instant::now();
    assert_eq!(f.odw(&["help"], &[]).status.code(), Some(0));
    assert!(start.elapsed() < std::time::Duration::from_secs(5));
}

#[test]
fn a_summary_over_the_limit_is_reported_and_the_tool_still_runs() {
    let f = workspace();
    let long = "x".repeat(118);
    let text = tools_toml(&[("sim", "stable")]).replace("The sim tool", &long);
    f.write("ws/public/other/tools.toml", &text, false);
    let o = f.odw(&["help"], &[]);
    assert!(stderr(&o).contains("tool sim: the summary has 118 characters (1 to 100)"));
    assert!(stdout(&o).contains(&format!("  sim              {long}")));
    assert_eq!(stdout(&f.odw(&["sim"], &[])).lines().next(), Some("sim "));
}

#[test]
fn the_current_folder_never_chooses_the_workspace() {
    // A copy of odw outside any workspace, run inside a folder that holds a repos.tsv and a tools.toml.
    let outside = std::env::temp_dir().join(format!("odw-cwd-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&outside);
    std::fs::create_dir_all(outside.join("bin")).unwrap();
    let copy = outside.join("bin/odw");
    std::fs::copy(ODW, &copy).unwrap();
    let f = Fixture::new();
    f.write("cwd/repos.tsv", "path\trepository\nr\to/r\n", false);
    f.write(
        "cwd/r/tools.toml",
        &tools_toml(&[("evil", "stable")]),
        false,
    );
    f.plugin("cwd/r/bin/odapkit-evil", "evil");
    let o = Command::new(&copy)
        .arg("evil")
        .current_dir(f.dir.join("cwd"))
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .stdin(Stdio::null())
        .output()
        .unwrap();
    let _ = std::fs::remove_dir_all(&outside);
    assert_eq!(o.status.code(), Some(2), "{}", stdout(&o));
    assert!(stderr(&o).contains("no tool evil"));
}
