# wechat-watch

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

低成本的微信桌面「聊天列表」变化检测器。用哈希和像素差分代替每次都把整张截图交给视觉模型，有字的变化块再用 OCR 变成纯文本。

仓库：[https://github.com/nicechencs/wechat-watch](https://github.com/nicechencs/wechat-watch)

本工具**只截屏、对比、识别文字**，不会打开微信、不会登录、不会发消息。

## 它解决什么问题

轮询微信未读时，如果每 10 秒就把整屏丢给多模态模型，token 会非常贵。本项目把成本拆成三层：

1. **哈希**：聊天列表没变 → 输出 `UNCHANGED`，到此结束，零视觉、零 OCR
2. **区域差分**：有变化 → 只切出真正变了的小矩形
3. **OCR**：小块里是字 → 输出 `textN=` 纯文本，调用方不必再读图

一次未变化的检查大约只需一次 ffmpeg 截屏 + sha256，耗时大约 200–400ms。

## 工作原理

```
桌面 1280x800
    │  ffmpeg x11grab（关闭鼠标）
    ▼
full.png
    │  裁出左侧会话列表 440x700+70+30
    ▼
list.png  ──sha256──►  与上次相同？──是──► UNCHANGED（退出）
    │
    │ 否
    ▼
CHANGED
    │  与 list.prev.png 做像素差分
    ▼
若干变化矩形 r0.png …
    │  tesseract chi_sim+eng
    ▼
kind=text  →  textN=识别结果（调用方不要读图）
kind=image →  只给出 region= 路径（照片 / 空白 / 图标）
```

会话列表的时间戳一般是「最后一条消息的时间」，不会每秒跳变，所以哈希在没新消息时是稳定的。输入框闪烁的光标在裁剪区域之外，不会误触发。

## 依赖

| 软件 | 用途 | 安装 |
|---|---|---|
| bash | `wechat-watch-diff` 入口 | 系统自带 |
| Python 3.11+ | `wechat-watch-regions`（仅标准库） | 系统自带 |
| ffmpeg（需 x11grab） | 截屏、裁剪、解码 | 静态包或 `apt install ffmpeg` |
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
sudo cp wechat-watch-diff wechat-watch-regions "$PERSIST/"
sudo cp wechat-watch-diff /home/box/.local/bin/wechat-watch-diff
sudo chmod 0755 /home/box/.local/bin/wechat-watch-diff
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

此时不要读任何图片，也不要跑视觉模型。

### 有变化

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

调用约定：

- `kindN=text`：只用 `textN=`，**不要**再 `Read` 对应 PNG
- `kindN=image`：需要看图时，只读 `region=` 那一小块
- `textN=` 里的换行已转义成 `\n`，保证每行一条
- 第一次运行没有上一帧，会输出整块列表，并做一次 OCR

单独跑区域 + OCR：

```bash
./wechat-watch-regions --prev prev.png --curr curr.png \
  --out-dir ./out --json ./out/regions.json \
  --ffmpeg /usr/bin/ffmpeg
```

## 区域差分规则

1. 覆盖 `list.png` 前，先复制为 `list.prev.png`
2. ffmpeg 把两张 PNG 解成 raw rgb24
3. 任一 RGB 通道差值 `> 12` 视为变化
4. 去掉孤立单像素噪声，其余像素按 8 连通域成框
5. 间距 `≤ 16px` 的框合并，再向外垫 `8px`
6. 小于 `20×20` 的框丢掉；左侧 `80px` 内、不小于 `12×12` 的当作红点徽章保留
7. 最多 6 个框；变化面积超过裁剪区 `40%`（滚动、整页刷新）则退回整块列表

## OCR 规则

- 语言：`chi_sim+eng`
- 排版：普通块 `--psm 6`，短条 / 徽章状 `--psm 7`（按框尺寸判断，不以 x=0 当徽章）
- `kind=text`：去掉空白后，至少 1 个汉字，或至少 2 个字母数字
- `kind=image`：空、纯空白、或只有标点（照片、空白、图标）
- `UNCHANGED` 不会跑 OCR

语言包优先读 `$WECHAT_PERSIST/tessdata`（`TESSDATA_PREFIX`）。reset 后可用 `ensure-wechat` 按 `apt-deps.txt` 把 tesseract 装回来。

## 测试

```bash
python3 tests/test_regions.py
```

当前覆盖：假色块切框、徽章保留、大面积变化回退、中英 OCR、CLI 的 `textN=` / `kindN=` 输出。

## 运行时文件（不进 git）

| 路径 | 作用 |
|---|---|
| `persist/bin/ffmpeg` | 静态 ffmpeg（约 77MB，带 x11grab） |
| `persist/tessdata/*.traineddata` | 中英 OCR 模型 |
| `persist/watch/full.png` | 最近一次整屏 |
| `persist/watch/list.png` | 当前会话列表裁剪 |
| `persist/watch/list.prev.png` | 上一帧裁剪 |
| `persist/watch/list.sha256` | 上一帧哈希 |
| `persist/watch/regions.json` | 变化框 + OCR |
| `persist/watch/regions/rN.png` | 变化小块 |
| `persist/watch/changes.log` | 哈希变更记录 |

## 仓库结构

```
wechat-watch/
├── LICENSE                 MIT
├── README.md               本文件
├── CONTRIBUTING.md         贡献说明
├── wechat-watch-diff       Bash 入口：截屏、哈希、调用区域/OCR
├── wechat-watch-regions    Python：差分切框 + Tesseract
└── tests/test_regions.py   单元测试
```

## 开源协议

[MIT](LICENSE)。欢迎提 Issue 和 PR，见 [CONTRIBUTING.md](CONTRIBUTING.md)。
