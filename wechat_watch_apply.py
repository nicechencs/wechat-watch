from __future__ import annotations
import os
import subprocess
import sys

def apply_private_text(peer: str, text: str) -> bool:
    """Best-effort AT-SPI: open existing 1:1 row and set composer text."""
    if not peer or not text:
        return False
    code = (
        'import os,sys\n'
        'os.environ.setdefault("DISPLAY", os.environ.get("DISPLAY", ":8"))\n'
        'peer=os.environ.get("WECHAT_SEND_PEER","")\n'
        'text=os.environ.get("WECHAT_SEND_TEXT","")\n'
        'try:\n'
        ' import gi\n'
        ' gi.require_version("Atspi","2.0")\n'
        ' from gi.repository import Atspi\n'
        ' desk=Atspi.get_desktop(0)\n'
        'except Exception:\n'
        ' sys.exit(0)\n'
        'def walk(node, depth=0, acc=None):\n'
        ' if acc is None: acc=[]\n'
        ' if node is None or depth>8 or len(acc)>80: return acc\n'
        ' name=role=""\n'
        ' try: name=node.get_name() or ""\n'
        ' except Exception: pass\n'
        ' try: role=node.get_role_name() or ""\n'
        ' except Exception: pass\n'
        ' acc.append((node,name,role))\n'
        ' n=0\n'
        ' try: n=node.get_child_count()\n'
        ' except Exception: n=0\n'
        ' for i in range(min(n,40)):\n'
        '  try: ch=node.get_child_at_index(i)\n'
        '  except Exception: continue\n'
        '  walk(ch, depth+1, acc)\n'
        ' return acc\n'
        'try: n=desk.get_child_count()\n'
        'except Exception: sys.exit(0)\n'
        'app=None\n'
        'for i in range(n):\n'
        ' try: cand=desk.get_child_at_index(i)\n'
        ' except Exception: continue\n'
        ' if cand is None: continue\n'
        ' nm=rl=""\n'
        ' try: nm=cand.get_name() or ""\n'
        ' except Exception: pass\n'
        ' try: rl=cand.get_role_name() or ""\n'
        ' except Exception: pass\n'
        ' blob=(nm+" "+rl).lower()\n'
        ' if "wechat-watch" in blob: continue\n'
        ' if any(k in blob for k in ("wechat","weixin","微信")):\n'
        '  app=cand; break\n'
        'if app is None: sys.exit(0)\n'
        'nodes=walk(app)\n'
        'row=None\n'
        'for node,name,role in nodes:\n'
        ' if not name: continue\n'
        ' if name==peer or name.startswith(peer):\n'
        '  if "群" in name or "chatroom" in name.lower():\n'
        '   continue\n'
        '  row=node; break\n'
        'if row is None: sys.exit(0)\n'
        'try:\n'
        ' na=row.get_n_actions()\n'
        'except Exception:\n'
        ' na=0\n'
        'for i in range(na):\n'
        ' try:\n'
        '  row.do_action(i); break\n'
        ' except Exception:\n'
        '  continue\n'
        'box=None\n'
        'for node,name,role in nodes:\n'
        ' rl=role.lower()\n'
        ' if rl in ("text","entry","passwordtext","edit","editabletext"):\n'
        '  box=node\n'
        'if box is None: sys.exit(0)\n'
        'ok=False\n'
        'try:\n'
        ' box.set_text_contents(text); ok=True\n'
        'except Exception:\n'
        ' ok=False\n'
        'if not ok: sys.exit(0)\n'
        'print("OK")\n'
    )
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
