//! `opendap.check-result/v1` (command standard §6, §7): what the umbrella's own checks report, as JSON or
//! as one text line per finding.

use serde_json::{Value, json};

pub const CHECK_RESULT_SCHEMA: &str = "opendap.check-result/v1";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    Pass,
    Fail,
}

impl Verdict {
    pub fn as_str(self) -> &'static str {
        match self {
            Verdict::Pass => "pass",
            Verdict::Fail => "fail",
        }
    }
}

/// One result: what was checked (`id`), the rule, the verdict and what was found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub id: String,
    pub rule: String,
    pub verdict: Verdict,
    pub message: String,
    pub path: Option<String>,
}

/// A check-result document under construction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CheckResult {
    pub tool: String,
    pub repo: String,
    pub findings: Vec<Finding>,
}

impl CheckResult {
    pub fn failed(&self) -> bool {
        self.findings.iter().any(|f| f.verdict == Verdict::Fail)
    }

    pub fn json(&self, generated: &str) -> String {
        let results: Vec<Value> = self
            .findings
            .iter()
            .map(|f| {
                let mut r = json!({"id": f.id, "rule": f.rule, "verdict": f.verdict.as_str(), "message": f.message});
                if let Some(p) = &f.path {
                    r["location"] = json!({ "path": p });
                }
                r
            })
            .collect();
        json!({
            "schema": CHECK_RESULT_SCHEMA,
            "tool": {"name": self.tool},
            "subject": {"repo": self.repo},
            "generated": generated,
            "results": results,
        })
        .to_string()
    }

    /// One line per finding that is not a pass: `<verdict> <rule> <id>: <message>`; quiet when clean.
    pub fn text(&self) -> String {
        self.findings
            .iter()
            .filter(|f| f.verdict != Verdict::Pass)
            .map(|f| {
                format!(
                    "{} {} {}: {}\n",
                    f.verdict.as_str(),
                    f.rule,
                    f.id,
                    f.message
                )
            })
            .collect()
    }
}

/// `YYYY-MM-DDTHH:MM:SSZ` for seconds since the epoch (UTC; days to civil date, Howard Hinnant's method).
pub fn timestamp(secs: u64) -> String {
    let (days, rem) = ((secs / 86_400) as i64, secs % 86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60
    )
}

/// Now, as [`timestamp`].
pub fn now() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    timestamp(secs)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timestamps_are_utc_civil_dates() {
        assert_eq!(timestamp(0), "1970-01-01T00:00:00Z");
        assert_eq!(timestamp(951_782_400), "2000-02-29T00:00:00Z");
        assert_eq!(timestamp(1_793_212_496), "2026-10-28T18:34:56Z");
    }

    #[test]
    fn text_lists_what_did_not_pass() {
        let mut r = CheckResult {
            tool: "odw doctor".into(),
            repo: "workspace".into(),
            findings: vec![],
        };
        let f = |verdict| Finding {
            id: "plan".into(),
            rule: "tool.help".into(),
            verdict,
            message: "m".into(),
            path: None,
        };
        r.findings = vec![f(Verdict::Pass), f(Verdict::Fail)];
        assert_eq!(r.text(), "fail tool.help plan: m\n");
        assert!(r.failed());
        let v: Value = serde_json::from_str(&r.json("2026-10-28T00:00:00Z")).unwrap();
        assert_eq!(v["results"][1]["verdict"], "fail");
        assert!(v["results"][0].get("location").is_none());
    }
}
