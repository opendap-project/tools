//! `odw check` (HL-61, command standard §7): a root's `check.toml` stage, from the current directory and the
//! changed paths, as text or one `opendap.check-result/v1`; exit 0 every step passes, 1 one fails, 2 usage
//! or `check.toml` error.

mod common;

use common::*;
use serde_json::Value;

const EMITTED: &str = r#"{"schema":"opendap.check-result/v1","tool":{"name":"t"},"subject":{"repo":"r"},"generated":"2026-10-28T00:00:00Z","results":[{"id":"a","verdict":"pass","message":"fine"},{"id":"b","verdict":"fail","message":"broken"}]}"#;

/// A root `ws/r` whose check.toml has fast (ok, src-only bad), full (emit) and release (extra) steps.
fn root() -> Fixture {
    let f = Fixture::new();
    let marker = f.dir.join("ran");
    f.write(
        "ws/r/check.toml",
        &format!(
            r#"schema = "opendap.check/v1"

[[stage.fast]]
name = "ok"
run = ["sh", "-c", "echo ok-out; echo ok-err >&2; touch {m}-ok"]

[[stage.fast]]
name = "bad"
run = ["sh", "-c", "exit 3"]
paths = ["src/**/*.rs", "Cargo.toml"]

[[stage.full]]
name = "emit"
run = ["cat", "emitted.json"]
emits = "check-result"

[[stage.release]]
name = "extra"
run = ["sh", "-c", "touch {m}-extra"]
"#,
            m = marker.display()
        ),
        false,
    );
    f.write("ws/r/src/deep/a.rs", "", false);
    f.write("ws/r/emitted.json", EMITTED, false);
    f.write("ws/r/.git/HEAD", "ref: refs/heads/main\n", false);
    f
}

fn json(o: &std::process::Output) -> Value {
    serde_json::from_str(&stdout(o)).unwrap_or_else(|e| panic!("{e}: {}", stdout(o)))
}

fn verdicts(doc: &Value) -> Vec<(String, String)> {
    doc["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| {
            (
                r["id"].as_str().unwrap().into(),
                r["verdict"].as_str().unwrap().into(),
            )
        })
        .collect()
}

fn pairs(v: &[(&str, &str)]) -> Vec<(String, String)> {
    v.iter()
        .map(|(a, b)| (a.to_string(), b.to_string()))
        .collect()
}

#[test]
fn the_fast_stage_runs_every_step_when_no_path_is_given() {
    let f = root();
    let o = f.odw_in(&f.dir.join("ws/r"), &["check", "--json"], &[]);
    assert_eq!(o.status.code(), Some(1));
    let doc = json(&o);
    assert_eq!(doc["schema"], "opendap.check-result/v1");
    assert_eq!(doc["tool"]["name"], "odw check");
    assert_eq!(verdicts(&doc), pairs(&[("ok", "pass"), ("bad", "fail")]));
    assert!(
        doc["results"][1]["message"]
            .as_str()
            .unwrap()
            .contains("exits 3")
    );
    // A step's own output goes to stderr: stdout carries the document only.
    assert!(stderr(&o).contains("ok-out") && stderr(&o).contains("ok-err"));
}

#[test]
fn paths_select_the_steps_and_the_root_is_found_from_a_subfolder() {
    let f = root();
    let sub = f.dir.join("ws/r/src/deep");
    let o = f.odw_in(
        &sub,
        &["check", "--stage", "fast", "--json", "../../docs/x.md"],
        &[],
    );
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    assert_eq!(verdicts(&json(&o)), pairs(&[("ok", "pass")]));
    let o = f.odw_in(&sub, &["check", "--json", "a.rs"], &[]);
    assert_eq!(o.status.code(), Some(1));
    assert_eq!(
        verdicts(&json(&o)),
        pairs(&[("ok", "pass"), ("bad", "fail")])
    );
    let o = f.odw_in(&f.dir.join("ws/r"), &["check", "--json", "Cargo.toml"], &[]);
    assert_eq!(
        verdicts(&json(&o)),
        pairs(&[("ok", "pass"), ("bad", "fail")])
    );
}

#[test]
fn emitted_check_results_are_collected_and_release_runs_full_first() {
    let f = root();
    let r = f.dir.join("ws/r");
    let o = f.odw_in(&r, &["check", "--stage", "full", "--json"], &[]);
    assert_eq!(
        o.status.code(),
        Some(1),
        "an emitted failure fails the step"
    );
    assert_eq!(
        verdicts(&json(&o)),
        pairs(&[("emit", "fail"), ("emit/a", "pass"), ("emit/b", "fail")])
    );
    let o = f.odw_in(&r, &["check", "--stage", "release", "--json"], &[]);
    let ids: Vec<String> = verdicts(&json(&o)).into_iter().map(|(i, _)| i).collect();
    assert_eq!(ids, ["emit", "emit/a", "emit/b", "extra"]);
    assert!(f.dir.join("ran-extra").exists());
}

#[test]
fn dry_run_names_the_steps_and_runs_nothing() {
    let f = root();
    let o = f.odw_in(&f.dir.join("ws/r"), &["check", "--dry-run"], &[]);
    assert_eq!(o.status.code(), Some(0));
    let out = stdout(&o);
    let lines: Vec<&str> = out.lines().collect();
    assert_eq!(lines.len(), 2, "{out}");
    assert!(
        lines[0].starts_with("would run ok: sh -c 'echo ok-out;"),
        "{out}"
    );
    assert_eq!(lines[1], "would run bad: sh -c 'exit 3'");
    assert!(!f.dir.join("ran-ok").exists());
}

#[test]
fn text_output_names_failures_only() {
    let f = root();
    let o = f.odw_in(&f.dir.join("ws/r"), &["check"], &[]);
    assert_eq!(o.status.code(), Some(1));
    assert_eq!(stdout(&o), "fail check.step bad: bad exits 3\n");
    assert!(f.dir.join("ran-ok").exists());
}

#[test]
fn usage_and_check_toml_errors_exit_2() {
    let f = root();
    let r = f.dir.join("ws/r");
    let o = f.odw_in(&r, &["check", "--stage", "nightly"], &[]);
    assert_eq!(o.status.code(), Some(2));
    assert!(stderr(&o).contains("error input.invalid: unknown stage nightly"));
    let o = f.odw_in(&f.dir, &["check", "--json"], &[]);
    assert_eq!(o.status.code(), Some(2));
    assert!(stderr(&o).contains("error check.not_found:"));
    assert_eq!(json(&o)["error"]["code"], "check.not_found");
    f.write(
        "ws/r/check.toml",
        "schema = \"opendap.check/v1\"\n[[stage.fast]]\nname = \"x\"\n",
        false,
    );
    let o = f.odw_in(&r, &["check"], &[]);
    assert_eq!(o.status.code(), Some(2));
    assert!(stderr(&o).contains("error check.invalid:") && stderr(&o).contains("check.toml"));
    let o = f.odw_in(&r, &["check", "--stage", "full"], &[]);
    assert_eq!(
        o.status.code(),
        Some(2),
        "a stage the file does not declare"
    );
    let o = f.odw_in(&r, &["check", "--help"], &[]);
    assert_eq!(o.status.code(), Some(0));
    assert!(stdout(&o).starts_with("odw check: "));
}

#[test]
fn a_check_toml_above_the_repository_is_never_used() {
    // The search stops at the repository's top (its .git): a check.toml planted above it never runs.
    let f = root();
    let marker = f.dir.join("planted");
    f.write(
        "ws/check.toml",
        &format!("schema = \"opendap.check/v1\"\n[[stage.fast]]\nname = \"x\"\nrun = [\"touch\", \"{}\"]\n", marker.display()),
        false,
    );
    f.write("ws/other/.git", "gitdir: elsewhere\n", false);
    f.write("ws/other/src/a.rs", "", false);
    let o = f.odw_in(&f.dir.join("ws/other/src"), &["check"], &[]);
    assert_eq!(o.status.code(), Some(2));
    assert!(
        stderr(&o).contains("error check.not_found:"),
        "{}",
        stderr(&o)
    );
    assert!(!marker.exists());
}
