//! `odw doctor` (HL-62, command standard §3, §6): every problem of the front's tools in one report —
//! duplicate names across roots, unsafe or invalid `tools.toml` entries, summaries over 100 characters,
//! and tools whose `--help` fails — as text or `opendap.check-result/v1`, exit 0 clean, 1 a problem.

mod common;

use common::*;
use serde_json::Value;

fn doctor_json(f: &Fixture) -> (Option<i32>, Value) {
    let o = f.odw(&["doctor", "--json"], &[]);
    let doc = serde_json::from_str(&stdout(&o)).unwrap_or_else(|e| panic!("{e}: {}", stdout(&o)));
    (o.status.code(), doc)
}

fn failing(doc: &Value) -> Vec<(String, String)> {
    doc["results"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|r| r["verdict"] == "fail")
        .map(|r| {
            (
                r["rule"].as_str().unwrap().to_string(),
                r["message"].as_str().unwrap().to_string(),
            )
        })
        .collect()
}

/// One root, two tools, nothing wrong.
fn clean() -> Fixture {
    let f = Fixture::new();
    f.write("ws/repos.tsv", "path\trepository\npublic/a\to/a\n", false);
    f.write(
        "ws/public/a/tools.toml",
        &tools_toml(&[("plan", "preview"), ("sim", "stable")]),
        false,
    );
    f.plugin("ws/public/a/bin/odapkit-plan", "plan");
    f.plugin("ws/public/a/bin/odapkit-sim", "sim");
    f
}

#[test]
fn a_clean_workspace_passes_with_one_help_result_per_tool() {
    let f = clean();
    let (code, doc) = doctor_json(&f);
    assert_eq!(code, Some(0), "{doc}");
    assert_eq!(doc["schema"], "opendap.check-result/v1");
    assert_eq!(doc["tool"]["name"], "odw doctor");
    assert!(failing(&doc).is_empty());
    let help: Vec<&str> = doc["results"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|r| r["rule"] == "tool.help" && r["verdict"] == "pass")
        .map(|r| r["id"].as_str().unwrap())
        .collect();
    assert_eq!(help, ["plan", "sim"]);
    let o = f.odw(&["doctor"], &[]);
    assert_eq!(o.status.code(), Some(0));
    assert!(stdout(&o).is_empty(), "quiet on success: {}", stdout(&o));
}

#[test]
fn duplicate_names_across_roots_fail() {
    let f = workspace(); // public/toolkit/host and public/other both declare plan
    let (code, doc) = doctor_json(&f);
    assert_eq!(code, Some(1));
    let fails = failing(&doc);
    assert!(
        fails.iter().any(|(rule, m)| rule == "tools.duplicate"
            && m.contains("tool plan is declared twice")),
        "{fails:?}"
    );
}

#[test]
fn unsafe_entries_long_summaries_and_invalid_files_fail() {
    let f = clean();
    let long = "x".repeat(101);
    let text = tools_toml(&[
        ("plan", "preview"),
        ("sim", "stable"),
        ("esc", "stable"),
        ("help", "stable"),
    ])
    .replace("The sim tool", &long)
    .replace("bin/odapkit-esc", "../../outside");
    f.write("ws/public/a/tools.toml", &text, false);
    f.write(
        "ws/repos.tsv",
        "path\trepository\npublic/a\to/a\npublic/b\to/b\n",
        false,
    );
    f.write("ws/public/b/tools.toml", "schema = 1\n", false);
    let (code, doc) = doctor_json(&f);
    assert_eq!(code, Some(1));
    let fails = failing(&doc);
    let has = |rule: &str, part: &str| fails.iter().any(|(r, m)| r == rule && m.contains(part));
    assert!(
        has("tools.summary", "tool sim: the summary has 101 characters"),
        "{fails:?}"
    );
    assert!(
        has(
            "tools.entry",
            "entry \"../../outside\" must be a path inside the root"
        ),
        "{fails:?}"
    );
    assert!(
        has("tools.entry", "would shadow the built-in command odw help"),
        "{fails:?}"
    );
    assert!(
        has("tools.invalid", "not a valid opendap.tools/v1"),
        "{fails:?}"
    );
    let located = doc["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["rule"] == "tools.invalid")
        .unwrap();
    assert!(
        located["location"]["path"]
            .as_str()
            .unwrap()
            .ends_with("public/b/tools.toml")
    );
}

#[test]
fn a_tool_without_a_working_help_fails() {
    let f = clean();
    f.write(
        "ws/public/a/bin/odapkit-sim",
        "#!/bin/sh\necho no help >&2\nexit 3\n",
        true,
    );
    f.write("ws/public/a/bin/odapkit-plan", "#!/bin/sh\nexit 0\n", true);
    let (code, doc) = doctor_json(&f);
    assert_eq!(code, Some(1));
    let fails = failing(&doc);
    assert!(
        fails
            .iter()
            .any(|(r, m)| r == "tool.help" && m.contains("sim --help exits 3")),
        "{fails:?}"
    );
    assert!(
        fails
            .iter()
            .any(|(r, m)| r == "tool.help" && m.contains("plan --help prints nothing on stdout")),
        "{fails:?}"
    );
    let o = f.odw(&["doctor"], &[]);
    assert_eq!(o.status.code(), Some(1));
    assert!(
        stdout(&o).contains("fail tool.help sim: sim --help exits 3"),
        "{}",
        stdout(&o)
    );
}

#[test]
fn a_help_that_waits_for_input_or_hangs_fails_without_hanging_doctor() {
    let f = clean();
    f.write(
        "ws/public/a/bin/odapkit-sim",
        "#!/bin/sh\nread x\nsleep 60\n",
        true,
    );
    let start = std::time::Instant::now();
    let o = f.odw(&["doctor", "--json"], &[("ODAP_DOCTOR_TIMEOUT", "1")]);
    assert!(start.elapsed() < std::time::Duration::from_secs(10));
    assert_eq!(o.status.code(), Some(1));
    assert!(
        stdout(&o).contains("sim --help did not finish within 1 s"),
        "{}",
        stdout(&o)
    );
}

#[test]
fn usage_errors_exit_2_and_doctor_is_a_builtin() {
    let f = clean();
    let o = f.odw(&["doctor", "--nope"], &[]);
    assert_eq!(o.status.code(), Some(2));
    assert!(stderr(&o).contains("error input.invalid: unknown flag --nope"));
    let o = f.odw(&["doctor", "--help"], &[]);
    assert_eq!(o.status.code(), Some(0));
    assert!(stdout(&o).starts_with("odw doctor: "), "{}", stdout(&o));
    f.write(
        "ws/public/a/tools.toml",
        &tools_toml(&[("doctor", "stable")]),
        false,
    );
    f.plugin("ws/public/a/bin/odapkit-doctor", "evil-doctor");
    let o = f.odw(&["doctor", "--json"], &[]);
    assert!(!stdout(&o).contains("evil-doctor"));
    assert!(stdout(&o).contains("would shadow the built-in command odw doctor"));
}

#[test]
fn a_tool_found_only_on_path_is_never_run() {
    // Workspace roots are trusted (§3); a PATH executable is only run when someone asks for it by name.
    let f = clean();
    let marker = f.dir.join("ran");
    f.write(
        "path/odw-stray",
        &format!(
            "#!/bin/sh\n# odw stray: a stray tool\ntouch {}\necho usage\n",
            marker.display()
        ),
        true,
    );
    let (code, doc) = doctor_json(&f);
    assert_eq!(code, Some(0), "{doc}");
    assert!(!marker.exists(), "doctor ran a PATH tool");
    let stray = doc["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["id"] == "stray")
        .unwrap();
    assert_eq!(stray["verdict"], "skip");
    assert!(
        stray["message"]
            .as_str()
            .unwrap()
            .contains("not declared in a workspace root")
    );
}
