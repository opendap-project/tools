//! `odw doctor` (command standard §3, §6): every problem of the front's tools in one report. Discovery's
//! findings (an invalid `tools.toml`, an unsafe or shadowing entry, a summary outside 1–100 characters, an
//! audience that does not fit the front, a name declared twice) and the `--help` of each tool a workspace
//! root declares, run with stdin closed and a timeout (a tool found only on `PATH` is never run).
//! Dispatch itself stays quiet; this is where those problems are looked at.

use std::time::Duration;

use crate::discovery::{Host, Search, discover};
use crate::model::Front;
use crate::report::{CheckResult, Finding, Verdict};

/// Seconds a `--help` may take; `ODAP_DOCTOR_TIMEOUT` overrides it.
pub const HELP_TIMEOUT_SECS: u64 = 10;

/// The report; `env` is what a tool gets when the umbrella runs it (the exported roots).
pub fn doctor(
    front: &Front,
    host: &dyn Host,
    search: &Search,
    env: &[(String, String)],
) -> CheckResult {
    let catalog = discover(host, front, search);
    let mut findings: Vec<Finding> = catalog
        .warnings
        .iter()
        .map(|w| {
            let path = w.path.to_string_lossy().into_owned();
            Finding {
                id: path.clone(),
                rule: w.rule.into(),
                verdict: Verdict::Fail,
                message: w.message.clone(),
                path: Some(path),
            }
        })
        .collect();
    let secs = host
        .var("ODAP_DOCTOR_TIMEOUT")
        .and_then(|v| v.parse().ok())
        .unwrap_or(HELP_TIMEOUT_SECS);
    for tool in &catalog.tools {
        // Workspace roots are trusted (§3); a tool found only on PATH runs only when asked for by name.
        if tool.declared.is_none() {
            findings.push(Finding {
                id: tool.name.clone(),
                rule: "tool.help".into(),
                verdict: Verdict::Skip,
                message: format!("{} is not declared in a workspace root: not run", tool.name),
                path: Some(tool.entry.to_string_lossy().into_owned()),
            });
            continue;
        }
        let ran = host.run(
            &tool.entry,
            &["--help".into()],
            env,
            None,
            Duration::from_secs(secs),
        );
        let name = &tool.name;
        let problem = match ran {
            None => Some(format!(
                "{name} --help cannot be started ({})",
                tool.entry.display()
            )),
            Some(r) if r.timed_out => Some(format!("{name} --help did not finish within {secs} s")),
            Some(r) if r.status != Some(0) => Some(match r.status {
                Some(code) => format!("{name} --help exits {code}"),
                None => format!("{name} --help was ended by a signal"),
            }),
            Some(r) if r.stdout.trim().is_empty() => {
                Some(format!("{name} --help prints nothing on stdout"))
            }
            Some(_) => None,
        };
        findings.push(Finding {
            id: name.clone(),
            rule: "tool.help".into(),
            verdict: if problem.is_some() {
                Verdict::Fail
            } else {
                Verdict::Pass
            },
            message: problem
                .unwrap_or_else(|| format!("{name} --help exits 0 and prints its usage")),
            path: Some(tool.entry.to_string_lossy().into_owned()),
        });
    }
    CheckResult {
        tool: format!("{} doctor", front.name),
        repo: "workspace".into(),
        findings,
    }
}

/// `odw doctor --help`.
pub fn usage(front: &str) -> String {
    format!(
        "{front} doctor: Check the tools every root declares: duplicates, unsafe entries, summaries and --help\n\n\
         Usage: {front} doctor [--json]\n\n\
         Reports, as opendap.check-result/v1 with --json or one line per problem, every tools.toml that does\n\
         not parse, every entry that is not dispatched (a bad name, a built-in, an entry outside its root or\n\
         not executable), summaries outside 1-100 characters, audiences that do not fit the front, names\n\
         declared twice, and the --help of each tool a workspace root declares (exit 0, usage on stdout,\n\
         stdin closed, within ODAP_DOCTOR_TIMEOUT seconds, {HELP_TIMEOUT_SECS} by default); a tool found only on\n\
         PATH is listed as skip, never run. Quiet when everything passes.\n\n\
         Exit status: 0 no problem, 1 a problem, 2 usage error.\n\n\
         Examples:\n  {front} doctor\n  {front} doctor --json\n"
    )
}
