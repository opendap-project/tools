//! The workspace and its manifest (`repos.tsv`): where the workspace is, its roots in manifest order,
//! and `ODAP_<NAME>_DIR`, which moves a root. The one place the dispatch reads the manifest.

use std::path::{Path, PathBuf};

use crate::discovery::Host;

/// A root of the workspace manifest.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Root {
    /// The path from the workspace (`public/toolkit/host`).
    pub path: String,
    /// The repository's name without its owner (`toolkit-host`).
    pub name: String,
}

/// The rows of `repos.tsv` (a header line, then `path<TAB>owner/repository`).
pub fn parse_repos(text: &str) -> Vec<Root> {
    text.lines()
        .skip(1)
        .filter_map(|line| {
            let mut cols = line.split('\t');
            let path = cols.next()?.trim();
            let repo = cols.next()?.trim();
            // A root stays inside the workspace: no absolute path, no `..`.
            if path.is_empty()
                || path.starts_with('#')
                || path.starts_with('/')
                || path.split('/').any(|p| p == "..")
            {
                return None;
            }
            let name = repo.rsplit('/').next().unwrap_or(repo).to_string();
            Some(Root {
                path: path.to_string(),
                name,
            })
        })
        .collect()
}

/// `ODAP_<NAME>_DIR` for a repository name: upper case, every other character an underscore
/// (`toolkit-host` → `ODAP_TOOLKIT_HOST_DIR`, `.github` → `ODAP_GITHUB_DIR`).
pub fn root_env_var(name: &str) -> String {
    let mut s: String = name
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() {
                c.to_ascii_uppercase()
            } else {
                '_'
            }
        })
        .collect();
    while s.starts_with('_') {
        s.remove(0);
    }
    while s.ends_with('_') {
        s.pop();
    }
    format!("ODAP_{s}_DIR")
}

/// A workspace root resolved to a folder (`ODAP_<NAME>_DIR` overrides the manifest's path).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedRoot {
    pub root: Root,
    pub dir: PathBuf,
}

/// The roots of the workspace manifest, in manifest order. Outside a workspace, the tool folders whose
/// `fallback` variables are set (`ODAP_TOOLS_DIR`, `ODAP_TOOLKIT_HOST_DIR`, which the `odapkit` alias
/// sets), in that order; a folder already listed, or directly below one listed, is read once.
pub fn roots(host: &dyn Host, workspace: Option<&Path>, fallback: &[String]) -> Vec<ResolvedRoot> {
    let Some(text) = workspace.and_then(|ws| host.read_to_string(&ws.join("repos.tsv"))) else {
        return variable_roots(host, fallback);
    };
    let ws = workspace.unwrap_or(Path::new(""));
    parse_repos(&text)
        .into_iter()
        .map(|root| {
            let dir = match host.var(&root_env_var(&root.name)) {
                Some(v) if !v.is_empty() => PathBuf::from(v),
                _ => ws.join(&root.path),
            };
            ResolvedRoot { root, dir }
        })
        .collect()
}

fn variable_roots(host: &dyn Host, vars: &[String]) -> Vec<ResolvedRoot> {
    let mut out: Vec<ResolvedRoot> = Vec::new();
    for var in vars {
        let Some(v) = host.var(var).filter(|v| !v.is_empty()) else {
            continue;
        };
        let dir = PathBuf::from(&v);
        if out
            .iter()
            .any(|r| r.dir == dir || dir.parent() == Some(r.dir.as_path()))
        {
            continue;
        }
        let name = var
            .trim_start_matches("ODAP_")
            .trim_end_matches("_DIR")
            .to_lowercase()
            .replace('_', "-");
        out.push(ResolvedRoot {
            root: Root { path: v, name },
            dir,
        });
    }
    out
}

/// The nearest folder at or above `start` that holds `repos.tsv`.
pub fn find_workspace(start: &Path) -> Option<PathBuf> {
    start
        .ancestors()
        .find(|d| d.join("repos.tsv").is_file())
        .map(Path::to_path_buf)
}

/// The workspace: `ODAP_WORKSPACE_DIR`, else the folder above this executable (links resolved) that
/// holds `repos.tsv`. Never the current folder: a checkout someone else made would then choose what
/// `odw` runs.
pub fn workspace() -> Option<PathBuf> {
    if let Some(v) = std::env::var_os("ODAP_WORKSPACE_DIR").filter(|v| !v.is_empty()) {
        return Some(PathBuf::from(v));
    }
    let exe = std::env::current_exe().ok()?.canonicalize().ok()?;
    exe.parent().and_then(find_workspace)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repos_rows_and_env_names() {
        let roots = parse_repos(
            "path\trepository\npublic/toolkit/host\topendap-project/toolkit-host\npublic/.github\topendap-project/.github\n\n",
        );
        assert_eq!(roots.len(), 2);
        assert_eq!(
            roots[0],
            Root {
                path: "public/toolkit/host".into(),
                name: "toolkit-host".into()
            }
        );
        assert_eq!(root_env_var(&roots[0].name), "ODAP_TOOLKIT_HOST_DIR");
        assert_eq!(root_env_var(&roots[1].name), "ODAP_GITHUB_DIR");
        assert_eq!(root_env_var("virtual-devices"), "ODAP_VIRTUAL_DEVICES_DIR");
    }

    #[test]
    fn roots_outside_the_workspace_are_dropped() {
        let roots =
            parse_repos("path\trepository\n/etc\to/abs\n../up\to/up\na/../../b\to/mid\nok\to/ok\n");
        assert_eq!(
            roots.iter().map(|r| r.path.as_str()).collect::<Vec<_>>(),
            ["ok"]
        );
    }
}
