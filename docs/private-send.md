# 私聊发送办法

本文是 Linux 微信 4.x 上 1:1 `--send` 的操作经验。群聊仍然走 [group-handling.md](group-handling.md)，**任何群都禁止发送**。

读消息用 wx-linux-read（`wx-cli --json`），操作已打开的微信窗口才用本仓库。不要扫内存、不要提密钥、不要解密。

## 铁律

1. **显示名不是身份。** 通讯录里可以有两个「陈」。活的私聊是 `wxid_9qpru7ht5s4h12`；另一个「陈」是名片（预览常为 `[Name Card] 成🐰`，`wxid_amsgrzqh10rc21`）。发给「成🐰」必须进成的会话（`wxid_iirwg5zdlh5t21`），**禁止**停在名片那个陈。CLI 必须带 `--username`，禁止只靠 `--peer` 显示名。
2. **JSON 下标不是点击目标。** 不要用 `sessions[i]` 乘估算行高当纵坐标。列表顺序、置顶、未读会变。要点屏幕上那一行：标题加预览/`last` 能唯一对上才点。对不上就拒绝，不要猜。
3. **`ok:true` 不等于发出去了。** 进程没崩、JSON 写了成功，都不够。必须看到：标题栏是目标人、最新一条绿色气泡正文就是 `--text`。对不上当失败。
4. **不要在本进程接无障碍总线。** 总线 `org.a11y.Bus` 不在时，进程内 `Atspi.get_desktop(0)` 会 SIGTRAP（退出码 133）。窗口操作只走普通 X11。无障碍如需探测，只放可杀死的子进程。
5. **先把微信窗口提到前台，再往里送键。** 终端、Grok、浏览器盖在上面时，粘贴和回车会进别的窗口（常见是终端里一个回车符），微信输入框仍空。提到前台之后要等活动窗口真是微信。
6. **会话来源是 wx-cli，不是过期 `list.png`。** 省略 `--sessions-json` 时必须 `wx-cli chat sessions --data-dir $WX_LINUX_DATA`（默认 `/home/box/wx-linux-read/data`）。不传 `--data-dir` 会读 `cwd/data`，报 `need_unlock` / account not found。把旧的 `persist/watch/list.png` 当身份，会 `peer-not-found` 或点错人。
7. **目标会话已经打开就不要再点列表。** `--already-open` 只在「当前右侧就是这个 username」时跳过列表。标题只写「陈」不够（两个陈）。跳过列表是为了避免点到旁边的名片陈。
8. **中文不要盲用系统剪贴板。** 当前实现先把正文交给剪贴板，短等，再粘贴。别的程序（Grok Build、浏览器）可能仍占着剪贴板，贴出去的可能是一段路径而不是 `--text`。粘贴前必须读回剪贴板，和 `--text` 逐字相同才继续；对不上就拒绝，或改成直接打字。
9. **敏感话题不自动回。** 家人、关系、拿不准的，把原文和一条建议发给用户，等用户给原句再发。

## 实况踩过的坑（2026-08-20）

| 现象 | 实际原因 | 以后怎么做 |
|---|---|---|
| 收到「你好」很久不回 | 10s 循环看到了，`send` 先 `peer-not-found`（没带 sessions-json），再 SIGTRAP 133 | 循环里写 `/tmp/wx-sessions.json`，命令必须带 `--sessions-json` 和 `--username`。崩了不要死循环重试，改在已打开窗口里点打 |
| `ok:true` 但微信没字，终端里多了回车 | 没把微信提到前台，键进了盖着的终端 | 提到前台并确认活动窗口；发完截屏核对气泡 |
| `ok:true` 却点进名片「陈」 | 用 JSON 下标估算行高；两个陈视觉顺序不等于数组顺序 | 按标题加预览点可见行；点完核对标题栏和预览 |
| 发给成🐰 仍 `ok:true`，其实停在名片陈 | 假成功：点错行也报 ok | 点错或标题对不上必须 `ok:false`（`ambiguous-peer` / `peer-not-found`） |
| dry-run 带上 `wxid_9qpru7ht5s4h12` 仍 `ambiguous-peer` | 两个陈重名后，唯一 `last` 对上了，但 sessions JSON 没有点击坐标，被当成看不清 | username 唯一命中就不是 ambiguous。缺坐标：dry-run 仍应计划成功；实况再去找可见行，找不到就 `peer-not-found` |
| 实况发给成🐰 报 `peer-not-found` | 实况去识别过期 `list.png`，认不出带兔的「成🐰」 | 不要用过期列表图当可见行。会话已打开就 `--already-open`。识别失败应拒绝，不要乱点 |
| 成的窗口已打开，发出去的是 `new-api/service-newapi-md2gs5` | 剪贴板被别的程序占用，粘贴了旧内容，仍报 `ok:true` | 粘贴前校验剪贴板；失败则在已打开输入框直接打字。`ok:true` 必须以绿色气泡正文为准 |
| 错消息要去掉 | Linux 微信 4.x 右键自己的绿气泡，选撤回（大约两分钟内） | 只撤回错的那条。对的那句留下。成功后线程里是 “You recalled a message.” |

## 正确调用

循环里先把本次 `wx-cli` sessions 写到 `/tmp/wx-sessions.json`，然后：

```
python3 /home/box/wechat-watch/wechat-watch.py send \
  --peer '成🐰' \
  --username 'wxid_iirwg5zdlh5t21' \
  --text '短回复' \
  --sessions-json /tmp/wx-sessions.json
```

右侧已经是这个人时加上 `--already-open`，不要再点列表。`--data-dir` 缺省对准 `/home/box/wx-linux-read/data`（或 `$WX_LINUX_DATA`）。不要对群、文件传输助手、微信团队发。

## 发出去之后

1. 看标题栏是不是这个人（两个陈看 wxid / 预览，不看「陈」两个字）。
2. 最新绿色气泡等于 `--text`。列表预览也应变成这句话。
3. 对了再改 `persist/watch/sessions.prev.json`，避免 10 秒循环把己方发出去的当未回复。
4. 错了：两分钟内右键撤回；不要再发一条解释除非用户要求。然后用打字（不经过剪贴板）重发。
5. 只有 `last_is_self=true` 的变化：循环保持沉默。

## 还没修完

- 唯一 username 加唯一 last，但行上没有点击几何时，dry-run 仍可能 `ambiguous-peer`（2026-08-20，commit `7f72dce` 之后仍能复现）。
- 实况可见行若来自过期 `list.png` 识别，成🐰 会 `peer-not-found`。应在发之前截当前列表，或会话已打开则跳过列表。
- 剪贴板没有读回校验。粘贴前必须确认内容和 `--text` 相同。
- 不要用 JSON 数组下标当纵坐标；测试里「两个陈加顶上未读成🐰」必须点成那一行。

改这些走本机 Grok Build（`~/.grok/bin/grok`），不要用 Cursor Cloud Agent 代替。测绿再提交：`python3 tests/test_regions.py`。
