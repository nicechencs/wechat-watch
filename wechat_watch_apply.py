from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time


_RAISE_SLEEP_S = 0.2
_FOCUS_SESSION_SLEEP_S = 0.3
_FOCUS_INPUT_SLEEP_S = 0.05


def apply_send_plan(plan: dict, x11=None) -> bool:
    """Raise WeChat, click planned x/y, paste via clipboard+ctrl+v, Return.

    Never talks to AT-SPI in this process. A missing accessibility bus
    aborts the interpreter (SIGTRAP); clicks go through X11 instead.
    ok is False unless a required session-row click actually ran and the
    optional selected-session check still matches the peer.
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
    need_session = any((s.get("op") == "focus-session") for s in actions)
    clicked_session = False
    try:
        wid = plan.get("window_id") or plan.get("id")
        driver.raise_window(None if not wid else str(wid))
        waiter = getattr(driver, "wait_active", None)
        if callable(waiter):
            waiter(None if not wid else str(wid))
        time.sleep(_RAISE_SLEEP_S)
        for step in actions:
            op = step.get("op")
            if op == "focus-session":
                driver.click(int(step["x"]), int(step["y"]))
                clicked_session = True
                switched = getattr(driver, "wait_session", None)
                if callable(switched):
                    switched(peer)
                time.sleep(_FOCUS_SESSION_SLEEP_S)
                if not _selected_matches_plan(driver, plan):
                    return False
            elif op == "focus-input":
                driver.click(int(step["x"]), int(step["y"]))
                time.sleep(_FOCUS_INPUT_SLEEP_S)
            elif op == "type":
                body = str(step.get("text") or text)
                driver.paste(body)
                typed = True
            elif op == "submit":
                driver.submit()
                submitted = True
    except Exception:
        return False
    if need_session and not clicked_session:
        return False
    if not _selected_matches_plan(driver, plan):
        return False
    return bool(typed and submitted)


def _fold_verify(text: str) -> str:
    raw = (text or "").replace("\n", " ").replace("\r", " ")
    raw = raw.replace("…", "").replace("...", "")
    return "".join(raw.split()).lower()


def _selected_matches_plan(driver, plan: dict) -> bool:
    """Fail send if the driver still shows a different session after the click."""
    fn = getattr(driver, "selected_session", None)
    if not callable(fn):
        return True
    try:
        got = fn()
    except Exception:
        return True
    if not got:
        return True
    peer = str(plan.get("peer") or "")
    sess = plan.get("session") if isinstance(plan.get("session"), dict) else {}
    want_u = str(plan.get("username") or (sess or {}).get("username") or "")
    want_last = str((sess or {}).get("last") or (sess or {}).get("preview") or "")
    click = plan.get("click") if isinstance(plan.get("click"), dict) else {}
    if not want_last and click:
        want_last = str(click.get("last") or click.get("preview") or "")
    if isinstance(got, str):
        g = got.strip()
        if peer and g and g != peer and peer not in g and g not in peer:
            return False
        return True
    if isinstance(got, dict):
        name = str(got.get("name") or got.get("display") or got.get("title") or "")
        uname = str(got.get("username") or got.get("user") or got.get("wxid") or "")
        snippet = str(got.get("last") or got.get("preview") or got.get("snippet") or "")
        if want_u and uname and want_u.strip().lower() != uname.strip().lower():
            return False
        if name and peer and name != peer and peer not in name and name not in peer:
            return False
        if want_last and snippet:
            fa, fb = _fold_verify(want_last), _fold_verify(snippet)
            if fa and fb and fa != fb and fa not in fb and fb not in fa:
                return False
        return True
    return True


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

    def raise_window(self, win_id: str | None = None) -> None:
        """Map-raise and activate WeChat so XTEST keys do not hit another client."""
        wid = (win_id or "").strip() or _find_send_window_id()
        if _xlib_raise(wid) or _ctypes_raise(wid) or _xdotool_raise(wid) or _wmctrl_raise(wid):
            self.wait_active(wid)
            return
        raise RuntimeError("x11-raise-failed")

    def wait_active(self, win_id: str | None = None) -> None:
        """Block until _NET_ACTIVE_WINDOW is WeChat (keys must not hit a terminal)."""
        wid = (win_id or "").strip() or _find_send_window_id()
        if _wait_active_window(wid):
            return
        xid = _parse_xid(wid)
        cur = _xlib_active_window_xid() or _xdotool_active_window_xid()
        if xid is not None and cur is not None and int(cur) != xid:
            raise RuntimeError("x11-raise-failed")
        time.sleep(_RAISE_SLEEP_S)

    def wait_session(self, peer: str | None = None) -> None:
        """Give the client time to switch the open thread after a list click."""
        time.sleep(_FOCUS_SESSION_SLEEP_S)

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


def _parse_xid(win_id: str | None) -> int | None:
    raw = (win_id or "").strip()
    if not raw:
        return None
    try:
        return int(raw, 16) if raw.lower().startswith("0x") else int(raw)
    except ValueError:
        return None


def _find_send_window_id() -> str:
    spec = (os.environ.get("WECHAT_WINDOW_ID") or "").strip()
    if spec:
        return spec
    mod = sys.modules.get("wechat_watch_regions")
    if mod is not None:
        finder = getattr(mod, "find_window_id", None)
        if callable(finder):
            try:
                found = finder(probe=True)
            except Exception:
                found = None
            if found:
                return str(found)
    return _xdotool_search_wechat() or _wmctrl_search_wechat() or ""


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


def _xlib_raise(win_id: str) -> bool:
    xid = _parse_xid(win_id)
    if xid is None:
        return False
    mods = _xlib_mods()
    if mods is None:
        return False
    X, display, _xtest = mods
    try:
        from Xlib.protocol import event

        dpy = display.Display()
        root = dpy.screen().root
        win = dpy.create_resource_object("window", xid)
        win.map()
        win.configure(stack_mode=X.Above)
        try:
            win.set_input_focus(X.RevertToParent, X.CurrentTime)
        except Exception:
            pass
        atom = dpy.intern_atom("_NET_ACTIVE_WINDOW")
        ev = event.ClientMessage(
            window=win,
            client_type=atom,
            data=(32, [1, X.CurrentTime, 0, 0, 0]),
        )
        mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
        root.send_event(ev, event_mask=mask)
        dpy.flush()
        dpy.close()
        return True
    except Exception:
        return False


def _xlib_active_window_xid() -> int | None:
    """EWMH _NET_ACTIVE_WINDOW on the root. None if the property is missing."""
    mods = _xlib_mods()
    if mods is None:
        return None
    X, display, _xtest = mods
    try:
        dpy = display.Display()
        root = dpy.screen().root
        atom = dpy.intern_atom("_NET_ACTIVE_WINDOW")
        prop = root.get_full_property(atom, X.AnyPropertyType)
        dpy.close()
        if not prop or not getattr(prop, "value", None):
            return None
        return int(prop.value[0])
    except Exception:
        return None


def _xdotool_active_window_xid() -> int | None:
    exe = shutil.which("xdotool")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "getactivewindow"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    tok = (proc.stdout or "").strip().split()
    if not tok:
        return None
    raw = tok[0]
    try:
        return int(raw, 16) if raw.lower().startswith("0x") else int(raw)
    except ValueError:
        return None


def _wait_active_window(win_id: str, timeout: float = 1.0) -> bool:
    """True if the WM reports our xid as _NET_ACTIVE_WINDOW before timeout.

    Unknown (no property / no xdotool) is not a hard fail: some WMs omit it.
    A *different* active window means keys would hit the covering client.
    """
    xid = _parse_xid(win_id)
    if xid is None:
        return False
    deadline = time.time() + max(0.05, float(timeout))
    seen = False
    last = None
    while time.time() < deadline:
        last = _xlib_active_window_xid()
        if last is None:
            last = _xdotool_active_window_xid()
        if last is None:
            time.sleep(0.05)
            continue
        seen = True
        if int(last) == xid:
            return True
        time.sleep(0.05)
    if not seen:
        return False
    return last is not None and int(last) == xid


def _ctypes_raise(win_id: str) -> bool:
    xid = _parse_xid(win_id)
    if xid is None:
        return False
    try:
        from ctypes import (
            Structure,
            byref,
            c_char_p,
            c_int,
            c_long,
            c_ulong,
            c_void_p,
            cdll,
        )

        x11 = cdll.LoadLibrary("libX11.so.6")
        x11.XOpenDisplay.restype = c_void_p
        x11.XOpenDisplay.argtypes = [c_char_p]
        x11.XCloseDisplay.argtypes = [c_void_p]
        x11.XFlush.argtypes = [c_void_p]
        x11.XDefaultScreen.restype = c_int
        x11.XDefaultScreen.argtypes = [c_void_p]
        x11.XRootWindow.restype = c_ulong
        x11.XRootWindow.argtypes = [c_void_p, c_int]
        x11.XMapRaised.argtypes = [c_void_p, c_ulong]
        x11.XRaiseWindow.argtypes = [c_void_p, c_ulong]
        x11.XSetInputFocus.argtypes = [c_void_p, c_ulong, c_int, c_ulong]
        x11.XInternAtom.restype = c_ulong
        x11.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
        x11.XSendEvent.argtypes = [c_void_p, c_ulong, c_int, c_long, c_void_p]
    except Exception:
        return False

    class _ClientMessage(Structure):
        _fields_ = [
            ("type", c_int),
            ("serial", c_ulong),
            ("send_event", c_int),
            ("display", c_void_p),
            ("window", c_ulong),
            ("message_type", c_ulong),
            ("format", c_int),
            ("data", c_long * 5),
        ]

    dpy = x11.XOpenDisplay(_display_name())
    if not dpy:
        return False
    try:
        screen = x11.XDefaultScreen(dpy)
        root = x11.XRootWindow(dpy, screen)
        x11.XMapRaised(dpy, xid)
        x11.XRaiseWindow(dpy, xid)
        revert_to_parent = 2
        x11.XSetInputFocus(dpy, xid, revert_to_parent, 0)
        atom = x11.XInternAtom(dpy, b"_NET_ACTIVE_WINDOW", 0)
        ev = _ClientMessage()
        ev.type = 33  # ClientMessage
        ev.serial = 0
        ev.send_event = 1
        ev.display = dpy
        ev.window = xid
        ev.message_type = atom
        ev.format = 32
        ev.data[0] = 1
        ev.data[1] = 0
        ev.data[2] = 0
        ev.data[3] = 0
        ev.data[4] = 0
        mask = (1 << 19) | (1 << 20)  # SubstructureNotify | SubstructureRedirect
        x11.XSendEvent(dpy, root, 0, mask, byref(ev))
        x11.XFlush(dpy)
        return True
    except Exception:
        return False
    finally:
        x11.XCloseDisplay(dpy)


def _xid_tokens(win_id: str) -> list[str]:
    raw = (win_id or "").strip()
    if not raw:
        return []
    out = [raw]
    xid = _parse_xid(raw)
    if xid is None:
        return out
    hex_id = hex(xid)
    dec_id = str(xid)
    for tok in (hex_id, dec_id):
        if tok not in out:
            out.append(tok)
    return out


def _xdotool_search_wechat() -> str:
    exe = shutil.which("xdotool")
    if not exe:
        return ""
    for args in (
        ["search", "--onlyvisible", "--class", "wechat"],
        ["search", "--onlyvisible", "--name", "WeChat"],
        ["search", "--onlyvisible", "--name", "微信"],
        ["search", "--class", "wechat"],
        ["search", "--name", "WeChat"],
    ):
        try:
            proc = subprocess.run(
                [exe, *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=4,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0:
            continue
        for line in (proc.stdout or "").splitlines():
            tok = line.strip()
            if tok.isdigit() or tok.lower().startswith("0x"):
                return tok
    return ""


def _wmctrl_search_wechat() -> str:
    exe = shutil.which("wmctrl")
    if not exe:
        return ""
    try:
        proc = subprocess.run(
            [exe, "-lx"],
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    best = ""
    for line in (proc.stdout or "").splitlines():
        low = line.lower()
        if "wechat-watch" in low:
            continue
        if any(k in low or k in line for k in ("wechat", "weixin", "微信")):
            tok = line.split(None, 1)[0] if line.strip() else ""
            if tok.lower().startswith("0x") or tok.isdigit():
                best = tok
                break
    return best


def _xdotool_raise(win_id: str) -> bool:
    for tok in _xid_tokens(win_id):
        if _xdotool("windowactivate", "--sync", tok) or _xdotool("windowraise", tok):
            return True
    exe = shutil.which("xdotool")
    if not exe:
        return False
    for args in (
        ["search", "--onlyvisible", "--class", "wechat", "windowactivate", "--sync"],
        ["search", "--onlyvisible", "--name", "WeChat", "windowactivate", "--sync"],
        ["search", "--onlyvisible", "--name", "微信", "windowactivate", "--sync"],
    ):
        try:
            proc = subprocess.run(
                [exe, *args],
                check=False,
                capture_output=True,
                timeout=4,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            return True
    return False


def _wmctrl_raise(win_id: str) -> bool:
    exe = shutil.which("wmctrl")
    if not exe:
        return False
    env = os.environ.copy()
    env.setdefault("DISPLAY", env.get("DISPLAY", ":8"))
    for tok in _xid_tokens(win_id):
        try:
            proc = subprocess.run(
                [exe, "-i", "-a", tok],
                check=False,
                capture_output=True,
                timeout=4,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            return True
    for name in ("WeChat", "微信", "weixin"):
        try:
            proc = subprocess.run(
                [exe, "-a", name],
                check=False,
                capture_output=True,
                timeout=4,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            return True
    return False


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
