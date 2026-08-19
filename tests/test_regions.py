#!/usr/bin/env python3
"""Region-diff + Chinese/English OCR tests for wechat-watch-regions."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "wechat-watch-regions")
PERSIST_FFMPEG = "/home/box/.local/share/wechat-persist/bin/ffmpeg"
SYSTEM_FFMPEG = "/usr/bin/ffmpeg"
CJK_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
KNOWN_ZH = "你好"
KNOWN_EN = "Hello"


def load_regions():
    import importlib.machinery
    loader = importlib.machinery.SourceFileLoader("wechat_watch_regions", SCRIPT)
    spec = importlib.util.spec_from_loader("wechat_watch_regions", loader)
    if spec is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


regions = load_regions()


def run_ffmpeg(ffmpeg: str, args: list[str]) -> None:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args]
    subprocess.run(cmd, check=True)


def write_solid_png(path: str, w: int, h: int, color: str = "white", ffmpeg: str = PERSIST_FFMPEG) -> None:
    run_ffmpeg(
        ffmpeg,
        [
            "-f",
            "lavfi",
            "-i",
            f"color={color}:s={w}x{h}",
            "-frames:v",
            "1",
            "-update",
            "1",
            path,
        ],
    )


def write_box_png(
    path: str,
    w: int,
    h: int,
    box: tuple[int, int, int, int],
    color: str = "black",
    bg: str = "white",
    ffmpeg: str = PERSIST_FFMPEG,
) -> None:
    x, y, bw, bh = box
    run_ffmpeg(
        ffmpeg,
        [
            "-f",
            "lavfi",
            "-i",
            f"color={bg}:s={w}x{h}",
            "-frames:v",
            "1",
            "-update",
            "1",
            "-vf",
            f"drawbox=x={x}:y={y}:w={bw}:h={bh}:color={color}:t=fill",
            path,
        ],
    )


def write_text_png(path: str, text: str, w: int = 640, h: int = 160) -> None:
    # persist ffmpeg has no drawtext; system ffmpeg + Noto Sans CJK does.
    ffmpeg = SYSTEM_FFMPEG if os.path.isfile(SYSTEM_FFMPEG) else "ffmpeg"
    font = CJK_FONT
    vf = (
        f"drawtext=fontfile={font}:text='{text}':"
        f"fontcolor=black:fontsize=48:x=40:y=50"
    )
    run_ffmpeg(
        ffmpeg,
        [
            "-f",
            "lavfi",
            "-i",
            f"color=white:s={w}x{h}",
            "-frames:v",
            "1",
            "-update",
            "1",
            "-vf",
            vf,
            path,
        ],
    )


class ClassifyOcrTests(unittest.TestCase):
    def test_cjk_is_text(self):
        kind, text = regions.classify_ocr("  你好  ")
        self.assertEqual(kind, "text")
        self.assertEqual(text, "你好")

    def test_two_alnum_is_text(self):
        kind, text = regions.classify_ocr("Hi")
        self.assertEqual(kind, "text")
        self.assertEqual(text, "Hi")

    def test_one_alnum_is_image(self):
        kind, text = regions.classify_ocr("A")
        self.assertEqual(kind, "image")
        self.assertEqual(text, "")

    def test_empty_is_image(self):
        kind, text = regions.classify_ocr("   \n  ")
        self.assertEqual(kind, "image")
        self.assertEqual(text, "")

    def test_punctuation_only_is_image(self):
        kind, text = regions.classify_ocr("...!!!")
        self.assertEqual(kind, "image")
        self.assertEqual(text, "")

    def test_mixed_cjk_english_is_text(self):
        kind, text = regions.classify_ocr("你好 Hello")
        self.assertEqual(kind, "text")
        self.assertIn("你好", text)


class FakeBoxRegionTests(unittest.TestCase):
    """Existing-style pixel-diff tests: a painted box must become a region."""

    def test_changed_box_is_detected(self):
        self.assertTrue(os.path.isfile(PERSIST_FFMPEG), "persist ffmpeg missing")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-test-") as td:
            prev = os.path.join(td, "prev.png")
            curr = os.path.join(td, "curr.png")
            write_solid_png(prev, 200, 200, "white")
            # Black rectangle well above MIN_BOX, away from edges.
            write_box_png(curr, 200, 200, (40, 50, 80, 60), "black")
            boxes, w, h, n = regions.compute_regions(prev, curr, PERSIST_FFMPEG)
            self.assertEqual((w, h), (200, 200))
            self.assertGreater(n, 0)
            self.assertEqual(len(boxes), 1)
            x, y, bw, bh = boxes[0]
            # pad=8 around the 40,50,80x60 painted box.
            self.assertLessEqual(x, 40)
            self.assertLessEqual(y, 50)
            self.assertGreaterEqual(x + bw, 40 + 80)
            self.assertGreaterEqual(y + bh, 50 + 60)
            # Box must cover the painted area (with pad, not the whole frame).
            self.assertLess(bw * bh, 200 * 200 * 0.4)

    def test_identical_frames_have_no_boxes(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-test-") as td:
            prev = os.path.join(td, "prev.png")
            curr = os.path.join(td, "curr.png")
            write_solid_png(prev, 120, 120, "white")
            write_solid_png(curr, 120, 120, "white")
            boxes, _w, _h, n = regions.compute_regions(prev, curr, PERSIST_FFMPEG)
            self.assertEqual(n, 0)
            self.assertEqual(boxes, [])

    def test_two_separated_boxes(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-test-") as td:
            prev = os.path.join(td, "prev.png")
            curr = os.path.join(td, "curr.png")
            write_solid_png(prev, 300, 300, "white")
            run_ffmpeg(
                PERSIST_FFMPEG,
                [
                    "-f",
                    "lavfi",
                    "-i",
                    "color=white:s=300x300",
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    "-vf",
                    "drawbox=x=20:y=20:w=50:h=50:color=black:t=fill,"
                    "drawbox=x=200:y=200:w=50:h=50:color=black:t=fill",
                    curr,
                ],
            )
            boxes, _w, _h, n = regions.compute_regions(prev, curr, PERSIST_FFMPEG)
            self.assertGreater(n, 0)
            self.assertEqual(len(boxes), 2)
            # Reading order: top-to-bottom, then left-to-right.
            self.assertLess(boxes[0][1], boxes[1][1])


class OcrTests(unittest.TestCase):
    def test_ocr_chinese_english_drawtext(self):
        self.assertTrue(os.path.isfile(CJK_FONT), f"missing CJK font: {CJK_FONT}")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-ocr-") as td:
            png = os.path.join(td, "hello.png")
            write_text_png(png, f"{KNOWN_ZH} {KNOWN_EN} 微信")
            text, kind = regions.ocr_png(png, (0, 0, 640, 160))
            self.assertEqual(kind, "text")
            self.assertIn(KNOWN_ZH[0], text)
            self.assertIn(KNOWN_ZH[1], text)
            # English may fold case; require the letters.
            folded = text.replace(" ", "")
            self.assertTrue(
                KNOWN_EN in text or KNOWN_EN.lower() in folded.lower(),
                f"expected {KNOWN_EN!r} in OCR {text!r}",
            )

    def test_ocr_blank_is_image(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-ocr-") as td:
            png = os.path.join(td, "blank.png")
            write_solid_png(png, 200, 200, "white")
            text, kind = regions.ocr_png(png, (0, 0, 200, 200))
            self.assertEqual(kind, "image")
            self.assertEqual(text, "")

    def test_cli_changed_emits_text_and_kind(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-cli-") as td:
            prev = os.path.join(td, "prev.png")
            curr = os.path.join(td, "curr.png")
            out_dir = os.path.join(td, "regions")
            js = os.path.join(td, "regions.json")
            write_solid_png(prev, 640, 160, "white")
            write_text_png(curr, f"{KNOWN_ZH} {KNOWN_EN}")
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--prev",
                    prev,
                    "--curr",
                    curr,
                    "--out-dir",
                    out_dir,
                    "--json",
                    js,
                    "--ffmpeg",
                    PERSIST_FFMPEG,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            lines = [ln for ln in proc.stdout.splitlines() if ln]
            self.assertTrue(lines[0].startswith("regions="), proc.stdout)
            self.assertTrue(any(ln.startswith("region=") for ln in lines), proc.stdout)
            kinds = [ln for ln in lines if ln.startswith("kind")]
            self.assertTrue(kinds, proc.stdout)
            self.assertTrue(
                any(ln.startswith("text0=") for ln in lines),
                f"missing text0= in:\n{proc.stdout}",
            )
            with open(js, encoding="utf-8") as fh:
                recs = json.loads(fh.read())
            self.assertGreaterEqual(len(recs), 1)
            self.assertIn("kind", recs[0])
            self.assertIn("text", recs[0])
            joined = " ".join(r.get("text", "") for r in recs)
            self.assertTrue(
                KNOWN_ZH[0] in joined or KNOWN_EN in joined,
                f"OCR missed known chars: {recs!r}",
            )

    def test_pick_psm_short_is_7(self):
        self.assertEqual(regions.pick_psm((10, 10, 40, 20)), 7)
        self.assertEqual(regions.pick_psm((0, 100, 16, 16)), 7)

    def test_pick_psm_block_is_6(self):
        self.assertEqual(regions.pick_psm((100, 100, 200, 80)), 6)
        # Full chat-list crop starts at x=0; must not be treated as a badge.
        self.assertEqual(regions.pick_psm((0, 0, 440, 700)), 6)
        # Tall narrow preview strip: still a block, not a single line.
        self.assertEqual(regions.pick_psm((356, 38, 84, 308)), 6)


if __name__ == "__main__":
    unittest.main()
