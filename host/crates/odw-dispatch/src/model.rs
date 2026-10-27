//! Domain model of the command standard's dispatch (§1–§4): a front, the tools it offers and where
//! each one came from. No I/O here.

use std::path::PathBuf;

/// One umbrella command: `opendap` or `odw` (command standard §1).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Front {
    /// The command as people type it, and the `front` value its `tools.toml` files carry.
    pub name: &'static str,
    /// Prefixes of plugin executables found on `PATH`, in order: `<front>-` first, then any transitional ones.
    pub path_prefixes: &'static [&'static str],
    /// Built-in commands; a plugin never shadows one (§2).
    pub builtins: &'static [&'static str],
    /// Repositories (manifest names) whose root is exported to plugins as `ODAP_<NAME>_DIR` when unset.
    pub export_roots: &'static [&'static str],
}

impl Front {
    /// The maintainers' umbrella. Plugins are still named `odapkit-<tool>` until the tools cut, so `PATH`
    /// discovery accepts that prefix after `odw-`; plugins find the shared helpers through toolkit-host.
    pub const ODW: Front = Front {
        name: "odw",
        path_prefixes: &["odw-", "odapkit-"],
        builtins: &["help", "check"],
        export_roots: &["toolkit-host"],
    };
}

/// Who a tool is for (`tools.toml` `audience`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Audience {
    Maintainer,
    Developer,
    User,
}

/// How settled a tool is (`tools.toml` `stability`). The order is the help listing's group order.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Stability {
    Stable,
    Preview,
    Deprecated,
    Internal,
}

impl Audience {
    pub fn as_str(self) -> &'static str {
        match self {
            Audience::Maintainer => "maintainer",
            Audience::Developer => "developer",
            Audience::User => "user",
        }
    }
}

impl Stability {
    pub fn as_str(self) -> &'static str {
        match self {
            Stability::Stable => "stable",
            Stability::Preview => "preview",
            Stability::Deprecated => "deprecated",
            Stability::Internal => "internal",
        }
    }
}

/// What a `tools.toml` entry declares about a tool.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Declared {
    pub audience: Audience,
    pub stability: Stability,
    pub replaces: Option<String>,
    /// The root's path as the workspace manifest lists it (`public/toolkit/host`).
    pub root: String,
}

/// A tool the front can run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Tool {
    pub name: String,
    pub summary: String,
    /// The executable, absolute.
    pub entry: PathBuf,
    /// `None` for a tool found only on `PATH`: its audience and stability are unknown (§3).
    pub declared: Option<Declared>,
}

/// A problem found while reading the roots; dispatch goes on without the offending entry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Warning {
    pub path: PathBuf,
    pub message: String,
}

/// Every tool a front offers, in resolution order (workspace roots in manifest order, then `PATH`).
#[derive(Debug, Clone, Default)]
pub struct Catalog {
    pub tools: Vec<Tool>,
    pub warnings: Vec<Warning>,
}

impl Catalog {
    /// The first tool with this name (§3: first match wins).
    pub fn get(&self, name: &str) -> Option<&Tool> {
        self.tools.iter().find(|t| t.name == name)
    }

    /// The tool whose `replaces` names this one, for a deprecated entry's listing.
    pub fn replacement_of(&self, name: &str) -> Option<&Tool> {
        self.tools
            .iter()
            .find(|t| t.declared.as_ref().and_then(|d| d.replaces.as_deref()) == Some(name))
    }
}

/// `^[a-z][a-z0-9-]*$` (§2).
pub fn valid_name(name: &str) -> bool {
    let mut chars = name.chars();
    matches!(chars.next(), Some('a'..='z'))
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_follow_the_pattern() {
        for good in ["plan", "dev-key", "a1", "security-check"] {
            assert!(valid_name(good), "{good}");
        }
        for bad in ["", "Plan", "1a", "-x", ".x", "a/b", "a_b", "a b", "á"] {
            assert!(!valid_name(bad), "{bad}");
        }
    }

    #[test]
    fn stability_orders_the_help_groups() {
        assert!(Stability::Stable < Stability::Preview);
        assert!(Stability::Preview < Stability::Deprecated);
        assert!(Stability::Deprecated < Stability::Internal);
    }
}
