# Changelog

本文件记录 `astrbot_plugin_bilibili_watch` 的版本变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [1.1.6] - 2026-11-09

本版修复画面证据链的配置接线、降级顺序与缓存失效问题；接口、配置键、数据目录保持兼容。

### 修复

- **日志记录器统一为 AstrBot 官方入口**：移除 `core/web/api.py`、`core/client/media_extractor.py`、`core/media/sampler.py`、`core/auth/qr_login.py`、`core/client/bili_client.py` 中的 `import logging` 回退，发布代码一律 `from astrbot.api import logger`；离线单测改由 `tests/stub` 提供最小 `astrbot.api` stub。
- **HUD 时间带张数配置生效**：`GatingRouter.decide()` 接收 `hud_sheet_max`，不再写死 6；设置页改成 2 或 12 后下一次观看即生效。
- **超过 30 分钟的游戏实况继续生成 HUD**：HUD 张数本来就受 `hud_sheet_max` 封顶，间隔公式会自动放稀；不再因为时长直接丢掉操作轴证据。
- **图片降级优先保留 HUD**：HUD 图排在场景图之前；Provider 限制图片数量时，降级为 HUD + 1 张场景，并同步重建提示词里的图像索引，不再把整条操作轴丢掉。
- **竖屏 HUD 与横屏自适应窗口**：`sample_stream` 传入实际宽高与 `portrait`；竖屏取底部 25% 通栏；横屏先取整条底栏，合成时按边缘密度在左/中/右三个窗口中自动选一段；追问截图 `_crop_hud` 与采样器共用 `hud_crop_fraction` / `crop_hud_frame`。
- **帧缓存加入画质与裁切版本**：`sample_meta.json` 记录 `source_width/source_height/portrait/crop_version`；登录后从 360P 升到 1080P 会重新采样，不再复用旧画质帧。
- **游戏实况 ASR 与画面采样并行**：早期判定为 gameplay 时，ASR 与单次解码同时进行，再复用采样结果，减少串行等待。
- **ASR 整段抽取 + 本地切块**：长音频先整段下载一次，再按 `asr_chunk_seconds` 本地切片；只有整段抽取失败才回退逐块连 CDN；上传超时从固定 30 秒放宽到按块时长 60–180 秒。
- **分 P 链接**：`resolve_ref` 保留 `?p=N`，`get_video_meta` 解析并只分析当前 P；标题和提示词标注「第 N/M P」。
- **长字幕覆盖全片**：纯文本模式最多送 240 段；超过时按时间均匀抽样，不再只取前 60 段（约前 25 分钟）。
- **游戏误判收窄**：从玩法关键词移除过宽的「教程」「轴」，避免 AutoCAD 教程等被当成游戏实况。
- **黑边检测不再依赖 numpy**：`estimate_content_box` 改为纯 Pillow 实现，避免缺失 numpy 时 HUD 时间带带着黑边合成。

### 已知限制 / 未做

- 横屏 HUD 用「边缘密度」在左/中/右三个窗口里选，不是 OCR 定位；极低对比度或 UI 与场景边缘混杂时仍可能选错。
- ASR 整段抽取会先下载完整音频（最多 `asr_max_seconds`），慢网下首块等待时间比逐块流式更长。
- `dm/list.so` 不是全量弹幕，热门视频的波峰仍可能偏低。

## [1.1.5] - 2026-11-08

本版是 v1.1.4 之后的非破坏性功能更新，重点有三块：
**完整游戏视频的操作轴与画面证据**、**AstrBot 可视化设置页（含扫码登录）**，
以及**品牌与视觉模型跟随修正**。内部插件 ID、配置键、数据目录和既有 Web API 路由保持兼容；
新增配置项会在加载时自动补默认值。

### 修复：完整游戏视频「操作轴」乱说、关键帧无作用

根因（均已复现）：

- 旧流程按 8 个均匀时间点**逐张起 ffmpeg 进程重新连流 seek**，374 秒的视频只覆盖 8 个瞬间；
- `get_stream_urls` 按 height 升序取第一个 ≥360 的流，**登录后反而恒选 360P**；
- 截帧统一缩到 480P，底栏费用/技能卡只剩几十像素；
- 视觉提示词要求「时间轴纲要」，但没有字幕/目录时模型只能脑补；
- B 站 AI 字幕可能与视频画面主题**完全无关**（实测某蔚蓝档案对局视频的 AI 字幕是一段 4000+ 字的健身口播，
  与标题/标签/评论零交集；是错配还是音轨本身无关都不影响结论），旧流程会直接当事实喂给模型。

修复：

- 新增 `core/media/`：**一次 ffmpeg 解码**同时产出场景缩略图与原生分辨率底栏 HUD 裁片
  （374 秒 1080P 实测约 10 秒，接近纯解码耗时；同一视频重复观看直接复用帧缓存，0 次解码）；
- 场景选点改为「等宽时间箱 + 箱内变化分」，保证全片覆盖并优先切镜/转场；
- 场景帧按 3×2 拼成 16:9 联表，每格标注时间，替代散装单帧；
- 游戏实况额外生成 **HUD 操作栏时间带**（4×8 格，约 1.95 秒/格，自动裁掉 letterbox 黑边），
  6 张覆盖约 6 分钟，模型可直接读 COST 数字、技能卡状态与阶段血条；
- 新增 `GAMEPLAY_EXTRACTOR_SYSTEM_PROMPT`：把结论强制分成「画面可复核 / 观众提供 / 推断」三档，
  读不清写「不可辨识」，采样间隔之间不得补全，只能读到费用时只输出费用时间轴；
- 新增**评论区文字轴挖掘**：识别「轴/选牌/费用/时间点」类评论，单独作为观众证据，注明作者与点赞。

### 修复：字幕、画质与长音频 ASR

- `get_subtitle_track`：`wbi/v2` 返回空时自动回退 `player/v2`（实测存在只有后者能看到 AI 字幕的视频）；
- **字幕可信度校验**：AI 字幕与标题/标签/评论的专有名词零交集且体量足够时判为 `suspect`，
  不进入分析材料，报告注明「疑似错配」；音乐/舞蹈区不做拦截；
- `get_stream_info`：取最高可用画质（登录态 1080P），并返回宽高与音频流；
- 评论抓取支持分页（最多 40 条）与楼中楼预览；
- 长音频 ASR 按 `asr_chunk_seconds`（默认 300 秒）分块抽轨与转录，单块失败不影响其它块；
  块内时间戳按块偏移合并，总时长受 `asr_max_seconds`（默认 1800 秒）约束；
- 字幕被判为错配时，如果配置了 ASR，会用音频转录替代；无字幕的长视频不再只能靠画面猜测。

### 新增：可视化设置页（`views/settings`）

- 基于 AstrBot `views/<name>/index.html` + `window.AstrBotPluginView` bridge；
  后端用 `context.register_web_api()` 注册 12 个接口，handler 走 `astrbot.api.web`（旧运行时回退 Quart）。
- 六个配置分组、22 个字段：连接与登录、模型与实况、ASR、画面证据、交互与频控、缓存与原文。
  字段规格在 `core/web/spec.py` 中与 `_conf_schema.json` 同源校验，页面不会和真实配置漂移。
- 支持白名单校验、类型/范围/枚举检查、密钥「留空保持 / 显式清除」、分组恢复默认、脏状态提示、Ctrl+S 保存。
- 保存直接写 AstrBot 的 `AstrBotConfig`（`save_config_async`），随后热重载 `AppConfig`、
  `MediaExtractor` 与原文缓存 TTL，无需重启；非 AstrBot 环境下回退写插件目录 `config.json`。
- 新增「概览」「缓存」「诊断」三个页面：运行状态卡片、最近观看、缓存占用条与分类清理、
  Python/平台/FFmpeg/已注册接口等诊断信息。
- 亮/暗主题跟随 AstrBot；视图标题与描述写入 `.astrbot-plugin/i18n/{zh-CN,en-US}.json`。

### 新增：设置页内扫码登录

- 页面内生成二维码（本地 `qrcode` 渲染，链接不出本机）、2.5 秒轮询扫码状态、倒计时、
  刷新二维码、登录后展示昵称/UID/等级/大会员、退出登录。
- 二维码文件名改为按链接哈希命名，避免并发扫码互相覆盖；读取后立即删除 PNG。
- 新增 `requirements.txt`（`qrcode>=7.4`），AstrBot 安装插件时自动安装；本地缺库时页面与
  `/bili登录` 都降级为只显示登录链接，不会调用第三方二维码服务。
- 登录成功后凭据写入原有 `CredentialManager`，优先于设置页手填的 SESSDATA；退出后自动回退。

### 新增：配置项

- `gameplay_mode`：auto / on / off，控制游戏实况判定；关闭后不再生成 HUD 时间带。
- `mimo_base_url`、`siliconflow_base_url`、`siliconflow_model` 进入配置面板
  （此前只能写 config.json / 环境变量）。
- `asr_max_seconds`、`asr_chunk_seconds`：ASR 总时长与分块时长。
- `frame_budget_cap`、`hud_sheet_max`、`keep_frame_dirs`：帧预算、HUD 张数与截图缓存保留目录数。
- `transcript_ttl_hours`：原文缓存有效期；设置页保存后 `gameplay_mode` 立即对下一次分析生效。

### 变更：品牌、视觉模型与头像

- **项目显示名改为「逐渐完善的预知眼」**：插件卡片、WebUI 视图标题、页面品牌、日志前缀、
  README/CONTRIBUTING 全部更新；内部插件 ID 仍保留 `astrbot_plugin_bilibili_watch`，
  避免配置路径、数据目录、Web API 路由与升级链路迁移。
- **视觉子代理默认跟随 AstrBot 全局默认对话模型**：此前留空 `vision_provider_id` 时取的是
  「当前会话 Provider」；现在改为 `context.get_using_provider_async()` 不带 umo，取全局默认模型
  （全局不可用时才回退会话模型），并在日志中打印实际使用的 Provider。
  设置页/配置 schema 的提示同步改为「跟随全局默认」；概览页显示实际生效的全局模型名。
- **登录账号卡显示 B 站头像**：后端从 `nav` 的 `face` 下载头像并转 data URL 返回，
  前端显示真实头像；下载失败时回退首字母，避免防盗链/CSP 导致的空白。
- 设置页视图标题改为插件名「逐渐完善的预知眼」，不再只显示「设置」。

### 新增：LLM 工具与缓存

- `bilibili_transcript` LLM 工具：按关键词/时间范围检索字幕或 ASR 原文，不重新看视频；
- `bilibili_frame` LLM 工具：按时间点返回整帧 + 底栏 UI 裁片（1080P，路径限定在插件数据目录）；
- `core/perception/transcript_store.py`：原文磁盘缓存（默认 72 小时，最多 200 条，原子写入）；
- `core/utils/timecode.py`：秒数 / `mm:ss` / `hh:mm:ss` 解析；
- `SessionStore.list_recent()`：设置页「最近观看」数据源，只返回标题/BV/类型/时间，不返回正文；
- 缓存清理会避开 `credentials.json`，并在有任务进行中时拒绝清理截图/临时音频缓存。

### 移植自参考项目（v0.7.0–v0.12.2）

| 参考版本 | 能力 | 处理 |
| --- | --- | --- |
| 0.7.1 | ASR 分块转录 | 已移植，时间戳按块偏移合并 |
| 0.8.0 | 多张 16:9 联系图、连续叙事 | 已移植（3×2 联表 + 格内时间） |
| 0.9.0 | 核心/其他客观内容拆分 | 未移植（本项目的三档证据结构已覆盖） |
| 0.10.0 | 极致模式动态帧预算、原文直读 | 部分移植：动态帧预算 + 原文缓存/工具 |
| 0.10.1 | 配置页分组 | 未移植（本项目配置项少，保持扁平） |
| 0.11.0 | 像素预算、候选帧、超预算压缩 | 未移植（等宽联表不需要 DP 装箱；超预算压缩收益低） |
| 0.12.0 | WebUI 管理页 | 已改为按 AstrBot 官方 Plugin Views 文档重做，非移植 |
| 0.12.1 | 双语字幕、补帧修复 | 未移植（补帧问题由时间箱选点规避） |
| 0.12.2 | 内嵌字幕 OCR | 未移植：实测 RapidOCR 对日文游戏 UI 仅能读出英文，收益不足 |

情感/人格化回复相关的 0.5.2–0.5.13 更新按要求**不移植**，本项目保持纯工具属性。

### 测试与文档

- 新增 `tests/test_perception.py`、`tests/test_transcript.py`、`tests/test_web.py`、
  `tests/test_web_api.py`，均接入 CI；CI 增加 Pillow 依赖以支持画面证据测试。
- README 更新设置页、扫码登录、模型跟随与配置表；新增 `docs/settings-*.jpg` 页面预览；
- `SECURITY.md`、`CONTRIBUTING.md`、`config.example.json` 同步到新配置与新品牌。

### 已知限制

- 采样帧之间（约 1.9 秒）发生的操作无法被看到；完整循环轴仍可能需要人工复核；
- B 站字幕接口对同一条视频可能返回**互不相同且与画面无关**的文本（实测连续三次拿到健身口播、中文歌词、他人录像开场白），
  插件会按可信度拦截，但拦截后如果没配 ASR，就只能依赖评论与画面；
- HUD 时间带默认只裁画面右侧 48.5%、下方 18.5%，对底部居中 HUD 的游戏覆盖不足；
- 超过 30 分钟的视频不生成 HUD 时间带（解码成本），回退为场景联表；
- `views/` 需要较新的 AstrBot（Plugin Views 文档对应 2026 年版本）；很旧的 4.x 只能看到插件配置面板；
- 设置页的保存权限等同于 Dashboard 登录权限；多人共用 Dashboard 时请自行限制账号；
- 设置页未做正文多语言，仅视图标题/描述提供 zh-CN / en-US。

## [1.1.4] - 2026-10-26

### 元数据

- 按插件市场模板完善 `metadata.yaml`：新增 `category: entertainment`、`social_link`，并把 `tags` 扩充为 B站 / bilibili / 视频总结 / 视频理解 / 弹幕 / 多模态 / ASR / LLM，便于市场分类与搜索。

## [1.1.3] - 2026-10-26

### 改进

- **上下文注入方式**：视频报告/追问上下文改为通过 `req.extra_user_content_parts` + `mark_as_temp()` 追加注入，保留用户原始发言、不写入会话历史；旧环境不支持时自动回退为改写 `req.prompt`。
- **登录二维码本地渲染**：移除对 `api.qrserver.com` 的依赖，改用 `qrcode` 库在本地生成 PNG（`data/plugin_data/astrbot_plugin_bilibili_watch/qrcode/`），含 `qrcode_key` 的登录链接不再发往第三方；未安装 `qrcode` 时退化为只发文本链接。
- **流地址白名单**：ffmpeg 抽音轨/截帧前校验输入为 http(s)，保持输入源白名单意识。

## [1.1.2] - 2026-10-23

### 修复

- **日志记录规范**：`core/client/`、`core/auth/`、`core/asr/` 下 7 个模块统一改用 `from astrbot.api import logger`，移除内置 `logging`，满足插件商店审查要求。
- **数据持久化规范**：扫码凭据、ASR 临时音频与关键帧截图由插件目录 `data/` 迁移至 AstrBot 标准插件数据目录 `data/plugin_data/astrbot_plugin_bilibili_watch/`，便于用户备份、迁移与审计；文档中的路径说明同步更新。

## [1.1.1] - 2026-10-23

### 文档

- 补充原版插件署名：本项目缘起于 [AcceleratorYue/astrbot_plugin_bilibili_eyes](https://github.com/AcceleratorYue/astrbot_plugin_bilibili_eyes)，已在 README 标题下方与致谢名单注明。

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

[Unreleased]: https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution/compare/v1.1.6...HEAD
[1.1.6]: https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution/releases/tag/v1.1.6
[1.1.5]: https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution/releases/tag/v1.1.5
[1.1.4]: https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution/releases/tag/v1.1.4
[1.1.3]: https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution/releases/tag/v1.1.3
[1.1.2]: https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution/releases/tag/v1.1.2
[1.1.1]: https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution/releases/tag/v1.1.1
[1.1.0]: https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution/releases/tag/v1.1.0
[1.0.0]: https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution/releases/tag/v1.0.0
