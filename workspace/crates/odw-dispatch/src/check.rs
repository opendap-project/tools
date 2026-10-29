//! `odw check` (command standard §7): run a root's `check.toml` stage. The root is the nearest folder at or
//! above the current directory that holds a `check.toml`, inside its git repository; a step runs when the stage selects it and its
//! `paths` globs match a changed path (every step when no path is given or it lists none). Each step runs in
//! the root with stdin closed, its output passed to stderr; a step that `emits = "check-result"` has its
//! document's results collected under its name. The report is one `opendap.check-result/v1`.

use std::path::{Component, Path, PathBuf};
use std::time::Duration;

use serde::Deserialize;

use crate::discovery::Host;
use crate::error::DispatchError;
use crate::report::{CheckResult, Finding, Verdict};

pub const CHECK_SCHEMA: &str = "opendap.check/v1";
pub const FILE: &str = "check.toml";
pub const STAGES: [&str; 3] = ["fast", "full", "release"];

/// One step of a stage (`opendap.check/v1`).
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Step {
    pub name: String,
    pub run: Vec<String>,
    pub paths: Option<Vec<String>>,
    pub emits: Option<String>,
    pub verifies: Option<Vec<String>>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Stages {
    fast: Option<Vec<Step>>,
    full: Option<Vec<Step>>,
    release: Option<Vec<Step>>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CheckFile {
    schema: String,
    stage: Stages,
}

/// `odw check [--stage S] [--json] [--dry-run] [PATHS…]`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Args {
    pub stage: String,
    pub json: bool,
    pub dry_run: bool,
    pub help: bool,
    pub paths: Vec<String>,
}

fn usage_error(front: &str, message: String) -> DispatchError {
    let mut e = DispatchError::usage(front, message);
    e.hint = format!("the usage is in {front} check --help");
    e.fix = format!("{front} check --help");
    e
}

pub fn parse_args(front: &str, args: &[String]) -> Result<Args, DispatchError> {
    let mut a = Args {
        stage: "fast".into(),
        json: false,
        dry_run: false,
        help: false,
        paths: vec![],
    };
    let mut it = args.iter();
    while let Some(x) = it.next() {
        match x.as_str() {
            "--json" => a.json = true,
            "--dry-run" => a.dry_run = true,
            "-h" | "--help" => a.help = true,
            "--" => a.paths.extend(it.by_ref().cloned()),
            "--stage" => match it.next() {
                Some(s) => a.stage = s.clone(),
                None => {
                    return Err(usage_error(
                        front,
                        "--stage takes fast, full or release".into(),
                    ));
                }
            },
            s if s.starts_with("--stage=") => a.stage = s["--stage=".len()..].into(),
            f if f.starts_with('-') && f != "-" => {
                return Err(usage_error(front, format!("unknown flag {f}")));
            }
            p => a.paths.push(p.into()),
        }
    }
    if !STAGES.contains(&a.stage.as_str()) {
        return Err(usage_error(
            front,
            format!("unknown stage {} (fast, full or release)", a.stage),
        ));
    }
    Ok(a)
}

fn check_error(code: &'static str, message: String, hint: &str, fix: String) -> DispatchError {
    DispatchError {
        code,
        message,
        hint: hint.into(),
        fix,
        status: 2,
    }
}

/// The nearest folder at or above `cwd` that holds a `check.toml`, inside the git repository `cwd` is in:
/// the search stops at the repository's top (the folder holding `.git`), so a `check.toml` above it never
/// runs; outside a repository there is no root.
pub fn find_root(host: &dyn Host, cwd: &Path) -> Option<PathBuf> {
    let top = cwd
        .ancestors()
        .find(|d| host.list_dir(d).iter().any(|n| n == ".git"))?;
    cwd.ancestors()
        .take_while(|d| d.starts_with(top))
        .find(|d| host.read_to_string(&d.join(FILE)).is_some())
        .map(Path::to_path_buf)
}

fn invalid(file: &Path, message: impl std::fmt::Display) -> DispatchError {
    check_error(
        "check.invalid",
        format!("{}: not a valid {CHECK_SCHEMA}: {message}", file.display()),
        "check.toml follows schemas/check.schema.json (command standard §7)",
        format!("$EDITOR {}", file.display()),
    )
}

/// The steps `stage` runs, in order: `release` runs `full`, then its own (command standard §7: "full, plus").
pub fn load(host: &dyn Host, root: &Path, stage: &str) -> Result<Vec<Step>, DispatchError> {
    let file = root.join(FILE);
    let text = host.read_to_string(&file).unwrap_or_default();
    let doc: CheckFile = toml::from_str(&text).map_err(|e| invalid(&file, e.message()))?;
    if doc.schema != CHECK_SCHEMA {
        return Err(invalid(&file, format!("schema is {:?}", doc.schema)));
    }
    let lists = [
        ("fast", &doc.stage.fast),
        ("full", &doc.stage.full),
        ("release", &doc.stage.release),
    ];
    for (name, list) in lists {
        for s in list.iter().flatten() {
            if !crate::model::valid_name(&s.name) || s.run.is_empty() {
                return Err(invalid(
                    &file,
                    format!("stage {name}: step {:?} needs a name and a run", s.name),
                ));
            }
            if s.emits
                .as_deref()
                .is_some_and(|e| e != "check-result" && e != "none")
            {
                return Err(invalid(
                    &file,
                    format!("step {}: emits is check-result or none", s.name),
                ));
            }
        }
        if list.as_ref().is_some_and(Vec::is_empty) {
            return Err(invalid(&file, format!("stage {name} lists no step")));
        }
    }
    let wanted: &[&Option<Vec<Step>>] = match stage {
        "fast" => &[&doc.stage.fast],
        "full" => &[&doc.stage.full],
        _ => &[&doc.stage.full, &doc.stage.release],
    };
    let steps: Vec<Step> = wanted
        .iter()
        .flat_map(|l| l.iter().flatten().cloned())
        .collect();
    if steps.is_empty() {
        return Err(check_error(
            "check.no_stage",
            format!("{} declares no {stage} stage", file.display()),
            "a stage runs only the steps check.toml lists under it",
            format!("$EDITOR {}", file.display()),
        ));
    }
    Ok(steps)
}

/// `path` (relative to `cwd`, or absolute) as a path inside `root`, or `None` when it is outside.
pub fn relative(root: &Path, cwd: &Path, path: &str) -> Option<String> {
    let mut out = PathBuf::new();
    for c in cwd.join(path).components() {
        match c {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            other => out.push(other),
        }
    }
    out.strip_prefix(root)
        .ok()
        .map(|p| p.to_string_lossy().into_owned())
}

/// A `paths` glob: `*` and `?` stay within one folder, `**` crosses folders (`a/**/b` also matches `a/b`).
pub fn glob(pattern: &str, path: &str) -> bool {
    fn m(p: &[u8], s: &[u8]) -> bool {
        match p {
            [] => s.is_empty(),
            [b'*', b'*', rest @ ..] => {
                let after = rest.strip_prefix(b"/").unwrap_or(rest);
                (0..=s.len()).any(|i| m(rest, &s[i..]) || m(after, &s[i..]))
            }
            [b'*', rest @ ..] => (0..=s.len())
                .take_while(|&i| i == 0 || s[i - 1] != b'/')
                .any(|i| m(rest, &s[i..])),
            [b'?', rest @ ..] => s.first().is_some_and(|&c| c != b'/') && m(rest, &s[1..]),
            [c, rest @ ..] => s.first() == Some(c) && m(rest, &s[1..]),
        }
    }
    m(pattern.as_bytes(), path.as_bytes())
}

/// The steps a change selects: all of them without changed paths; else those with no `paths` or a match.
pub fn select(steps: Vec<Step>, changed: Option<&[String]>) -> Vec<Step> {
    let Some(changed) = changed else { return steps };
    steps
        .into_iter()
        .filter(|s| match &s.paths {
            None => true,
            Some(globs) if globs.is_empty() => true,
            Some(globs) => changed.iter().any(|c| globs.iter().any(|g| glob(g, c))),
        })
        .collect()
}

/// An argument as a shell reads it back: bare when safe, else single-quoted.
fn quoted(arg: &str) -> String {
    let safe = !arg.is_empty()
        && arg
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_./=:,+@%".contains(&b));
    if safe {
        arg.into()
    } else {
        format!("'{}'", arg.replace('\'', "'\\''"))
    }
}

pub fn dry_run(steps: &[Step]) -> String {
    steps
        .iter()
        .map(|s| {
            format!(
                "would run {}: {}\n",
                s.name,
                s.run
                    .iter()
                    .map(|a| quoted(a))
                    .collect::<Vec<_>>()
                    .join(" ")
            )
        })
        .collect()
}

/// Run one step; its findings: the step itself, then the results it emitted.
fn run_step(host: &dyn Host, root: &Path, step: &Step) -> Vec<Finding> {
    host.log(&format!("odw check: {}", step.name));
    let ran = host.run(
        Path::new(&step.run[0]),
        &step.run[1..],
        &[],
        Some(root),
        Duration::MAX,
    );
    let finding = |verdict, message: String| Finding {
        id: step.name.clone(),
        rule: "check.step".into(),
        verdict,
        message,
        path: Some(FILE.into()),
    };
    let Some(ran) = ran else {
        return vec![finding(
            Verdict::Fail,
            format!("{} cannot start {}", step.name, step.run[0]),
        )];
    };
    let emits = step.emits.as_deref() == Some("check-result");
    if !emits && !ran.stdout.is_empty() {
        host.log(ran.stdout.trim_end());
    }
    if !ran.stderr.is_empty() {
        host.log(ran.stderr.trim_end());
    }
    let mut nested = vec![];
    let mut problem = match ran.status {
        Some(0) => None,
        Some(code) => Some(format!("{} exits {code}", step.name)),
        None => Some(format!("{} was ended by a signal", step.name)),
    };
    if emits {
        match crate::report::parse_results(&ran.stdout) {
            Some(found) => nested = found,
            None => {
                problem = problem.or(Some(format!(
                    "{} emits no {} document",
                    step.name,
                    crate::report::CHECK_RESULT_SCHEMA
                )))
            }
        }
    }
    if problem.is_none()
        && nested
            .iter()
            .any(|f| matches!(f.verdict, Verdict::Fail | Verdict::Error))
    {
        problem = Some(format!("{} reports a failing result", step.name));
    }
    let mut out = vec![match problem {
        Some(p) => finding(Verdict::Fail, p),
        None => finding(Verdict::Pass, format!("{} passes", step.name)),
    }];
    out.extend(nested.into_iter().map(|mut f| {
        f.id = format!("{}/{}", step.name, f.id);
        f
    }));
    out
}

/// The whole command: what to print on stdout and the exit status, or the error.
pub fn check(front: &str, host: &dyn Host, a: &Args) -> Result<(String, u8), DispatchError> {
    if a.help {
        return Ok((usage(front), 0));
    }
    let not_found = |cwd: &str| {
        check_error(
            "check.not_found",
            format!("no {FILE} in {cwd} or above"),
            "a root declares its lifecycle checks in check.toml (opendap.check/v1); run odw check inside it",
            format!("{front} check --help"),
        )
    };
    let cwd = host
        .current_dir()
        .ok_or_else(|| not_found("the current directory"))?;
    let root = find_root(host, &cwd).ok_or_else(|| not_found(&cwd.display().to_string()))?;
    let steps = load(host, &root, &a.stage)?;
    let changed: Vec<String> = a
        .paths
        .iter()
        .filter_map(|p| relative(&root, &cwd, p))
        .collect();
    let steps = select(steps, (!a.paths.is_empty()).then_some(&changed[..]));
    if a.dry_run {
        return Ok((dry_run(&steps), 0));
    }
    let findings = steps
        .iter()
        .flat_map(|s| run_step(host, &root, s))
        .collect();
    let report = CheckResult {
        tool: format!("{front} check"),
        repo: root.display().to_string(),
        findings,
    };
    let stdout = if a.json {
        report.json(&crate::report::now()) + "\n"
    } else {
        report.text()
    };
    Ok((stdout, u8::from(report.failed())))
}

pub fn usage(front: &str) -> String {
    format!(
        "{front} check: Run a root's check.toml stage on the changed paths and collect the results\n\n\
         Usage: {front} check [--stage fast|full|release] [--json] [--dry-run] [PATHS…]\n\n\
         The root is the nearest folder at or above the current directory that holds check.toml\n\
         (opendap.check/v1), inside the git repository: one above the repository never runs. The stage\n\
         (fast by default; release runs full, then release) runs the steps whose paths globs match a\n\
         changed path, and every step when no path is given or a step lists none. Steps\n\
         run in the root with stdin closed; their output goes to stderr. A step that emits check-result has its\n\
         results collected under its name. --json prints one opendap.check-result/v1; text names the failures.\n\
         --dry-run names the steps and runs nothing.\n\n\
         Exit status: 0 every step passes, 1 one fails, 2 usage or check.toml error.\n\n\
         Examples:\n  {front} check --stage fast $(git diff --cached --name-only)\n  {front} check --stage full --json\n  \
         {front} check --dry-run\n"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn globs_keep_star_within_a_folder_and_double_star_across() {
        assert!(glob("src/**/*.rs", "src/a.rs") && glob("src/**/*.rs", "src/x/y/a.rs"));
        assert!(!glob("src/*.rs", "src/x/a.rs") && glob("src/*.rs", "src/a.rs"));
        assert!(glob("schemas/**", "schemas/a/b.json") && glob("Cargo.toml", "Cargo.toml"));
        assert!(!glob("Cargo.toml", "cli/Cargo.toml") && glob("**/Cargo.toml", "cli/Cargo.toml"));
        assert!(glob("a?.md", "ab.md") && !glob("a?.md", "a/.md"));
    }

    #[test]
    fn paths_are_taken_inside_the_root() {
        let (root, cwd) = (Path::new("/w/r"), Path::new("/w/r/src"));
        assert_eq!(relative(root, cwd, "a.rs").as_deref(), Some("src/a.rs"));
        assert_eq!(
            relative(root, cwd, "../docs/./x.md").as_deref(),
            Some("docs/x.md")
        );
        assert_eq!(relative(root, cwd, "../../other/x"), None);
        assert_eq!(
            relative(root, cwd, "/w/r/Cargo.toml").as_deref(),
            Some("Cargo.toml")
        );
    }

    #[test]
    fn arguments_and_stages() {
        let a =
            |v: &[&str]| parse_args("odw", &v.iter().map(|s| s.to_string()).collect::<Vec<_>>());
        let got = a(&["--stage=full", "--json", "x", "--", "--y"]).unwrap();
        assert_eq!(
            (got.stage.as_str(), got.json, got.paths),
            ("full", true, vec!["x".into(), "--y".into()])
        );
        assert_eq!(a(&[]).unwrap().stage, "fast");
        assert_eq!(a(&["--nope"]).unwrap_err().status, 2);
        assert_eq!(a(&["--stage"]).unwrap_err().status, 2);
        assert_eq!(quoted("exit 3"), "'exit 3'");
        assert_eq!(quoted("--json"), "--json");
    }
}
