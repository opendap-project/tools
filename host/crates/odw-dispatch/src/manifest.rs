//! Reading a root's `tools.toml` (`opendap.tools/v1`, command standard §3) and the workspace manifest
//! (`repos.tsv`). Pure parsing: the caller supplies the text.

use serde::Deserialize;

use crate::model::{Audience, Stability};

pub const TOOLS_SCHEMA: &str = "opendap.tools/v1";
pub const SUMMARY_MAX: usize = 100;

/// A `tools.toml` file.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ToolsFile {
    pub schema: String,
    pub front: String,
    pub tool: Vec<Entry>,
}

/// One `[[tool]]` entry.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Entry {
    pub name: String,
    pub summary: String,
    pub entry: String,
    pub audience: Audience,
    pub stability: Stability,
    pub replaces: Option<String>,
    /// The conformance test's read-only `--json` calls (arguments after the entry); dispatch ignores them.
    pub probe: Option<Probe>,
}

/// A `probe`: one call, or a list of calls when the tool's scripts answer different calls (HL-105).
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(untagged)]
pub enum Probe {
    One(Vec<String>),
    Many(Vec<Call>),
}

/// One call of a list: its arguments, or a table naming the script it reaches.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(untagged)]
pub enum Call {
    Args(Vec<String>),
    Reaching(Reaching),
}

/// `{ call = [...], reaches = "tools/x.py" }`: `reaches` is a path from the tools.toml folder.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Reaching {
    pub call: Vec<String>,
    pub reaches: Option<String>,
}

impl Probe {
    /// Every call's arguments, in order.
    pub fn calls(&self) -> Vec<Vec<String>> {
        match self {
            Probe::One(call) => vec![call.clone()],
            Probe::Many(calls) => calls
                .iter()
                .map(|c| match c {
                    Call::Args(a) => a.clone(),
                    Call::Reaching(r) => r.call.clone(),
                })
                .collect(),
        }
    }
}

/// Parse and check what the schema says (the JSON Schema is the reference; this mirrors it).
pub fn parse_tools(text: &str) -> Result<ToolsFile, String> {
    let file: ToolsFile = toml::from_str(text).map_err(|e| e.message().to_string())?;
    if file.schema != TOOLS_SCHEMA {
        return Err(format!(
            "schema is {:?}, expected {TOOLS_SCHEMA:?}",
            file.schema
        ));
    }
    if file.front != "odw" && file.front != "opendap" {
        return Err(format!(
            "front is {:?}, expected \"odw\" or \"opendap\"",
            file.front
        ));
    }
    if file.tool.is_empty() {
        return Err("no [[tool]] entry".into());
    }
    Ok(file)
}

/// A problem with one entry (conformance §8 item 1, the parts that need no execution).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Problem {
    /// The `odw doctor` rule: `tools.entry` (a blocking problem), `tools.summary`, `tools.audience`.
    pub rule: &'static str,
    pub message: String,
    /// The entry is not dispatched: its name is not a tool name, it would shadow a built-in, or its
    /// entry leaves the root. Other problems are reported and the tool still runs.
    pub blocking: bool,
}

/// Every problem of one entry for this front, blocking ones first.
pub fn entry_problems(e: &Entry, front: &str, builtins: &[&str]) -> Vec<Problem> {
    let mut out = Vec::new();
    let mut add = |blocking, message: String| {
        let rule = if blocking {
            "tools.entry"
        } else {
            "tools.summary"
        };
        out.push(Problem {
            rule,
            message,
            blocking,
        })
    };
    if !crate::model::valid_name(&e.name) {
        add(
            true,
            format!("tool name {:?} does not match ^[a-z][a-z0-9-]*$", e.name),
        );
    }
    if builtins.contains(&e.name.as_str()) {
        add(
            true,
            format!(
                "tool {} would shadow the built-in command {front} {}",
                e.name, e.name
            ),
        );
    }
    if e.entry.is_empty() || e.entry.starts_with('/') || e.entry.split('/').any(|p| p == "..") {
        add(
            true,
            format!(
                "tool {}: entry {:?} must be a path inside the root",
                e.name, e.entry
            ),
        );
    }
    let len = e.summary.chars().count();
    if len == 0 || len > SUMMARY_MAX {
        add(
            false,
            format!(
                "tool {}: the summary has {len} characters (1 to {SUMMARY_MAX})",
                e.name
            ),
        );
    }
    let fits = match e.audience {
        Audience::Maintainer => front == "odw",
        Audience::Developer | Audience::User => front == "opendap",
    };
    if !fits {
        let expected = if front == "odw" {
            "maintainer"
        } else {
            "developer or user"
        };
        add(
            false,
            format!(
                "tool {}: audience {} on front {front} (expected {expected})",
                e.name,
                e.audience.as_str()
            ),
        );
        if let Some(p) = out.last_mut() {
            p.rule = "tools.audience";
        }
    }
    out
}

/// The first blocking problem's message, if any.
pub fn blocking_problem(problems: &[Problem]) -> Option<&str> {
    problems
        .iter()
        .find(|p| p.blocking)
        .map(|p| p.message.as_str())
}

#[cfg(test)]
mod tests {
    use super::*;

    const GOOD: &str = r#"
schema = "opendap.tools/v1"
front = "odw"

[[tool]]
name = "plan"
summary = "A programme as data"
entry = "bin/odapkit-plan"
audience = "maintainer"
stability = "preview"
"#;

    fn entry(name: &str) -> Entry {
        parse_tools(&GOOD.replace("\"plan\"", &format!("{name:?}")))
            .unwrap()
            .tool[0]
            .clone()
    }

    #[test]
    fn a_good_file_parses() {
        let f = parse_tools(GOOD).unwrap();
        assert_eq!(f.front, "odw");
        assert_eq!(f.tool[0].stability, Stability::Preview);
        assert!(entry_problems(&f.tool[0], "odw", &["help", "check"]).is_empty());
    }

    #[test]
    fn a_probe_is_read_and_not_needed() {
        // HL-63: the conformance test's read-only --json call, declared with the tool.
        let f = parse_tools(&format!(
            "{GOOD}probe = [\"list\", \"--json\", \"{{root}}/x\"]\n"
        ))
        .unwrap();
        assert_eq!(
            f.tool[0].probe.as_ref().map(Probe::calls),
            Some(vec![vec!["list".to_string(), "--json".into(), "{root}/x".into()]])
        );
        assert_eq!(parse_tools(GOOD).unwrap().tool[0].probe, None);
    }

    #[test]
    fn a_probe_may_be_a_list_of_calls() {
        // HL-105: a tool whose scripts answer different calls declares each (craft: check and rules render).
        let f = parse_tools(&format!(
            "{GOOD}probe = [[\"check\", \"--json\", \"x.kicad_sym\"], [\"rules\", \"render\", \"s.md\", \"--check\", \"--json\"]]\n"
        ))
        .unwrap();
        let calls = f.tool[0].probe.as_ref().unwrap().calls();
        assert_eq!(calls.len(), 2);
        assert_eq!(calls[1][..2], ["rules".to_string(), "render".into()]);
        // One call, as before, is a list of one.
        let one = parse_tools(&format!("{GOOD}probe = [\"list\", \"--json\"]\n")).unwrap();
        assert_eq!(one.tool[0].probe.as_ref().unwrap().calls().len(), 1);
        assert!(parse_tools(&format!("{GOOD}probe = [[\"list\", 1]]\n")).is_err());
    }

    #[test]
    fn a_probe_call_may_name_the_script_it_reaches() {
        // HL-105: a call as a table, `reaches` the script it runs (from the tools.toml folder).
        let f = parse_tools(&format!(
            "{GOOD}probe = [[\"check\", \"--json\"], {{ call = [\"rules\", \"--json\"], reaches = \"tools/craft/craftrules.py\" }}]\n"
        ))
        .unwrap();
        let p = f.tool[0].probe.as_ref().unwrap();
        assert_eq!(p.calls()[1], ["rules".to_string(), "--json".into()]);
        assert!(parse_tools(&format!("{GOOD}probe = [{{ call = [\"x\"], colour = \"red\" }}]\n")).is_err());
    }

    #[test]
    fn schema_front_and_fields_are_checked() {
        assert!(parse_tools(&GOOD.replace("opendap.tools/v1", "opendap.tools/v2")).is_err());
        assert!(parse_tools(&GOOD.replace("front = \"odw\"", "front = \"odapkit\"")).is_err());
        assert!(parse_tools(&GOOD.replace("preview", "beta")).is_err());
        assert!(parse_tools(&format!("{GOOD}colour = \"red\"\n")).is_err());
        assert!(
            parse_tools("schema = \"opendap.tools/v1\"\nfront = \"odw\"\ntool = []\n").is_err()
        );
    }

    fn blocking(e: &Entry, front: &str) -> Option<String> {
        blocking_problem(&entry_problems(e, front, &["help", "check"])).map(String::from)
    }

    #[test]
    fn bad_names_and_builtins_block_the_entry() {
        assert!(
            blocking(&entry("Plan"), "odw")
                .unwrap()
                .contains("does not match")
        );
        assert!(
            blocking(&entry("help"), "odw")
                .unwrap()
                .contains("shadow the built-in command odw help")
        );
        assert!(
            blocking(&entry("check"), "odw")
                .unwrap()
                .contains("shadow the built-in")
        );
    }

    #[test]
    fn entries_stay_inside_their_root() {
        let mut e = entry("plan");
        e.entry = "../other/bin/x".into();
        assert!(blocking(&e, "odw").unwrap().contains("inside the root"));
        e.entry = "/usr/bin/x".into();
        assert!(blocking(&e, "odw").is_some());
    }

    #[test]
    fn long_summaries_and_wrong_audiences_are_reported_without_blocking() {
        let mut e = entry("plan");
        e.summary = "x".repeat(101);
        let p = entry_problems(&e, "odw", &[]);
        assert_eq!(p.len(), 1);
        assert!(!p[0].blocking && p[0].message.contains("101 characters"));
        let p = entry_problems(&entry("plan"), "opendap", &[]);
        assert!(
            !p[0].blocking
                && p[0]
                    .message
                    .contains("audience maintainer on front opendap")
        );
    }
}
