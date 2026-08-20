#!/usr/bin/env python3
"""Unit tests for wx-watch-gate: debounce, exit mapping, dry-run, no live net."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
GATE = os.path.join(ROOT, "wx-watch-gate")


def load_gate():
    spec = importlib.util.spec_from_loader(
        "wx_watch_gate",
        importlib.machinery.SourceFileLoader("wx_watch_gate", GATE),
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {GATE}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


import importlib.machinery  # noqa: E402

gate = load_gate()


CHANGED = (
    "CHANGED act=1 quiet=0 refresh_skipped=true n=8 sessions_json=/tmp/wx-sessions.json\n"
    '[{"u":"wxid_demo","gate":"act","fields":["unread"]}]\n'
)
UNCHANGED = "UNCHANGED refresh_skipped=true n=8\n"
CHANGED_OTHER = (
    "CHANGED act=2 quiet=0 refresh_skipped=true n=9 sessions_json=/tmp/wx-sessions.json\n"
    '[{"u":"wxid_other","gate":"act","fields":["last"]}]\n'
)


class TestWatchGatePure(unittest.TestCase):
    def test_parse_n_and_fingerprint(self):
        self.assertEqual(gate.parse_n(CHANGED), 8)
        self.assertEqual(gate.parse_n(UNCHANGED), 8)
        self.assertEqual(gate.parse_n(""), 0)
        self.assertTrue(gate.fingerprint(CHANGED))
        self.assertNotEqual(gate.fingerprint(CHANGED), gate.fingerprint(CHANGED_OTHER))

    def test_bearer_and_curl_argv(self):
        self.assertEqual(gate.bearer_value("abc"), "Bearer abc")
        self.assertEqual(gate.bearer_value("Bearer xyz"), "Bearer xyz")
        argv = gate.build_curl_argv(
            "https://example.invalid/hook",
            "sekrit",
            {"reason": "sessions-changed", "n": 3},
        )
        self.assertIn("POST", argv)
        self.assertIn("https://example.invalid/hook", argv)
        joined = " ".join(argv)
        self.assertIn("Authorization: Bearer sekrit", joined)
        self.assertIn("Content-Type: application/json", joined)
        self.assertIn('{"reason":"sessions-changed","n":3}', joined)
        self.assertNotIn("unlock", joined)

    def test_exit0_never_ping(self):
        d = gate.handle_tick(0, UNCHANGED, 1000.0, {}, "https://x", "k", False)
        self.assertEqual(d["action"], "quiet")
        self.assertIsNone(d["body"])
        self.assertIn("QUIET", d["log"])

    def test_exit1_live_ping(self):
        d = gate.decide_exit1(1000.0, {}, CHANGED, "https://x.example/hook", "k", 20)
        self.assertEqual(d["action"], "ping")
        self.assertEqual(d["body"], {"reason": "sessions-changed", "n": 8})
        self.assertNotIn("sekrit", d["log"])

    def test_exit1_dry_run_missing_env(self):
        d = gate.decide_exit1(1000.0, {}, CHANGED, "", "", 20)
        self.assertEqual(d["action"], "would_ping")
        self.assertIn("WOULD_PING", d["log"])
        d2 = gate.decide_exit1(1000.0, {}, CHANGED, "https://x", "", 20)
        self.assertEqual(d2["action"], "would_ping")

    def test_debounce_recent_same_change(self):
        fp = gate.fingerprint(CHANGED)
        state = {"last_post_ts": 990.0, "last_fp": fp, "awaiting_prev": True}
        d = gate.decide_exit1(1005.0, state, CHANGED, "https://x", "k", 20)
        self.assertEqual(d["action"], "debounce")

    def test_debounce_stale_exit1_after_prev_not_updated(self):
        fp = gate.fingerprint(CHANGED)
        # last POST was 30s ago (outside 20s window) but same fp and awaiting_prev
        state = {"last_post_ts": 970.0, "last_fp": fp, "awaiting_prev": True}
        d = gate.decide_exit1(1005.0, state, CHANGED, "https://x", "k", 20)
        self.assertEqual(d["action"], "stale")

    def test_new_fingerprint_after_window_may_ping(self):
        old_fp = gate.fingerprint(CHANGED)
        state = {"last_post_ts": 970.0, "last_fp": old_fp, "awaiting_prev": True}
        d = gate.decide_exit1(1005.0, state, CHANGED_OTHER, "https://x", "k", 20)
        self.assertEqual(d["action"], "ping")

    def test_exit5_login_stop(self):
        d = gate.handle_tick(5, "error human", 1.0, {}, "https://x", "k", False)
        self.assertEqual(d["action"], "login_stop")
        self.assertTrue(d.get("write_login_needed"))

    def test_exit2_unlock_once_then_skip(self):
        d = gate.handle_tick(2, "need_unlock", 1.0, {}, "https://x", "k", False)
        self.assertEqual(d["action"], "unlock_retry")
        d2 = gate.handle_tick(2, "need_unlock", 1.0, d["state"], "https://x", "k", True)
        self.assertEqual(d2["action"], "unlock_skip")

    def test_exit1_login_once_file_skips_post(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["WX_WATCH_PERSIST"] = td
            Path(td, gate.LOGIN_ONCE_NAME).write_text("need login\n", encoding="utf-8")
            try:
                d = gate.decide_exit1(1000.0, {}, CHANGED, "https://x", "k", 20)
                self.assertEqual(d["action"], "login_stop")
            finally:
                os.environ.pop("WX_WATCH_PERSIST", None)


class TestWatchGateTickIO(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.persist = self.td.name
        self.env_save = {
            k: os.environ.get(k)
            for k in (
                "WX_WATCH_PERSIST",
                "WX_WATCH_WEBHOOK_URL",
                "WX_WATCH_WEBHOOK_KEY",
                "WX_WATCH_CURL",
                "WX_SESSIONS_CHANGED",
                "WX_WATCH_WEBHOOK_ENV",
            )
        }
        os.environ["WX_WATCH_PERSIST"] = self.persist
        os.environ.pop("WX_WATCH_WEBHOOK_URL", None)
        os.environ.pop("WX_WATCH_WEBHOOK_KEY", None)
        os.environ.pop("WX_WATCH_WEBHOOK_ENV", None)

    def tearDown(self):
        for k, v in self.env_save.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.td.cleanup()

    def _fake_gate(self, code: int, stdout: str):
        def run():
            return code, stdout, ""

        return run

    def test_tick_exit0_does_not_call_poster(self):
        calls = []

        def poster(url, key, body):
            calls.append((url, key, body))
            return True, "ok"

        action = gate.one_tick(
            poster=poster,
            gate_runner=self._fake_gate(0, UNCHANGED),
            clock=lambda: 1000.0,
        )
        self.assertEqual(action, "quiet")
        self.assertEqual(calls, [])
        log = Path(self.persist, gate.LOG_NAME).read_text(encoding="utf-8")
        self.assertIn("QUIET", log)
        self.assertNotIn("PING", log)

    def test_tick_dry_run_no_network(self):
        calls = []

        def poster(url, key, body):
            calls.append((url, key, body))
            raise AssertionError("dry-run must not POST")

        action = gate.one_tick(
            poster=poster,
            gate_runner=self._fake_gate(1, CHANGED),
            clock=lambda: 1000.0,
        )
        self.assertEqual(action, "would_ping")
        self.assertEqual(calls, [])
        log = Path(self.persist, gate.LOG_NAME).read_text(encoding="utf-8")
        self.assertIn("WOULD_PING", log)

    def test_tick_exit1_posts_once_then_debounce(self):
        calls = []

        def poster(url, key, body):
            calls.append((url, key, dict(body)))
            return True, "ok"

        os.environ["WX_WATCH_WEBHOOK_URL"] = "https://example.invalid/hook"
        os.environ["WX_WATCH_WEBHOOK_KEY"] = "test-key-not-real"
        t = {"now": 1000.0}

        def clock():
            return t["now"]

        a1 = gate.one_tick(
            poster=poster,
            gate_runner=self._fake_gate(1, CHANGED),
            clock=clock,
        )
        self.assertEqual(a1, "ping_ok")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], {"reason": "sessions-changed", "n": 8})

        t["now"] = 1010.0
        a2 = gate.one_tick(
            poster=poster,
            gate_runner=self._fake_gate(1, CHANGED),
            clock=clock,
        )
        self.assertEqual(a2, "debounce")
        self.assertEqual(len(calls), 1)

        t["now"] = 1030.0
        a3 = gate.one_tick(
            poster=poster,
            gate_runner=self._fake_gate(1, CHANGED),
            clock=clock,
        )
        self.assertEqual(a3, "stale")
        self.assertEqual(len(calls), 1)

        t["now"] = 1040.0
        a4 = gate.one_tick(
            poster=poster,
            gate_runner=self._fake_gate(0, UNCHANGED),
            clock=clock,
        )
        self.assertEqual(a4, "quiet")
        self.assertEqual(len(calls), 1)

    def test_tick_exit5_writes_once_file_and_stops(self):
        calls = []

        def poster(url, key, body):
            calls.append(body)
            return True, "ok"

        os.environ["WX_WATCH_WEBHOOK_URL"] = "https://example.invalid/hook"
        os.environ["WX_WATCH_WEBHOOK_KEY"] = "test-key-not-real"
        a1 = gate.one_tick(
            poster=poster,
            gate_runner=self._fake_gate(5, "need human"),
            clock=lambda: 1.0,
        )
        self.assertEqual(a1, "login_stop")
        self.assertTrue(Path(self.persist, gate.LOGIN_ONCE_NAME).is_file())
        a2 = gate.one_tick(
            poster=poster,
            gate_runner=self._fake_gate(1, CHANGED),
            clock=lambda: 2.0,
        )
        self.assertEqual(a2, "login_stop")
        self.assertEqual(calls, [])

    def test_tick_exit2_unlock_once_no_loop_bomb(self):
        unlocks = []
        gates = {"n": 0}

        def run_gate():
            gates["n"] += 1
            return 2, "need_unlock", ""

        def run_unlock():
            unlocks.append(1)
            return 0, '{"ok":true}', ""

        a1 = gate.one_tick(
            poster=lambda *a: (_ for _ in ()).throw(AssertionError("no post")),
            gate_runner=run_gate,
            unlock_runner=run_unlock,
            clock=lambda: 1.0,
        )
        self.assertEqual(a1, "unlock_skip")
        self.assertEqual(len(unlocks), 1)
        self.assertGreaterEqual(gates["n"], 2)

        a2 = gate.one_tick(
            poster=lambda *a: (_ for _ in ()).throw(AssertionError("no post")),
            gate_runner=run_gate,
            unlock_runner=run_unlock,
            clock=lambda: 2.0,
        )
        self.assertEqual(a2, "unlock_skip")
        self.assertEqual(len(unlocks), 1)

    def test_subprocess_once_dry_run_uses_mock_gate(self):
        fake = Path(self.persist) / "fake-sessions-changed"
        fake.write_text(
            "#!/bin/sh\necho 'CHANGED act=1 quiet=0 n=3'\nexit 1\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        env = os.environ.copy()
        env["WX_WATCH_PERSIST"] = self.persist
        env["WX_SESSIONS_CHANGED"] = str(fake)
        env.pop("WX_WATCH_WEBHOOK_URL", None)
        env.pop("WX_WATCH_WEBHOOK_KEY", None)
        import subprocess

        p = subprocess.run(
            [sys.executable, GATE, "--once"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        log = Path(self.persist, gate.LOG_NAME).read_text(encoding="utf-8")
        self.assertIn("WOULD_PING", log)

    def test_subprocess_exit0_no_curl(self):
        fake = Path(self.persist) / "fake-sessions-changed"
        fake.write_text("#!/bin/sh\necho 'UNCHANGED n=4'\nexit 0\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        curl_log = Path(self.persist) / "curl.log"
        fake_curl = Path(self.persist) / "fake-curl"
        fake_curl.write_text(
            f"#!/bin/sh\necho posted >> {curl_log}\nexit 0\n",
            encoding="utf-8",
        )
        fake_curl.chmod(fake_curl.stat().st_mode | stat.S_IEXEC)
        env = os.environ.copy()
        env["WX_WATCH_PERSIST"] = self.persist
        env["WX_SESSIONS_CHANGED"] = str(fake)
        env["WX_WATCH_CURL"] = str(fake_curl)
        env["WX_WATCH_WEBHOOK_URL"] = "https://example.invalid/hook"
        env["WX_WATCH_WEBHOOK_KEY"] = "test-key-not-real"
        import subprocess

        p = subprocess.run(
            [sys.executable, GATE, "--once"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(curl_log.is_file())
        log = Path(self.persist, gate.LOG_NAME).read_text(encoding="utf-8")
        self.assertIn("QUIET", log)


if __name__ == "__main__":
    unittest.main()
