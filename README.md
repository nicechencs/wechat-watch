# wechat-watch

Reset-proof WeChat chat-list change detector for the Grok Bot cloud computer.

There is no marketplace plugin for this. The watcher lives on disk under
`/home/box` so it survives image reset. Runtime state and the static ffmpeg
binary stay in `/home/box/.local/share/wechat-persist`.

## What it does

`wechat-watch-diff` grabs the 1280x800 desktop (`DISPLAY`, default `:8`) with
persist `bin/ffmpeg` (x11grab), crops the chat list (`440x700+70+30`), and
sha256-compares it to `watch/list.sha256`.

- First line of stdout is `UNCHANGED` or `CHANGED`.
- On `UNCHANGED`, that is the whole signal: skip vision. No `region=` lines.
- On `CHANGED`, it also finds **which rectangles** of the crop actually
  changed and writes those small slices so an agent can `Read` only the
  changed regions (token savings vs the full list or full screen).

Do not launch WeChat or send messages from this tool. It only screenshots.

## Region crops

When `watch/list.png` is about to be replaced, the previous crop is copied to
`watch/list.prev.png`. If the hash says `CHANGED` and a previous crop exists:

1. Persist ffmpeg decodes both PNGs to raw rgb24 (stdlib Python, no Pillow).
2. A pixel counts as changed if any RGB channel delta is `> 12`.
3. Isolated single-pixel noise is dropped. Remaining changed pixels are
   grouped into 8-connected bounding boxes.
4. Boxes within `16px` are merged, then padded by `8px`.
5. Boxes smaller than `20x20` are dropped unless they look like a badge
   (`12x12+` whose left edge is in the left `80px` — nav / avatar badges).
6. At most `6` boxes. If the changed area is `> 40%` of the crop (scroll /
   huge restyle), one box = the full crop.
7. Writes `watch/regions.json` (`[{x,y,w,h,path}, ...]`, coords in the crop)
   and `watch/regions/r0.png` … cropped from the **current** `list.png`.
8. Extra stdout after the usual `CHANGED` / `hash=` / `at=` / `list=` lines:

       regions=N
       region=/home/box/.local/share/wechat-persist/watch/regions/r0.png
       ...

First run (no previous crop): `CHANGED` with `regions=1` pointing at the full
`watch/list.png`.

`wechat-watch-regions` is the standalone helper (`--prev --curr --out-dir --json`)
used by the watcher and for tests.

## Install (live paths)

```bash
# source of truth in this repo
sudo cp /home/box/wechat-watch/wechat-watch-diff \
        /home/box/.local/share/wechat-persist/wechat-watch-diff
sudo cp /home/box/wechat-watch/wechat-watch-regions \
        /home/box/.local/share/wechat-persist/wechat-watch-regions
chmod +x /home/box/.local/share/wechat-persist/wechat-watch-diff \
         /home/box/.local/share/wechat-persist/wechat-watch-regions
sudo cp /home/box/wechat-watch/wechat-watch-diff \
        /home/box/.local/bin/wechat-watch-diff
sudo chmod 0755 /home/box/.local/bin/wechat-watch-diff
```

`ensure-wechat` already restores `/home/box/.local/bin/wechat-watch-diff` from
the persist copy after a reset.

## Runtime files (not in git)

| path | role |
|---|---|
| `persist/bin/ffmpeg` | static x11grab ffmpeg (~77MB) |
| `persist/watch/full.png` | last full desktop grab |
| `persist/watch/list.png` | current chat-list crop |
| `persist/watch/list.prev.png` | previous crop |
| `persist/watch/list.sha256` | last hash |
| `persist/watch/regions.json` | last changed boxes |
| `persist/watch/regions/rN.png` | small slices |
| `persist/watch/changes.log` | hash changelog |

Poll with `wechat-watch-diff`. If the first line is `UNCHANGED`, do nothing.
If `CHANGED`, `Read` each `region=` path (or `list=` if `regions=1` and it
points at the full crop).
