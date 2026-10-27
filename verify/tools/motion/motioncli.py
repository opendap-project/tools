"""odapkit motion: record motion on screen and measure it (transitions, animations, scrolling).

Usage: odapkit motion record (--region X,Y,W,H [--screen N] | --qmp SOCKET [--keys QCODE@S,…]) [--seconds S] [--fps F] [--run CMD] [--out FILE] [--dry-run]
       odapkit motion analyze FILE [--roi X,Y,W,H] [--track RRGGBB[/TOL]] [--threshold T] [--json]
       odapkit motion frames FILE (--at T,T,… | --segment K [--count N]) [--roi X,Y,W,H] [--width PX] [--out PNG] [--dry-run]
       odapkit motion --help

  record   records a region of a screen (points from its top-left corner, as a screenshot shows
           them; scaled to the screen's pixels) at its full frame rate, lossless enough to
           measure; --run starts CMD once recording runs (the input that triggers the motion), and
           recording stops --seconds after. Prints the file (default: a temporary .mp4).
           --qmp records a QEMU machine's own display instead (its QMP socket; for the virtual
           T1, odap-vdev's): the frames the guest drew, sampled on a clock (2 ms each), whatever
           windows cover the screen, and without the host's compositor in the chain. QEMU serves
           one QMP client at a time, so the input that triggers the motion goes over the
           recorder's own connection: --keys presses each QEMU key code at its time in the
           recording (volumeup@0.5,volumeup@1.4); a --run command must not use that socket.
  analyze  finds the motion: segments of frames that change, each with its start, end and
           visible duration (frames that change more than --threshold; an easing's slowest tail
           can fall under it, so a curve measures a little short), the nearest design-system
           duration token (fast 120 ms, standard 200 ms, slow 320 ms), its frames, the frames held
           inside it, its effective frame rate (new pictures a second, whatever the sampling: a
           held frame at twice the display's rate is the sampling, a drop shows as a lower rate)
           and the longest frame interval. --track follows a colour (the focus ring, an accent)
           and gives its box per frame: the curve, whether it overshoots or goes back on itself.
  frames   extracts only the frames worth looking at, cropped to --roi and scaled to --width,
           side by side in one PNG (a contact sheet; the times are printed): --at given times, or
           --count frames spread over segment K (its first and last included).

--dry-run (record, frames) checks the arguments, names the file it would write and records or writes
nothing. analyze --json prints opendap.motion-analysis/v1. Errors carry a dotted code, a hint and a
fix (odw.clikit from toolkit-host; with --json, also an opendap.error/v1 document on stdout).

Times are in seconds from the recording's start. Needs ffmpeg (toolkit-host's Brewfile). On macOS
the terminal needs the Screen Recording permission once. The capture is the host's: what it says
of frame pacing covers the whole chain on screen (for the virtual T1: the guest, VNC, the
simulator window, the host's compositor).
"""

# SPDX-FileCopyrightText: 2026 The OpenDAP Project
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import tomllib

TOKENS = {"fast": 0.120, "standard": 0.200, "slow": 0.320}
ANALYSIS_WIDTH = 160


TOOL = "odapkit motion"
HERE = os.path.dirname(os.path.abspath(__file__))


def _host_dir():
    """toolkit-host: ODAP_TOOLKIT_HOST_DIR (odapkit sets it), else the workspace's repos.tsv."""
    d = HERE
    if os.environ.get("ODAP_TOOLKIT_HOST_DIR"):
        return os.environ["ODAP_TOOLKIT_HOST_DIR"]
    while d != os.path.dirname(d):
        if os.path.isfile(os.path.join(d, "repos.tsv")) and os.stat(os.path.join(d, "repos.tsv")).st_uid == os.getuid():
            for line in open(os.path.join(d, "repos.tsv")).read().splitlines()[1:]:
                path, _, slug = line.partition("\t")
                full = os.path.realpath(os.path.join(d, path))  # only a folder inside the workspace
                if slug.split("/")[-1] == "toolkit-host" and full.startswith(os.path.realpath(d) + os.sep) \
                        and os.path.isdir(full):
                    return full
            return None
        d = os.path.dirname(d)
    return None


def _clikit():
    host = _host_dir()
    if not host:
        sys.exit(f"{TOOL}: error repo.not_found: no toolkit-host checkout\n  hint: its odw.clikit runs this tool's "
                 "command line\n  fix: export ODAP_TOOLKIT_HOST_DIR=<toolkit-host checkout>")
    sys.path.insert(0, os.path.join(host, "src"))
    from odw import clikit
    return clikit


clikit = _clikit()


def die(msg, code=2, err="input.invalid", hint="the usage is in odapkit motion --help", fix=f"{TOOL} --help"):
    """An error with a stable code, a hint and a fix (odw.clikit); exit status `code`."""
    raise clikit.CliError(err, msg, hint, fix, status=code)


def box(text):
    try:
        x, y, w, h = (float(v) for v in text.split(","))
    except ValueError:
        die(f"not X,Y,W,H: {text}")
    if w <= 0 or h <= 0:
        die(f"empty region: {text}")
    return x, y, w, h


def run(argv, **kw):
    return subprocess.run(argv, check=True, capture_output=True, **kw)


# Record ------------------------------------------------------------------------------------------

def screens():
    """Each screen's size in points and its scale (macOS), in the system's order."""
    js = ("ObjC.import('AppKit'); var s = $.NSScreen.screens, o = [];"
          "for (var i = 0; i < s.count; i++) { var f = s.objectAtIndex(i).frame;"
          " o.push(f.size.width + ' ' + f.size.height + ' ' + s.objectAtIndex(i).backingScaleFactor) }; o.join('\\n')")
    out = run(["osascript", "-l", "JavaScript", "-e", js], text=True).stdout
    return [tuple(float(v) for v in line.split()) for line in out.split("\n") if line.strip()]


def capture_device(n):
    """avfoundation's index of screen n (its devices list the cameras first)."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                         capture_output=True, text=True).stderr
    found = [int(m.group(1)) for m in re.finditer(r"\[(\d+)\] Capture screen \d+", out)]
    if n >= len(found):
        die(f"no screen {n} (screens: {len(found)})", err="screen.not_found", hint="screens are numbered from 0", fix="system_profiler SPDisplaysDataType")
    return found[n]


def record(a):
    if a.qmp:
        return record_qmp(a)
    if not a.region:
        die("record needs --region or --qmp")
    if sys.platform != "darwin":
        die("record runs on macOS (avfoundation); on Linux record with ffmpeg's x11grab or kmsgrab", err="platform.unsupported", hint="on Linux, --qmp records a QEMU machine's display", fix=f"{TOOL} record --qmp SOCKET")
    sc = screens()
    if a.screen >= len(sc):
        die(f"no screen {a.screen} (screens: {len(sc)})", err="screen.not_found", hint="screens are numbered from 0", fix="system_profiler SPDisplaysDataType")
    sw, sh, scale = sc[a.screen]
    x, y, w, h = a.region
    if x < 0 or y < 0 or x + w > sw or y + h > sh:
        die(f"region outside screen {a.screen} ({sw:g} x {sh:g} points)")
    # Pixels, even (the encoder's chroma needs it).
    px = [int(round(v * scale)) for v in (x, y, w, h)]
    px[2] -= px[2] % 2
    px[3] -= px[3] % 2
    if a.dry_run:
        print(f"{TOOL}: would record {w:g}x{h:g} points of screen {a.screen} for {a.seconds:g} s at {a.fps} fps to "
              f"{a.out or 'a temporary .mp4'}" + (f", running {a.run!r}" if a.run else ""), file=sys.stderr)
        return
    out = a.out or tempfile.NamedTemporaryFile(prefix="motion-", suffix=".mp4", delete=False).name
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "avfoundation", "-capture_cursor", "0",
            "-framerate", str(a.fps), "-pixel_format", "nv12", "-i", f"{capture_device(a.screen)}:none",
            "-t", str(a.seconds), "-vf", f"crop={px[2]}:{px[3]}:{px[0]}:{px[1]}",
            # Every frame a key frame, near lossless: exact seeking and honest differences.
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8", "-g", "1", "-pix_fmt", "yuv444p",
            "-fps_mode", "passthrough", out]
    p = subprocess.Popen(argv, stderr=subprocess.PIPE, text=True)
    if a.run:
        time.sleep(a.lead)
        # Its words as given, never through a shell.
        subprocess.run(shlex.split(a.run))
    err = p.communicate()[1]
    if p.returncode != 0:
        die(f"ffmpeg failed: {err.strip()}", 1, err="ffmpeg.failed", hint="on macOS the terminal needs the Screen Recording permission", fix="open \"x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture\"")
    print(out)


class Qmp:
    """QEMU's QMP: line-delimited JSON over a UNIX socket."""

    def __init__(self, path):
        import socket
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(path)
        self.f = self.sock.makefile("rw")
        self.read()
        self.cmd("qmp_capabilities")

    def read(self):
        while True:
            msg = json.loads(self.f.readline())
            if "event" not in msg:
                return msg

    def cmd(self, name, **args):
        self.f.write(json.dumps({"execute": name, "arguments": args}) + "\n")
        self.f.flush()
        reply = self.read()
        if "error" in reply:
            die(f"QMP {name}: {reply['error']['desc']}", 1, err="qmp.refused", hint="QEMU refused the command; QMP serves one client at a time", fix="odap-vdev status")
        return reply.get("return")


def record_qmp(a):
    """The machine's display, sampled every 1/fps seconds on a deadline (not after the last frame:
    the rate holds), each dump read back and piped to the encoder."""
    if a.dry_run:
        if not os.path.exists(a.qmp):
            die(f"no QMP socket at {a.qmp}", err="qmp.not_found", hint="a running QEMU machine serves it", fix="odap-vdev status")
        print(f"{TOOL}: would record the display behind {a.qmp} for {a.seconds:g} s at {a.fps} fps to "
              f"{a.out or 'a temporary .mp4'}", file=sys.stderr)
        return
    q = Qmp(a.qmp)
    out = a.out or tempfile.NamedTemporaryFile(prefix="motion-", suffix=".mp4", delete=False).name
    tmp = tempfile.mkdtemp(prefix="motion-")
    dump = os.path.join(tmp, "frame.ppm")
    enc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe", "-c:v", "ppm",
                            "-framerate", str(a.fps), "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8",
                            "-g", "1", "-pix_fmt", "yuv444p", out], stdin=subprocess.PIPE)
    trigger = None
    keys = []
    for k in (a.keys or "").split(","):
        if k.strip():
            code, _, at = k.partition("@")
            try:
                keys.append([float(at), code.strip(), "down"])
            except ValueError:
                die(f"--keys: not QCODE@SECONDS: {k}")
    keys = sorted(keys + [[t + 0.06, c, "up"] for t, c, _ in keys])
    step = 1.0 / a.fps
    start = time.monotonic()
    late = 0
    n = int(a.seconds * a.fps)
    for i in range(n):
        due = start + i * step
        wait = due - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        elif wait < -step:
            late += 1
        if trigger is None and a.run and time.monotonic() - start >= a.lead:
            trigger = subprocess.Popen(shlex.split(a.run))
        while keys and time.monotonic() - start >= keys[0][0]:
            _, code, edge = keys.pop(0)
            q.cmd("input-send-event", events=[{"type": "key", "data": {"down": edge == "down", "key": {"type": "qcode", "data": code}}}])
        q.cmd("screendump", filename=dump, format="ppm")
        with open(dump, "rb") as f:
            enc.stdin.write(f.read())
    enc.stdin.close()
    enc.wait()
    if trigger:
        trigger.wait()
    os.remove(dump)
    os.rmdir(tmp)
    if enc.returncode != 0:
        die("ffmpeg failed to encode the frames", 1, err="ffmpeg.failed", hint="the encoder's message is above", fix="ffmpeg -version")
    if late:
        print(f"odapkit motion: {late} of {n} samples late by more than a frame: lower --fps", file=sys.stderr)
    print(out)


# Analyze -----------------------------------------------------------------------------------------

def probe(path):
    if not os.path.exists(path):
        die(f"no recording at {path}", err="recording.not_found", hint="FILE is a recording made by odapkit motion record", fix=f"{TOOL} record --help")
    check = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                           capture_output=True, text=True)
    if check.returncode != 0:
        # An MP4 has its index written when recording ends: until then it cannot be read.
        die(f"{path} cannot be read yet: is it still recording? ({check.stderr.strip().splitlines()[-1] if check.stderr.strip() else 'no details'})", 1, err="recording.unreadable", hint="an MP4 is readable once its recording ends", fix=f"ffprobe {path}")
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
               "-of", "csv=p=0", path], text=True).stdout.strip().split(",")
    times = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pts_time",
                 "-of", "csv=p=0", path], text=True).stdout.split()
    times = [float(t.strip(",")) for t in times if t.strip(",")]
    t0 = times[0] if times else 0.0
    return int(out[0]), int(out[1]), [t - t0 for t in times]


def crop_filter(roi, width, height):
    if not roi:
        return []
    x, y, w, h = (int(round(v)) for v in roi)
    if x < 0 or y < 0 or x + w > width or y + h > height:
        die(f"--roi outside the recording ({width} x {height} pixels)")
    return [f"crop={w - w % 2}:{h - h % 2}:{x}:{y}"]


def frames_raw(path, filters, fmt, width):
    """Every frame, scaled to `width` pixels across, as raw bytes."""
    vf = filters + [f"scale={width}:-2:flags=area"]
    return run(["ffmpeg", "-v", "error", "-i", path, "-vf", ",".join(vf), "-fps_mode", "passthrough",
                "-f", "rawvideo", "-pix_fmt", fmt, "-"]).stdout


def analyze(a):
    width, height, times = probe(a.file)
    filters = crop_filter(a.roi, width, height)
    cw, ch = (int(a.roi[2]), int(a.roi[3])) if a.roi else (width, height)
    gw = min(ANALYSIS_WIDTH, cw - cw % 2)
    gh = int(round(ch * gw / cw / 2)) * 2
    gray = frames_raw(a.file, filters, "gray", gw)
    size = gw * gh
    n = min(len(gray) // size, len(times))
    frames = [gray[i * size:(i + 1) * size] for i in range(n)]
    # Change between frames: the mean absolute difference of their grey levels (0–255).
    energy = [0.0] + [sum(abs(p - q) for p, q in zip(frames[i], frames[i - 1])) / size for i in range(1, n)]
    moving = [e > a.threshold for e in energy]
    # Segments: runs of changing frames; a few still frames inside one (up to 3) are held frames,
    # not an end.
    segments, i = [], 1
    while i < n:
        if not moving[i]:
            i += 1
            continue
        start, last, held, j = i, i, [], i + 1
        while j < n:
            if moving[j]:
                held += [k for k in range(last + 1, j)]
                last = j
            elif j - last > 3:
                break
            j += 1
        segments.append({"start_frame": start - 1, "end_frame": last, "held": held})
        i = last + 1
    report = {"schema": "opendap.motion-analysis/v1", "file": a.file, "frames": n, "seconds": round(times[n - 1], 3) if n else 0,
              "fps": round((n - 1) / times[n - 1], 1) if n > 1 and times[n - 1] > 0 else None, "segments": []}
    track = parse_track(a.track) if a.track else None
    rgb = None
    if track:
        rgb = frames_raw(a.file, filters, "rgb24", gw)
    for k, s in enumerate(segments):
        f0, f1 = s["start_frame"], s["end_frame"]
        dur = times[f1] - times[f0]
        gaps = [times[f + 1] - times[f] for f in range(f0, f1)]
        nearest = min(TOKENS, key=lambda t: abs(TOKENS[t] - dur))
        distinct = f1 - f0 + 1 - len(s["held"])
        seg = {"segment": k, "start": round(times[f0], 3), "end": round(times[f1], 3), "duration_ms": round(dur * 1000),
               "frames": f1 - f0 + 1, "held_frames": len(s["held"]),
               # New pictures a second while it moves: what the eye gets, whatever the sampling.
               "effective_fps": round((distinct - 1) / dur, 1) if dur > 0 else None,
               "longest_interval_ms": round(max(gaps) * 1000, 1) if gaps else 0,
               "nearest_token": f"{nearest} ({round(TOKENS[nearest] * 1000)} ms)"}
        if track:
            seg["track"] = follow(rgb, gw, gh, track, f0, f1, times)
        report["segments"].append(seg)
    if a.json:
        print(json.dumps(report, indent=2))
        return
    print(f"{a.file}: {n} frames, {report['seconds']} s, {report['fps']} fps")
    if not segments:
        print("no motion above the threshold")
    print("| segment | start s | end s | visible duration ms | nearest token | frames | held | effective fps | longest interval ms |")
    print("|---|---|---|---|---|---|---|---|---|")
    for s in report["segments"]:
        print(f"| {s['segment']} | {s['start']} | {s['end']} | {s['duration_ms']} | {s['nearest_token']} | {s['frames']} | "
              f"{s['held_frames']} | {s['effective_fps']} | {s['longest_interval_ms']} |")
    for s in report["segments"]:
        t = s.get("track")
        if not t:
            continue
        print(f"\nsegment {s['segment']}, tracked colour: {t['summary']}")
        print("| t s | x | y | w | h | progress |")
        print("|---|---|---|---|---|---|")
        for r in t["frames"]:
            print(f"| {r['t']} | {r['box'][0] if r['box'] else '-'} | {r['box'][1] if r['box'] else '-'} | "
                  f"{r['box'][2] if r['box'] else '-'} | {r['box'][3] if r['box'] else '-'} | {r['progress']} |")


def parse_track(text):
    colour, _, tol = text.partition("/")
    if not re.fullmatch(r"#?[0-9a-fA-F]{6}", colour):
        die(f"--track: not RRGGBB: {text}")
    c = int(colour.lstrip("#"), 16)
    return (c >> 16 & 255, c >> 8 & 255, c & 255), int(tol or 24)


def follow(rgb, w, h, track, f0, f1, times):
    """The tracked colour's box per frame of a segment (in the analysis' scaled pixels), and its
    progress from the first box to the last along the axis that moves most."""
    (r, g, b), tol = track
    size = w * h * 3
    boxes = []
    for f in range(f0, f1 + 1):
        px = rgb[f * size:(f + 1) * size]
        xs, ys = [], []
        for i in range(0, len(px), 3):
            if abs(px[i] - r) <= tol and abs(px[i + 1] - g) <= tol and abs(px[i + 2] - b) <= tol:
                p = i // 3
                xs.append(p % w)
                ys.append(p // w)
        boxes.append((min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1) if len(xs) >= 4 else None)
    seen = [x for x in boxes if x]
    rows, summary = [], "not found"
    if len(seen) >= 2:
        a, z = seen[0], seen[-1]
        deltas = [abs(z[i] - a[i]) for i in range(4)]
        axis = deltas.index(max(deltas))
        span = z[axis] - a[axis]
        prog = [None if x is None or span == 0 else round((x[axis] - a[axis]) / span, 3) for x in boxes]
        values = [p for p in prog if p is not None]
        over = max(values) - 1 if values else 0
        back = any(values[i + 1] < values[i] - 0.02 for i in range(len(values) - 1))
        names = "x y w h".split()
        summary = (f"moves along {names[axis]} by {span} px (scaled); "
                   + (f"overshoots by {over:.0%}; " if over > 0.02 else "no overshoot; ")
                   + ("goes back on itself" if back else "monotonic"))
        for f, (x, p) in enumerate(zip(boxes, prog)):
            rows.append({"t": round(times[f0 + f], 3), "box": x, "progress": p})
    else:
        rows = [{"t": round(times[f0 + f], 3), "box": x, "progress": None} for f, x in enumerate(boxes)]
    return {"summary": summary, "frames": rows}


# Frames ------------------------------------------------------------------------------------------

def frames(a):
    width, height, times = probe(a.file)
    if a.at:
        at = [float(t) for t in a.at.split(",")]
    else:
        # The segment's frames, spread: first and last included.
        b = argparse.Namespace(file=a.file, roi=a.roi, track=None, threshold=a.threshold, json=True)
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            analyze(b)
        segs = json.loads(buf.getvalue())["segments"]
        if a.segment >= len(segs):
            die(f"no segment {a.segment} (found {len(segs)})", err="segment.not_found", hint="segments are numbered from 0 by analyze", fix=f"{TOOL} analyze {a.file}")
        s = segs[a.segment]
        n = max(2, a.count)
        at = [s["start"] + (s["end"] - s["start"]) * i / (n - 1) for i in range(n)]
    # The frame shown at each time: the last one that started at or before it.
    picks = [max([i for i, t in enumerate(times) if t <= x + 1e-6] or [0]) for x in at]
    vf = crop_filter(a.roi, width, height) + [f"scale={a.width}:-2:flags=lanczos"]
    if a.dry_run:
        print(f"{TOOL}: would write {len(set(picks))} frames side by side to {a.out or 'a temporary .png'}", file=sys.stderr)
        return
    out = a.out or tempfile.NamedTemporaryFile(prefix="motion-sheet-", suffix=".png", delete=False).name
    select = "+".join(f"eq(n\\,{p})" for p in picks)
    # Picked frames in time order, side by side; repeats (a held frame) kept once.
    unique = sorted(set(picks))
    run(["ffmpeg", "-v", "error", "-y", "-i", a.file, "-vf",
         ",".join([f"select='{select}'"] + vf + [f"tile={len(unique)}x1:padding=4:color=white"]),
         "-fps_mode", "passthrough", "-frames:v", "1", out])
    print(out)
    print("left to right: " + ", ".join(f"{times[p]:.3f} s (frame {p})" for p in unique))


def _main(argv):
    tools = tomllib.loads(open(os.path.join(HERE, "..", "..", "tools.toml")).read())["tool"]
    p = clikit.Parser(prog=TOOL, summary=next(t["summary"] for t in tools if t["name"] == "motion"), description=__doc__.split("\n\n", 1)[1],
                      formatter_class=argparse.RawDescriptionHelpFormatter,
                      examples=["odapkit motion record --region 0,0,400,300 --seconds 2 --dry-run",
                                "odapkit motion analyze /tmp/motion.mp4 --json"])
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=clikit.Parser)
    r = sub.add_parser("record")
    r.add_argument("--region", type=box)
    r.add_argument("--qmp", help="a QEMU machine's QMP socket: record its display")
    r.add_argument("--keys", help="with --qmp: QEMU key codes pressed at times in the recording, QCODE@S,…")
    r.add_argument("--screen", type=int, default=0)
    r.add_argument("--seconds", type=float, default=2.0)
    r.add_argument("--fps", type=int, default=60)
    r.add_argument("--run", help="command that triggers the motion, started once recording runs")
    r.add_argument("--lead", type=float, default=0.4, help="seconds recorded before --run starts")
    r.add_argument("--out")
    r.add_argument("--dry-run", action="store_true", help="check the arguments and record nothing")
    an = sub.add_parser("analyze")
    an.add_argument("file")
    an.add_argument("--roi", type=box)
    an.add_argument("--track")
    an.add_argument("--threshold", type=float, default=0.6, help="mean grey change that counts as motion")
    an.add_argument("--json", action="store_true")
    fr = sub.add_parser("frames")
    fr.add_argument("file")
    fr.add_argument("--at")
    fr.add_argument("--segment", type=int, default=0)
    fr.add_argument("--count", type=int, default=6)
    fr.add_argument("--roi", type=box)
    fr.add_argument("--width", type=int, default=200)
    fr.add_argument("--threshold", type=float, default=0.6)
    fr.add_argument("--out")
    fr.add_argument("--dry-run", action="store_true", help="pick the frames and write nothing")
    a = p.parse_args(argv)
    if not all(run_ok(t) for t in ("ffmpeg", "ffprobe")):
        die("needs ffmpeg and ffprobe", 1, err="tool.missing", hint="they come with toolkit-host's Brewfile",
            fix="brew install ffmpeg")
    {"record": record, "analyze": analyze, "frames": frames}[a.cmd](a)
    return 0


def main(argv=None):
    return clikit.main(TOOL, _main, argv)


def run_ok(tool):
    return any(os.access(os.path.join(d, tool), os.X_OK) for d in os.environ.get("PATH", "").split(os.pathsep))

