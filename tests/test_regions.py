#!/usr/bin/env python3
"""Region-diff + Chinese/English OCR + thread-pane tests for wechat-watch-regions."""
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



class ThreadPaneTests(unittest.TestCase):
    """Group-chat / right-pane: thread-sized split + left/right → in/out."""

    def test_infer_side_left_is_in(self):
        # 720-wide thread crop; left third ends at 240.
        self.assertEqual(regions.infer_side((20, 40, 80, 50), 720), "in")
        self.assertEqual(regions.infer_side((0, 10, 60, 40), 720), "in")

    def test_infer_side_right_is_out(self):
        # Right third starts at 480.
        self.assertEqual(regions.infer_side((500, 40, 80, 50), 720), "out")
        self.assertEqual(regions.infer_side((640, 80, 70, 40), 720), "out")

    def test_infer_side_middle_or_wide_is_unknown(self):
        self.assertEqual(regions.infer_side((300, 40, 80, 50), 720), "unknown")
        # Full-width fallback crop is not a single bubble.
        self.assertEqual(regions.infer_side((0, 0, 720, 660), 720), "unknown")

    def test_expand_top_grows_upward(self):
        box = (100, 80, 60, 40)
        grown = regions.expand_top(box, 720, 660, 22)
        self.assertEqual(grown[0], 100)
        self.assertEqual(grown[1], 58)
        self.assertEqual(grown[2], 60)
        self.assertEqual(grown[3], 62)
        self.assertEqual(regions.expand_top(box, 720, 660, 0), box)
        # Clamp at y=0.
        self.assertEqual(regions.expand_top((10, 5, 20, 20), 720, 660, 22)[1], 0)

    def test_thread_sized_crop_region_split(self):
        """A 720x620 thread crop with left + right bubbles yields two boxes."""
        self.assertTrue(os.path.isfile(PERSIST_FFMPEG), "persist ffmpeg missing")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-thread-") as td:
            prev = os.path.join(td, "prev.png")
            curr = os.path.join(td, "curr.png")
            write_solid_png(prev, 720, 620, "white")
            run_ffmpeg(
                PERSIST_FFMPEG,
                [
                    "-f",
                    "lavfi",
                    "-i",
                    "color=white:s=720x620",
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    "-vf",
                    "drawbox=x=30:y=80:w=120:h=70:color=black:t=fill,"
                    "drawbox=x=540:y=300:w=120:h=70:color=black:t=fill",
                    curr,
                ],
            )
            boxes, w, h, n = regions.compute_regions(prev, curr, PERSIST_FFMPEG)
            self.assertEqual((w, h), (720, 620))
            self.assertGreater(n, 0)
            self.assertEqual(len(boxes), 2)
            # Reading order: top-to-bottom.
            self.assertLess(boxes[0][1], boxes[1][1])
            sides = [regions.infer_side(b, w) for b in boxes]
            self.assertEqual(sides[0], "in")
            self.assertEqual(sides[1], "out")

    def test_cli_thread_prefix_label_side(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-thread-cli-") as td:
            prev = os.path.join(td, "prev.png")
            curr = os.path.join(td, "curr.png")
            out_dir = os.path.join(td, "thread-regions")
            js = os.path.join(td, "thread-regions.json")
            write_solid_png(prev, 720, 620, "white")
            write_box_png(curr, 720, 620, (520, 80, 100, 60), "black")
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
                    "--prefix",
                    "t",
                    "--label",
                    "thread_",
                    "--emit-side",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            lines = [ln for ln in proc.stdout.splitlines() if ln]
            self.assertTrue(lines[0].startswith("thread_regions="), proc.stdout)
            self.assertTrue(
                any(ln.startswith("thread_region=") and "/t0.png" in ln for ln in lines),
                proc.stdout,
            )
            self.assertTrue(any(ln == "thread_side0=out" for ln in lines), proc.stdout)
            self.assertTrue(any(ln.startswith("thread_kind0=") for ln in lines), proc.stdout)
            self.assertTrue(os.path.isfile(os.path.join(out_dir, "t0.png")))
            with open(js, encoding="utf-8") as fh:
                recs = json.loads(fh.read())
            self.assertEqual(recs[0].get("side"), "out")


class GcTests(unittest.TestCase):
    """wechat-watch-gc: expire clips/regions, keep hash crops + identities.json."""

    GC = os.path.join(ROOT, "wechat-watch-gc")

    def _touch_old(self, path: str, minutes_ago: int) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"x")
        age = minutes_ago * 60 + 30
        import time
        ts = time.time() - age
        os.utime(path, (ts, ts))

    def test_gc_keeps_hash_crops_and_expires_old_regions(self):
        self.assertTrue(os.path.isfile(self.GC), "wechat-watch-gc missing")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-gc-") as td:
            watch = td
            for name in ("list.png", "list.prev.png", "thread.png", "thread.prev.png"):
                with open(os.path.join(watch, name), "wb") as f:
                    f.write(b"keep")
            with open(os.path.join(watch, "identities.json"), "w") as f:
                f.write("{}")
            # current CHANGED set
            os.makedirs(os.path.join(watch, "regions"), exist_ok=True)
            current = os.path.join(watch, "regions", "r0.png")
            with open(current, "wb") as f:
                f.write(b"cur")
            with open(os.path.join(watch, "regions.json"), "w") as f:
                json.dump([{"path": current}], f)
            # stale region + old extra full
            stale = os.path.join(watch, "regions", "r9.png")
            self._touch_old(stale, 20)
            extra_full = os.path.join(watch, "full-old.png")
            self._touch_old(extra_full, 1)
            newest_full = os.path.join(watch, "full.png")
            with open(newest_full, "wb") as f:
                f.write(b"full")
            # parsed clip should vanish immediately
            os.makedirs(os.path.join(watch, "clips"), exist_ok=True)
            clip = os.path.join(watch, "clips", "scroll.mp4")
            with open(clip, "wb") as f:
                f.write(b"mp4")
            with open(os.path.join(watch, "clips", "scroll.json"), "w") as f:
                f.write("[]")
            # old avatar
            av = os.path.join(watch, "avatars", "a.png")
            self._touch_old(av, 8 * 24 * 60)
            proc = subprocess.run(
                ["bash", self.GC],
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, "WECHAT_WATCH_DIR": watch},
            )
            self.assertTrue(os.path.isfile(os.path.join(watch, "list.png")))
            self.assertTrue(os.path.isfile(os.path.join(watch, "thread.prev.png")))
            self.assertTrue(os.path.isfile(os.path.join(watch, "identities.json")))
            self.assertTrue(os.path.isfile(current), "latest region set must stay")
            self.assertFalse(os.path.isfile(stale), "15-min-old region must go")
            self.assertFalse(os.path.isfile(clip), "parsed clip must go")
            self.assertFalse(os.path.isfile(av), "7-day-old avatar must go")
            self.assertTrue(os.path.isfile(newest_full))
            self.assertFalse(os.path.isfile(extra_full), "older full*.png must go")

    def test_gc_excludes_images_from_leftover_keeps_sidecar(self):
        """15-min leftover must not wipe */images/*; 7-day expire may drop png only."""
        self.assertTrue(os.path.isfile(self.GC), "wechat-watch-gc missing")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-gc-img-") as td:
            recent = os.path.join(td, "images", "abc.png")
            self._touch_old(recent, 20)
            recent_side = os.path.join(td, "images", "abc.json")
            with open(recent_side, "w", encoding="utf-8") as f:
                f.write("{}")
            old = os.path.join(td, "images", "old.png")
            self._touch_old(old, 8 * 24 * 60)
            old_side = os.path.join(td, "images", "old.json")
            with open(old_side, "w", encoding="utf-8") as f:
                f.write("{}")
            subprocess.run(
                ["bash", self.GC],
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, "WECHAT_WATCH_DIR": td},
            )
            self.assertTrue(os.path.isfile(recent), "20-min image crop must survive leftover")
            self.assertTrue(os.path.isfile(recent_side), "sidecar json must stay")
            self.assertFalse(os.path.isfile(old), "7-day-old image crop may expire")
            self.assertTrue(os.path.isfile(old_side), "expired crop sidecar json must stay")


def write_banded_thread(
    path: str,
    w: int,
    h: int,
    y0: int,
    colors: list[str],
    thumb_y: int,
    thumb_h: int = 40,
    bar_h: int = 22,
    gap: int = 18,
    ffmpeg: str = PERSIST_FFMPEG,
    scrollbar: bool = True,
) -> None:
    """Synthetic thread crop: horizontal content bands + optional right scrollbar."""
    parts: list[str] = []
    if scrollbar:
        parts.append(f"drawbox=x={w - 12}:y=0:w=12:h={h}:color=0xDDDDDD:t=fill")
    for i, color in enumerate(colors):
        y = y0 + i * (bar_h + gap)
        if y + bar_h > h - 4:
            break
        parts.append(
            f"drawbox=x=24:y={y}:w={w - 50}:h={bar_h}:color={color}:t=fill"
        )
    if scrollbar:
        ty = max(0, min(h - thumb_h, thumb_y))
        parts.append(
            f"drawbox=x={w - 10}:y={ty}:w=8:h={thumb_h}:color=0x555555:t=fill"
        )
    vf = ",".join(parts) if parts else "null"
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


class AverageHashTests(unittest.TestCase):
    def test_solid_colors_are_stable_and_distinct(self):
        red = bytes([200, 20, 20] * 64)
        blue = bytes([20, 20, 200] * 64)
        white = bytes([255, 255, 255] * 64)
        h_red = regions.average_hash_rgb(red, 8, 8)
        h_blue = regions.average_hash_rgb(blue, 8, 8)
        h_white = regions.average_hash_rgb(white, 8, 8)
        self.assertGreaterEqual(len(h_red), 16)
        self.assertRegex(h_red, r"^[0-9a-f]+$")
        self.assertEqual(h_red, regions.average_hash_rgb(red, 8, 8))
        self.assertNotEqual(h_red, h_blue)
        self.assertNotEqual(h_red, h_white)

    def test_png_hash_roundtrip(self):
        self.assertTrue(os.path.isfile(PERSIST_FFMPEG), "persist ffmpeg missing")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-ahash-") as td:
            a = os.path.join(td, "a.png")
            b = os.path.join(td, "b.png")
            write_solid_png(a, 48, 48, "red")
            write_solid_png(b, 48, 48, "red")
            ha = regions.average_hash_png(a, PERSIST_FFMPEG)
            hb = regions.average_hash_png(b, PERSIST_FFMPEG)
            self.assertEqual(ha, hb)
            write_solid_png(b, 48, 48, "blue")
            self.assertNotEqual(ha, regions.average_hash_png(b, PERSIST_FFMPEG))


class IdentityTests(unittest.TestCase):
    def test_bind_and_lookup_roundtrip(self):
        data = regions.empty_identities()
        regions.bind_identity(data, "阿坤", "aabbccddeeff0011")
        self.assertEqual(regions.lookup_name(data, "aabbccddeeff0011"), "阿坤")
        self.assertEqual(regions.lookup_hash(data, "阿坤"), "aabbccddeeff0011")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-id-") as td:
            path = os.path.join(td, "identities.json")
            regions.save_identities(path, data)
            loaded = regions.load_identities(path)
            self.assertEqual(regions.lookup_name(loaded, "AABBCCDDEEFF0011"), "阿坤")

    def test_empty_name_or_hash_ignored(self):
        data = regions.empty_identities()
        regions.bind_identity(data, "阿坤", "")
        self.assertEqual(data["by_name"], {})
        regions.bind_identity(data, "", "abcd")
        self.assertIn("abcd", data["by_hash"])
        self.assertEqual(regions.lookup_name(data, "abcd"), "")


class ScrollDetectTests(unittest.TestCase):
    COLORS = ("red", "blue", "green", "black")

    def test_should_record_never_on_list_change(self):
        self.assertTrue(regions.should_record_scroll(False, True))
        self.assertFalse(regions.should_record_scroll(True, True))
        self.assertFalse(regions.should_record_scroll(False, False))
        self.assertFalse(regions.should_record_scroll(True, False))

    def test_cli_should_record_gates_list_change(self):
        def run(list_changed: int, scrolling: int) -> str:
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--should-record",
                    "--list-changed",
                    str(list_changed),
                    "--scrolling",
                    str(scrolling),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return proc.stdout.strip()

        self.assertEqual(run(0, 1), "record=1")
        self.assertEqual(run(1, 1), "record=0")
        self.assertEqual(run(0, 0), "record=0")

    def test_identical_frames_not_scrolling(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-scroll-") as td:
            prev = os.path.join(td, "prev.png")
            curr = os.path.join(td, "curr.png")
            write_banded_thread(prev, 360, 240, 30, self.COLORS, thumb_y=40)
            write_banded_thread(curr, 360, 240, 30, self.COLORS, thumb_y=40)
            info = regions.detect_scroll(prev, curr, PERSIST_FFMPEG)
            self.assertFalse(info["scrolling"])
            self.assertEqual(info["changed_frac"], 0.0)

    def test_shifted_bands_and_thumb_is_scrolling(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-scroll-") as td:
            prev = os.path.join(td, "prev.png")
            curr = os.path.join(td, "curr.png")
            write_banded_thread(prev, 360, 240, 20, self.COLORS, thumb_y=30)
            write_banded_thread(curr, 360, 240, 56, self.COLORS, thumb_y=70)
            info = regions.detect_scroll(prev, curr, PERSIST_FFMPEG)
            self.assertTrue(info["scrolling"], info)
            self.assertGreaterEqual(abs(info["shift"]), regions.SCROLL_SHIFT_MIN)
            self.assertTrue(info["scrollbar"])

    def test_small_new_bubble_is_not_scrolling(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-scroll-") as td:
            prev = os.path.join(td, "prev.png")
            curr = os.path.join(td, "curr.png")
            write_banded_thread(prev, 360, 240, 20, self.COLORS, thumb_y=160)
            # Same bands + a tiny new box; thumb stays put.
            write_banded_thread(curr, 360, 240, 20, self.COLORS, thumb_y=160)
            run_ffmpeg(
                PERSIST_FFMPEG,
                [
                    "-i",
                    curr,
                    "-vf",
                    "drawbox=x=80:y=200:w=36:h=20:color=black:t=fill",
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    curr + ".new.png",
                ],
            )
            os.replace(curr + ".new.png", curr)
            info = regions.detect_scroll(prev, curr, PERSIST_FFMPEG)
            self.assertFalse(info["scrolling"], info)

    def test_chat_switch_is_not_scrolling(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-scroll-") as td:
            prev = os.path.join(td, "prev.png")
            curr = os.path.join(td, "curr.png")
            write_banded_thread(prev, 360, 240, 20, self.COLORS, thumb_y=30)
            # Unrelated pattern: vertical-ish solid blocks, no shared row structure.
            write_solid_png(curr, 360, 240, "0x3366CC")
            info = regions.detect_scroll(prev, curr, PERSIST_FFMPEG)
            self.assertFalse(info["scrolling"], info)

    def test_cli_detect_scroll(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-scroll-cli-") as td:
            prev = os.path.join(td, "prev.png")
            curr = os.path.join(td, "curr.png")
            write_banded_thread(prev, 360, 240, 20, self.COLORS, thumb_y=30)
            write_banded_thread(curr, 360, 240, 56, self.COLORS, thumb_y=70)
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--detect-scroll",
                    "--prev",
                    prev,
                    "--curr",
                    curr,
                    "--ffmpeg",
                    PERSIST_FFMPEG,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("scrolling=1", proc.stdout)


class TimelineParseTests(unittest.TestCase):
    def test_parse_frames_emits_timeline_and_binds_avatar(self):
        self.assertTrue(os.path.isfile(PERSIST_FFMPEG), "persist ffmpeg missing")
        self.assertTrue(os.path.isfile(CJK_FONT), f"missing CJK font: {CJK_FONT}")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-tl-") as td:
            frames = os.path.join(td, "frames")
            os.makedirs(frames)
            prev = os.path.join(frames, "f0001.png")
            curr = os.path.join(frames, "f0002.png")
            write_solid_png(prev, 720, 220, "white")
            # Incoming row: red avatar on the left + CJK text (system ffmpeg drawtext).
            ffmpeg = SYSTEM_FFMPEG if os.path.isfile(SYSTEM_FFMPEG) else "ffmpeg"
            vf = (
                "drawbox=x=8:y=50:w=40:h=40:color=red:t=fill,"
                f"drawtext=fontfile={CJK_FONT}:text='{KNOWN_ZH}':"
                "fontcolor=black:fontsize=36:x=70:y=54"
            )
            run_ffmpeg(
                ffmpeg,
                [
                    "-f",
                    "lavfi",
                    "-i",
                    "color=white:s=720x220",
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    "-vf",
                    vf,
                    curr,
                ],
            )
            timeline_path = os.path.join(td, "timeline.json")
            identities_path = os.path.join(td, "identities.json")
            avatars_dir = os.path.join(td, "avatars")
            recs = regions.parse_frames_dir(
                frames,
                PERSIST_FFMPEG,
                timeline_path,
                identities_path,
                avatars_dir,
                fps=4.0,
            )
            self.assertTrue(os.path.isfile(timeline_path))
            with open(timeline_path, encoding="utf-8") as fh:
                saved = json.loads(fh.read())
            self.assertEqual(saved, recs)
            self.assertGreaterEqual(len(recs), 1)
            ev = recs[0]
            self.assertIn(ev["side"], ("in", "out", "unknown"))
            self.assertIn("t", ev)
            self.assertIn("name", ev)
            self.assertIn("avatar_hash", ev)
            self.assertIn("text", ev)
            # Incoming left box should classify as in and carry an aHash.
            ins = [e for e in recs if e["side"] == "in"]
            if ins:
                self.assertRegex(ins[0]["avatar_hash"], r"^[0-9a-f]{16,}$")
                joined = " ".join(e.get("text", "") for e in recs)
                if KNOWN_ZH[0] in joined or KNOWN_ZH in joined:
                    self.assertTrue(os.path.isfile(identities_path))

    def test_timeline_event_shape(self):
        ev = regions._timeline_event(0.5, "in", "阿坤", "abcd", "hello")
        self.assertEqual(
            ev,
            {
                "t": 0.5,
                "side": "in",
                "name": "阿坤",
                "avatar_hash": "abcd",
                "text": "hello",
            },
        )


class DiffScriptContractTests(unittest.TestCase):
    """wechat-watch-diff must hash the left list only and never start video on list change."""

    DIFF = os.path.join(ROOT, "wechat-watch-diff")

    def test_diff_script_hashes_list_only_and_gates_video(self):
        self.assertTrue(os.path.isfile(self.DIFF))
        with open(self.DIFF, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("LEFT chat list", text)
        self.assertIn("maybe_record_scroll", text)
        self.assertIn("should-record", text)
        self.assertIn("detect-scroll", text)
        self.assertIn("x11grab", text)
        # Video grab uses the thread crop offset, not the full desktop.
        self.assertIn("${THREAD_X}", text)
        self.assertIn("${THREAD_Y}", text)
        # CHANGED (list) path must not call record.
        changed_tail = text.split('echo "CHANGED"', 1)[-1]
        self.assertNotIn("maybe_record_scroll", changed_tail)
        self.assertIn("run_gc", changed_tail)



class SummaryTimeTests(unittest.TestCase):
    def test_known_utc_instant_labels_shanghai(self):
        from datetime import datetime, timezone

        when = datetime(2026, 8, 19, 7, 2, 0, tzinfo=timezone.utc)
        times = regions.format_summary_times(when)
        self.assertEqual(times["utc"], "2026-08-19T07:02:00+00:00")
        self.assertEqual(times["shanghai"], "2026-08-19T15:02:00+08:00")
        self.assertEqual(times["utc_label"], "UTC")
        self.assertEqual(times["shanghai_label"], "Asia/Shanghai")

    def test_naive_when_is_utc(self):
        from datetime import datetime

        times = regions.format_summary_times(datetime(2026, 1, 1, 0, 0, 0))
        self.assertEqual(times["utc"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(times["shanghai"], "2026-01-01T08:00:00+08:00")

    def test_parse_when_z_and_offset(self):
        dt = regions.parse_when("2026-08-19T07:02:00Z")
        self.assertEqual(regions.format_summary_times(dt)["shanghai"], "2026-08-19T15:02:00+08:00")
        dt2 = regions.parse_when("2026-08-19T15:02:00+08:00")
        self.assertEqual(regions.format_summary_times(dt2)["utc"], "2026-08-19T07:02:00+00:00")

    def test_cli_format_time(self):
        proc = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--format-time",
                "--when",
                "2026-08-19T07:02:00+00:00",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("at_utc=2026-08-19T07:02:00+00:00", proc.stdout)
        self.assertIn("at_shanghai=2026-08-19T15:02:00+08:00", proc.stdout)

    def test_crop_constants_match_diff_script(self):
        self.assertEqual(regions.LIST_CROP, (440, 700, 70, 30))
        self.assertEqual(regions.THREAD_CROP, (720, 660, 414, 40))
        with open(os.path.join(ROOT, "wechat-watch-diff"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("CROP_W=440", src)
        self.assertIn("CROP_H=700", src)
        self.assertIn("CROP_X=70", src)
        self.assertIn("CROP_Y=30", src)
        self.assertIn("THREAD_W=720", src)
        self.assertIn("THREAD_H=660", src)
        self.assertIn("THREAD_X=414", src)
        self.assertIn("THREAD_Y=40", src)


class ThreadHelperTests(unittest.TestCase):
    HELPER = os.path.join(ROOT, "wechat-watch-thread")
    DOC = os.path.join(ROOT, "docs", "group-handling.md")

    def test_helper_is_readonly_and_uses_thread_crop(self):
        self.assertTrue(os.path.isfile(self.HELPER))
        self.assertTrue(os.access(self.HELPER, os.X_OK))
        with open(self.HELPER, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("THREAD_W=720", src)
        self.assertIn("THREAD_H=660", src)
        self.assertIn("THREAD_X=414", src)
        self.assertIn("THREAD_Y=40", src)
        self.assertIn("--ocr-still", src)
        self.assertIn("wechat-group-summaries", src)
        self.assertIn("never_send=1", src)
        self.assertIn("list_is_signal_only=1", src)
        self.assertNotIn("compose", src.lower())

    def test_helper_png_ocrs_thread_and_prints_dual_times(self):
        self.assertTrue(os.path.isfile(CJK_FONT), "missing CJK font: %s" % CJK_FONT)
        with tempfile.TemporaryDirectory(prefix="wechat-watch-th-") as td:
            png = os.path.join(td, "thread.png")
            write_text_png(png, KNOWN_ZH, w=720, h=660)
            out_json = os.path.join(td, "out.json")
            watch = os.path.join(td, "watch")
            os.makedirs(watch)
            proc = subprocess.run(
                [
                    "bash",
                    self.HELPER,
                    "--png",
                    png,
                    "--json",
                    out_json,
                    "--when",
                    "2026-08-19T07:02:00+00:00",
                ],
                check=True,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "WECHAT_WATCH_DIR": watch,
                    "WECHAT_PERSIST": os.path.dirname(watch),
                },
            )
            self.assertIn("never_send=1", proc.stdout)
            self.assertIn("list_is_signal_only=1", proc.stdout)
            self.assertIn("summaries_repo=wechat-group-summaries", proc.stdout)
            self.assertIn("at_utc=2026-08-19T07:02:00+00:00", proc.stdout)
            self.assertIn("at_shanghai=2026-08-19T15:02:00+08:00", proc.stdout)
            self.assertIn("source=thread", proc.stdout)
            self.assertIn("crop=720x660+414+40", proc.stdout)
            self.assertTrue(os.path.isfile(out_json))
            with open(out_json, encoding="utf-8") as fh:
                recs = json.loads(fh.read())
            self.assertGreaterEqual(len(recs), 1)
            joined = " ".join(r.get("text", "") for r in recs)
            self.assertTrue(
                KNOWN_ZH[0] in joined or KNOWN_ZH in joined,
                "thread OCR missed known chars: %r" % recs,
            )

    def test_cli_ocr_still(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-still-") as td:
            png = os.path.join(td, "thread.png")
            write_text_png(png, KNOWN_EN, w=720, h=160)
            out_json = os.path.join(td, "out.json")
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--ocr-still",
                    png,
                    "--json",
                    out_json,
                    "--when",
                    "2026-08-19T07:02:00Z",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("never_send=1", proc.stdout)
            self.assertIn("at_shanghai=2026-08-19T15:02:00+08:00", proc.stdout)
            self.assertTrue(os.path.isfile(out_json))

    def test_group_doc_states_required_method(self):
        self.assertTrue(os.path.isfile(self.DOC))
        with open(self.DOC, encoding="utf-8") as fh:
            doc = fh.read()
        for needle in (
            "列表预览只是信号",
            "必须点进群",
            "右侧对话区",
            "独立产品创业联盟3群",
            "wechat-group-summaries",
            "Asia/Shanghai",
            "440x700+70+30",
            "720x660+414+40",
            "identities.json",
            "wechat-watch-gc",
        ):
            self.assertIn(needle, doc)
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn("docs/group-handling.md", readme)
        self.assertIn("--detect-unread", readme)
        self.assertIn("[N条]", doc)
        self.assertIn("--detect-unread", doc)


def _parse_unread_stdout(stdout: str) -> dict:
    rec = {}
    for ln in stdout.splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            rec[k] = v
    return rec


def write_red_circle_png(
    path: str,
    w: int,
    h: int,
    cx: int,
    cy: int,
    radius: int,
    digit: str | None = None,
    fontsize: int = 16,
) -> None:
    """Synthetic WeChat badge or muted dot via ffmpeg geq (+ optional white digit)."""
    ffmpeg = SYSTEM_FFMPEG if os.path.isfile(SYSTEM_FFMPEG) else "ffmpeg"
    geq = (
        f"geq=r='if(lt(hypot(X-{cx},Y-{cy}),{radius}),250,255)':"
        f"g='if(lt(hypot(X-{cx},Y-{cy}),{radius}),81,255)':"
        f"b='if(lt(hypot(X-{cx},Y-{cy}),{radius}),81,255)'"
    )
    vf = geq
    if digit:
        font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        # Center the digit roughly inside the circle.
        tx = max(0, cx - fontsize // 3)
        ty = max(0, cy - fontsize // 2)
        vf = (
            f"{geq},drawtext=fontfile={font}:text='{digit}':"
            f"fontcolor=white:fontsize={fontsize}:x={tx}:y={ty}"
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


class UnreadMarkerTests(unittest.TestCase):
    """Left-list unread: red number badge, muted red dot, [N条] text."""

    def test_wechat_red_and_not_avatar_pink(self):
        self.assertTrue(regions.is_wechat_red(250, 81, 81))
        self.assertTrue(regions.is_wechat_red(255, 0, 0))
        self.assertFalse(regions.is_wechat_red(255, 255, 255))
        self.assertFalse(regions.is_wechat_red(80, 80, 80))
        # Skin / warm avatar: not badge-red.
        self.assertFalse(regions.is_wechat_red(220, 160, 140))

    def test_parse_text_marks_tiao_at_z(self):
        marks = regions.parse_text_marks("ragnarok: [3条] hello @me")
        labels = [m["label"] for m in marks]
        self.assertIn("[3条]", labels)
        self.assertIn("@", labels)
        tiao = next(m for m in marks if m["label"] == "[3条]")
        self.assertEqual(tiao["count"], 3)
        self.assertEqual(tiao["kind"], "text")
        zmarks = regions.parse_text_marks("preview z end")
        self.assertEqual([m["label"] for m in zmarks], ["z"])
        zm2 = regions.parse_text_marks("Z")
        self.assertEqual([m["label"] for m in zm2], ["Z"])
        # Do not invent marks; do not treat z inside a Latin word.
        self.assertEqual(regions.parse_text_marks("Zoom soze 你好"), [])
        self.assertEqual(regions.parse_text_marks("[Photo] [Link]"), [])
        twelve = regions.parse_text_marks("[12条]")
        self.assertEqual(twelve[0]["count"], 12)

    def test_unread_source_box_full_desktop(self):
        self.assertEqual(regions.unread_source_box(1280, 800), (70, 30, 440, 700))
        self.assertIsNone(regions.unread_source_box(440, 700))
        self.assertIsNone(regions.unread_source_box(200, 100))

    def test_unread_source_box_window_local(self):
        self.assertEqual(regions.unread_source_box(1280, 800), (70, 30, 440, 700))
        self.assertIsNone(regions.unread_source_box(440, 700))
        self.assertIsNone(regions.unread_source_box(200, 100))
        box = regions.unread_source_box(1019, 736)
        self.assertIsNotNone(box)
        self.assertNotEqual(box, (70, 30, 440, 700))
        self.assertLessEqual(abs(box[0] - regions.LIST_INSET), 2, box)
        self.assertNotEqual(box[0], 70)
        # 200px list: avatar_r=64, badge_l=144. cx=175 kept; cx=40 dropped.
        self.assertTrue(regions.is_list_row_badge((163, 20, 24, 24), 200))
        self.assertFalse(regions.is_list_row_badge((28, 20, 24, 24), 200))
        # 440px list: avatar_r=96, badge_l=316.
        self.assertTrue(regions.is_list_row_badge((400, 20, 24, 24), 440))
        self.assertFalse(regions.is_list_row_badge((28, 20, 24, 24), 440))

    def test_red_circle_digit_is_number(self):
        self.assertTrue(os.path.isfile(SYSTEM_FFMPEG), "system ffmpeg missing")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-unread-n-") as td:
            png = os.path.join(td, "badge.png")
            write_red_circle_png(png, 200, 100, cx=175, cy=36, radius=12, digit="5", fontsize=16)
            recs = regions.detect_unread(png, PERSIST_FFMPEG)
            self.assertEqual(len(recs), 1, recs)
            self.assertEqual(recs[0]["kind"], "number")
            self.assertEqual(recs[0]["count"], 5)
            self.assertGreater(recs[0]["w"], 0)
            self.assertGreater(recs[0]["h"], 0)

    def test_small_red_dot_is_dot(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-unread-d-") as td:
            png = os.path.join(td, "dot.png")
            write_red_circle_png(png, 120, 80, cx=100, cy=40, radius=4, digit=None)
            recs = regions.detect_unread(png, PERSIST_FFMPEG)
            self.assertEqual(len(recs), 1, recs)
            self.assertEqual(recs[0]["kind"], "dot")
            self.assertEqual(recs[0]["count"], 0)

    def test_tiao_preview_is_text(self):
        self.assertTrue(os.path.isfile(CJK_FONT), "missing CJK font")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-unread-t-") as td:
            png = os.path.join(td, "tiao.png")
            write_text_png(png, "[3条]")
            recs = regions.detect_unread(png, PERSIST_FFMPEG)
            self.assertTrue(recs, "expected [3条] text unread")
            text_recs = [r for r in recs if r["kind"] == "text"]
            self.assertTrue(text_recs, recs)
            self.assertEqual(text_recs[0]["count"], 3)
            self.assertIn("3", text_recs[0]["label"])
            self.assertIn("条", text_recs[0]["label"])

    def test_cli_detect_unread_number(self):
        self.assertTrue(os.path.isfile(SYSTEM_FFMPEG), "system ffmpeg missing")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-unread-cli-") as td:
            png = os.path.join(td, "badge.png")
            write_red_circle_png(png, 200, 100, cx=175, cy=36, radius=12, digit="5", fontsize=16)
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--detect-unread",
                    png,
                    "--ffmpeg",
                    PERSIST_FFMPEG,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            parsed = _parse_unread_stdout(proc.stdout)
            self.assertIn("unread_rows", parsed, proc.stdout)
            self.assertEqual(int(parsed["unread_rows"]), 1, proc.stdout)
            self.assertEqual(parsed.get("unread0_kind"), "number", proc.stdout)
            self.assertEqual(parsed.get("unread0_count"), "5", proc.stdout)
            self.assertEqual(parsed.get("unread0"), "5", proc.stdout)
            self.assertIn("unread0_x", parsed)
            self.assertIn("unread0_y", parsed)
            self.assertIn("unread0_w", parsed)
            self.assertIn("unread0_h", parsed)
            self.assertIn("unread0_name", parsed)

    def test_blank_list_has_no_unread(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-unread-b-") as td:
            png = os.path.join(td, "blank.png")
            write_solid_png(png, 200, 100, "white")
            recs = regions.detect_unread(png, PERSIST_FFMPEG)
            self.assertEqual(recs, [])

    def test_avatar_red_is_not_unread(self):
        self.assertTrue(os.path.isfile(SYSTEM_FFMPEG), "system ffmpeg missing")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-unread-av-") as td:
            png = os.path.join(td, "avatar.png")
            write_red_circle_png(png, 200, 100, cx=28, cy=40, radius=16, digit="7", fontsize=16)
            recs = regions.detect_unread(png, PERSIST_FFMPEG)
            self.assertEqual(recs, [])

    def test_selected_row_highlight_is_not_unread(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-unread-hl-") as td:
            png = os.path.join(td, "highlight.png")
            write_box_png(png, 200, 80, (0, 8, 200, 64), color="0xE8E8E8", bg="white")
            recs = regions.detect_unread(png, PERSIST_FFMPEG)
            self.assertEqual(recs, [])

    def test_unread_compact_tokens(self):
        self.assertEqual(
            regions.unread_line_token({"kind": "number", "count": 5}), "5"
        )
        self.assertEqual(
            regions.unread_line_token({"kind": "dot", "count": 0}), "dot"
        )
        self.assertEqual(
            regions.unread_line_token({"kind": "text", "count": 3}), "3"
        )
        self.assertEqual(
            regions.unread_line_token({"kind": "text", "count": 0}), "0"
        )
        self.assertTrue(os.path.isfile(SYSTEM_FFMPEG), "system ffmpeg missing")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-unread-c-") as td:
            five = os.path.join(td, "five.png")
            write_red_circle_png(five, 200, 100, cx=175, cy=36, radius=12, digit="5", fontsize=16)
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--detect-unread",
                    five,
                    "--ffmpeg",
                    PERSIST_FFMPEG,
                    "--unread-compact",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            parsed = _parse_unread_stdout(proc.stdout)
            self.assertEqual(parsed.get("unread_rows"), "1", proc.stdout)
            self.assertEqual(parsed.get("unread0"), "5", proc.stdout)
            self.assertNotIn("unread0_kind", parsed, proc.stdout)
            self.assertNotIn("unread0_name", parsed, proc.stdout)

            dot = os.path.join(td, "dot.png")
            write_red_circle_png(dot, 200, 80, cx=175, cy=40, radius=4, digit=None)
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--detect-unread",
                    dot,
                    "--ffmpeg",
                    PERSIST_FFMPEG,
                    "--unread-compact",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            parsed = _parse_unread_stdout(proc.stdout)
            self.assertEqual(parsed.get("unread_rows"), "1", proc.stdout)
            self.assertEqual(parsed.get("unread0"), "dot", proc.stdout)

            blank = os.path.join(td, "blank.png")
            write_solid_png(blank, 200, 100, "white")
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--detect-unread",
                    blank,
                    "--ffmpeg",
                    PERSIST_FFMPEG,
                    "--unread-compact",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            parsed = _parse_unread_stdout(proc.stdout)
            self.assertEqual(parsed.get("unread_rows"), "0", proc.stdout)
            self.assertNotIn("unread0", parsed, proc.stdout)
            cheap = regions.detect_unread(five, PERSIST_FFMPEG, cheap=True)
            self.assertEqual(len(cheap), 1, cheap)
            self.assertEqual(cheap[0]["kind"], "number")
            self.assertEqual(cheap[0]["count"], 5)
            self.assertEqual(cheap[0].get("name") or "", "")

    def test_diff_calls_unread_compact(self):
        with open(os.path.join(ROOT, "wechat-watch-diff"), encoding="utf-8") as fh:
            diff = fh.read()
        self.assertIn("--detect-unread", diff)
        self.assertIn("--unread-compact", diff)
        self.assertNotIn("--cache-images", diff)
        cheap = diff.split('if [ "$LIST_CHANGED" -eq 0 ]', 1)[1]
        cheap_branch = cheap.split("fi", 1)[0]
        self.assertIn('echo "UNCHANGED"', cheap_branch)
        self.assertIn("exit 0", cheap_branch)
        self.assertNotIn("--detect-unread", cheap_branch)
        self.assertNotIn("--unread-compact", cheap_branch)
        after = cheap.split("fi", 1)[1]
        self.assertIn("--detect-unread", after)
        self.assertIn("--unread-compact", after)
        self.assertIn("UNREAD_OUT", after)
        self.assertIn("flap=1", after)
        self.assertIn('--detect-unread "$CROP"', after)
        self.assertIn('--detect-unread "$PREV"', after)
        self.assertIn("unread[0-9]", after)

    def test_diff_unread_on_changed(self):
        with open(os.path.join(ROOT, "wechat-watch-diff"), encoding="utf-8") as fh:
            diff = fh.read()
        self.assertIn("--detect-unread", diff)
        self.assertIn("--unread-compact", diff)
        self.assertNotIn("--cache-images", diff)
        cheap = diff.split('if [ "$LIST_CHANGED" -eq 0 ]', 1)[1]
        cheap_branch = cheap.split("fi", 1)[0]
        self.assertNotIn("--detect-unread", cheap_branch)
        after = cheap.split("fi", 1)[1]
        self.assertIn('echo "CHANGED"', after)
        self.assertIn("UNREAD_OUT", after)
        self.assertIn('printf \'%s\\n\' "$UNREAD_OUT"', after)
        # Badge-only: same text fingerprint, different unread tokens → CHANGED.
        self.assertIn("UNREAD_SAME", after)
        self.assertIn('--detect-unread "$PREV"', after)
        self.assertIn("unread[0-9]", after)


class NavIconTests(unittest.TestCase):
    """Left-nav icon slots + red badge/dot. Icon-only; no glyph OCR."""

    def test_blank_nav_has_no_badges(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-nav-b-") as td:
            png = os.path.join(td, "nav.png")
            write_solid_png(png, 62, 736, "white")
            recs = regions.detect_nav(png, PERSIST_FFMPEG)
            self.assertEqual(len(recs), len(regions.NAV_SLOTS))
            self.assertEqual([r["id"] for r in recs], [s[0] for s in regions.NAV_SLOTS])
            for rec in recs:
                self.assertEqual(rec["badge"], "none", rec)
                self.assertEqual(rec["count"], 0)
                self.assertNotIn(rec["badge"], ("number", "dot"))

    def test_chat_red_circle_digit(self):
        self.assertTrue(os.path.isfile(SYSTEM_FFMPEG), "system ffmpeg missing")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-nav-n-") as td:
            png = os.path.join(td, "nav.png")
            write_red_circle_png(
                png, 62, 736, cx=38, cy=88, radius=8, digit="4", fontsize=12
            )
            recs = regions.detect_nav(png, PERSIST_FFMPEG)
            chat = next(r for r in recs if r["id"] == "chat" or r["name"] == "chat")
            self.assertEqual(chat["badge"], "number", recs)
            self.assertEqual(chat["count"], 4)

    def test_chat_small_red_dot(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-nav-d-") as td:
            png = os.path.join(td, "nav.png")
            write_red_circle_png(png, 62, 736, cx=38, cy=88, radius=4, digit=None)
            recs = regions.detect_nav(png, PERSIST_FFMPEG)
            chat = next(r for r in recs if r["id"] == "chat" or r["name"] == "chat")
            self.assertEqual(chat["badge"], "dot", recs)
            self.assertEqual(chat["count"], 0)

    def test_cli_detect_nav(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-nav-cli-") as td:
            png = os.path.join(td, "nav.png")
            write_solid_png(png, 62, 736, "white")
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--detect-nav",
                    png,
                    "--ffmpeg",
                    PERSIST_FFMPEG,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            parsed = _parse_unread_stdout(proc.stdout)
            self.assertIn("nav_slots", parsed, proc.stdout)
            self.assertEqual(int(parsed["nav_slots"]), 10, proc.stdout)
            self.assertEqual(parsed.get("nav0_id"), "avatar", proc.stdout)
            self.assertEqual(parsed.get("nav1_id"), "chat", proc.stdout)
            self.assertEqual(parsed.get("nav0_badge"), "none", proc.stdout)
            self.assertEqual(parsed.get("nav0_count"), "0", proc.stdout)
            for i in range(10):
                for key in ("id", "badge", "count", "x", "y", "w", "h"):
                    self.assertIn(f"nav{i}_{key}", parsed, proc.stdout)

    def test_window_crops_floating(self):
        crops = regions.window_crops(130, 6, 1019, 736)
        nx, _ny, nw, _nh = crops["nav"]
        lx, _ly, _lw, _lh = crops["list"]
        tx, _ty, _tw, _th = crops["thread"]
        self.assertGreaterEqual(nx, 128)
        self.assertLessEqual(nx, 135)
        self.assertGreaterEqual(lx, 190)
        self.assertLessEqual(lx, 196)
        self.assertGreaterEqual(tx, 408)
        self.assertLessEqual(tx, 416)
        self.assertTrue(50 <= nw <= 70)

    def test_window_crops_maximized(self):
        crops = regions.window_crops(0, 0, 1280, 800)
        lx, ly, lw, lh = crops["list"]
        self.assertEqual((lx, ly), (70, 30))
        self.assertEqual((lw, lh), (440, 700))

    def test_nav_source_box(self):
        box = regions.nav_source_box(1280, 800)
        self.assertIsNotNone(box)
        x, _y, w, _h = box
        self.assertLessEqual(abs(x - 0), 2)
        self.assertTrue(50 <= w <= 70)
        self.assertIsNone(regions.nav_source_box(62, 736))
        self.assertIsNone(regions.nav_source_box(200, 100))

    def test_docs_mention_detect_nav(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        doc_path = os.path.join(ROOT, "docs", "group-handling.md")
        with open(doc_path, encoding="utf-8") as fh:
            doc = fh.read()
        self.assertTrue(
            "--detect-nav" in readme or "--detect-nav" in doc,
            "README.md and/or docs/group-handling.md must mention --detect-nav",
        )


FAKE_XWININFO_TREE = """
xwininfo: Window id: 0x15e (the root window) (has no name)

  Root window id: 0x15e (the root window) (has no name)

     4 children:
     0x200007 "WeChat": ("wechat" "WeChat")  1019x736+130+6  +130+6
        1 child:
        0x200008 (has no name): ()  1019x736+0+0  +130+6
     0x1600001 "Desktop": ("xfdesktop" "Xfdesktop")  1280x800+0+0  +0+0
     0x1800022 "wechat-watch": ("gnome-terminal" "Gnome-terminal")  800x500+10+10  +10+10
     0x1a00003 "Clock": ("clock" "Clock")  120x40+20+20  +20+20
"""

FAKE_WMCTRL_LG = """
0x01600001  0 0    0    1280 800 box Desktop
0x02000007  0 130  6    1019 736 box WeChat
0x01800022  0 10   10   800  500 box wechat-watch
0x01a00003  0 20   20   120  40  box Clock
"""


class WindowGeomTests(unittest.TestCase):
    """Find-window helper: env, fake tree/wm dumps, desktop fallback. No live app."""

    def test_parse_geom_spec_x11_and_csv(self):
        self.assertEqual(regions.parse_geom_spec("1019x736+130+6"), (130, 6, 1019, 736))
        self.assertEqual(regions.parse_geom_spec("1019x736-20+8"), (-20, 8, 1019, 736))
        self.assertEqual(regions.parse_geom_spec("130,6,1019,736"), (130, 6, 1019, 736))
        self.assertEqual(regions.parse_geom_spec(" 130, 6, 1019, 736 "), (130, 6, 1019, 736))
        self.assertIsNone(regions.parse_geom_spec(""))
        self.assertIsNone(regions.parse_geom_spec("not-a-geom"))
        self.assertIsNone(regions.parse_geom_spec("0x0+0+0"))

    def test_parse_root_tree_fake_string(self):
        box = regions.parse_root_tree(FAKE_XWININFO_TREE)
        self.assertEqual(box, (130, 6, 1019, 736))
        self.assertIsNone(regions.parse_root_tree("no windows here"))
        zh = '     0x200007 "微信": ("wechat" "WeChat")  900x700+40+20  +40+20\n'
        self.assertEqual(regions.parse_root_tree(zh), (40, 20, 900, 700))

    def test_parse_wm_geometry_fake_string(self):
        box = regions.parse_wm_geometry(FAKE_WMCTRL_LG)
        self.assertEqual(box, (130, 6, 1019, 736))
        self.assertIsNone(regions.parse_wm_geometry("0x1 0 0 0 1280 800 box Desktop"))

    def test_find_window_env_override(self):
        win, source = regions.find_window(
            env={"WECHAT_WINDOW": "200,40,900,700"},
            tree_text=FAKE_XWININFO_TREE,
            wm_text=FAKE_WMCTRL_LG,
            probe=False,
        )
        self.assertEqual(win, (200, 40, 900, 700))
        self.assertEqual(source, "env")
        win2, src2 = regions.find_window(
            env={"WECHAT_WINDOW": "900x700+40+20"},
            tree_text=FAKE_XWININFO_TREE,
            probe=False,
        )
        self.assertEqual(win2, (40, 20, 900, 700))
        self.assertEqual(src2, "env")

    def test_find_window_tree_then_wm(self):
        win, source = regions.find_window(
            env={},
            tree_text=FAKE_XWININFO_TREE,
            wm_text=FAKE_WMCTRL_LG,
            probe=False,
        )
        self.assertEqual(win, (130, 6, 1019, 736))
        self.assertEqual(source, "tree")
        win2, src2 = regions.find_window(
            env={},
            tree_text="nothing matching",
            wm_text=FAKE_WMCTRL_LG,
            probe=False,
        )
        self.assertEqual(win2, (130, 6, 1019, 736))
        self.assertEqual(src2, "wm")

    def test_find_window_desktop_fallback(self):
        win, source = regions.find_window(
            env={},
            tree_text="no WeChat here",
            wm_text="",
            probe=False,
        )
        self.assertEqual(win, (0, 0, 1280, 800))
        self.assertEqual(source, "desktop")
        crops = regions.window_crops(*win)
        self.assertEqual(crops["list"], (70, 30, 440, 700))
        self.assertEqual(crops["thread"], (414, 40, 720, 660))

    def test_cli_window_geom_env(self):
        env = {**os.environ, "WECHAT_WINDOW": "1019x736+130+6"}
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--window-geom"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        parsed = _parse_unread_stdout(proc.stdout)
        self.assertEqual(parsed.get("win"), "130,6,1019,736", proc.stdout)
        self.assertEqual(parsed.get("source"), "env", proc.stdout)
        for key in ("nav", "list", "thread"):
            self.assertIn(key, parsed, proc.stdout)
            parts = parsed[key].split(",")
            self.assertEqual(len(parts), 4, parsed[key])
            self.assertTrue(all(p.lstrip("-").isdigit() for p in parts), parsed[key])
        crops = regions.window_crops(130, 6, 1019, 736)
        self.assertEqual(parsed["nav"], regions.fmt_xywh(crops["nav"]))
        self.assertEqual(parsed["list"], regions.fmt_xywh(crops["list"]))
        self.assertEqual(parsed["thread"], regions.fmt_xywh(crops["thread"]))

    def test_docs_mention_window_follow(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn("--window-geom", readme)
        self.assertIn("WECHAT_WINDOW", readme)
        self.assertIn("AT-SPI", readme)
        self.assertIn("flap=1", readme)
        self.assertIn("list.text.sha", readme)

    def test_diff_and_thread_call_window_geom(self):
        with open(os.path.join(ROOT, "wechat-watch-diff"), encoding="utf-8") as fh:
            diff = fh.read()
        with open(os.path.join(ROOT, "wechat-watch-thread"), encoding="utf-8") as fh:
            thread = fh.read()
        self.assertIn("--window-geom", diff)
        self.assertIn("CROP_W=440", diff)
        self.assertIn("--window-png", diff)
        self.assertIn("window.png", diff)
        self.assertTrue(
            "window.sha256" in diff or "window.boxes" in diff,
            "diff must persist window.sha256 or window.boxes",
        )
        self.assertIn("--window-geom", thread)
        self.assertIn("THREAD_W=720", thread)
        self.assertIn("--window-png", thread)
        self.assertTrue(
            "-window_id" in diff or "window_id" in diff,
            "diff must prefer ffmpeg x11grab -window_id",
        )
        self.assertIn("1280x800", diff)
        self.assertIn("list.text.sha", diff)
        self.assertIn("flap=1", diff)
        # Cheap pixel-hash UNCHANGED path must exit before any OCR helper.
        cheap = diff.split('if [ "$LIST_CHANGED" -eq 0 ]', 1)[1]
        cheap_branch = cheap.split("fi", 1)[0]
        self.assertIn('echo "UNCHANGED"', cheap_branch)
        self.assertIn("exit 0", cheap_branch)
        self.assertNotIn("emit_list_diff", cheap_branch)
        self.assertNotIn("emit_full_list", cheap_branch)
        self.assertNotIn("ocr", cheap_branch.lower())


def write_three_pane_png(
    path: str,
    w: int,
    h: int,
    gutter1: int,
    gutter2: int,
    header_h: int,
    footer_h: int,
    badge_xy: tuple[int, int] | None = None,
    ffmpeg: str = PERSIST_FFMPEG,
) -> None:
    """Three panes + 1px black gutters + header/footer; optional red badge box."""
    list_w = max(1, gutter2 - gutter1 - 1)
    parts = [
        f"drawbox=x=0:y=0:w={gutter1}:h={h}:color=0x2D2D2D:t=fill",
        f"drawbox=x={gutter1}:y=0:w=1:h={h}:color=black:t=fill",
        f"drawbox=x={gutter1 + 1}:y=0:w={list_w}:h={h}:color=0xE8E8E8:t=fill",
        f"drawbox=x={gutter2}:y=0:w=1:h={h}:color=black:t=fill",
        f"drawbox=x=0:y=0:w={w}:h={header_h}:color=0x1A1A1A:t=fill",
        f"drawbox=x=0:y={h - footer_h}:w={w}:h={footer_h}:color=0x1A1A1A:t=fill",
    ]
    if badge_xy is not None:
        bx, by = badge_xy
        parts.append(
            f"drawbox=x={bx - 8}:y={by - 8}:w=16:h=16:color=0xFA5151:t=fill"
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
            ",".join(parts),
            path,
        ],
    )


def _decode_png(path: str) -> tuple[bytes, int, int]:
    w, h = regions.png_size(path)
    with tempfile.TemporaryDirectory(prefix="wechat-watch-pane-dec-") as td:
        rgb = regions.decode_rgb24(
            PERSIST_FFMPEG, path, w, h, os.path.join(td, "a.rgb")
        )
    return rgb, w, h


def _box_contains(box: tuple[int, int, int, int], px: int, py: int) -> bool:
    x, y, bw, bh = box
    return x <= px < x + bw and y <= py < y + bh


class PaneDetectTests(unittest.TestCase):
    """Scan window-local gutters; synthetic PNGs only. No live WeChat."""

    def test_1280x800_three_pane_scan_and_badge(self):
        self.assertTrue(os.path.isfile(PERSIST_FFMPEG), "persist ffmpeg missing")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-pane-a-") as td:
            png = os.path.join(td, "win.png")
            write_three_pane_png(
                png, 1280, 800, gutter1=62, gutter2=411,
                header_h=24, footer_h=70, badge_xy=(180, 100),
            )
            rgb, w, h = _decode_png(png)
            panes = regions.detect_panes(rgb, w, h)
            self.assertIsNotNone(panes, "expected gutters on three-pane 1280x800")
            self.assertLessEqual(abs(panes["list_x"] - 63), 12, panes)
            self.assertLessEqual(abs(panes["thread_x"] - 412), 16, panes)
            crops = regions.window_crops(0, 0, 1280, 800, rgb=rgb, frame_w=w, frame_h=h)
            self.assertTrue(_box_contains(crops["list"], 180, 100), crops)
            self.assertFalse(_box_contains(crops["thread"], 180, 100), crops)

    def test_gutter_move_updates_list_x(self):
        """Two synthetic windows: dragged splitter must move list x / list_x."""
        self.assertTrue(os.path.isfile(PERSIST_FFMPEG), "persist ffmpeg missing")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-pane-move-") as td:
            a = os.path.join(td, "a.png")
            b = os.path.join(td, "b.png")
            write_three_pane_png(
                a, 900, 600, gutter1=49, gutter2=250,
                header_h=24, footer_h=60,
            )
            write_three_pane_png(
                b, 900, 600, gutter1=120, gutter2=400,
                header_h=24, footer_h=60,
            )
            rgb_a, wa, ha = _decode_png(a)
            rgb_b, wb, hb = _decode_png(b)
            pa = regions.detect_panes(rgb_a, wa, ha)
            pb = regions.detect_panes(rgb_b, wb, hb)
            self.assertIsNotNone(pa, "expected gutters on gutter2=250")
            self.assertIsNotNone(pb, "expected gutters on gutter2=400")
            ca = regions.window_crops(0, 0, 900, 600, rgb=rgb_a, frame_w=wa, frame_h=ha)
            cb = regions.window_crops(0, 0, 900, 600, rgb=rgb_b, frame_w=wb, frame_h=hb)
            self.assertNotEqual(pa["list_x"], pb["list_x"], (pa, pb))
            self.assertNotEqual(ca["list"][0], cb["list"][0], (ca, cb))
            self.assertNotEqual(pa["thread_x"], pb["thread_x"], (pa, pb))

    def test_900x600_different_gutters(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-pane-b-") as td:
            png = os.path.join(td, "win.png")
            write_three_pane_png(
                png, 900, 600, gutter1=49, gutter2=250,
                header_h=24, footer_h=60, badge_xy=(120, 90),
            )
            rgb, w, h = _decode_png(png)
            panes = regions.detect_panes(rgb, w, h)
            self.assertIsNotNone(panes, "expected gutters on 900x600")
            self.assertLessEqual(abs(panes["list_x"] - 50), 12, panes)
            self.assertLessEqual(abs(panes["thread_x"] - 251), 16, panes)
            self.assertNotEqual(panes["thread_x"], 282, panes)
            crops = regions.window_crops(0, 0, 900, 600, rgb=rgb, frame_w=w, frame_h=h)
            self.assertTrue(_box_contains(crops["list"], 120, 90), crops)
            self.assertFalse(_box_contains(crops["thread"], 120, 90), crops)
            self.assertLessEqual(abs(crops["thread"][0] - 251), 16, crops)

    def test_window_crops_translates_scanned_panes(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-pane-c-") as td:
            png = os.path.join(td, "win.png")
            write_three_pane_png(
                png, 900, 600, gutter1=49, gutter2=250,
                header_h=24, footer_h=60, badge_xy=(120, 90),
            )
            rgb, w, h = _decode_png(png)
            panes = regions.detect_panes(rgb, w, h)
            self.assertIsNotNone(panes)
            crops = regions.window_crops(
                10, 20, 900, 600, rgb=rgb, frame_w=w, frame_h=h
            )
            self.assertLessEqual(abs(crops["list"][0] - (10 + panes["list_x"])), 2, crops)
            self.assertTrue(_box_contains(crops["list"], 10 + 120, 20 + 90), crops)
            self.assertFalse(_box_contains(crops["thread"], 10 + 120, 20 + 90), crops)

    def test_fallback_thread_width_follows_win_w(self):
        a = regions.window_crops(130, 6, 800, 600)
        b = regions.window_crops(130, 6, 1019, 736)
        self.assertNotEqual(a["thread"][2], b["thread"][2], (a["thread"], b["thread"]))

    def test_flat_white_returns_none(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-pane-e-") as td:
            png = os.path.join(td, "white.png")
            write_solid_png(png, 200, 200, "white")
            rgb, w, h = _decode_png(png)
            self.assertIsNone(regions.detect_panes(rgb, w, h))

    def test_readme_mentions_move_resize_dpi_gutters(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertTrue("移动" in readme or "move" in readme.lower(), readme)
        self.assertTrue(
            "缩放" in readme or "resize" in readme.lower() or "DPI" in readme,
            readme,
        )
        self.assertTrue("gutter" in readme.lower() or "分隔" in readme, readme)

    def test_maximized_without_rgb_keeps_list_crop(self):
        crops = regions.window_crops(0, 0, 1280, 800)
        self.assertEqual(crops["list"], (70, 30, 440, 700))
        self.assertEqual(crops["thread"], (414, 40, 720, 660))

    def test_cli_window_png_missing_exits_2(self):
        proc = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--window-geom",
                "--window-png",
                "/no/such/window.png",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)

    def test_cli_window_png_scans_gutters(self):
        with tempfile.TemporaryDirectory(prefix="wechat-watch-pane-cli-") as td:
            png = os.path.join(td, "win.png")
            write_three_pane_png(
                png, 900, 600, gutter1=49, gutter2=250,
                header_h=24, footer_h=60, badge_xy=(120, 90),
            )
            env = {**os.environ, "WECHAT_WINDOW": "900x600+0+0"}
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--window-geom",
                    "--window-png",
                    png,
                    "--ffmpeg",
                    PERSIST_FFMPEG,
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            parsed = _parse_unread_stdout(proc.stdout)
            self.assertEqual(parsed.get("win"), "0,0,900,600", proc.stdout)
            tx = int(parsed["thread"].split(",")[0])
            self.assertLessEqual(abs(tx - 251), 16, parsed)
            self.assertNotEqual(tx, 282, parsed)


class WindowIdTests(unittest.TestCase):
    """Parse X11 window id from a fake tree. No live app."""

    def test_parse_tree_window_id_fake_line(self):
        wid = regions.parse_tree_window_id(FAKE_XWININFO_TREE)
        self.assertEqual(wid.lower(), "0x200007")
        self.assertIsNone(regions.parse_tree_window_id("no windows here"))
        zh = '     0xabc "微信": ("wechat" "WeChat")  900x700+40+20  +40+20\n'
        self.assertEqual(regions.parse_tree_window_id(zh).lower(), "0xabc")

    def test_find_window_id_env_and_tree(self):
        self.assertEqual(
            regions.find_window_id(
                env={"WECHAT_WINDOW_ID": "0xFEED"},
                tree_text=FAKE_XWININFO_TREE,
                probe=False,
            ),
            "0xFEED",
        )
        self.assertEqual(
            regions.find_window_id(
                env={},
                tree_text=FAKE_XWININFO_TREE,
                probe=False,
            ),
            "0x200007",
        )
        self.assertIsNone(
            regions.find_window_id(env={}, tree_text="nothing", probe=False)
        )

    def test_window_crops_non_desktop_positive_boxes(self):
        for geom in ((130, 6, 1019, 736), (10, 20, 800, 600), (0, 0, 900, 700)):
            crops = regions.window_crops(*geom)
            for key in ("list", "thread"):
                x, y, w, h = crops[key]
                self.assertGreater(w, 0, (geom, key, crops[key]))
                self.assertGreater(h, 0, (geom, key, crops[key]))


class AtspiProbeTests(unittest.TestCase):
    """Injected tree/extents only. No live WeChat / Atspi bus."""

    FAKE_TREE = (
        "WeChat application 0 0 1019 736\n"
        "list list 63 30 349 700\n"
        "thread document 412 40 607 660\n"
    )

    def test_probe_atspi_fake_tree(self):
        panes = regions.probe_atspi(tree=self.FAKE_TREE)
        self.assertIsNotNone(panes)
        self.assertEqual(panes["list"], (63, 30, 349, 700))
        self.assertEqual(panes["thread"], (412, 40, 607, 660))

    def test_probe_atspi_injected_extents(self):
        extents = [
            {"name": "session", "role": "list", "x": 70, "y": 32, "w": 340, "h": 680},
            {"name": "chat", "role": "document", "x": 420, "y": 40, "w": 600, "h": 650},
        ]
        panes = regions.probe_atspi(extents=extents)
        self.assertEqual(panes["list"][2], 340)
        self.assertEqual(panes["thread"][0], 420)
        self.assertGreater(panes["list"][2], 0)
        self.assertGreater(panes["thread"][3], 0)

    def test_probe_atspi_empty_or_unrelated(self):
        self.assertIsNone(regions.probe_atspi(tree=""))
        self.assertIsNone(regions.probe_atspi(tree="Clock application 0 0 120 40\n"))
        self.assertIsNone(regions.probe_atspi(extents=[]))
        self.assertIsNone(
            regions.probe_atspi(
                extents=[{"name": "clock", "role": "clock", "x": 0, "y": 0, "w": 40, "h": 40}]
            )
        )


class ListTextFingerprintTests(unittest.TestCase):
    """Normalize + fingerprint. No vision / tesseract."""

    def test_normalize_collapses_whitespace(self):
        a = regions.normalize_list_text("stone  撤回\n\nWeixin Pay")
        b = regions.normalize_list_text("stone 撤回 Weixin Pay")
        self.assertEqual(a, b)
        self.assertEqual(a, "stone 撤回 Weixin Pay")

    def test_fingerprint_same_text(self):
        a = regions.list_text_fingerprint("stone  撤回\n\nWeixin Pay")
        b = regions.list_text_fingerprint("stone 撤回 Weixin Pay")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)
        self.assertEqual(a, regions.list_text_fingerprint("stone 撤回 Weixin Pay"))

    def test_fingerprint_different_text(self):
        a = regions.list_text_fingerprint("stone 撤回")
        b = regions.list_text_fingerprint("Weixin Pay")
        self.assertNotEqual(a, b)

    def test_fingerprint_first_200_chars(self):
        long = ("阿" * 180) + ("坤" * 120)
        self.assertEqual(
            regions.list_text_fingerprint(long),
            regions.list_text_fingerprint(long[:200]),
        )
        changed = long[:199] + "X" + long[200:]
        self.assertNotEqual(
            regions.list_text_fingerprint(long),
            regions.list_text_fingerprint(changed),
        )
        tail = long[:200] + "YYYY"
        self.assertEqual(
            regions.list_text_fingerprint(long),
            regions.list_text_fingerprint(tail),
        )

    def test_cli_fingerprint_and_json(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--fingerprint", "stone  撤回  Weixin Pay"],
            check=True,
            capture_output=True,
            text=True,
        )
        hexd = proc.stdout.strip()
        self.assertEqual(hexd, regions.list_text_fingerprint("stone 撤回 Weixin Pay"))
        with tempfile.TemporaryDirectory(prefix="wechat-watch-fp-") as td:
            path = os.path.join(td, "regions.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([{"text": "stone  撤回\nWeixin Pay", "kind": "text"}], fh)
            proc2 = subprocess.run(
                [sys.executable, SCRIPT, "--fingerprint-json", path],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc2.stdout.strip(), hexd)


def write_image_and_text_png(
    path: str,
    w: int = 720,
    h: int = 400,
    box: tuple[int, int, int, int] = (80, 40, 200, 160),
    color: str = "0x0A6E80",
    text: str = "Hello",
) -> None:
    """White canvas + one large solid block + a separate text line (not the block)."""
    write_box_png(path, w, h, box, color=color, bg="white")
    ffmpeg = SYSTEM_FFMPEG if os.path.isfile(SYSTEM_FFMPEG) else "ffmpeg"
    vf = (
        f"drawtext=fontfile={CJK_FONT}:text='{text}':"
        f"fontcolor=black:fontsize=48:x=80:y=260"
    )
    tmp = path + ".txt.png"
    run_ffmpeg(
        ffmpeg,
        [
            "-i",
            path,
            "-vf",
            vf,
            "-frames:v",
            "1",
            "-update",
            "1",
            tmp,
        ],
    )
    os.replace(tmp, path)


def write_time_chip_png(
    path: str,
    text: str = "Today 16:04",
    w: int = 720,
    h: int = 400,
) -> None:
    """White canvas + thin centered gray time chip + drawtext on the chip."""
    chip = (260, 90, 200, 32)
    write_box_png(path, w, h, chip, color="0xB4B4B4", bg="white")
    ffmpeg = SYSTEM_FFMPEG if os.path.isfile(SYSTEM_FFMPEG) else "ffmpeg"
    cx, cy, cw, ch = chip
    # drawtext treats ':' as an option separator; escape clock colons.
    drawn = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    vf = (
        f"drawtext=fontfile={CJK_FONT}:text='{drawn}':"
        f"fontcolor=0x333333:fontsize=20:"
        f"x={cx}+({cw}-text_w)/2:y={cy}+({ch}-text_h)/2"
    )
    tmp = path + ".time.png"
    run_ffmpeg(
        ffmpeg,
        [
            "-i",
            path,
            "-vf",
            vf,
            "-frames:v",
            "1",
            "-update",
            "1",
            tmp,
        ],
    )
    os.replace(tmp, path)


class ImageCacheTests(unittest.TestCase):
    """Read-only image-bubble detect + persist cache. No GUI, no send."""

    def test_synthetic_block_cached_once_and_cli(self):
        self.assertTrue(os.path.isfile(PERSIST_FFMPEG), "persist ffmpeg missing")
        self.assertTrue(os.path.isfile(CJK_FONT), f"missing CJK font: {CJK_FONT}")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-img-") as td:
            thread = os.path.join(td, "thread.png")
            images_dir = os.path.join(td, "images")
            write_image_and_text_png(thread)
            recs = regions.cache_images(thread, images_dir, PERSIST_FFMPEG)
            pngs = [
                n for n in os.listdir(images_dir) if n.endswith(".png")
            ]
            self.assertEqual(len(recs), 1, recs)
            self.assertEqual(len(pngs), 1, pngs)
            crop = os.path.join(images_dir, pngs[0])
            self.assertTrue(os.path.isfile(crop))
            stem, _ext = os.path.splitext(pngs[0])
            side = os.path.join(images_dir, stem + ".json")
            self.assertTrue(os.path.isfile(side), "sidecar json missing")
            with open(side, encoding="utf-8") as fh:
                meta = json.loads(fh.read())
            self.assertIn("hash", meta)
            self.assertIn("box", meta)
            self.assertIn("at", meta)
            self.assertEqual(meta["hash"], stem)
            box = meta["box"]
            self.assertGreaterEqual(box["w"], 180)
            self.assertGreaterEqual(box["h"], 140)
            self.assertLess(box["w"], 400, "box must not be the whole canvas")
            self.assertLess(box["h"], 300, "box must not be the whole canvas")
            recs2 = regions.cache_images(thread, images_dir, PERSIST_FFMPEG)
            pngs2 = [
                n for n in os.listdir(images_dir) if n.endswith(".png")
            ]
            self.assertEqual(len(recs2), 1, recs2)
            self.assertEqual(len(pngs2), 1, pngs2)
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--cache-images",
                    thread,
                    "--images-dir",
                    images_dir,
                    "--ffmpeg",
                    PERSIST_FFMPEG,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("images=1", proc.stdout)
            parsed = _parse_unread_stdout(proc.stdout)
            img_path = parsed.get("image", "")
            self.assertTrue(img_path, proc.stdout)
            self.assertTrue(os.path.isfile(img_path), img_path)
            pngs3 = [
                n for n in os.listdir(images_dir) if n.endswith(".png")
            ]
            self.assertEqual(len(pngs3), 1, pngs3)

    def test_text_only_is_zero_images(self):
        self.assertTrue(os.path.isfile(CJK_FONT), f"missing CJK font: {CJK_FONT}")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-img-txt-") as td:
            png = os.path.join(td, "hello.png")
            images_dir = os.path.join(td, "images")
            write_text_png(png, KNOWN_EN)
            recs = regions.cache_images(png, images_dir, PERSIST_FFMPEG)
            self.assertEqual(recs, [])
            if os.path.isdir(images_dir):
                pngs = [n for n in os.listdir(images_dir) if n.endswith(".png")]
                self.assertEqual(pngs, [])
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--cache-images",
                    png,
                    "--images-dir",
                    images_dir,
                    "--ffmpeg",
                    PERSIST_FFMPEG,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("images=0", proc.stdout)

    def test_docs_mention_cache_images_local_only(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn("--cache-images", readme)
        self.assertIn("--detect-unread", readme)
        self.assertIn("--detect-nav", readme)
        folded = readme.lower()
        self.assertTrue(
            "不会上传" in readme or "never uploaded" in folded,
            "README must say images are never uploaded",
        )
        self.assertTrue(
            "不会发送" in readme or "never sent" in folded,
            "README must say images are never sent",
        )
        self.assertTrue(
            "本地" in readme or "local" in folded,
            "README must say images stay local",
        )

    def test_cli_missing_png_exits_2(self):
        proc = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--cache-images",
                "/no/such/thread.png",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("ERROR", proc.stderr)

    def test_diff_does_not_call_cache_images(self):
        with open(os.path.join(ROOT, "wechat-watch-diff"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("--cache-images", src)


class ClassifyRegionTests(unittest.TestCase):
    """UIED-inspired pixel-first classify: image / text / line / chrome. No OCR."""

    def test_classify_region_line_and_solid_teal_units(self):
        w, h = 400, 200
        rgb = bytes([255, 255, 255] * (w * h))
        self.assertEqual(regions.classify_region(rgb, w, h, (10, 90, 380, 2)), "line")
        self.assertEqual(regions.classify_region(rgb, w, h, (0, 0, 2, 400)), "line")

        fw, fh = 720, 400
        buf = bytearray([255, 255, 255] * (fw * fh))
        # 0x0A6E80 teal block at (80,40,200,160)
        for yy in range(40, 200):
            for xx in range(80, 280):
                o = (yy * fw + xx) * 3
                buf[o] = 0x0A
                buf[o + 1] = 0x6E
                buf[o + 2] = 0x80
        self.assertEqual(
            regions.classify_region(bytes(buf), fw, fh, (80, 40, 200, 160)),
            "image",
        )

    def test_image_and_text_detect_and_classify_and_cli(self):
        self.assertTrue(os.path.isfile(PERSIST_FFMPEG), "persist ffmpeg missing")
        self.assertTrue(os.path.isfile(CJK_FONT), f"missing CJK font: {CJK_FONT}")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-cls-") as td:
            thread = os.path.join(td, "thread.png")
            write_image_and_text_png(thread)
            w, h = regions.png_size(thread)
            rgb = regions.decode_rgb24(
                PERSIST_FFMPEG, thread, w, h, os.path.join(td, "t.rgb")
            )
            recs = regions.detect_and_classify(rgb, w, h)
            kinds = [r["kind"] for r in recs]
            self.assertEqual(kinds.count("image"), 1, recs)
            self.assertIn("text", kinds, recs)
            self.assertEqual(
                regions.classify_region(rgb, w, h, (80, 40, 200, 160)),
                "image",
            )
            text_boxes = [r["box"] for r in recs if r["kind"] == "text"]
            self.assertTrue(text_boxes, recs)
            self.assertEqual(
                regions.classify_region(rgb, w, h, text_boxes[0]),
                "text",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--classify",
                    thread,
                    "--ffmpeg",
                    PERSIST_FFMPEG,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            indexed_image = [
                ln
                for ln in proc.stdout.splitlines()
                if ln.startswith("kind")
                and len(ln) > 4
                and ln[4].isdigit()
                and ln.endswith("=image")
            ]
            self.assertEqual(len(indexed_image), 1, proc.stdout)
            self.assertIn("kind=text", proc.stdout)
            parsed = _parse_unread_stdout(proc.stdout)
            n = int(parsed.get("regions", "0"))
            self.assertGreaterEqual(n, 2, proc.stdout)
            self.assertTrue(
                any(ln.startswith("kind0=") for ln in proc.stdout.splitlines()),
                proc.stdout,
            )

    def test_thin_full_width_line_is_line_not_image(self):
        self.assertTrue(os.path.isfile(PERSIST_FFMPEG), "persist ffmpeg missing")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-cls-line-") as td:
            path = os.path.join(td, "line.png")
            write_box_png(path, 400, 200, (10, 90, 380, 2), color="black", bg="white")
            w, h = regions.png_size(path)
            rgb = regions.decode_rgb24(
                PERSIST_FFMPEG, path, w, h, os.path.join(td, "t.rgb")
            )
            recs = regions.detect_and_classify(rgb, w, h)
            kinds = [r["kind"] for r in recs]
            self.assertIn("line", kinds, recs)
            self.assertNotIn("image", kinds, recs)
            self.assertEqual(
                regions.classify_region(rgb, w, h, (10, 90, 380, 2)),
                "line",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--classify",
                    path,
                    "--ffmpeg",
                    PERSIST_FFMPEG,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            kind_lines = [
                ln for ln in proc.stdout.splitlines() if ln.startswith("kind")
            ]
            self.assertTrue(
                any(ln.endswith("=line") for ln in kind_lines),
                proc.stdout,
            )
            self.assertFalse(
                any(ln.endswith("=image") for ln in kind_lines),
                proc.stdout,
            )

    def test_cache_images_synthetic_still_one_png(self):
        self.assertTrue(os.path.isfile(PERSIST_FFMPEG), "persist ffmpeg missing")
        self.assertTrue(os.path.isfile(CJK_FONT), f"missing CJK font: {CJK_FONT}")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-cls-cache-") as td:
            thread = os.path.join(td, "thread.png")
            images_dir = os.path.join(td, "images")
            write_image_and_text_png(thread)
            recs = regions.cache_images(thread, images_dir, PERSIST_FFMPEG)
            pngs = [n for n in os.listdir(images_dir) if n.endswith(".png")]
            self.assertEqual(len(recs), 1, recs)
            self.assertEqual(len(pngs), 1, pngs)
            recs2 = regions.cache_images(thread, images_dir, PERSIST_FFMPEG)
            pngs2 = [n for n in os.listdir(images_dir) if n.endswith(".png")]
            self.assertEqual(len(recs2), 1, recs2)
            self.assertEqual(len(pngs2), 1, pngs2)

    def test_docs_mention_classify_pixel_first(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn("--classify", readme)
        self.assertIn("--detect-unread", readme)
        self.assertIn("--detect-nav", readme)
        self.assertIn("--cache-images", readme)
        folded = readme.lower()
        self.assertTrue(
            "uied" in folded
            or "pixel-first" in folded
            or "像素优先" in readme
            or "像素先" in readme
            or "梯度" in readme,
            "README must mention UIED / pixel-first / 像素 / 梯度",
        )

    def test_cli_classify_missing_png_exits_2(self):
        proc = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--classify",
                "/no/such/thread.png",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("ERROR", proc.stderr)

    def test_diff_does_not_call_classify(self):
        with open(os.path.join(ROOT, "wechat-watch-diff"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("--classify", src)
        self.assertNotIn("classify_region", src)


class TimeDividerTests(unittest.TestCase):
    """Centered WeChat time/date chips. Pixel-first, OCR only those boxes."""

    def test_parse_time_divider_units(self):
        today = regions.parse_time_divider("今天 16:04")
        self.assertEqual(today["kind"], "today")
        self.assertEqual(today["hh"], 16)
        self.assertEqual(today["mm"], 4)
        self.assertEqual(today["raw"], "今天 16:04")
        self.assertIn("今天", today["text"])

        ascii_today = regions.parse_time_divider("Today 16:04")
        self.assertEqual(ascii_today["kind"], "today")
        self.assertEqual(ascii_today["hh"], 16)
        self.assertEqual(ascii_today["mm"], 4)
        self.assertEqual(ascii_today["raw"], "Today 16:04")

        yest = regions.parse_time_divider("昨天 21:10")
        self.assertEqual(yest["kind"], "yesterday")
        self.assertEqual(yest["hh"], 21)
        self.assertEqual(yest["mm"], 10)
        self.assertEqual(yest["raw"], "昨天 21:10")

        date = regions.parse_time_divider("2026年8月19日")
        self.assertEqual(date["kind"], "date")
        self.assertEqual(date["raw"], "2026年8月19日")
        self.assertNotIn("hh", date)
        self.assertNotIn("mm", date)

        just = regions.parse_time_divider("刚刚")
        self.assertEqual(just["kind"], "just_now")
        self.assertEqual(just["raw"], "刚刚")
        self.assertNotIn("hh", just)
        self.assertNotIn("mm", just)

        self.assertIsNone(regions.parse_time_divider("hello world"))
        self.assertIsNone(regions.parse_time_divider(""))
        self.assertIsNone(regions.parse_time_divider("你好"))

    def test_synthetic_chip_cli_and_api(self):
        self.assertTrue(os.path.isfile(PERSIST_FFMPEG), "persist ffmpeg missing")
        self.assertTrue(os.path.isfile(CJK_FONT), f"missing CJK font: {CJK_FONT}")
        raw = "Today 16:04"
        with tempfile.TemporaryDirectory(prefix="wechat-watch-time-") as td:
            thread = os.path.join(td, "thread.png")
            write_time_chip_png(thread, raw)
            recs = regions.detect_time_dividers(thread, ffmpeg=PERSIST_FFMPEG)
            self.assertGreaterEqual(len(recs), 1, recs)
            rec = recs[0]
            self.assertEqual(rec["kind"], "time")
            self.assertTrue(
                "16:04" in rec["text"] or raw in rec["text"],
                rec,
            )
            x, y, bw, bh = rec["box"]
            self.assertLess(bw, 600, rec)
            self.assertLess(bh, 80, rec)
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--detect-time",
                    thread,
                    "--ffmpeg",
                    PERSIST_FFMPEG,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            parsed = _parse_unread_stdout(proc.stdout)
            n = int(parsed.get("times", "0"))
            self.assertGreaterEqual(n, 1, proc.stdout)
            time0 = parsed.get("time0", "")
            self.assertTrue(
                "16:04" in time0 or raw in time0,
                proc.stdout,
            )

    def test_teal_image_block_is_not_a_time(self):
        self.assertTrue(os.path.isfile(PERSIST_FFMPEG), "persist ffmpeg missing")
        with tempfile.TemporaryDirectory(prefix="wechat-watch-time-img-") as td:
            thread = os.path.join(td, "thread.png")
            write_image_and_text_png(thread)
            recs = regions.detect_time_dividers(thread, ffmpeg=PERSIST_FFMPEG)
            self.assertEqual(recs, [], recs)
            proc = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--detect-time",
                    thread,
                    "--ffmpeg",
                    PERSIST_FFMPEG,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            parsed = _parse_unread_stdout(proc.stdout)
            self.assertEqual(int(parsed.get("times", "0")), 0, proc.stdout)
            self.assertNotIn("time0=", proc.stdout)

    def test_cli_missing_png_exits_2(self):
        proc = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--detect-time",
                "/no/such/thread.png",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("ERROR", proc.stderr)

    def test_diff_does_not_call_detect_time(self):
        with open(os.path.join(ROOT, "wechat-watch-diff"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("--detect-time", src)
        self.assertNotIn("detect_time_dividers", src)


class NickRobustTests(unittest.TestCase):
    """Nickname / session-name: empty, tesseract junk, emoji leftovers."""

    def _cli(self, raw: str) -> dict:
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--normalize-nick", raw],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertNotIn("ERROR", proc.stderr)
        parsed = _parse_unread_stdout(proc.stdout)
        self.assertIn("nick", parsed, proc.stdout)
        self.assertIn("ok", parsed, proc.stdout)
        return parsed

    def test_empty_is_not_a_name(self):
        self.assertEqual(regions.normalize_nick(""), "")
        self.assertEqual(regions.normalize_nick("   \n\t  "), "")
        self.assertFalse(regions.is_plausible_nick(""))
        self.assertFalse(regions.is_plausible_nick("   "))
        self.assertEqual(regions.parse_nickname(""), "")
        self.assertEqual(regions.parse_row_title(""), "")
        self.assertEqual(regions.parse_row_title("   \n"), "")
        kind, text = regions.classify_ocr("")
        self.assertEqual(kind, "image")
        self.assertEqual(text, "")
        cli = self._cli("")
        self.assertEqual(cli["nick"], "")
        self.assertEqual(cli["ok"], "0")


    def test_tess_junk_is_not_a_name(self):
        self.assertEqual(regions.normalize_nick("|||~~~"), "")
        self.assertFalse(regions.is_plausible_nick("|||~~~"))
        self.assertFalse(regions.is_plausible_nick("A"))
        self.assertFalse(regions.is_plausible_nick("...!!!"))
        self.assertEqual(regions.parse_nickname("|||~~~"), "")
        self.assertEqual(regions.parse_row_title("|||~~~"), "")
        kind, text = regions.classify_ocr("|||~~~")
        self.assertEqual(kind, "image")
        self.assertEqual(text, "")
        cli = self._cli("|||~~~")
        self.assertEqual(cli["nick"], "")
        self.assertEqual(cli["ok"], "0")

    def test_cjk_nick_is_plausible(self):
        chen = "\u9648"
        self.assertEqual(regions.normalize_nick("  " + chen + "  "), chen)
        self.assertTrue(regions.is_plausible_nick("  " + chen + "  "))
        self.assertTrue(regions.is_plausible_nick(chen))
        self.assertEqual(regions.parse_nickname("|||" + chen + "|||"), chen)
        self.assertEqual(regions.parse_row_title("  " + chen + "  12:03"), chen)
        kind, text = regions.classify_ocr("  " + chen + "  ")
        self.assertEqual(kind, "text")
        self.assertEqual(text, chen)
        cli = self._cli(chen)
        self.assertEqual(cli["nick"], chen)
        self.assertEqual(cli["ok"], "1")

    def test_emoji_leftover_keeps_cjk(self):
        raw = "\u963f\u5764\n\U0001F600"
        expect = "\u963f\u5764 \U0001F600"
        nick = regions.normalize_nick(raw)
        self.assertEqual(nick, expect)
        self.assertIn("\u963f\u5764", nick)
        self.assertTrue(regions.is_plausible_nick(raw))
        self.assertTrue(regions.is_plausible_nick(nick))
        kind, text = regions.classify_ocr(raw)
        self.assertEqual(kind, "text")
        self.assertEqual(text, expect)
        self.assertIn("\u963f\u5764", text)
        cli = self._cli(raw)
        self.assertEqual(cli["nick"], expect)
        self.assertEqual(cli["ok"], "1")

    def test_isolated_latin_near_cjk_dropped(self):
        chen = "\u9648"
        self.assertEqual(regions.normalize_nick(chen + "A"), chen)
        self.assertEqual(regions.normalize_nick("A" + chen), chen)
        self.assertEqual(regions.normalize_nick(chen + " Hello"), chen + " Hello")
        self.assertTrue(regions.is_plausible_nick(chen + "A"))

    def test_recognized_symbols_are_kept(self):
        star = "\u2605"
        heart = "\u2665"
        a_kun = "\u963f\u5764"
        decorated = a_kun + star + heart
        wrapped = star + a_kun + star
        self.assertEqual(regions.normalize_nick(decorated), decorated)
        self.assertTrue(regions.is_plausible_nick(decorated))
        self.assertEqual(regions.normalize_nick(wrapped), wrapped)
        self.assertTrue(regions.is_plausible_nick(wrapped))
        cli = self._cli(decorated)
        self.assertEqual(cli["nick"], decorated)
        self.assertEqual(cli["ok"], "1")

        lq = "\u300c"
        rq = "\u300d"
        product = "\u72ec\u7acb\u4ea7\u54c1"
        corner = lq + product + rq
        self.assertEqual(regions.normalize_nick(corner), corner)
        self.assertTrue(regions.is_plausible_nick(corner))
        cli = self._cli(corner)
        self.assertEqual(cli["nick"], corner)
        self.assertEqual(cli["ok"], "1")

        self.assertEqual(regions.normalize_nick("$Nice"), "$Nice")
        self.assertEqual(regions.normalize_nick("Nice@x"), "Nice@x")
        self.assertTrue(regions.is_plausible_nick("$Nice"))
        self.assertTrue(regions.is_plausible_nick("Nice@x"))
        cli = self._cli("$Nice")
        self.assertEqual(cli["nick"], "$Nice")
        self.assertEqual(cli["ok"], "1")
        cli = self._cli("Nice@x")
        self.assertEqual(cli["nick"], "Nice@x")
        self.assertEqual(cli["ok"], "1")

        self.assertEqual(regions.normalize_nick("|||~~~"), "")
        self.assertFalse(regions.is_plausible_nick("|||~~~"))
        cli = self._cli("|||~~~")
        self.assertEqual(cli["nick"], "")
        self.assertEqual(cli["ok"], "0")

        chen = "\u9648"
        self.assertEqual(regions.normalize_nick(chen + "A"), chen)
        self.assertEqual(regions.normalize_nick("A" + chen), chen)
        self.assertEqual(regions.normalize_nick(chen + " Hello"), chen + " Hello")

        raw = "\u963f\u5764\n\U0001F600"
        expect = "\u963f\u5764 \U0001F600"
        self.assertEqual(regions.normalize_nick(raw), expect)

    def test_emoji_only_is_plausible(self):
        face = "\U0001F600"
        self.assertEqual(regions.normalize_nick(face), face)
        self.assertTrue(regions.is_plausible_nick(face))
        self.assertEqual(regions.parse_nickname(face), face)
        kind, text = regions.classify_ocr(face)
        self.assertEqual(kind, "text")
        self.assertEqual(text, face)

    def test_cli_missing_args_keeps_error_style(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--normalize-nick", proc.stderr)
        self.assertIn("required unless", proc.stderr)

    def test_diff_does_not_call_normalize_nick(self):
        with open(os.path.join(ROOT, "wechat-watch-diff"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("--normalize-nick", src)
        self.assertNotIn("normalize_nick", src)


class SendHelperTests(unittest.TestCase):
    """1:1 --send: refuse groups/empty, match existing peers. Never live-types."""

    def _cli(self, argv):
        return subprocess.run(
            [sys.executable, SCRIPT, *argv],
            capture_output=True,
            text=True,
        )

    def _last_json(self, stdout: str) -> dict:
        lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
        self.assertTrue(lines, stdout)
        return json.loads(lines[-1])

    def test_refuse_group_names(self):
        names = (
            "独立产品创业联盟3群",
            "foo@chatroom",
            "collage group",
            "Collage Group",
            "产品群",
        )
        for name in names:
            self.assertTrue(regions.looks_like_group(name), name)
            with self.assertRaises(regions.SendRefused) as ctx:
                regions.validate_send(name, "好")
            self.assertEqual(ctx.exception.error, "group", name)
            plan_err = None
            try:
                regions.plan_private_send(peer=name, text="好", sessions=[{"name": name}])
            except regions.SendRefused as exc:
                plan_err = exc.error
            self.assertEqual(plan_err, "group", name)

    def test_refuse_empty_text(self):
        for text in ("", "   ", None):
            with self.assertRaises(regions.SendRefused) as ctx:
                regions.validate_send("阿坤", text)
            self.assertEqual(ctx.exception.error, "empty-text")

    def test_peer_matching_exact_prefix_username(self):
        sessions = [
            {"name": "阿坤", "username": "wxid_akun"},
            {"name": "独立产品创业联盟3群"},
            {"name": "文件传输助手"},
            {"name": "Weixin Team"},
            {"name": "阿坤的群"},
        ]
        hit = regions.match_peer("阿坤", sessions)
        self.assertEqual(hit["name"], "阿坤")
        hit = regions.match_peer("  阿坤  ", sessions)
        self.assertEqual(hit["name"], "阿坤")
        hit = regions.match_peer("nobody", sessions, username="wxid_akun")
        self.assertEqual(hit["name"], "阿坤")
        with self.assertRaises(regions.SendRefused) as ctx:
            regions.match_peer("独立产品创业联盟3群", sessions)
        self.assertEqual(ctx.exception.error, "group")
        with self.assertRaises(regions.SendRefused) as ctx:
            regions.match_peer("文件传输助手", sessions)
        self.assertEqual(ctx.exception.error, "skipped-peer")
        with self.assertRaises(regions.SendRefused) as ctx:
            regions.match_peer("Weixin Team", sessions)
        self.assertEqual(ctx.exception.error, "skipped-peer")
        with self.assertRaises(regions.SendRefused) as ctx:
            regions.match_peer("ghost", sessions)
        self.assertEqual(ctx.exception.error, "peer-not-found")
        # group/skipped rows are not valid prefix hits
        hit = regions.match_peer("阿", sessions)
        self.assertEqual(hit["name"], "阿坤")

    def test_peer_ambiguous_and_skipped_units(self):
        sessions = [{"name": "阿坤"}, {"name": "阿强"}]
        with self.assertRaises(regions.SendRefused) as ctx:
            regions.match_peer("阿", sessions)
        self.assertEqual(ctx.exception.error, "ambiguous-peer")
        for name in ("文件传输助手", "File Transfer", "Weixin Team", "微信团队"):
            self.assertTrue(regions.is_skipped_peer(name), name)
            with self.assertRaises(regions.SendRefused) as ctx:
                regions.validate_send(name, "好")
            self.assertEqual(ctx.exception.error, "skipped-peer", name)
        self.assertFalse(regions.looks_like_group("阿坤"))
        self.assertFalse(regions.is_skipped_peer("阿坤"))

    def test_plan_existing_session_only(self):
        plan = regions.plan_private_send(
            peer="阿坤",
            text="好",
            sessions=[{"name": "阿坤", "row": 1}, {"name": "产品群"}],
            win=(0, 0, 1280, 800),
        )
        ops = [a["op"] for a in plan["actions"]]
        self.assertEqual(ops[0], "focus-session")
        self.assertIn("focus-input", ops)
        self.assertIn("type", ops)
        self.assertIn("submit", ops)
        self.assertNotIn("search", ops)
        self.assertNotIn("add-contact", ops)
        self.assertNotIn("new-chat", ops)
        self.assertEqual(plan["peer"], "阿坤")
        self.assertEqual(plan["text"], "好")
        self.assertEqual(plan["session"]["name"], "阿坤")
        crops = regions.window_crops(0, 0, 1280, 800)
        _tx, ty, _tw, th = crops["thread"]
        _ix, iy = plan["input"]
        self.assertGreaterEqual(iy, ty + th - 20)
        self.assertLess(iy, 800)

    def test_dry_driver_executes_plan_without_desktop(self):
        drv = regions.DrySendDriver()
        result = regions.run_send(
            "阿坤",
            "好",
            sessions=[{"name": "阿坤", "row": 1}],
            dry_run=False,
            driver=drv,
            probe_window=False,
            env={"WECHAT_WINDOW": "1019x736+10+20"},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["peer"], "阿坤")
        self.assertEqual(result["text"], "好")
        ops = [c["op"] for c in drv.calls]
        self.assertIn("focus", ops)
        self.assertIn("type", ops)
        self.assertIn("submit", ops)
        typed = [c for c in drv.calls if c.get("op") == "type"]
        self.assertEqual(typed[-1]["text"], "好")
        kinds = {c.get("kind") for c in drv.calls if c.get("op") == "focus"}
        self.assertIn("session", kinds)
        self.assertIn("input", kinds)

    def test_cli_refuse_group_and_empty(self):
        proc = self._cli(["--send", "--peer", "独立产品创业联盟3群", "--text", "好"])
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        rec = self._last_json(proc.stdout)
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["error"], "group")
        self.assertEqual(rec["peer"], "独立产品创业联盟3群")
        self.assertEqual(rec["text"], "好")

        proc = self._cli(["send", "--peer", "collage group", "--text", "hi"])
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        rec = self._last_json(proc.stdout)
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["error"], "group")

        proc = self._cli(["send", "--peer", "阿坤", "--text", ""])
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        rec = self._last_json(proc.stdout)
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["error"], "empty-text")
        self.assertEqual(rec["peer"], "阿坤")

        proc = self._cli(["--send", "--peer", "阿坤"])
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        rec = self._last_json(proc.stdout)
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["error"], "empty-text")

    def test_cli_dry_run_match_injected_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "sessions.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(
                    [
                        {"name": "阿坤"},
                        {"name": "独立产品创业联盟3群"},
                        {"name": "文件传输助手"},
                    ],
                    fh,
                    ensure_ascii=False,
                )
            proc = self._cli(
                [
                    "--send",
                    "--peer",
                    "阿坤",
                    "--text",
                    "好",
                    "--dry-run",
                    "--sessions-json",
                    path,
                ]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            rec = self._last_json(proc.stdout)
            self.assertTrue(rec["ok"])
            self.assertEqual(rec["peer"], "阿坤")
            self.assertEqual(rec["text"], "好")
            self.assertNotIn("error", rec)

            proc = self._cli(
                [
                    "send",
                    "--peer",
                    "独立产品创业联盟3群",
                    "--text",
                    "好",
                    "--dry-run",
                    "--sessions-json",
                    path,
                ]
            )
            self.assertNotEqual(proc.returncode, 0)
            rec = self._last_json(proc.stdout)
            self.assertFalse(rec["ok"])
            self.assertEqual(rec["error"], "group")

    def test_apply_source_submits_after_fill(self):
        path = os.path.join(ROOT, "wechat_watch_apply.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("set_text_contents", src)
        self.assertIn("print(\"OK\")", src)
        has_return = "0xFF0D" in src or "Return" in src or "generate_keyboard_event" in src
        has_send_btn = "发送" in src
        self.assertTrue(has_return or has_send_btn, "apply must submit after fill")
        fill_at = src.index("set_text_contents")
        ok_at = src.index('print("OK")')
        self.assertLess(fill_at, ok_at)
        submit_at = min(
            i
            for i in (
                src.find("generate_keyboard_event"),
                src.find("0xFF0D"),
                src.find("发送"),
            )
            if i >= 0
        )
        self.assertLess(fill_at, submit_at)
        self.assertLess(submit_at, ok_at)

    def test_missing_args_mentions_send(self):

        proc = self._cli([])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--send", proc.stderr)
        self.assertIn("--normalize-nick", proc.stderr)



if __name__ == "__main__":
    unittest.main()
