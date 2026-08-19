from __future__ import annotations
import os
import subprocess
import sys
import time


def _atspi():
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        return Atspi
    except Exception:
        return None


def apply_send_plan(plan: dict) -> bool:
    """Click planned x/y, fill composer, submit. No AT-SPI tree walk."""
    if not isinstance(plan, dict):
        return False
    peer = str(plan.get("peer") or "")
    text = str(plan.get("text") or "")
    if not peer or not text:
        return False
    if "群" in peer or "chatroom" in peer.lower():
        return False
    actions = list(plan.get("actions") or [])
    if not actions:
        return False
    banned = {"search", "add-contact", "new-chat", "compose-new"}
    if any((s.get("op") in banned) for s in actions):
        return False
    Atspi = _atspi()
    if Atspi is None:
        return False
    os.environ.setdefault("DISPLAY", os.environ.get("DISPLAY", ":8"))
    typed = False
    submitted = False
    for step in actions:
        op = step.get("op")
        if op in ("focus-session", "focus-input"):
            try:
                x, y = int(step["x"]), int(step["y"])
                Atspi.generate_mouse_event(x, y, "b1c")
            except Exception:
                return False
            time.sleep(0.05)
        elif op == "type":
            body = str(step.get("text") or text)
            if not _fill_focused(Atspi, body):
                return False
            typed = True
        elif op == "submit":
            if not _submit_return(Atspi):
                return False
            submitted = True
    return bool(typed and submitted)


def _fill_focused(Atspi, text: str) -> bool:
    box = _focused_edit(Atspi)
    if box is not None:
        try:
            box.set_text_contents(text)
            return True
        except Exception:
            pass
    return _paste_clipboard(Atspi, text)


def _focused_edit(Atspi):
    try:
        desk = Atspi.get_desktop(0)
        n = desk.get_child_count()
    except Exception:
        return None
    for i in range(min(n or 0, 20)):
        try:
            app = desk.get_child_at_index(i)
        except Exception:
            continue
        if app is None:
            continue
        try:
            nm = (app.get_name() or "").lower()
        except Exception:
            nm = ""
        if "wechat-watch" in nm:
            continue
        if not any(k in nm for k in ("wechat", "weixin", "微信")):
            continue
        box = _find_edit(app, 0)
        if box is not None:
            return box
    return None


def _find_edit(node, depth: int):
    if node is None or depth > 6:
        return None
    try:
        role = (node.get_role_name() or "").lower()
    except Exception:
        role = ""
    if role in ("text", "entry", "passwordtext", "edit", "editabletext"):
        return node
    try:
        n = node.get_child_count()
    except Exception:
        n = 0
    last = None
    for i in range(min(n or 0, 24)):
        try:
            ch = node.get_child_at_index(i)
        except Exception:
            continue
        hit = _find_edit(ch, depth + 1)
        if hit is not None:
            last = hit
    return last


def _paste_clipboard(Atspi, text: str) -> bool:
    return False


def _submit_return(Atspi) -> bool:
    for kind_name in ("PRESSRELEASE", "SYM", "STRING", "PRESS"):
        kind = getattr(Atspi.KeySynthType, kind_name, None)
        if kind is None:
            continue
        try:
            Atspi.generate_keyboard_event(0xFF0D, "Return", kind)
            return True
        except Exception:
            continue
    return False


def apply_private_text(peer: str, text: str) -> bool:
    """Best-effort AT-SPI: open existing 1:1 row, fill composer, then submit."""
    if not peer or not text:
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
