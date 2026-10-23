# Changelog

本文件记录 `astrbot_plugin_bilibili_watch` 的版本变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [1.1.0] - 2026-10-23

### 修复

- **配置 schema 兼容 AstrBot 4.x**：面板只支持 `int` / `bool` 等类型，`number`、`boolean` 会导致插件在构造配置阶段直接加载失败；同步把长说明改为 `hint`，`asr_provider` 改为 `options` 下拉，API Key 使用 `secret` 遮蔽。
- **视觉子代理调用兼容**：`Context.llm_generate` 在 4.x 中必须显式传 `chat_provider_id`。现在按「配置了 `vision_provider_id` → 当前会话主模型 → `provider.text_chat` 兜底」的顺序解析，避免分析整体失败。
- **b23 短链解析不再阻塞事件循环**：短链跳转与卡片解析移入插件线程池执行。
- **避免重复处理**：报告注入成功后，从本次请求的工具集中摘除 `bilibili_watch`，防止主模型再抓一遍同一视频。

### 变更

- 报告结构恢复 emoji 标题（📌 核心概览 / ⏱️ 时间轴纲要 等），在浏览器渲染的 t2i 方案下可正常显示。
- 文档整体重写：新增安装方式、配置表、测试说明、隐私与安全、贡献指南、CI。

## [1.0.0]

### 新增

- 首次公开版本：双模门控、文本/弹幕/字幕/评论清洗、dHash 关键帧去重、MiMo / SiliconFlow ASR、30 分钟会话记忆、扫码登录解锁 1080P、每日配额与会话冷却、`bilibili_watch` LLM 工具。

[Unreleased]: https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution/releases/tag/v1.1.0
[1.0.0]: https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution/releases/tag/v1.0.0
