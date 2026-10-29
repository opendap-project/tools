//! The machine adapter: the real file system and environment behind the [`Host`] port, and where the
//! workspace is.

use std::io::Read;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::Duration;

use crate::discovery::{Host, Ran, Search};

/// How much of a plugin's header is read for its summary.
const HEAD_BYTES: u64 = 8 * 1024;

pub struct FsHost;

impl Host for FsHost {
    fn read_to_string(&self, path: &Path) -> Option<String> {
        std::fs::read_to_string(path).ok()
    }

    fn is_executable(&self, path: &Path) -> bool {
        std::fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
    }

    fn list_dir(&self, path: &Path) -> Vec<String> {
        let Ok(rd) = std::fs::read_dir(path) else {
            return Vec::new();
        };
        let mut names: Vec<String> = rd
            .filter_map(|e| e.ok()?.file_name().into_string().ok())
            .collect();
        names.sort();
        names
    }

    fn var(&self, name: &str) -> Option<String> {
        std::env::var(name).ok()
    }

    fn current_dir(&self) -> Option<std::path::PathBuf> {
        std::env::current_dir().ok()
    }

    fn log(&self, line: &str) {
        eprintln!("{line}");
    }

    fn read_head(&self, path: &Path) -> Option<String> {
        let mut buf = Vec::new();
        std::fs::File::open(path)
            .ok()?
            .take(HEAD_BYTES)
            .read_to_end(&mut buf)
            .ok()?;
        if buf.contains(&0) {
            return None;
        }
        // A cut may split a character: keep the valid prefix.
        Some(match String::from_utf8(buf) {
            Ok(s) => s,
            Err(e) => {
                let valid = e.utf8_error().valid_up_to();
                String::from_utf8_lossy(&e.into_bytes()[..valid]).into_owned()
            }
        })
    }
    fn run(
        &self,
        program: &Path,
        args: &[String],
        env: &[(String, String)],
        cwd: Option<&Path>,
        timeout: Duration,
    ) -> Option<Ran> {
        let mut cmd = Command::new(program);
        cmd.args(args)
            .envs(env.iter().map(|(k, v)| (k, v)))
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            // Its own process group, so a timeout ends what it started too.
            .process_group(0);
        if let Some(dir) = cwd {
            cmd.current_dir(dir);
        }
        let child = cmd.spawn().ok()?;
        let pid = child.id();
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let _ = tx.send(child.wait_with_output());
        });
        match rx.recv_timeout(timeout) {
            Ok(Ok(out)) => Some(Ran {
                status: out.status.code(),
                stdout: String::from_utf8_lossy(&out.stdout).into_owned(),
                stderr: String::from_utf8_lossy(&out.stderr).into_owned(),
                timed_out: false,
            }),
            Ok(Err(_)) => None,
            Err(_) => {
                let _ = Command::new("kill")
                    .args(["-KILL", &format!("-{pid}")])
                    .stderr(Stdio::null())
                    .status();
                Some(Ran {
                    timed_out: true,
                    ..Ran::default()
                })
            }
        }
    }
}

/// The search of this process: its workspace and its `PATH`.
pub fn search() -> Search {
    let path = std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).collect())
        .unwrap_or_default();
    Search {
        workspace: crate::workspace::workspace(),
        path,
    }
}
