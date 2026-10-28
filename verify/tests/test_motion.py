"""Tests for odapkit motion's engine (tools/motion/motioncli.py): its argument parsers and, on a synthetic recording
made with ffmpeg (a 16-pixel red square moving 40 pixels in 200 ms), the analysis and the frames it picks.
tests/cli.sh covers the command-line contract.

Run from the repository root: python3 -m unittest discover tests
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "tools", "motion", "motioncli.py")
EXE = os.path.join(ROOT, "bin", "odapkit-motion")
FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]


def engine():
    spec = importlib.util.spec_from_file_location("motion", ENGINE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Parsers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = engine()

    def assertCliError(self, fn, *args, code="input.invalid"):
        with self.assertRaises(self.m.clikit.CliError) as e:
            fn(*args)
        self.assertEqual(e.exception.code, code)

    def test_box(self):
        self.assertEqual(self.m.box("1,2,30,40"), (1.0, 2.0, 30.0, 40.0))
        self.assertCliError(self.m.box, "1,2,3")
        self.assertCliError(self.m.box, "0,0,0,10")

    def test_parse_track(self):
        self.assertEqual(self.m.parse_track("FF8000"), ((255, 128, 0), 24))
        self.assertEqual(self.m.parse_track("#00ff00/10"), ((0, 255, 0), 10))
        self.assertCliError(self.m.parse_track, "red")

    def test_crop_filter_keeps_even_sizes_inside_the_frame(self):
        self.assertEqual(self.m.crop_filter(None, 64, 48), [])
        self.assertEqual(self.m.crop_filter((2, 4, 31, 21), 64, 48), ["crop=30:20:2:4"])
        self.assertCliError(self.m.crop_filter, (40, 0, 30, 10), 64, 48)

    def test_tokens_are_the_design_system_durations(self):
        self.assertEqual(self.m.TOKENS, {"fast": 0.120, "standard": 0.200, "slow": 0.320})


@unittest.skipUnless(FFMPEG, "needs ffmpeg and ffprobe")
class Analysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.video = os.path.join(cls.tmp, "move.mkv")
        graph = ("color=c=black:s=64x48:r=50:d=1.2[bg];color=c=0xFF0000:s=16x16:r=50:d=1.2[fg];"
                 "[bg][fg]overlay=x='if(lt(t,0.4),0,if(lt(t,0.6),(t-0.4)*200,40))':y=12,format=yuv444p")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-filter_complex", graph, "-c:v", "ffv1", cls.video], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def motion(self, *args):
        return subprocess.run([EXE, *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def test_one_segment_of_200_ms(self):
        r = self.motion("analyze", self.video, "--json", "--track", "FF0000")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["schema"], "opendap.motion-analysis/v1")
        self.assertEqual((doc["frames"], doc["fps"]), (60, 50.0))
        (seg,) = doc["segments"]
        self.assertEqual((seg["start"], seg["end"], seg["duration_ms"]), (0.4, 0.6, 200))
        self.assertEqual((seg["frames"], seg["held_frames"], seg["effective_fps"]), (11, 0, 50.0))
        self.assertEqual(seg["nearest_token"], "standard (200 ms)")
        self.assertIn("no overshoot; monotonic", seg["track"]["summary"])

    def test_still_region_has_no_motion(self):
        r = self.motion("analyze", self.video, "--json", "--roi", "0,32,64,16")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["segments"], [])

    def test_frames_dry_run_writes_nothing(self):
        out = os.path.join(self.tmp, "sheet.png")
        r = self.motion("frames", self.video, "--segment", "0", "--count", "3", "--out", out, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(out))

    def test_frames_writes_a_contact_sheet(self):
        out = os.path.join(self.tmp, "sheet2.png")
        r = self.motion("frames", self.video, "--at", "0.3,0.5,0.7", "--width", "32", "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out, "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
