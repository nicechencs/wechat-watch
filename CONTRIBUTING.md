# 参与贡献

感谢你愿意改进 wechat-watch。提 PR 前请先看完下面几条。

## 开发环境

- Linux，Python 3.11+
- `ffmpeg`（需要 `x11grab`，用于截屏）
- `tesseract-ocr`，以及语言包 `chi_sim`、`eng`
- 可选：静态 ffmpeg 放到 `WECHAT_PERSIST/bin/ffmpeg`

## 跑测试

```bash
python3 tests/test_regions.py
```

应看到 14 个用例全部通过。新增区域合并、OCR 分类逻辑时，请同时补测试。

## 提交约定

- 不要提交运行时文件：截图、哈希、日志、tessdata、静态 ffmpeg
- 提交说明写清楚「为什么」
- 文档请用中文维护（`README.md`、本文件）

## 行为边界

本仓库只做「截屏 → 比图 → 切变化块 → OCR」。不要在这里加自动登录、自动发消息或采集聊天记录上传的逻辑。
