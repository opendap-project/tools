//! The help listing (command standard §4): text grouped by stability, or `opendap.tools-list/v1`.

use serde_json::{Value, json};

use crate::model::{Catalog, Front, Stability, Tool};

pub const LIST_SCHEMA: &str = "opendap.tools-list/v1";

/// The tools a listing shows: `internal` ones only with `--all`.
fn shown(tool: &Tool, all: bool) -> bool {
    all || tool
        .declared
        .as_ref()
        .is_none_or(|d| d.stability != Stability::Internal)
}

/// The text listing, on stdout.
pub fn text(front: &Front, catalog: &Catalog, all: bool) -> String {
    let f = front.name;
    let mut out = format!(
        "Usage: {f} TOOL [ARGS…]   ({f} help TOOL for a tool's help; {f} help --json; --all adds internal tools)\n"
    );
    let groups = [
        Stability::Stable,
        Stability::Preview,
        Stability::Deprecated,
        Stability::Internal,
    ];
    for stability in groups {
        let tools: Vec<&Tool> = catalog
            .tools
            .iter()
            .filter(|t| {
                shown(t, all)
                    && t.declared
                        .as_ref()
                        .is_some_and(|d| d.stability == stability)
            })
            .collect();
        if tools.is_empty() {
            continue;
        }
        out += &format!("\n{}:\n", stability.as_str());
        for t in tools {
            let d = t.declared.as_ref().expect("declared");
            let mut tail = format!("{}, {}", d.audience.as_str(), d.stability.as_str());
            if stability == Stability::Deprecated
                && let Some(r) = catalog.replacement_of(&t.name)
            {
                tail += &format!("; use {}", r.name);
            }
            out += &format!("  {:<16} {}  ({tail})\n", t.name, t.summary);
        }
    }
    let undeclared: Vec<&Tool> = catalog
        .tools
        .iter()
        .filter(|t| t.declared.is_none())
        .collect();
    if !undeclared.is_empty() {
        out += "\nfound on PATH (audience and stability unknown):\n";
        for t in undeclared {
            out += &format!("  {:<16} {}\n", t.name, t.summary);
        }
    }
    out
}

/// One listing item.
pub fn item(tool: &Tool) -> Value {
    let d = tool.declared.as_ref();
    json!({
        "name": tool.name,
        "summary": tool.summary,
        "audience": d.map_or("unknown", |d| d.audience.as_str()),
        "stability": d.map_or("unknown", |d| d.stability.as_str()),
        "replaces": d.and_then(|d| d.replaces.clone()),
        "entry": tool.entry.to_string_lossy(),
        "root": d.map_or(String::new(), |d| d.root.clone()),
    })
}

/// `opendap.tools-list/v1`, on stdout.
pub fn json(catalog: &Catalog, all: bool) -> String {
    let items: Vec<Value> = catalog
        .tools
        .iter()
        .filter(|t| shown(t, all))
        .map(item)
        .collect();
    json!({"schema": LIST_SCHEMA, "items": items}).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{Audience, Declared};

    fn tool(name: &str, stability: Option<Stability>, replaces: Option<&str>) -> Tool {
        Tool {
            name: name.into(),
            summary: format!("{name} summary"),
            entry: format!("/r/bin/{name}").into(),
            declared: stability.map(|stability| Declared {
                audience: Audience::Maintainer,
                stability,
                replaces: replaces.map(String::from),
                root: "public/r".into(),
            }),
        }
    }

    fn catalog() -> Catalog {
        Catalog {
            tools: vec![
                tool("plan", Some(Stability::Preview), None),
                tool("old", Some(Stability::Deprecated), None),
                tool("new", Some(Stability::Stable), Some("old")),
                tool("secret", Some(Stability::Internal), None),
                tool("loose", None, None),
            ],
            warnings: vec![],
        }
    }

    #[test]
    fn text_groups_by_stability_and_hides_internal_tools() {
        let t = text(&Front::ODW, &catalog(), false);
        let stable = t.find("\nstable:\n").unwrap();
        let preview = t.find("\npreview:\n").unwrap();
        let deprecated = t.find("\ndeprecated:\n").unwrap();
        assert!(stable < preview && preview < deprecated);
        assert!(t.contains("  plan             plan summary  (maintainer, preview)\n"));
        assert!(t.contains("(maintainer, deprecated; use new)"));
        assert!(t.contains("found on PATH (audience and stability unknown):\n  loose"));
        assert!(!t.contains("secret"));
        assert!(text(&Front::ODW, &catalog(), true).contains("\ninternal:\n  secret"));
    }

    #[test]
    fn json_items_carry_every_field() {
        let v: Value = serde_json::from_str(&json(&catalog(), false)).unwrap();
        assert_eq!(v["schema"], LIST_SCHEMA);
        let items = v["items"].as_array().unwrap();
        assert_eq!(items.len(), 4);
        assert_eq!(items[0]["root"], "public/r");
        assert_eq!(items[0]["replaces"], Value::Null);
        assert_eq!(items[2]["replaces"], "old");
        assert_eq!(items[3]["audience"], "unknown");
        assert_eq!(items[3]["stability"], "unknown");
        assert_eq!(items[3]["root"], "");
        let all: Value = serde_json::from_str(&json(&catalog(), true)).unwrap();
        assert_eq!(all["items"].as_array().unwrap().len(), 5);
    }
}
