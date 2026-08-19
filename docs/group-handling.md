# 群聊处理办法

本仓库是**公开**的截屏 / 差分 / OCR 工具，不保存任何群聊正文。
私有摘要写到 [wechat-group-summaries](https://github.com/nicechencs/wechat-group-summaries)，**不要**把聊天记录提交进 wechat-watch。

本文是微信群（含「独立产品创业联盟3群」）的唯一处理办法。不要另起一套。

## 铁律

1. **列表预览只是信号，不是记录。**
   左侧会话列表每一行只有很短的预览。`wechat-watch-diff` 对列表做哈希 / OCR，只能回答「这个群可能有新消息」。**禁止**把 `list.png`、`textN=`、列表 `regions.json` 当成群记录、纪要或转写。
2. **必须点进群，在右侧对话区翻最近历史。**
   点开目标群后，只在**右侧对话区**往上滚，看最近一段气泡。以点进去之后、滚过的右侧内容为准。
3. **任何群都禁止发送或回复。**
   包括「独立产品创业联盟3群」。不要点输入框，不要打字，不要回车，不要点发送，不要往输入框塞内容。本仓库的脚本也**不会**替你点、打、发。
4. **转写内容：昵称 + 时间 + 文本 / 图片 / 系统消息。**
   对方气泡、自己气泡、图片说明、撤回 / 进群 / 拍一拍等系统行都记。图片本身不进本仓库；摘要里写「[图片]」或一句可见说明即可。
5. **Markdown 只写到私有仓库 wechat-group-summaries。**
   本仓库只放工具和办法。截图、录像、哈希、`identities.json` 都在 `$WECHAT_PERSIST/watch`，不进 git。
6. **时间：云桌面微信是 UTC；摘要同时标 Asia/Shanghai。**
   气泡上的钟、`wechat-watch-diff` 的 `at=` 都是 UTC。写摘要时同一时刻写两行，例如 `2026-08-19T07:02:00+00:00`（UTC）和 `2026-08-19T15:02:00+08:00`（Asia/Shanghai）。`wechat-watch-regions --format-time` 和 `wechat-watch-thread` 会打出 `at_utc=` / `at_shanghai=`。

## 现有工具怎么配合

桌面按 1280×800、微信 4.1 来裁（竖线约在 x=412，输入框约从 y=742 起）：

| 区域 | 几何（宽x高+左+上） | 谁用 | 作用 |
|---|---|---|---|
| 左侧会话列表 | `440x700+70+30`（ffmpeg `crop=440:700:70:30`） | `wechat-watch-diff` 哈希；`--detect-unread` | **廉价未读信号**。没变 → `UNCHANGED`，零 OCR。变了 → 列表小块 OCR；若要知道哪一行有红数字 / 红点 / `[N条]`，再跑 `--detect-unread`。**到此为止，不当群记录** |
| 右侧对话区 | `720x660+414+40`（ffmpeg `crop=720:660:414:40`） | 滚动检测、短录像、`wechat-watch-thread` | **唯一的群内容来源**。下沿停在输入框之上，避免光标和 dock |

流程可以记成：

```
列表哈希 440x700+70+30
    │
    ├─ 没变 ──► UNCHANGED（不要读列表图）
    │              └─ 若右侧正在滚（滚动条 / 滚像素）
    │                   才录 720x660+414+40 → 逐帧 OCR → 时间线 JSON
    │
    └─ 变了 ──► CHANGED =「可能有新消息」
                 必须点进该群，右侧翻历史
                 用 wechat-watch-thread 裁对话区并 OCR
                 或等滚动录像解析
                 转写进 wechat-group-summaries
                 解析完 wechat-watch-gc 删录像 / 过期截图
```

### 各脚本

- **`wechat-watch-diff`**：只哈希左侧列表。列表变化**不会**开始录像。录像只在右侧对话区判定为滚动时启动。
- **`wechat-watch-regions`**：区域差分 + `chi_sim+eng` OCR；`--detect-scroll`；`--parse-frames` 写出 `{t,side,name,avatar_hash,text}`；`--ocr-still` 识别一张已裁好的对话区；`--format-time` 打出 UTC / Asia/Shanghai；`--detect-unread LIST.png` 扫左侧列表的鲜红圆标 / 红点 / `[N条]` / `@` / 单独的 `z`（只读，不点击）。`--detect-nav NAV.png` 扫左侧导航图标红数字 / 红点（只读）。群聊气泡上方昵称用 `--extra-top-pad 22`，左右气泡用 `--emit-side`（`in` / `out`）。列表哈希仍是廉价信号；`--detect-unread` 只告诉你哪一行有未读标记，列表预览仍然不是群记录。
- **`wechat-watch-thread`**：只读助手。对当前 `DISPLAY`（默认 `:8`）截屏，裁右侧 `720x660+414+40`，调用 `--ocr-still`，把 `thread_textN=` 打到 stdout，供 agent 写进 summaries。可用 `--png` 识别已有图（整屏会先裁对话区）。**不点击、不打字、不发送。**
- **身份表**：对方头像 average-hash 写入 `persist/watch/identities.json`（名字↔哈希）。下次同一头像即使 OCR 失败也能补昵称。
- **`wechat-watch-gc`**：解析完成后删录像和抽帧；`regions` / `thread-regions` 小块 15 分钟过期；头像 7 天；`list.png` / `thread.png` / `identities.json` 保留。每次 diff / thread 跑完会带一次 GC。

单独复用对话区切框时请加 `--prefix t --label thread_ --emit-side --extra-top-pad 22`，不要复制一份差分逻辑。

## 推荐操作顺序

1. 跑 `./wechat-watch-diff`。`UNCHANGED` 且无 `scroll=1` → 停，不要读图。
2. `CHANGED` → 只把列表 OCR 当作「哪个群可能有新消息」，**不要**据此写摘要。需要定位行时再跑 `--detect-unread`（红数字 / 红点 / `[N条]`）；列表预览仍然不是群记录。
3. 在桌面上**点进**该群（人手或桌面操作；本仓库不提供「点群 / 发消息」自动化）。
4. 只在右侧对话区往上翻最近历史。不要点输入框，不要回复，包括「独立产品创业联盟3群」。
5. 跑 `./wechat-watch-thread`（或等 diff 在滚动时写出 `timeline=`）。
6. 按「昵称 / 时间 / 文本或[图片]或系统行」写成 Markdown，提交到 **wechat-group-summaries**。
7. 时间写 UTC，并并列 Asia/Shanghai。

## 禁止

- 把列表 `textN=` 当群记录
- 在任何群里打字、回车、点发送（含「独立产品创业联盟3群」）
- 把聊天正文、截图、录像、token、凭据提交进本仓库
- 另写一套「只看列表就出纪要」的流程

## 相关入口

```bash
export DISPLAY="${DISPLAY:-:8}"

# 未读信号（列表哈希）；翻历史且右侧在滚才录像
./wechat-watch-diff

# 当前右侧对话区 → OCR 文本（给 summaries，不发送）
./wechat-watch-thread
./wechat-watch-thread --png /path/to/full-or-thread.png

# 双时区标签（桌面 UTC，摘要同时标上海）
./wechat-watch-regions --format-time --when 2026-08-19T07:02:00+00:00

# 已有对话区 PNG
./wechat-watch-regions --ocr-still thread.png --json /tmp/thread.json

# 哪一行有红数字 / 红点 / [N条]（只读；列表预览仍不是群记录）
./wechat-watch-regions --detect-unread "$WECHAT_PERSIST/watch/list.png"

# 左侧导航图标红点 / 数字（只读）
./wechat-watch-regions --detect-nav "$WECHAT_PERSIST/watch/full.png"
```
