//! The umbrella's own command line (command standard §2, §4) and what each invocation does, as data:
//! the binary prints it or `exec`s it. No I/O here.

use std::path::{Path, PathBuf};

use crate::discovery::{Host, Search, discover, tool_folders};
use crate::error::DispatchError;
use crate::help;
use crate::model::{Front, valid_name};
use crate::workspace::{ResolvedRoot, root_env_var, roots};

/// A parsed invocation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Command {
    /// `<front>`, `<front> help`, `--help`, `--json`, `--all`.
    List { json: bool, all: bool },
    /// `<front> help TOOL`: the tool's `--help`.
    ToolHelp(String),
    /// A built-in that is specified but not implemented yet (`odw check`, §7).
    Unavailable(String),
    /// `<front> TOOL ARGS…`.
    Run { name: String, args: Vec<String> },
}

/// What the binary does.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// Print on stdout (and warnings on stderr), exit 0.
    Print {
        stdout: String,
        warnings: Vec<String>,
    },
    /// Replace the process with the tool; `env` is added to the environment.
    Exec {
        entry: PathBuf,
        args: Vec<String>,
        env: Vec<(String, String)>,
    },
    /// Print the error (stderr; with `json`, also the document on stdout) and exit with its status.
    Fail { error: DispatchError, json: bool },
}

/// Parse the arguments after the umbrella's name. `json` in the error says whether `--json` was asked.
pub fn parse(front: &Front, args: &[String]) -> Result<Command, (DispatchError, bool)> {
    let Some(first) = args.first() else {
        return Ok(Command::List {
            json: false,
            all: false,
        });
    };
    let wants_json = args.iter().any(|a| a == "--json");
    let fail = |e: DispatchError| Err((e, wants_json));
    if first == "help" || first.starts_with('-') {
        let rest = if first == "help" { &args[1..] } else { args };
        let (mut json, mut all, mut tool) = (false, false, None::<&String>);
        for a in rest {
            match a.as_str() {
                "--json" => json = true,
                "--all" => all = true,
                "-h" | "--help" => {}
                f if f.starts_with('-') => {
                    return fail(DispatchError::usage(
                        front.name,
                        format!("unknown flag {f}"),
                    ));
                }
                _ if first != "help" => {
                    return fail(DispatchError::usage(
                        front.name,
                        "a tool comes before its arguments",
                    ));
                }
                _ if tool.is_some() => {
                    return fail(DispatchError::usage(front.name, "help takes one tool"));
                }
                _ => tool = Some(a),
            }
        }
        return match tool {
            None => Ok(Command::List { json, all }),
            Some(_) if json || all => fail(DispatchError::usage(
                front.name,
                "help --json and --all take no tool",
            )),
            Some(t) => Ok(Command::ToolHelp(t.clone())),
        };
    }
    if front.builtins.contains(&first.as_str()) {
        return Ok(Command::Unavailable(first.clone()));
    }
    Ok(Command::Run {
        name: first.clone(),
        args: args[1..].to_vec(),
    })
}

/// Decide what an invocation does on this host.
pub fn run(front: &Front, host: &dyn Host, search: &Search, args: &[String]) -> Outcome {
    let command = match parse(front, args) {
        Ok(c) => c,
        Err((error, json)) => return Outcome::Fail { error, json },
    };
    match command {
        Command::List { json, all } => {
            let catalog = discover(host, front, search);
            let warnings = catalog
                .warnings
                .iter()
                .map(|w| {
                    format!(
                        "{}: warning: {}: {}",
                        front.name,
                        w.path.display(),
                        w.message
                    )
                })
                .collect();
            let stdout = if json {
                help::json(&catalog, all) + "\n"
            } else {
                help::text(front, &catalog, all)
            };
            Outcome::Print { stdout, warnings }
        }
        Command::ToolHelp(name) => exec(front, host, search, &name, vec!["--help".into()]),
        Command::Unavailable(name) => Outcome::Fail {
            error: DispatchError {
                code: "command.unavailable",
                message: format!(
                    "{} {name} is specified (command standard §7) but not implemented yet",
                    front.name
                ),
                hint: "run the root's checks through pre-commit until it exists".into(),
                fix: "pre-commit run --all-files".into(),
                status: 1,
            },
            json: false,
        },
        Command::Run { name, args } => exec(front, host, search, &name, args),
    }
}

fn exec(front: &Front, host: &dyn Host, search: &Search, name: &str, args: Vec<String>) -> Outcome {
    // §2: a name with `/`, starting with `.` or `-`, or outside the pattern is never looked up.
    if !valid_name(name) {
        return Outcome::Fail {
            error: DispatchError::no_tool(front.name, name),
            json: false,
        };
    }
    match discover(host, front, search).get(name) {
        Some(tool) => Outcome::Exec {
            entry: tool.entry.clone(),
            args,
            env: exported_roots(front, host, search),
        },
        None => Outcome::Fail {
            error: DispatchError::no_tool(front.name, name),
            json: false,
        },
    }
}

/// `ODAP_<NAME>_DIR` for the front's export roots that are not set already, so a plugin finds the
/// shared helpers (toolkit-host) the way the umbrella found it: a root of that name, else that folder
/// of a root whose tool folders hold `tools.toml`. Such a root is exported too (`ODAP_TOOLS_DIR`).
fn exported_roots(front: &Front, host: &dyn Host, search: &Search) -> Vec<(String, String)> {
    let all: Vec<ResolvedRoot> = roots(host, search.workspace.as_deref());
    let folders: Vec<(&ResolvedRoot, Vec<String>)> = all
        .iter()
        .map(|r| (r, tool_folders(host, r)))
        .filter(|(_, f)| !f.is_empty())
        .collect();
    let mut out: Vec<(String, String)> = Vec::new();
    let mut add = |var: String, dir: &Path| {
        if !host.var(&var).is_some_and(|v| !v.is_empty()) && !out.iter().any(|(v, _)| *v == var) {
            out.push((var, dir.to_string_lossy().into_owned()));
        }
    };
    for e in front.export_roots {
        let var = root_env_var(e.name);
        if let Some(r) = all.iter().find(|r| r.root.name == e.name) {
            add(var, &r.dir);
        } else if let Some((r, _)) = folders
            .iter()
            .find(|(_, f)| f.iter().any(|n| n == e.folder))
        {
            add(var, &r.dir.join(e.folder));
        }
    }
    for (r, _) in &folders {
        add(root_env_var(&r.root.name), &r.dir);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(args: &[&str]) -> Result<Command, (DispatchError, bool)> {
        parse(
            &Front::ODW,
            &args.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
        )
    }

    fn usage(args: &[&str]) -> (String, bool) {
        let (e, json) = p(args).unwrap_err();
        assert_eq!((e.code, e.status), ("input.invalid", 2), "{args:?}");
        (e.message, json)
    }

    #[test]
    fn listing_forms() {
        let list = |json, all| Ok(Command::List { json, all });
        assert_eq!(p(&[]), list(false, false));
        assert_eq!(p(&["help"]), list(false, false));
        assert_eq!(p(&["--help"]), list(false, false));
        assert_eq!(p(&["-h"]), list(false, false));
        assert_eq!(p(&["--json"]), list(true, false));
        assert_eq!(p(&["help", "--json"]), list(true, false));
        assert_eq!(p(&["help", "--all", "--json"]), list(true, true));
    }

    #[test]
    fn tool_help_and_runs() {
        assert_eq!(p(&["help", "plan"]), Ok(Command::ToolHelp("plan".into())));
        assert_eq!(
            p(&["plan", "list", "--json", "--x"]),
            Ok(Command::Run {
                name: "plan".into(),
                args: vec!["list".into(), "--json".into(), "--x".into()]
            })
        );
        assert_eq!(
            p(&["check", "--stage", "fast"]),
            Ok(Command::Unavailable("check".into()))
        );
    }

    #[test]
    fn usage_errors_exit_2_and_keep_the_json_request() {
        assert_eq!(usage(&["--nope"]), ("unknown flag --nope".into(), false));
        assert!(usage(&["--json", "--nope"]).1);
        assert_eq!(usage(&["help", "a", "b"]).0, "help takes one tool");
        assert_eq!(
            usage(&["help", "--json", "plan"]).0,
            "help --json and --all take no tool"
        );
        assert_eq!(
            usage(&["--json", "plan"]).0,
            "a tool comes before its arguments"
        );
    }
}
