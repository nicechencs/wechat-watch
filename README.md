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
- On `UNCHANGED`, that is the whole signal: skip vision. No `region=` lines
  and no OCR.
- On `CHANGED`, it also finds **which rectangles** of the crop actually
  changed, writes those small slices, and OCRs each slice (Chinese + English)
  so an agent can use plain text when the region is text.

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
7. Writes `watch/regions.json` (`[{x,y,w,h,path,text,kind}, ...]`, coords in
   the crop) and `watch/regions/r0.png` … cropped from the **current**
   `list.png`.
8. Extra stdout after the usual `CHANGED` / `hash=` / `at=` / `list=` lines:

       regions=N
       region=/home/box/.local/share/wechat-persist/watch/regions/r0.png
       ...
       text0=...
       kind0=text
       kind1=image

First run (no previous crop): `CHANGED` with `regions=1` pointing at the full
`watch/list.png` (also OCR'd).

`wechat-watch-regions` is the standalone helper (`--prev --curr --out-dir --json`,
or `--full LIST.png`) used by the watcher and for tests.

## OCR (plain text, no vision)

Each `rN.png` (or the full crop on first run) is passed to tesseract with
`-l chi_sim+eng` and `--psm 6` (or `7` for short / badge-like boxes).
`TESSDATA_PREFIX` prefers `/home/box/.local/share/wechat-persist/tessdata`
(`chi_sim.traineddata` + `eng.traineddata`, copied from the apt language
packs; not in git).

Classification (no confidence score):

- `kind=text` if the stripped OCR string has **at least one CJK character**
  or **two or more alphanumeric characters**.
- `kind=image` if OCR is empty, whitespace-only, or punctuation-only junk
  (photo / blank / icon). `text` is then `""`.

Stdout after every `region=` line, one pair per region index:

    text0=你好 Hello
    kind0=text
    kind1=image

`textN=` is omitted when `kind` is `image`. Newlines inside OCR text are
escaped as `\n` so each `textN=` stays one line.

**When `kindN=text`, the agent must use `textN=` and must not `Read` the
PNG.** Vision is wasted on text the helper already extracted. When
`kindN=image`, `Read` the `region=` path if the pixels matter.

`UNCHANGED` does not run OCR.

The engine is `tesseract-ocr` + `tesseract-ocr-chi-sim` + `tesseract-ocr-eng`
from apt. `ensure-wechat` reinstalls those packages after a reset via
`persist/apt-deps.txt`; traineddata also lives under persist so
`TESSDATA_PREFIX` still works once the binary is back.

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
| `persist/tessdata/chi_sim.traineddata` | Simplified Chinese OCR data |
| `persist/tessdata/eng.traineddata` | English OCR data |
| `persist/watch/full.png` | last full desktop grab |
| `persist/watch/list.png` | current chat-list crop |
| `persist/watch/list.prev.png` | previous crop |
| `persist/watch/list.sha256` | last hash |
| `persist/watch/regions.json` | last changed boxes + OCR |
| `persist/watch/regions/rN.png` | small slices |
| `persist/watch/changes.log` | hash changelog |

Poll with `wechat-watch-diff`. If the first line is `UNCHANGED`, do nothing.
If `CHANGED`, for each region: if `kindN=text`, use `textN=` and do **not**
`Read` the png; if `kindN=image`, `Read` the `region=` path (or `list=` if
`regions=1` and it points at the full crop).
