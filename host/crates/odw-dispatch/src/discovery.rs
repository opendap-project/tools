//! Discovery (command standard §3): the workspace roots' `tools.toml`, then `PATH`. It never matches
//! repository or folder names. The file system and the environment come through the [`Host`] port.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use crate::manifest::{self, Root};
use crate::model::{Catalog, Declared, Front, Tool, Warning, valid_name};

/// What discovery needs from the machine.
pub trait Host {
    fn read_to_string(&self, path: &Path) -> Option<String>;
    /// A regular file (after links) with an execute bit.
    fn is_executable(&self, path: &Path) -> bool;
    /// The names in a folder, sorted; empty when it cannot be read.
    fn list_dir(&self, path: &Path) -> Vec<String>;
    fn var(&self, name: &str) -> Option<String>;
    /// The start of a file as text (a plugin's header); `None` for a binary.
    fn read_head(&self, path: &Path) -> Option<String> {
        self.read_to_string(path)
    }
}

/// Where to look: the workspace (when there is one) and the `PATH` folders, in order.
#[derive(Debug, Clone, Default)]
pub struct Search {
    pub workspace: Option<PathBuf>,
    pub path: Vec<PathBuf>,
}

/// A workspace root resolved to a folder (`ODAP_<NAME>_DIR` overrides the manifest's path).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedRoot {
    pub root: Root,
    pub dir: PathBuf,
}

/// The roots of the workspace manifest, in manifest order; empty outside a workspace.
pub fn roots(host: &dyn Host, workspace: Option<&Path>) -> Vec<ResolvedRoot> {
    let Some(ws) = workspace else {
        return Vec::new();
    };
    let Some(text) = host.read_to_string(&ws.join("repos.tsv")) else {
        return Vec::new();
    };
    manifest::parse_repos(&text)
        .into_iter()
        .map(|root| {
            let dir = match host.var(&manifest::root_env_var(&root.name)) {
                Some(v) if !v.is_empty() => PathBuf::from(v),
                _ => ws.join(&root.path),
            };
            ResolvedRoot { root, dir }
        })
        .collect()
}

/// Every tool of the front, first match first.
pub fn discover(host: &dyn Host, front: &Front, search: &Search) -> Catalog {
    let mut catalog = Catalog::default();
    for r in roots(host, search.workspace.as_deref()) {
        read_root(host, front, &r, &mut catalog);
    }
    let mut seen: HashSet<String> = catalog.tools.iter().map(|t| t.name.clone()).collect();
    for dir in &search.path {
        for (name, entry) in path_plugins(host, front, dir) {
            if seen.insert(name.clone()) {
                let summary = header_summary(host, &entry, front);
                catalog.tools.push(Tool {
                    name,
                    summary,
                    entry,
                    declared: None,
                });
            }
        }
    }
    catalog
}

fn read_root(host: &dyn Host, front: &Front, r: &ResolvedRoot, catalog: &mut Catalog) {
    let file = r.dir.join("tools.toml");
    let Some(text) = host.read_to_string(&file) else {
        return;
    };
    let warn = |catalog: &mut Catalog, message: String| {
        catalog.warnings.push(Warning {
            path: file.clone(),
            message,
        })
    };
    let parsed = match manifest::parse_tools(&text) {
        Ok(p) => p,
        Err(e) => {
            return warn(
                catalog,
                format!("not a valid {}: {e}", manifest::TOOLS_SCHEMA),
            );
        }
    };
    if parsed.front != front.name {
        return;
    }
    for e in parsed.tool {
        let problems = manifest::entry_problems(&e, front.name, front.builtins);
        for p in &problems {
            warn(catalog, p.message.clone());
        }
        if manifest::blocking_problem(&problems).is_some() {
            continue;
        }
        let entry = r.dir.join(&e.entry);
        if !host.is_executable(&entry) {
            warn(
                catalog,
                format!(
                    "tool {}: entry {} is not an executable file",
                    e.name, e.entry
                ),
            );
            continue;
        }
        if catalog.get(&e.name).is_some() {
            warn(
                catalog,
                format!(
                    "tool {} is declared twice for {}; the first one is used",
                    e.name, front.name
                ),
            );
            continue;
        }
        let declared = Declared {
            audience: e.audience,
            stability: e.stability,
            replaces: e.replaces,
            root: r.root.path.clone(),
        };
        catalog.tools.push(Tool {
            name: e.name,
            summary: e.summary,
            entry,
            declared: Some(declared),
        });
    }
}

/// The plugins one `PATH` folder holds: `<prefix><name>` executables, the front's own prefix first.
fn path_plugins(host: &dyn Host, front: &Front, dir: &Path) -> Vec<(String, PathBuf)> {
    let names = host.list_dir(dir);
    let mut out: Vec<(String, PathBuf)> = Vec::new();
    for prefix in front.path_prefixes {
        for file in &names {
            let Some(name) = file.strip_prefix(prefix) else {
                continue;
            };
            if !valid_name(name)
                || front.builtins.contains(&name)
                || out.iter().any(|(n, _)| n == name)
            {
                continue;
            }
            let entry = dir.join(file);
            if host.is_executable(&entry) {
                out.push((name.to_string(), entry));
            }
        }
    }
    out
}

/// A `PATH` tool's summary: the first comment or docstring line after the first line of its header,
/// without a leading `<prefix><name>:`; empty for a binary.
pub fn header_summary(host: &dyn Host, entry: &Path, front: &Front) -> String {
    let Some(text) = host.read_head(entry) else {
        return String::new();
    };
    for line in text.lines().skip(1).take(40) {
        let body = if let Some(rest) = line.strip_prefix('#') {
            rest.strip_prefix(' ').unwrap_or(rest)
        } else if let Some(rest) = line.strip_prefix("\"\"\"") {
            rest
        } else {
            continue;
        };
        if body.is_empty() || body.starts_with(' ') || body.starts_with('!') {
            continue;
        }
        return strip_tool_prefix(body.trim_end_matches("\"\"\"").trim(), front).to_string();
    }
    String::new()
}

fn strip_tool_prefix<'a>(line: &'a str, front: &Front) -> &'a str {
    let umbrellas = std::iter::once(front.name)
        .chain(front.path_prefixes.iter().map(|p| p.trim_end_matches('-')));
    for u in umbrellas {
        if let Some(rest) = line.strip_prefix(u).and_then(|r| r.strip_prefix(' '))
            && let Some((tool, summary)) = rest.split_once(": ")
            && valid_name(tool)
        {
            return summary;
        }
    }
    line
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    /// An in-memory machine: files (with an executable flag) and variables.
    #[derive(Default)]
    struct Fake {
        files: HashMap<PathBuf, (String, bool)>,
        vars: HashMap<String, String>,
    }

    impl Fake {
        fn file(mut self, p: &str, text: &str, exec: bool) -> Self {
            self.files
                .insert(PathBuf::from(p), (text.to_string(), exec));
            self
        }
        fn var(mut self, k: &str, v: &str) -> Self {
            self.vars.insert(k.into(), v.into());
            self
        }
    }

    impl Host for Fake {
        fn read_to_string(&self, p: &Path) -> Option<String> {
            self.files.get(p).map(|(t, _)| t.clone())
        }
        fn is_executable(&self, p: &Path) -> bool {
            self.files.get(p).is_some_and(|(_, x)| *x)
        }
        fn list_dir(&self, p: &Path) -> Vec<String> {
            let mut v: Vec<String> = self
                .files
                .keys()
                .filter(|k| k.parent() == Some(p))
                .map(|k| k.file_name().unwrap().to_string_lossy().into_owned())
                .collect();
            v.sort();
            v
        }
        fn var(&self, k: &str) -> Option<String> {
            self.vars.get(k).cloned()
        }
    }

    fn tools_toml(names: &[&str]) -> String {
        let mut s = String::from("schema = \"opendap.tools/v1\"\nfront = \"odw\"\n");
        for n in names {
            s += &format!(
                "[[tool]]\nname = \"{n}\"\nsummary = \"{n} summary\"\nentry = \"bin/odapkit-{n}\"\naudience = \"maintainer\"\nstability = \"preview\"\n"
            );
        }
        s
    }

    const REPOS: &str = "path\trepository\nr/a\to/toolkit-a\nr/b\to/b\n";

    fn search(path: &[&str]) -> Search {
        Search {
            workspace: Some("/ws".into()),
            path: path.iter().map(PathBuf::from).collect(),
        }
    }

    #[test]
    fn roots_in_manifest_order_then_path_and_first_match_wins() {
        let host = Fake::default()
            .file("/ws/repos.tsv", REPOS, false)
            .file("/ws/r/a/tools.toml", &tools_toml(&["plan"]), false)
            .file("/ws/r/a/bin/odapkit-plan", "#!/bin/sh\n", true)
            .file("/ws/r/b/tools.toml", &tools_toml(&["plan", "sim"]), false)
            .file("/ws/r/b/bin/odapkit-plan", "#!/bin/sh\n", true)
            .file("/ws/r/b/bin/odapkit-sim", "#!/bin/sh\n", true)
            .file("/p/odw-sim", "#!/bin/sh\n", true)
            .file(
                "/p/odw-extra",
                "#!/bin/sh\n# odw extra: does extra things\n",
                true,
            );
        let c = discover(&host, &Front::ODW, &search(&["/p"]));
        let names: Vec<_> = c.tools.iter().map(|t| t.name.as_str()).collect();
        assert_eq!(names, ["plan", "sim", "extra"]);
        assert_eq!(
            c.get("plan").unwrap().entry,
            PathBuf::from("/ws/r/a/bin/odapkit-plan")
        );
        assert_eq!(
            c.get("sim").unwrap().entry,
            PathBuf::from("/ws/r/b/bin/odapkit-sim")
        );
        assert!(
            c.warnings
                .iter()
                .any(|w| w.message.contains("declared twice"))
        );
        let extra = c.get("extra").unwrap();
        assert_eq!(extra.declared, None);
        assert_eq!(extra.summary, "does extra things");
    }

    #[test]
    fn a_root_override_and_no_repository_name_matching() {
        let host = Fake::default()
            .file("/ws/repos.tsv", REPOS, false)
            // A toolkit-named root with plugins in bin/ but no tools.toml offers nothing.
            .file("/ws/r/a/bin/odapkit-plan", "#!/bin/sh\n", true)
            .file("/elsewhere/tools.toml", &tools_toml(&["sim"]), false)
            .file("/elsewhere/bin/odapkit-sim", "#!/bin/sh\n", true)
            .var("ODAP_B_DIR", "/elsewhere");
        let c = discover(&host, &Front::ODW, &search(&[]));
        assert!(c.get("plan").is_none());
        assert_eq!(
            c.get("sim").unwrap().entry,
            PathBuf::from("/elsewhere/bin/odapkit-sim")
        );
        assert_eq!(c.get("sim").unwrap().declared.as_ref().unwrap().root, "r/b");
    }

    #[test]
    fn bad_files_and_entries_warn_and_are_skipped() {
        let host = Fake::default()
            .file("/ws/repos.tsv", REPOS, false)
            .file("/ws/r/a/tools.toml", "schema = 1", false)
            .file(
                "/ws/r/b/tools.toml",
                &tools_toml(&["help", "sim", "gone"]),
                false,
            )
            .file("/ws/r/b/bin/odapkit-help", "#!/bin/sh\n", true)
            .file("/ws/r/b/bin/odapkit-sim", "", false);
        let c = discover(&host, &Front::ODW, &search(&[]));
        assert!(c.tools.is_empty());
        let msgs: Vec<_> = c.warnings.iter().map(|w| w.message.as_str()).collect();
        assert!(msgs[0].starts_with("not a valid opendap.tools/v1"));
        assert!(
            msgs.iter()
                .any(|m| m.contains("shadow the built-in command odw help"))
        );
        assert!(
            msgs.iter()
                .any(|m| m.contains("odapkit-sim is not an executable"))
        );
        assert!(
            msgs.iter()
                .any(|m| m.contains("odapkit-gone is not an executable"))
        );
    }

    #[test]
    fn another_fronts_file_is_ignored() {
        let host = Fake::default()
            .file("/ws/repos.tsv", REPOS, false)
            .file(
                "/ws/r/a/tools.toml",
                &tools_toml(&["plan"]).replace("\"odw\"", "\"opendap\""),
                false,
            )
            .file("/ws/r/a/bin/odapkit-plan", "#!/bin/sh\n", true);
        let c = discover(&host, &Front::ODW, &search(&[]));
        assert!(c.tools.is_empty() && c.warnings.is_empty());
    }

    #[test]
    fn path_prefers_the_fronts_prefix_and_skips_builtins_and_bad_names() {
        let host = Fake::default()
            .file("/p/odapkit-x", "", true)
            .file("/p/odw-x", "", true)
            .file("/p/odw-help", "", true)
            .file("/p/odw-Bad", "", true)
            .file("/p/odw-noexec", "", false)
            .file("/q/odw-x", "", true);
        let c = discover(
            &host,
            &Front::ODW,
            &Search {
                workspace: None,
                path: vec!["/p".into(), "/q".into()],
            },
        );
        let names: Vec<_> = c
            .tools
            .iter()
            .map(|t| (t.name.as_str(), t.entry.to_str().unwrap()))
            .collect();
        assert_eq!(names, [("x", "/p/odw-x")]);
    }

    #[test]
    fn header_summaries() {
        let f = Front::ODW;
        let host = Fake::default()
            .file("/a", "#!/bin/sh\n# odapkit qemu: QEMU for devices\n", true)
            .file(
                "/b",
                "#!/usr/bin/env python3\n\"\"\"Checks things.\"\"\"\n",
                true,
            )
            .file("/c", "#!/bin/sh\n#\n#   indented\nset -eu\n", true);
        assert_eq!(
            header_summary(&host, Path::new("/a"), &f),
            "QEMU for devices"
        );
        assert_eq!(header_summary(&host, Path::new("/b"), &f), "Checks things.");
        assert_eq!(header_summary(&host, Path::new("/c"), &f), "");
        assert_eq!(header_summary(&host, Path::new("/missing"), &f), "");
    }
}
