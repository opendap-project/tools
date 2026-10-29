//! Fixture workspaces shared by the `odw` binary's integration tests.
#![allow(dead_code)]

use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};

pub const ODW: &str = env!("CARGO_BIN_EXE_odw");

/// Every child process starts from an empty environment: tests never inherit a hook's GIT_* variables
/// (HL-64), or the plugins' git would act on the repository being committed.
pub fn command(program: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut c = Command::new(program);
    c.env_clear();
    c
}

/// A throwaway folder under the target directory.
pub struct Fixture {
    pub dir: PathBuf,
}

impl Fixture {
    pub fn new() -> Self {
        static N: AtomicUsize = AtomicUsize::new(0);
        let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join(format!(
            "odw-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        Fixture { dir }
    }

    pub fn write(&self, rel: &str, text: &str, exec: bool) -> PathBuf {
        let p = self.dir.join(rel);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(&p, text).unwrap();
        std::fs::set_permissions(
            &p,
            std::fs::Permissions::from_mode(if exec { 0o755 } else { 0o644 }),
        )
        .unwrap();
        p
    }

    /// A plugin that prints its name, its arguments and ODAP_TOOLKIT_HOST_DIR, and exits with $EXIT_WITH.
    pub fn plugin(&self, rel: &str, label: &str) -> PathBuf {
        let body = format!(
            "#!/bin/sh\n# odapkit {label}: the {label} fixture\necho \"{label} $*\"\necho \"host=${{ODAP_TOOLKIT_HOST_DIR:-}}\"\nexit \"${{EXIT_WITH:-0}}\"\n"
        );
        self.write(rel, &body, true)
    }

    pub fn odw(&self, args: &[&str], env: &[(&str, &str)]) -> Output {
        let mut c = command(ODW);
        c.args(args)
            .env("ODAP_WORKSPACE_DIR", self.dir.join("ws"))
            .env(
                "PATH",
                format!("{}:/usr/bin:/bin", self.dir.join("path").display()),
            )
            .stdin(Stdio::null());
        for (k, v) in env {
            c.env(k, v);
        }
        c.output().unwrap()
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

pub fn tools_toml(entries: &[(&str, &str)]) -> String {
    let mut s = String::from("schema = \"opendap.tools/v1\"\nfront = \"odw\"\n");
    for (name, stability) in entries {
        s += &format!(
            "\n[[tool]]\nname = \"{name}\"\nsummary = \"The {name} tool\"\nentry = \"bin/odapkit-{name}\"\naudience = \"maintainer\"\nstability = \"{stability}\"\n"
        );
    }
    s
}

/// Two roots: `public/toolkit/host` (plan, internal secret) and `public/other` (sim, plan again).
pub fn workspace() -> Fixture {
    let f = Fixture::new();
    f.write(
        "ws/repos.tsv",
        "path\trepository\npublic/toolkit/host\topendap-project/toolkit-host\npublic/other\topendap-project/other\n",
        false,
    );
    f.write(
        "ws/public/toolkit/host/tools.toml",
        &tools_toml(&[("plan", "preview"), ("secret", "internal")]),
        false,
    );
    f.plugin("ws/public/toolkit/host/bin/odapkit-plan", "plan");
    f.plugin("ws/public/toolkit/host/bin/odapkit-secret", "secret");
    f.write(
        "ws/public/other/tools.toml",
        &tools_toml(&[("sim", "stable"), ("plan", "preview")]),
        false,
    );
    f.plugin("ws/public/other/bin/odapkit-sim", "sim");
    f.plugin("ws/public/other/bin/odapkit-plan", "other-plan");
    f
}

pub fn stdout(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).into_owned()
}

pub fn stderr(o: &Output) -> String {
    String::from_utf8_lossy(&o.stderr).into_owned()
}
