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
    pub message: String,
    /// The entry is not dispatched: its name is not a tool name, it would shadow a built-in, or its
    /// entry leaves the root. Other problems are reported and the tool still runs.
    pub blocking: bool,
}

/// Every problem of one entry for this front, blocking ones first.
pub fn entry_problems(e: &Entry, front: &str, builtins: &[&str]) -> Vec<Problem> {
    let mut out = Vec::new();
    let mut add = |blocking, message: String| out.push(Problem { message, blocking });
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
