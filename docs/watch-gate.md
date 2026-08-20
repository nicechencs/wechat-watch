# 本地 watch 门闩：少叫醒 LLM

每 10 秒叫醒一次 Grok Bot（整段系统提示）才是 token 账单。本机 `sessions-changed` 大约 0.2s 就能判断有没有 incoming。`wx-watch-gate` 在 **shell 里轮询**，只有真正有私聊/群 incoming 时才 POST 一次 Grok Bot webhook。

## 它怎么砍 token

| 以前 | 现在 |
|---|---|
| `@every 10s` 叫醒 LLM，再跑 cheap check | 本机 daemon 每 10s 跑 `sessions-changed` |
| exit 0 仍付一轮系统提示 | exit 0 只写一行 persist 日志，**不**叫醒模型 |
| 有变化才干活 | exit 1 才 POST webhook，由 Grok Bot 的 webhook routine 接手 |

旧的 10s routine **上线本 daemon 并确认日志里有 QUIET / WOULD_PING / PING 之后再暂停**。本仓库不替你关平台 routine。

## 命令与文件

| 路径 | 作用 |
|---|---|
| `wx-watch-gate` | 门闩本体：`--once` 一拍，`--loop` 常驻 |
| `sessions-changed` | 已有 refresh+sessions 对比（exit 0/1/2/5）。**不要重写** |
| `ensure-wx-watch-gate` | 拷到 persist、写 `~/.local/bin` 包装、启动/重启 |
| `webhook.env.example` | 空占位。真密钥只放 persist，chmod 600 |
| persist `watch/wx-watch-gate.log` | 每拍一行短日志 |
| persist `watch/wx-watch-gate.pid` | PID |
| persist `watch/wx-watch-gate.login-needed` | exit 5 一次文件，出现后停止 POST |
| persist `watch/webhook.env` | URL + sender key，**不进 git** |

persist 根目录：`/home/box/.local/share/wechat-persist/watch`。

## 环境变量

| 变量 | 默认 | 含义 |
|---|---|---|
| `WX_GATE_INTERVAL` | `10` | 轮询间隔（秒） |
| `WX_GATE_DEBOUNCE` | `20` | 成功 POST 后至少这么久不再 POST |
| `WX_WATCH_WEBHOOK_URL` | 空 | Grok Bot / 同类 webhook URL |
| `WX_WATCH_WEBHOOK_KEY` | 空 | sender key。空则 dry-run，打 `WOULD_PING`，daemon 不退出 |
| `WX_WATCH_WEBHOOK_AUTH_HEADER` | `Authorization` | 放 key 的请求头 |
| `WX_SESSIONS_CHANGED` | 仓库内 `sessions-changed` | 门闩脚本路径 |
| `WX_WATCH_PERSIST` | persist/watch | 日志、state、PID |
| `WX_WATCH_CURL` | `curl` | 测试可换成假 curl |

`webhook.env` 里写同样的键。已在环境里的值不会被覆盖。

## Webhook POST 格式（公开资料）

xAI Grok Bot 的 [Skills and routines](https://docs.x.ai/grok-bot/skills-routines-and-automations) 写的是「按日程或事件触发」，**没有公布 inbound webhook 的 header/body 合同**。[Grok Bot overview](https://docs.x.ai/grok-bot/overview) 同样没有 POST 样例。

当前能对上的公开合同是 Cursor Cloud Agent Automations 的 webhook（Grok Bot 与 Cursor 账号事件同源；论坛也提到 Bot 侧已有 webhook-trigger routine）：

- 打开过：https://cursor.com/docs/cloud-agent/automations
- 打开过：https://forum.cursor.com/t/webhook-trigger-endpoint-returns-401/155752
- 打开过：https://forum.cursor.com/t/grok-bot-can-i-send-it-a-message-from-outside/168199
- 打开过：https://docs.x.ai/grok-bot/skills-routines-and-automations

实现按这个发：

```http
POST $WX_WATCH_WEBHOOK_URL
Authorization: Bearer $WX_WATCH_WEBHOOK_KEY
Content-Type: application/json

{"reason":"sessions-changed","n":8}
```

- `Authorization: Bearer <key>`（key 已带 `Bearer ` 则不重复加）。
- body **只有** `reason` + `n`（sessions 条数）。不贴消息正文、不贴密钥、不贴 sessions JSON。
- 若平台 UI 给的是别的 header 名，设 `WX_WATCH_WEBHOOK_AUTH_HEADER`。

## 退出码

| `sessions-changed` | daemon |
|---|---|
| 0 | 日志一行 `QUIET …`，不 POST |
| 1 | POST；dry-run 则 `WOULD_PING` |
| 1 且上次 POST &lt; 20s | `DEBOUNCE recent` |
| 1 且指纹未变（prev 还没被 agent 写回） | `DEBOUNCE stale-exit1`，不连打 |
| 2 | 可选跑一次 `wx-cli unlock derive` 再重试；同一进程不循环炸 |
| 5 | 写 `wx-watch-gate.login-needed`，之后不再 POST（等人登录） |

## 启动 / 停止

```bash
# 安装 persist 副本并启动（没填 webhook 就是 dry-run，可以看日志）
/home/box/wechat-watch/ensure-wx-watch-gate start

# 或
/home/box/.local/bin/wx-watch-gate --once
/home/box/.local/bin/ensure-wx-watch-gate start
/home/box/.local/bin/ensure-wx-watch-gate stop
/home/box/.local/bin/ensure-wx-watch-gate status

tail -n 20 /home/box/.local/share/wechat-persist/watch/wx-watch-gate.log
```

有 `systemd --user` 时 ensure 会尝试 user unit；否则 `nohup`。可重复执行（已在跑则重启）。

镜像重置后先跑 `ensure-wechat` 恢复微信，再跑 `ensure-wx-watch-gate`。没有自动挂进 `ensure-wechat`，避免破坏微信恢复。工作日早上 restore 若方便，加一行：

```bash
/home/box/.local/bin/ensure-wx-watch-gate start
```

## 旧 10s routine 怎么停

1. 填 persist `webhook.env`（chmod 600），或先 dry-run 看 `QUIET`。
2. 确认 exit 1 时有 `PING` / `WOULD_PING`，且 20s 内不会连打。
3. 在 Grok Bot 里 **暂停**「Watch WeChat and reply」`@every 10s`。
4. 另建一条 **webhook 触发** 的 routine（同一套回复办法），不要再按 10s 叫醒。
5. 不满意：`ensure-wx-watch-gate stop`，再打开旧 routine。

## 测试

```bash
python3 tests/test_regions.py
python3 tests/test_watch_gate.py
```

门闩测试 mock `sessions-changed` 和 curl，不碰实况微信、不打真 webhook。
