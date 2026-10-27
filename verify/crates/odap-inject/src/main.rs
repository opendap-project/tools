//! `odap-inject`: touch contacts for automated tests on a running instance (development only;
//! shipped in the development system extension, never in images).
//!
//! It creates a direct-touch multi-touch device through uinput, the size of the display in
//! pixels, so a contact at (x, y) lands on that pixel. Unlike host-side injection (QEMU 11.1.2's
//! virtio-multitouch passes no contact slot or tracking id from QMP), the contact goes through the
//! same path as a real panel: evdev, libinput, `odap-composer`, the client.
//!
//! Usage: odap-inject tap X Y [HOLD_MS]
//!        odap-inject swipe X1 Y1 X2 Y2 [DURATION_MS]
//!        (--size WxH: the display in pixels; default: the device description's display)

#[cfg(target_os = "linux")]
mod uinput;

fn usage() -> ! {
    eprintln!("usage: odap-inject [--size WxH] tap X Y [HOLD_MS] | swipe X1 Y1 X2 Y2 [DURATION_MS]");
    std::process::exit(2);
}

fn main() {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    let mut size = description_size().unwrap_or((0, 0));
    if args.first().map(String::as_str) == Some("--size") {
        let spec = args.get(1).cloned().unwrap_or_else(|| usage());
        let (w, h) = spec.split_once('x').unwrap_or_else(|| usage());
        size = (w.parse().unwrap_or_else(|_| usage()), h.parse().unwrap_or_else(|_| usage()));
        args.drain(..2);
    }
    if size.0 <= 0 || size.1 <= 0 {
        eprintln!("odap-inject: no display size in the device description; pass --size WxH");
        std::process::exit(2);
    }
    let n = |i: usize| -> i32 { args.get(i).and_then(|v| v.parse().ok()).unwrap_or_else(|| usage()) };
    let opt = |i: usize, d: i32| -> i32 { args.get(i).map(|v| v.parse().unwrap_or_else(|_| usage())).unwrap_or(d) };
    let points: Vec<(i32, i32)> = match args.first().map(String::as_str) {
        Some("tap") => vec![(n(1), n(2))],
        Some("swipe") => {
            let (x1, y1, x2, y2) = (n(1), n(2), n(3), n(4));
            let steps = 20;
            (0..=steps).map(|i| (x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)).collect()
        }
        _ => usage(),
    };
    let duration = if args[0] == "tap" { opt(3, 80) } else { opt(5, 250) };
    run(size, &points, duration);
}

/// The display's size from the device description (system hardware support spec §3), read with a
/// plain scan of its [display] table so the tool needs no TOML parser.
fn description_size() -> Option<(i32, i32)> {
    let text = ["/run/opendap/device/description.toml", "/usr/share/opendap/device/description.toml"]
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
fn run(size: (i32, i32), points: &[(i32, i32)], duration_ms: i32) {
    if let Err(err) = uinput::contact(size, points, duration_ms) {
        eprintln!("odap-inject: {err}");
        std::process::exit(1);
    }
}

#[cfg(not(target_os = "linux"))]
fn run(_: (i32, i32), _: &[(i32, i32)], _: i32) {
    eprintln!("odap-inject runs on a Blackbird OS instance (Linux uinput)");
    std::process::exit(2);
}
