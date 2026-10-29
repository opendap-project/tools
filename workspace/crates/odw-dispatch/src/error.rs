//! Errors an agent can act on (command standard §5): a stable dotted code, a hint and a fix, as text on
//! stderr and, with `--json`, as an `opendap.error/v1` document on stdout. The text form is the one the
//! toolkit's shell and Python tools print (`lib/cli.sh`, `odw.clikit`).

use serde_json::json;

pub const ERROR_SCHEMA: &str = "opendap.error/v1";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DispatchError {
    pub code: &'static str,
    pub message: String,
    pub hint: String,
    pub fix: String,
    /// 1 failure, 2 usage (§6).
    pub status: i32,
}

impl DispatchError {
    /// An unknown flag or a bad argument to the umbrella itself.
    pub fn usage(front: &str, message: impl Into<String>) -> Self {
        DispatchError {
            code: "input.invalid",
            message: message.into(),
            hint: format!("the usage is in {front} --help"),
            fix: format!("{front} --help"),
            status: 2,
        }
    }

    /// A name that is not a tool: unknown, or not a valid name (§2).
    pub fn no_tool(front: &str, name: &str) -> Self {
        DispatchError {
            code: "tool.not_found",
            message: format!("no tool {name} ({front} help lists them)"),
            hint: format!("{front} help lists the tools this host has"),
            fix: format!("{front} help"),
            status: 2,
        }
    }

    pub fn text(&self, front: &str) -> String {
        format!(
            "{front}: error {}: {}\n  hint: {}\n  fix: {}\n",
            self.code, self.message, self.hint, self.fix
        )
    }

    pub fn json(&self) -> String {
        json!({
            "schema": ERROR_SCHEMA,
            "ok": false,
            "error": {"code": self.code, "message": self.message, "hint": self.hint, "fix": [{"command": self.fix}]},
        })
        .to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_text_form_has_a_code_a_hint_and_a_fix() {
        let e = DispatchError::no_tool("odw", "nope");
        assert_eq!(e.status, 2);
        assert_eq!(
            e.text("odw"),
            "odw: error tool.not_found: no tool nope (odw help lists them)\n  hint: odw help lists the tools this host has\n  fix: odw help\n"
        );
    }

    #[test]
    fn the_json_form_is_an_error_document() {
        let v: serde_json::Value =
            serde_json::from_str(&DispatchError::usage("odw", "unknown flag --x").json()).unwrap();
        assert_eq!(v["schema"], ERROR_SCHEMA);
        assert_eq!(v["ok"], false);
        assert_eq!(v["error"]["code"], "input.invalid");
        assert_eq!(v["error"]["fix"][0]["command"], "odw --help");
    }
}
