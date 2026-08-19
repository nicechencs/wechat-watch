# wechat-watch

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

低成本的微信桌面「聊天列表」变化检测器，外加「翻历史才录屏」。用哈希和像素差分代替每次都把整张截图交给视觉模型，有字的变化块再用 OCR 变成纯文本。

仓库：[https://github.com/nicechencs/wechat-watch](https://github.com/nicechencs/wechat-watch)

本工具**只截屏、对比、识别文字**，不会打开微信、不会登录、不会发消息。

## 它解决什么问题

轮询微信未读时，如果每 10 秒就把整屏丢给多模态模型，token 会非常贵。本项目把成本拆成三层：

1. **哈希（只哈希左侧列表）**：`wechat-watch-diff` 只对左侧会话列表做 sha256。没变 → 输出 `UNCHANGED`，到此结束，零视觉、零 OCR、**不开始录像**
2. **区域差分**：列表变了 → 只切出真正变了的小矩形并 OCR
3. **翻历史才录屏**：只有右侧对话区正在滚动（看得到滚动条 / 内容在滚像素）时，才对**对话区那一块**做短录像；列表变化不会启动录像

一次未变化的检查大约只需一次 ffmpeg 截屏 + sha256，耗时大约 200–400ms。

## 工作原理

```
窗口优先，找不到才桌面 1280x800
    │  ffmpeg x11grab（关闭鼠标）
    ▼
full.png
    │  裁左侧列表 440x700+70+30     裁右侧对话区 720x660+414+40
    ▼                              ▼
list.png ──sha256── list.sha256   thread.png（只用来判断滚没滚）
    │
    ├─ 列表没变 ──────────────────────────────► UNCHANGED（退出）
    │         └─ 若 thread 在滚动（滚动条 / 滚像素）
    │              才 x11grab 对话区矩形 → 短片 → 逐帧 OCR
    │
    └─ 列表变了 ─► CHANGED + 列表 regions=
                   不开始录像
```

会话列表的时间戳一般是「最后一条消息的时间」，不会每秒跳变，所以列表哈希在没新消息时是稳定的。输入框闪烁的光标在两块裁剪之外，不会误触发。

裁剪跟找到的微信窗口走（`WECHAT_WINDOW=WxH+X+Y` 或 `X,Y,W,H`，或只读解析 `xwininfo -root -tree` / `wmctrl -lG`）；找不到再用上面的 1280×800 常量。实况优先按窗口 id 或窗口矩形抓帧，避免只抓桌面 1280×800 时裁不到移出/放大后的窗口。

**窗口移动 / 缩放 / DPI（诚实说明）：** 窗口**移动**后，只要 `find_window` 拿到矩形（`WECHAT_WINDOW` / root-tree / `wmctrl -lG`），裁剪会跟着平移，这一步已经可用。窗口被拉窄、或用户拖了列表|对话区分隔线（**resize**）时，固定的 `NAV_INSET=1`、`LIST_INSET=63`、`THREAD_INSET=282` **不会**跟着分隔线走，窄窗或拖过的 list|thread 线会裁到错误的栏；`--window-geom --window-png` 会在窗口内部扫描竖向 gutter（分隔线）来跟栏。高度上若直接用整窗 `win_h`，会把标题栏、搜索框、输入框/发送都包进去（旧的最大化路径用的是 `+30/700` 和 `+40/660`）。150% / 200% 等分数缩放（DPI）**仍未处理**：像素 inset 会对不上，需要真实像素矩形再扫描，不能凭空缩放未知布局。实况 `wechat-watch-diff` / `wechat-watch-thread` 现在会写下 `watch/window.png` 并传给 `--window-png`，拖分隔线后裁剪会更新；150%/200% 分数 DPI 仍不会自动处理。AT-SPI（python3-gi / Atspi）已能导入，但无障碍总线未运行、没有可用控件树，因此跳过实况 AT-SPI；不设置 QT_ACCESSIBILITY，也不重启应用。列表像素哈希若因光标/选区/徽章动画抖动，OCR 后的 text0 指纹（list.text.sha）相同则输出 UNCHANGED + flap=1，不当新内容。

## 翻历史、有滚动条才录屏

人在打开的会话里往上翻历史时，左侧列表通常不变（未读轮询仍是 `UNCHANGED`），但右侧气泡在滚。这时才值得花录像的成本：

- **启动条件**：右侧对话区裁剪里出现滚动条，或内容发生明显的纵向滚像素。新气泡、切会话、列表高亮变化都**不会**开始录像
- **只录这一块**：`ffmpeg x11grab` 的画面是 `720x660+414+40`（对话区），不含左侧列表、输入框、dock
- **默认**：约 4 秒、4 fps（可用 `WECHAT_SCROLL_SECS` / `WECHAT_SCROLL_FPS` 改）
- **逐帧**：相邻帧做区域差分 + tesseract `chi_sim+eng`。对方头像切出来做 average-hash，OCR 昵称，写入 `persist/watch/identities.json`（名字↔哈希）
- **时间线**：`persist/watch/clips/scroll-*.json`，每条 `{t, side:in|out, name, avatar_hash, text}`

```
UNCHANGED
hash=…
at=…
scroll=1
timeline=/path/to/watch/clips/scroll-….json
messages=N
```

`side=in` 是左侧对方气泡，`side=out` 是右侧自己。解析完成后录像和抽帧按 GC 规则删掉，只留 JSON。

单独检测 / 解析：

```bash
./wechat-watch-regions --detect-scroll \
  --prev thread.prev.png --curr thread.png --ffmpeg "$WECHAT_PERSIST/bin/ffmpeg"

./wechat-watch-regions --parse-frames ./frames \
  --timeline ./out.json \
  --identities "$WECHAT_PERSIST/watch/identities.json" \
  --avatars-dir "$WECHAT_PERSIST/watch/avatars"
```

## 依赖

| 软件 | 用途 | 安装 |
|---|---|---|
| bash | `wechat-watch-diff` 入口 | 系统自带 |
| Python 3.11+ | `wechat-watch-regions`（仅标准库） | 系统自带 |
| ffmpeg（需 x11grab） | 截屏、裁剪、解码、短录像 | 静态包或 `apt install ffmpeg` |
| tesseract + chi_sim + eng | 中英 OCR | `apt install tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng` |

不依赖 Pillow、OpenCV。

## 安装

```bash
git clone https://github.com/nicechencs/wechat-watch.git
cd wechat-watch

# 系统依赖
sudo apt-get update
sudo apt-get install -y ffmpeg tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng

# 可选：把入口链到 PATH
sudo ln -sf "$PWD/wechat-watch-diff" /usr/local/bin/wechat-watch-diff
```

在 Grok Bot 云电脑上，运行时文件默认放在 `/home/box/.local/share/wechat-persist`（镜像 reset 后仍会保留）。静态 ffmpeg 和 tessdata 也可以放在这个目录：

```
$WECHAT_PERSIST/bin/ffmpeg
$WECHAT_PERSIST/tessdata/chi_sim.traineddata
$WECHAT_PERSIST/tessdata/eng.traineddata
```

把脚本装到本机 persist / PATH（云电脑示例）：

```bash
PERSIST="${WECHAT_PERSIST:-$HOME/.local/share/wechat-persist}"
sudo cp wechat-watch-diff wechat-watch-regions wechat-watch-gc wechat-watch-thread "$PERSIST/"
sudo cp wechat-watch-diff /home/box/.local/bin/wechat-watch-diff
sudo chmod 0755 /home/box/.local/bin/wechat-watch-diff "$PERSIST/wechat-watch-gc"
```

## 用法

```bash
export DISPLAY="${DISPLAY:-:8}"
./wechat-watch-diff
```

### 没有变化

```
UNCHANGED
hash=715d94b8…
at=2026-08-19T04:06:19+00:00
```

此时不要读任何图片，也不要跑视觉模型。若正在翻历史，后面可能多出 `scroll=1` / `timeline=` 行。若只有列表像素在抖、OCR text0 没变，也会走这条，并多一行 `flap=1`。

### 有变化（列表）

```
CHANGED
hash=…
at=…
list=/path/to/watch/list.png
regions=2
region=/path/to/watch/regions/r0.png
region=/path/to/watch/regions/r1.png
text0=阿坤
根据我的描述画：日落...
kind0=text
kind1=image
```

列表变了只做列表差分和 OCR，**不会**因此开始录像。

调用约定：

- `kindN=text`：只用对应的 `textN=`，**不要**再 `Read` PNG
- `kindN=image`：需要看图时，只读 `region=` 那一小块
- `textN=` 里的换行已转义成 `\n`，保证每行一条
- 第一次运行没有上一帧，会输出整块列表，并做一次 OCR

单独跑区域 + OCR（列表默认 `r` / 无前缀；对话区加 `--prefix t --label thread_`）：

```bash
./wechat-watch-regions --prev prev.png --curr curr.png \
  --out-dir ./out --json ./out/regions.json \
  --ffmpeg /usr/bin/ffmpeg

./wechat-watch-regions --prev thread.prev.png --curr thread.png \
  --out-dir ./thread-out --json ./thread-out/thread-regions.json \
  --prefix t --label thread_ --emit-side --extra-top-pad 22

# 当前右侧对话区只读 OCR（给 wechat-group-summaries，不发送）
./wechat-watch-thread
./wechat-watch-regions --format-time --when 2026-08-19T07:02:00+00:00

# 列表未读标记（只读，不点击）：哪一行有红数字 / 红点 / [N条]
./wechat-watch-regions --detect-unread "$WECHAT_PERSIST/watch/list.png"

# 左侧导航图标红点 / 数字（只读，不点图标）
./wechat-watch-regions --detect-nav "$WECHAT_PERSIST/watch/full.png"

# 对话区图片气泡（只读缓存到本地 persist，不会上传、不会发送；不进 10 秒轮询，不自动放大）
./wechat-watch-regions --cache-images thread.png --images-dir "$WECHAT_PERSIST/watch/images"

# 像素先分类（UIED 顺序：梯度 / 去细线 / 大框内再切 / 分类后再 OCR），本地 stdlib，无 OpenCV。不进 10 秒轮询。
./wechat-watch-regions --classify thread.png

# 对话区时间/日期分隔条（居中灰条：今天 16:04 / 昨天 / 年月日）。像素先找细条，只 OCR 那些框，不扫整页。不进 10 秒轮询。
./wechat-watch-regions --detect-time thread.png

# 会话名 / 群昵称清洗（空昵称、OCR 噪点、残留表情），不需要 PNG
./wechat-watch-regions --normalize-nick "陈"

# 当前微信窗口矩形 + nav/list/thread 裁剪（找不到则 1280x800 常量）
./wechat-watch-regions --window-geom
# 有窗口截图时扫描 gutter，list/thread 跟分隔线而不是固定 63/282
./wechat-watch-regions --window-geom --window-png "$WECHAT_PERSIST/watch/window.png"
```

## 区域差分规则

1. 覆盖 `list.png` / `thread.png` 前，先复制为 `list.prev.png` / `thread.prev.png`
2. ffmpeg 把两张 PNG 解成 raw rgb24
3. 任一 RGB 通道差值 `> 12` 视为变化
4. 去掉孤立单像素噪声，其余像素按 8 连通域成框
5. 间距 `≤ 16px` 的框合并，再向外垫 `8px`
6. 小于 `20×20` 的框丢掉；左侧 `80px` 内、不小于 `12×12` 的当作红点徽章保留
7. 最多 6 个框；变化面积超过裁剪区 `40%`（滚动、整页刷新）则退回整块列表

差分只保留左侧小变化框（`is_badge` / `BADGE_LEFT=80`），**不会**扫红像素，也**不会**给出未读条数。要知道「哪一行未读」，用下面的 `--detect-unread`。

## 未读标记（哪一行有红数字 / 红点 / [N条]）

列表哈希仍是廉价信号：没变就是 `UNCHANGED`，到此结束。`--detect-unread` 是只读的第二步，用来指出**哪一行**有未读标记：

- 头像右上角的鲜红圆标 + 白色数字（例如「5」）→ `kind=number`，`count` 为 OCR 到的数字
- 很小的红点（免打扰 / 不显示条数）→ `kind=dot`
- 预览里的 `[3条]` / `[12条]`、`@`、以及单独出现的 `z`/`Z` → `kind=text`

```
./wechat-watch-regions --detect-unread list.png
```

`list.png` 可以是已经裁好的 `440x700` 列表，也可以是整屏 `1280x800`（会先按 `LIST_CROP 440x700+70+30` 裁左侧）。输出例如：

```
unread_rows=1
unread0_kind=number
unread0_count=5
unread0_label=[5条]
unread0_x=166
unread0_y=54
unread0_w=14
unread0_h=14
unread0_name=独立产品创业
```

不点击、不打字、不发送。列表预览仍然**不是**群记录；群内容以右侧对话区为准（见 [docs/group-handling.md](docs/group-handling.md)）。

`--detect-nav` 同样只读：按竖直槽位标左侧导航图标上的红数字 / 红点（无独立「文件」槽）。窗口不在桌面 0,0 时仍可裁。

## OCR 规则

- 语言：`chi_sim+eng`
- 排版：普通块 `--psm 6`，短条 / 徽章状 `--psm 7`（按框尺寸判断，不以 x=0 当徽章）
- `kind=text`：去掉空白后，至少 1 个汉字，或至少 2 个字母数字
- `kind=image`：空、纯空白、或只有标点（照片、空白、图标）
- 会话名 / 群昵称走 `normalize_nick`：去掉空白和常见 OCR 噪点（`|||` `~~~`），空昵称或纯符号不当名字；汉字、字母数字、少量表情可保留。短垃圾不当 `text`。可用 `--normalize-nick` 单独检查（`nick=` / `ok=0|1`）
- `UNCHANGED` 不会跑 OCR（除非正在翻历史、已经开始解析录像帧）

语言包优先读 `$WECHAT_PERSIST/tessdata`（`TESSDATA_PREFIX`）。reset 后可用 `ensure-wechat` 按 `apt-deps.txt` 把 tesseract 装回来。

## 测试

```bash
python3 tests/test_regions.py
```

当前覆盖：假色块切框、徽章保留、大面积变化回退、中英 OCR、CLI 的 `textN=` / `kindN=`、thread 尺寸切框、左右气泡 → `in`/`out`、滚动检测、列表变化不录像、头像 average-hash、identities 绑定、时间线 JSON、`wechat-watch-gc` 过期删除、UTC/Asia/Shanghai 双时区、`wechat-watch-thread --png` 只读 OCR、左侧未读标记（红数字 / 红点 / `[N条]`）、窗口 gutter 扫描（移动 vs 缩放 / DPI）、窗口 id 解析、列表 text0 指纹 / flap、注入式 AT-SPI 探测、image-bubble read-only cache / `--cache-images`、像素先分类 / `--classify`、时间分隔条 / `--detect-time`、会话名 / 昵称 `normalize_nick`。

## 运行时文件（不进 git）

路径均在 `$WECHAT_PERSIST`（默认 `/home/box/.local/share/wechat-persist`）。

| 路径 | 作用 |
|---|---|
| `persist/bin/ffmpeg` | 静态 ffmpeg（约 77MB，带 x11grab） |
| `persist/tessdata/*.traineddata` | 中英 OCR 模型 |
| `persist/watch/full.png` | 最近一次整屏（只留最新一张） |
| `persist/watch/list.png` | 当前会话列表裁剪（**不删**，哈希依赖） |
| `persist/watch/list.prev.png` | 上一帧列表（**不删**） |
| `persist/watch/list.sha256` | 列表哈希（未读只看这个） |
| `persist/watch/list.text.sha` | 列表 OCR text0 指纹（像素抖但文字没变 → flap=1） |
| `persist/watch/thread.png` | 当前右侧对话区（**不删**，用来判断滚动） |
| `persist/watch/thread.prev.png` | 上一帧对话区（**不删**） |
| `persist/watch/regions.json` | 列表变化框 + OCR |
| `persist/watch/regions/rN.png` | 列表变化小块（15 分钟后回收） |
| `persist/watch/clips/scroll-*.mp4` | 翻历史短片（解析后立刻删，否则最多 10 分钟） |
| `persist/watch/clips/scroll-*.json` | 逐帧时间线 `{t,side,name,avatar_hash,text}` |
| `persist/watch/identities.json` | 名字↔头像哈希（**不删**） |
| `persist/watch/avatars/<hash>.png` | 对方头像裁剪（最多 7 天） |
| `persist/watch/images/<hash>.png` | 对话区图片气泡裁剪 + 同名 `.json` sidecar（只留本地，不上传、不发送） |
| `persist/watch/changes.log` | 哈希 / 滚动记录 |

## 仓库结构

```
wechat-watch/
├── LICENSE                 MIT
├── README.md               本文件
├── CONTRIBUTING.md         贡献说明
├── docs/group-handling.md  群聊处理办法（信号 / 右侧翻历史 / 不发言 / 摘要仓库）
├── wechat-watch-diff       Bash 入口：列表哈希、滚动才录像、GC
├── wechat-watch-regions    Python：差分切框 + OCR + 滚动检测 + 时间线 + 双时区 + 未读标记
├── wechat-watch-thread     只读：裁右侧对话区并 OCR，打印文本给 summaries
├── wechat-watch-gc         过期删除录像/截图，只留识别结果
└── tests/test_regions.py   单元测试
```

## 群聊 / 右侧对话区

群聊处理办法（列表只当信号、必须点进右侧翻历史、禁止在任何群发言、摘要写到私有仓库、时间同时标 UTC 与 Asia/Shanghai）见 **[docs/group-handling.md](docs/group-handling.md)**。只读助手：`./wechat-watch-thread` 裁当前右侧对话区并 OCR，供写入 [wechat-group-summaries](https://github.com/nicechencs/wechat-group-summaries)。**不要**把私聊 / 群记录提交进本仓库。

左侧会话列表每一行预览都很短，群聊里「谁说了什么」单靠列表看不清。翻历史时以右侧对话区为准：

- 未读轮询仍然只看左侧列表哈希，列表没变就是 `UNCHANGED`。`--detect-unread` 只回答「哪一行有红数字 / 红点 / [N条]」，列表预览仍不是群记录
- **翻历史、有滚动条才录屏**：只录对话区矩形，逐帧切气泡，`side=in|out`
- 群聊昵称一般在气泡上方；解析时会向上多留约 22px，OCR 进时间线的 `name`
- 对方头像 average-hash 后和昵称绑在 `identities.json`，下次同一头像即使 OCR 失败也能补上名字
- **限制**：切会话会让整块 thread 换内容，但那是列表变化，不会录像。快速连滑时变化面积超过裁区 40%，会退回整块再 OCR，而不是逐条气泡

1280×800 上实测：会话列表和对话区的竖线在 x=412，输入框/工具条从 y=742 开始。thread 裁剪为 `720x660+414+40`，下沿停在输入框之上，避免闪烁光标和 dock。窗口不在 0,0 时 `--window-geom` 会按找到的窗口算裁剪，并尽量带上 id=0x…；找不到仍用这些常量。有窗口 PNG 时扫描 gutter；其它 DPI / 缩放仍未处理，不能靠常量放大。

单独复用切框脚本时请加 `--prefix t --label thread_ --emit-side`，不要复制一份差分逻辑。

## 录像和截图定时删除

每次 `wechat-watch-diff` 结束都会跑一遍 `wechat-watch-gc`（也可以单独执行）。只动 `persist/watch` 下的图片和录像，识别结果留下：

| 对象 | 规则 |
|---|---|
| 滚动录像 `.mp4` / `.webm` 和抽帧 | 已经解析成 JSON 消息后立刻删；否则最多留最近一段 10 分钟 |
| `watch/regions/*.png`、`watch/thread-regions/*.png` | 超过 15 分钟删除；当前这次 CHANGED 写在 JSON 里的那一套始终保留 |
| `watch/full.png` | 只留最新一张 |
| `list.png` / `list.prev.png` / `thread.png` / `thread.prev.png` | **不删**（下一帧哈希和滚动检测要用） |
| `identities.json` | **不删**（名字↔头像哈希） |
| 头像 png（`avatars/`、`identities/`） | 超过 7 天删除，哈希仍留在 `identities.json` |
| 图片气泡（`images/<hash>.png`） | 超过 7 天删除 png；sidecar json 留下；15 分钟 leftover 清扫不删 |

`regions.json`、时间线 JSON 和 stdout 里的 `textN=` 会保留。不要把截图、录像、tessdata、ffmpeg 提交进 git。

## 开源协议

[MIT](LICENSE)。欢迎提 Issue 和 PR，见 [CONTRIBUTING.md](CONTRIBUTING.md)。
