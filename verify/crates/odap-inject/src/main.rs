//! `odap-inject`: touch contacts for automated tests on a running instance (development only;
//! shipped in the development system extension, never in images).
//!
//! It creates a direct-touch multi-touch device through uinput, the size of the display in
//! pixels, so a contact at (x, y) lands on that pixel. Unlike host-side injection (QEMU 11.1.2's
//! virtio-multitouch passes no contact slot or tracking id from QMP), the contact goes through the
//! same path as a real panel: evdev, libinput, `odap-composer`, the client.
//!
//! Errors carry a stable dotted code, a hint and a fix (command standard §5), on stderr and, with
//! `--json`, as an `opendap.error/v1` document on stdout.

#[cfg(target_os = "linux")]
mod uinput;

const USAGE: &str = "odap-inject: touch contacts for automated tests on a running instance

Usage: odap-inject [--size WxH] [--dry-run] [--json] tap X Y [HOLD_MS]
       odap-inject [--size WxH] [--dry-run] [--json] swipe X1 Y1 X2 Y2 [DURATION_MS]

  --size WxH  the display in pixels (default: the device description's display)
  --dry-run   check the gesture and say what it would inject; create no device
  --json      print the gesture as opendap.inject/v1 (and errors as opendap.error/v1)

Examples:
  odap-inject tap 120 200
  odap-inject --json --dry-run swipe 10 300 300 300 400

Exit status: 0 injected, 1 failure, 2 usage error.";

/// One gesture, parsed: the contact points in order and how long it lasts.
#[derive(Debug, PartialEq)]
struct Gesture {
    kind: &'static str,
    size: Option<(i32, i32)>,
    points: Vec<(i32, i32)>,
    duration_ms: i32,
    dry_run: bool,
    json: bool,
}

/// An error an agent can act on: a stable dotted code, a message, a hint and a fix.
#[derive(Debug, PartialEq)]
struct Error {
    code: &'static str,
    message: String,
    hint: &'static str,
    fix: &'static str,
    status: i32,
}

fn usage_error(message: impl Into<String>) -> Error {
    Error { code: "input.invalid", message: message.into(), hint: "the usage is in odap-inject --help",
            fix: "odap-inject --help", status: 2 }
}

/// Some(gesture), or None for --help.
fn parse(args: &[String]) -> Result<Option<Gesture>, Error> {
    let (mut size, mut dry_run, mut json, mut words) = (None, false, false, Vec::new());
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "-h" | "--help" => return Ok(None),
            "--dry-run" => dry_run = true,
            "--json" => json = true,
            "--size" => {
                let spec = it.next().ok_or_else(|| usage_error("--size needs WxH"))?;
                let (w, h) = spec.split_once('x').ok_or_else(|| usage_error(format!("--size: not WxH: {spec}")))?;
                let bad = || usage_error(format!("--size: not WxH: {spec}"));
                size = Some((w.parse().map_err(|_| bad())?, h.parse().map_err(|_| bad())?));
            }
            f if f.starts_with('-') && f.parse::<i32>().is_err() => return Err(usage_error(format!("unknown flag {f}"))),
            w => words.push(w.to_string()),
        }
    }
    let num = |i: usize| -> Result<i32, Error> {
        let w = words.get(i).ok_or_else(|| usage_error("missing coordinates"))?;
        w.parse().map_err(|_| usage_error(format!("not a number: {w}")))
    };
    let opt = |i: usize, d: i32| -> Result<i32, Error> { if words.len() > i { num(i) } else { Ok(d) } };
    let (kind, points, duration_ms, arity) = match words.first().map(String::as_str) {
        Some("tap") => ("tap", vec![(num(1)?, num(2)?)], opt(3, 80)?, 4),
        Some("swipe") => {
            let (x1, y1, x2, y2) = (num(1)?, num(2)?, num(3)?, num(4)?);
            let steps = 20;
            let pts = (0..=steps).map(|i| (x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)).collect();
            ("swipe", pts, opt(5, 250)?, 6)
        }
        Some(w) => return Err(usage_error(format!("unknown gesture {w} (tap, swipe)"))),
        None => return Err(usage_error("no gesture (tap, swipe)")),
    };
    if words.len() > arity {
        return Err(usage_error(format!("unexpected argument {}", words[arity])));
    }
    Ok(Some(Gesture { kind, size, points, duration_ms, dry_run, json }))
}

fn json_str(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out + "\""
}

fn fail(e: Error, json: bool) -> ! {
    eprintln!("odap-inject: error {}: {}\n  hint: {}\n  fix: {}", e.code, e.message, e.hint, e.fix);
    if json {
        println!("{{\"schema\": \"opendap.error/v1\", \"ok\": false, \"error\": {{\"code\": {}, \"message\": {}, \
                  \"hint\": {}, \"fix\": [{{\"command\": {}}}]}}}}",
                 json_str(e.code), json_str(&e.message), json_str(e.hint), json_str(e.fix));
    }
    std::process::exit(e.status);
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let json = args.iter().any(|a| a == "--json");
    let g = match parse(&args) {
        Ok(Some(g)) => g,
        Ok(None) => {
            println!("{USAGE}");
            return;
        }
        Err(e) => fail(e, json),
    };
    let size = g.size.or_else(description_size).unwrap_or((0, 0));
    if size.0 <= 0 || size.1 <= 0 {
        fail(Error { code: "display.unknown", message: "no display size in the device description".into(),
                     hint: "the device description names the display; elsewhere pass its size",
                     fix: "odap-inject --size 480x800 tap 10 10", status: 2 }, g.json);
    }
    if g.dry_run {
        eprintln!("odap-inject: would {} {} contact points over {} ms on a {}x{} display", g.kind, g.points.len(),
                  g.duration_ms, size.0, size.1);
    } else if let Err(e) = run(size, &g.points, g.duration_ms) {
        fail(e, g.json);
    }
    if g.json {
        let pts: Vec<String> = g.points.iter().map(|(x, y)| format!("[{x}, {y}]")).collect();
        println!("{{\"schema\": \"opendap.inject/v1\", \"gesture\": \"{}\", \"size\": [{}, {}], \"points\": [{}], \
                  \"duration_ms\": {}, \"dry_run\": {}}}", g.kind, size.0, size.1, pts.join(", "), g.duration_ms,
                 g.dry_run);
    }
}

/// The display's size from the device description (system hardware support spec §3), read with a
/// plain scan of its [display] table so the tool needs no TOML parser.
fn description_size() -> Option<(i32, i32)> {
    let text = ["/run/blackbird/device/description.toml", "/usr/share/blackbird/device/description.toml"]
        .iter()
        .find_map(|p| std::fs::read_to_string(p).ok())?;
    let mut in_display = false;
    let (mut w, mut h) = (None, None);
    for line in text.lines().map(str::trim) {
        if line.starts_with('[') {
            in_display = line == "[display]";
            continue;
        }
        if !in_display {
            continue;
        }
        if let Some((k, v)) = line.split_once('=') {
            let v = v.trim().parse::<i32>().ok();
            match k.trim() {
                "width_px" => w = v,
                "height_px" => h = v,
                _ => {}
            }
        }
    }
    Some((w?, h?))
}

#[cfg(target_os = "linux")]
fn run(size: (i32, i32), points: &[(i32, i32)], duration_ms: i32) -> Result<(), Error> {
    uinput::contact(size, points, duration_ms).map_err(|err| Error {
        code: "uinput.failed", message: err.to_string(), hint: "creating a uinput device needs write access to /dev/uinput",
        fix: "ls -l /dev/uinput", status: 1 })
}

#[cfg(not(target_os = "linux"))]
fn run(_: (i32, i32), _: &[(i32, i32)], _: i32) -> Result<(), Error> {
    Err(Error { code: "platform.unsupported", message: "odap-inject runs on a Blackbird OS instance (Linux uinput)".into(),
                hint: "run it on the device or the virtual T1", fix: "odap-vdev ssh -- odap-inject --help", status: 2 })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(s: &str) -> Vec<String> {
        s.split_whitespace().map(String::from).collect()
    }

    #[test]
    fn help_is_none() {
        assert_eq!(parse(&args("--help")), Ok(None));
        assert_eq!(parse(&args("tap 1 2 -h")), Ok(None));
    }

    #[test]
    fn tap_and_swipe_with_flags() {
        let g = parse(&args("--size 480x800 --dry-run --json tap 10 20")).unwrap().unwrap();
        assert_eq!((g.kind, g.size, g.points.clone(), g.duration_ms, g.dry_run, g.json),
                   ("tap", Some((480, 800)), vec![(10, 20)], 80, true, true));
        let s = parse(&args("swipe 0 0 20 0 300")).unwrap().unwrap();
        assert_eq!((s.points.len(), s.points[20], s.duration_ms), (21, (20, 0), 300));
    }

    #[test]
    fn usage_errors_exit_2_with_a_code() {
        for bad in ["--odw-no-such-flag", "tap 1", "tap x 2", "pinch 1 2", "", "--size 4 tap 1 2", "tap 1 2 3 4"] {
            let e = parse(&args(bad)).unwrap_err();
            assert_eq!((e.code, e.status), ("input.invalid", 2), "{bad}");
        }
    }

    #[test]
    fn json_strings_are_escaped() {
        assert_eq!(json_str("a\"b\\c\n"), "\"a\\\"b\\\\c\\n\"");
    }
}
