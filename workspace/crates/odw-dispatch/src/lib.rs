//! The umbrella-command dispatch of the command standard (toolkit-host `docs/specs/command-standard.md`
//! §1–§4): plugins are found from each workspace root's `tools.toml`, then `PATH`; listed with their
//! summary, audience and stability, as text or `opendap.tools-list/v1`; run by replacing the process.
//!
//! Layers: [`model`] (domain), [`manifest`] (the `tools.toml` format), [`workspace`] (where the
//! workspace is and its `repos.tsv` roots), [`discovery`]
//! (the core, behind the [`discovery::Host`] port), [`help`] and [`cli`] (the interface), [`fs`] (the
//! machine adapter), [`check`], [`doctor`] and [`report`] (`odw check`, `odw doctor`,
//! `opendap.check-result/v1`). A front is
//! data ([`model::Front`]), so the `opendap` CLI can reuse the crate with
//! its own built-ins.

pub mod check;
pub mod cli;
pub mod discovery;
pub mod doctor;
pub mod error;
pub mod fs;
pub mod help;
pub mod manifest;
pub mod model;
pub mod report;
pub mod workspace;

pub use cli::{Outcome, run};
pub use model::Front;
