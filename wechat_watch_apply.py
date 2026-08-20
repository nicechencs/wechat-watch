from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time


def apply_send_plan(plan: dict, x11=None) -> bool:
    """Click planned x/y, paste via clipboard+ctrl+v, submit Return.

    Never talks to AT-SPI in this process. A missing accessibility bus
    aborts the interpreter (SIGTRAP); clicks go through X11 instead.
    """
    if not isinstance(plan, dict):
        return False
    peer = str(plan.get("peer") or "")
    text = str(plan.get("text") or "")
    if not peer or not text:
        return False
    if _refused_send_peer(peer):
        return False
    actions = list(plan.get("actions") or [])
    if not actions:
        return False
    banned = {"search", "add-contact", "new-chat", "compose-new"}
    if any((s.get("op") in banned) for s in actions):
        return False
    os.environ.setdefault("DISPLAY", os.environ.get("DISPLAY", ":8"))
    driver = x11 if x11 is not None else X11SendDriver()
    typed = False
    submitted = False
    try:
        for step in actions:
            op = step.get("op")
            if op in ("focus-session", "focus-input"):
                driver.click(int(step["x"]), int(step["y"]))
                time.sleep(0.05)
            elif op == "type":
                body = str(step.get("text") or text)
                driver.paste(body)
                typed = True
            elif op == "submit":
                driver.submit()
                submitted = True
    except Exception:
        return False
    return bool(typed and submitted)


def _refused_send_peer(peer: str) -> bool:
    raw = peer or ""
    low = raw.lower()
    if "群" in raw or "chatroom" in low:
        return True
    blob = raw.replace(" ", "").replace("_", "").replace("-", "").lower()
    for tok in (
        "filehelper",
        "filetransfer",
        "文件传输助手",
        "weixinteam",
        "wechatteam",
        "微信团队",
    ):
        if tok in blob or tok in raw:
            return True
    return False


class X11SendDriver:
    """Absolute-coordinate click / clipboard paste / Return via X11."""

    def click(self, x: int, y: int) -> None:
        if _xlib_click(x, y):
            return
        if _xtest_click(x, y):
            return
        if _xdotool_click(x, y):
            return
        raise RuntimeError("x11-click-failed")

    def paste(self, text: str) -> None:
        if not text:
            raise RuntimeError("empty-paste")
        clip = _ClipboardOwner(text)
        clip.start()
        try:
            time.sleep(0.05)
            if not (_xlib_hotkey("ctrl", "v") or _xtest_hotkey("ctrl", "v") or _xdotool_key("ctrl+v")):
                raise RuntimeError("x11-paste-failed")
            time.sleep(0.05)
        finally:
            clip.stop()

    def submit(self) -> None:
        if _xlib_key("Return") or _xtest_key("Return") or _xdotool_key("Return"):
            return
        raise RuntimeError("x11-submit-failed")


class _ClipboardOwner:
    """Own CLIPBOARD in a child until paste. Parent never imports AT-SPI."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = os.environ.copy()
        env.setdefault("DISPLAY", env.get("DISPLAY", ":8"))
        payload = self.text.encode("utf-8")
        for argv in (
            ["xclip", "-selection", "clipboard", "-quiet", "-i"],
            ["xsel", "--clipboard", "--input"],
        ):
            if not shutil.which(argv[0]):
                continue
            try:
                self.proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                )
            except OSError:
                self.proc = None
                continue
            try:
                assert self.proc.stdin is not None
                self.proc.stdin.write(payload)
                self.proc.stdin.close()
                return
            except OSError:
                self.stop()
        code = "\n".join(
            [
                "import os,sys",
                'os.environ.setdefault("DISPLAY", os.environ.get("DISPLAY", ":8"))',
                "text=sys.stdin.read()",
                "import gi",
                'gi.require_version("Gtk","3.0")',
                "from gi.repository import Gtk,Gdk,GLib",
                "cb=Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)",
                "cb.set_text(text,-1)",
                "def stop():",
                " Gtk.main_quit(); return False",
                "GLib.timeout_add(4000, stop)",
                "Gtk.main()",
            ]
        )
        self.proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(payload)
            self.proc.stdin.close()
        except OSError:
            self.stop()
            raise RuntimeError("clipboard-failed")

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            proc.terminate()
        except OSError:
            return
        try:
            proc.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass


def _display_name() -> bytes:
    return os.environ.get("DISPLAY", ":8").encode("ascii", "replace")


def _xlib_mods():
    try:
        from Xlib import X, display
        from Xlib.ext import xtest
    except Exception:
        return None
    return X, display, xtest


def _xlib_click(x: int, y: int) -> bool:
    mods = _xlib_mods()
    if mods is None:
        return False
    X, display, xtest = mods
    try:
        dpy = display.Display()
        xtest.fake_input(dpy, X.MotionNotify, x=int(x), y=int(y))
        dpy.sync()
        xtest.fake_input(dpy, X.ButtonPress, 1)
        xtest.fake_input(dpy, X.ButtonRelease, 1)
        dpy.sync()
        dpy.close()
        return True
    except Exception:
        return False


def _xlib_key(name: str) -> bool:
    mods = _xlib_mods()
    if mods is None:
        return False
    X, display, xtest = mods
    try:
        from Xlib import XK

        dpy = display.Display()
        keysym = XK.string_to_keysym(name)
        if not keysym:
            dpy.close()
            return False
        code = dpy.keysym_to_keycode(keysym)
        xtest.fake_input(dpy, X.KeyPress, code)
        xtest.fake_input(dpy, X.KeyRelease, code)
        dpy.sync()
        dpy.close()
        return True
    except Exception:
        return False


def _xlib_hotkey(mod: str, key: str) -> bool:
    mods = _xlib_mods()
    if mods is None:
        return False
    X, display, xtest = mods
    try:
        from Xlib import XK

        dpy = display.Display()
        mod_name = "Control_L" if mod == "ctrl" else mod
        mk = dpy.keysym_to_keycode(XK.string_to_keysym(mod_name))
        kk = dpy.keysym_to_keycode(XK.string_to_keysym(key))
        if not mk or not kk:
            dpy.close()
            return False
        xtest.fake_input(dpy, X.KeyPress, mk)
        xtest.fake_input(dpy, X.KeyPress, kk)
        xtest.fake_input(dpy, X.KeyRelease, kk)
        xtest.fake_input(dpy, X.KeyRelease, mk)
        dpy.sync()
        dpy.close()
        return True
    except Exception:
        return False


def _xtest_libs():
    try:
        from ctypes import c_char_p, c_int, c_uint, c_ulong, c_void_p, cdll

        x11 = cdll.LoadLibrary("libX11.so.6")
        xtest = cdll.LoadLibrary("libXtst.so.6")
        x11.XOpenDisplay.restype = c_void_p
        x11.XOpenDisplay.argtypes = [c_char_p]
        x11.XCloseDisplay.argtypes = [c_void_p]
        x11.XFlush.argtypes = [c_void_p]
        x11.XDefaultScreen.restype = c_int
        x11.XDefaultScreen.argtypes = [c_void_p]
        x11.XKeysymToKeycode.restype = c_uint
        x11.XKeysymToKeycode.argtypes = [c_void_p, c_ulong]
        xtest.XTestFakeMotionEvent.argtypes = [c_void_p, c_int, c_int, c_int, c_ulong]
        xtest.XTestFakeButtonEvent.argtypes = [c_void_p, c_uint, c_int, c_ulong]
        xtest.XTestFakeKeyEvent.argtypes = [c_void_p, c_uint, c_int, c_ulong]
        return x11, xtest
    except Exception:
        return None


def _xtest_open():
    libs = _xtest_libs()
    if libs is None:
        return None
    x11, xtest = libs
    dpy = x11.XOpenDisplay(_display_name())
    if not dpy:
        return None
    return x11, xtest, dpy


def _xtest_click(x: int, y: int) -> bool:
    opened = _xtest_open()
    if opened is None:
        return False
    x11, xtest, dpy = opened
    try:
        screen = x11.XDefaultScreen(dpy)
        xtest.XTestFakeMotionEvent(dpy, screen, int(x), int(y), 0)
        x11.XFlush(dpy)
        xtest.XTestFakeButtonEvent(dpy, 1, 1, 0)
        xtest.XTestFakeButtonEvent(dpy, 1, 0, 0)
        x11.XFlush(dpy)
        return True
    except Exception:
        return False
    finally:
        x11.XCloseDisplay(dpy)


_XK_RETURN = 0xFF0D
_XK_CONTROL_L = 0xFFE3
_XK_V = 0x0076


def _xtest_key(name: str) -> bool:
    if name != "Return":
        return False
    opened = _xtest_open()
    if opened is None:
        return False
    x11, xtest, dpy = opened
    try:
        code = x11.XKeysymToKeycode(dpy, _XK_RETURN)
        if not code:
            return False
        xtest.XTestFakeKeyEvent(dpy, code, 1, 0)
        xtest.XTestFakeKeyEvent(dpy, code, 0, 0)
        x11.XFlush(dpy)
        return True
    except Exception:
        return False
    finally:
        x11.XCloseDisplay(dpy)


def _xtest_hotkey(mod: str, key: str) -> bool:
    if mod != "ctrl" or key.lower() != "v":
        return False
    opened = _xtest_open()
    if opened is None:
        return False
    x11, xtest, dpy = opened
    try:
        ctrl = x11.XKeysymToKeycode(dpy, _XK_CONTROL_L)
        vee = x11.XKeysymToKeycode(dpy, _XK_V)
        if not ctrl or not vee:
            return False
        xtest.XTestFakeKeyEvent(dpy, ctrl, 1, 0)
        xtest.XTestFakeKeyEvent(dpy, vee, 1, 0)
        xtest.XTestFakeKeyEvent(dpy, vee, 0, 0)
        xtest.XTestFakeKeyEvent(dpy, ctrl, 0, 0)
        x11.XFlush(dpy)
        return True
    except Exception:
        return False
    finally:
        x11.XCloseDisplay(dpy)


def _xdotool(*args: str) -> bool:
    exe = shutil.which("xdotool")
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, *args],
            check=False,
            capture_output=True,
            timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _xdotool_click(x: int, y: int) -> bool:
    return _xdotool("mousemove", "--sync", str(int(x)), str(int(y))) and _xdotool(
        "click", "1"
    )


def _xdotool_key(name: str) -> bool:
    return _xdotool("key", "--clearmodifiers", name)


def apply_private_text(peer: str, text: str) -> bool:
    """Best-effort AT-SPI in a child: open existing 1:1 row, fill, submit."""
    if not peer or not text:
        return False
    if _refused_send_peer(peer):
        return False
    code = "\n".join([
        "import os,sys",
        'os.environ.setdefault("DISPLAY", os.environ.get("DISPLAY", ":8"))',
        'peer=os.environ.get("WECHAT_SEND_PEER","")',
        'text=os.environ.get("WECHAT_SEND_TEXT","")',
        "try:",
        " import gi",
        ' gi.require_version("Atspi","2.0")',
        " from gi.repository import Atspi",
        " desk=Atspi.get_desktop(0)",
        "except Exception:",
        " sys.exit(0)",
        "def walk(node, depth=0, acc=None):",
        " if acc is None: acc=[]",
        " if node is None or depth>8 or len(acc)>80: return acc",
        ' name=role=""',
        " try: name=node.get_name() or \"\"",
        " except Exception: pass",
        " try: role=node.get_role_name() or \"\"",
        " except Exception: pass",
        " acc.append((node,name,role))",
        " n=0",
        " try: n=node.get_child_count()",
        " except Exception: n=0",
        " for i in range(min(n,40)):",
        "  try: ch=node.get_child_at_index(i)",
        "  except Exception: continue",
        "  walk(ch, depth+1, acc)",
        " return acc",
        "try: n=desk.get_child_count()",
        "except Exception: sys.exit(0)",
        "app=None",
        "for i in range(n):",
        " try: cand=desk.get_child_at_index(i)",
        " except Exception: continue",
        " if cand is None: continue",
        ' nm=rl=""',
        " try: nm=cand.get_name() or \"\"",
        " except Exception: pass",
        " try: rl=cand.get_role_name() or \"\"",
        " except Exception: pass",
        ' blob=(nm+" "+rl).lower()',
        ' if "wechat-watch" in blob: continue',
        ' if any(k in blob for k in ("wechat","weixin","微信")):',
        "  app=cand; break",
        "if app is None: sys.exit(0)",
        "nodes=walk(app)",
        "row=None",
        "for node,name,role in nodes:",
        " if not name: continue",
        " if name==peer or name.startswith(peer):",
        '  if "群" in name or "chatroom" in name.lower():',
        "   continue",
        "  row=node; break",
        "if row is None: sys.exit(0)",
        "try:",
        " na=row.get_n_actions()",
        "except Exception:",
        " na=0",
        "for i in range(na):",
        " try:",
        "  row.do_action(i); break",
        " except Exception:",
        "  continue",
        "nodes=walk(app)",
        "box=None",
        "for node,name,role in nodes:",
        " rl=role.lower()",
        ' if rl in ("text","entry","passwordtext","edit","editabletext"):',
        "  box=node",
        "if box is None: sys.exit(0)",
        "ok=False",
        "try:",
        " box.set_text_contents(text); ok=True",
        "except Exception:",
        " ok=False",
        "if not ok: sys.exit(0)",
        "try:",
        " got=box.get_text_contents() or \"\"",
        " if got and text not in got and got not in text:",
        "  sys.exit(0)",
        "except Exception:",
        " pass",
        "submitted=False",
        "try:",
        " na=box.get_n_actions()",
        "except Exception:",
        " na=0",
        "for i in range(na):",
        " try:",
        "  an=(box.get_action_name(i) or \"\").lower()",
        " except Exception:",
        "  an=\"\"",
        ' if any(k in an for k in ("activate","press","click","submit")):',
        "  try:",
        "   box.do_action(i); submitted=True; break",
        "  except Exception:",
        "   continue",
        "if not submitted:",
        " for kind_name in (\"PRESSRELEASE\",\"SYM\",\"STRING\",\"PRESS\"):",
        "  kind=getattr(Atspi.KeySynthType, kind_name, None)",
        "  if kind is None: continue",
        "  try:",
        "   Atspi.generate_keyboard_event(0xFF0D, \"Return\", kind)",
        "   submitted=True",
        "   break",
        "  except Exception:",
        "   continue",
        "if not submitted:",
        " nodes=walk(app)",
        " for node,name,role in nodes:",
        "  nm=(name or \"\").strip()",
        '  if nm in ("发送","Send","send") or nm.startswith("发送"):',
        "   try:",
        "    na=node.get_n_actions()",
        "   except Exception:",
        "    na=0",
        "   for i in range(na):",
        "    try:",
        "     node.do_action(i); submitted=True; break",
        "    except Exception:",
        "     continue",
        "  if submitted:",
        "   break",
        "if not submitted: sys.exit(0)",
        'print("OK")',
    ])
    env = os.environ.copy()
    env["WECHAT_SEND_PEER"] = peer
    env["WECHAT_SEND_TEXT"] = text
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            text=True,
            timeout=6,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and "OK" in (proc.stdout or "")
