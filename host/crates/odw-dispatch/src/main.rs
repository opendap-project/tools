//! `odw`: the maintainers' umbrella command (command standard §1).

use std::io::Write;
use std::os::unix::process::CommandExt;
use std::process::ExitCode;

use odw_dispatch::{Front, Outcome, fs, run};

fn main() -> ExitCode {
    let front = Front::ODW;
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&front, &fs::FsHost, &fs::search(), &args) {
        Outcome::Print { stdout, warnings } => {
            for w in warnings {
                eprintln!("{w}");
            }
            let mut out = std::io::stdout().lock();
            // A closed pipe (`odw help | head`) is not an error.
            let _ = out.write_all(stdout.as_bytes()).and_then(|()| out.flush());
            ExitCode::SUCCESS
        }
        Outcome::Exec { entry, args, env } => {
            let err = std::process::Command::new(&entry)
                .args(&args)
                .envs(env)
                .exec();
            eprint!(
                "{}",
                odw_dispatch::error::DispatchError {
                    code: "tool.exec_failed",
                    message: format!("cannot run {}: {err}", entry.display()),
                    hint: "the tool's entry must be an executable with a valid interpreter line"
                        .into(),
                    fix: format!("ls -l {}", entry.display()),
                    status: 1,
                }
                .text(front.name)
            );
            ExitCode::from(1)
        }
        Outcome::Fail { error, json } => {
            eprint!("{}", error.text(front.name));
            if json {
                println!("{}", error.json());
            }
            ExitCode::from(error.status as u8)
        }
    }
}
