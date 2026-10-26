<div align="center">

# B站之眼 · evolution

**智能清洗 × 双模门控 × ASR 补全的 B 站视频理解插件**

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16%2C%3C5-0ea5e9)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/License-MIT-22c55e)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3b82f6)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-OneBot%20v11%20%2F%20QQ-8b5cf6)](https://docs.astrbot.app)

</div>

---

> **项目缘起**：本项目是在 [AcceleratorYue / astrbot_plugin_bilibili_eyes](https://github.com/AcceleratorYue/astrbot_plugin_bilibili_eyes)（「B站之眼」：子代理看 B 站视频，主模型用自己的语气回复）的基础上重构演进而来。感谢原作者的开源工作。

## 简介

**B站之眼 evolution** 是一个面向 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的 B 站视频理解插件。它把一条 B 站链接变成一份**客观、结构化、可追溯**的内容报告：先抓取元数据/字幕/弹幕/评论，再按内容特征做**双模门控**——教程攻略类走纯文本极速总结（秒级、零视觉 Token），二次元切片类走弹幕高能波峰截帧 + 视觉模型观察；没有官方字幕时，按需接入 ASR 补齐台词。

插件以「工具属性优先」为设计原则：输出高信息密度的 Markdown 报告，不模拟真人语气，不臆测未出现的内容，拿不准的信息一律写入「不确定项」。

> **English abstract**: An AstrBot plugin for Bilibili video understanding. It cleans subtitles/danmaku/comments/description, picks a processing mode (text outline vs. visual meme), extracts keyframes with perceptual-hash deduplication, optionally transcribes audio via ASR (Xiaomi MiMo / SiliconFlow), and delivers a structured, source-grounded report. Zero heavy third-party dependencies — the Bilibili client is built on the Python standard library.

---

## 特性

- **双模自适应门控**：按字幕密度、时间戳目录、弹幕波峰自动选择「纯文本纲要」或「视觉截帧」路线，避免为长口播视频浪费视觉 Token。
- **深度清洗管线**：简介广告/引流剥离、野生时间戳目录挖掘、字幕合并去口癖、弹幕密度分桶与梗词云、置顶评论优先。
- **关键帧感知去重**：64 位 dHash + 汉明距离，相似帧自动剔除，黑屏/片头片尾自动规避。
- **ASR 按需介入**：无官方字幕时才抽音轨转录；支持小米 MiMo（中文/方言）与硅基流动 SenseVoiceSmall（中日英韩多语言）。
- **30 分钟会话记忆**：看完视频后可直接追问细节，无需二次抓取。
- **扫码登录**：管理员发 `/bili登录` 即可扫码绑定 B 站账号，解锁 1080P 截帧与更稳的字幕/评论接口。
- **零重型依赖**：B 站客户端基于标准库实现（IPv4 强制连接、WBI 动态签名、buvid 指纹、412 退避），不依赖 `bilibili-api` 等第三方包。
- **并发与频控**：独立线程池 + 信号量限流，支持每日配额、会话冷却、管理员豁免。
- **LLM Tool 接入**：注册 `bilibili_watch` 工具，主模型可在对话中主动调用。

---

## 工作原理

```
                        用户触发（斜杠指令 / @bot + 链接 / 小程序卡片 / 引用回复）
                                          │
                                          ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 1. 采集层 (core/client)                                                       │
│    • BiliClient：强制 IPv4、WBI 30s 密钥轮转、游客 buvid、412/-352 指数退避      │
│    • CardExtractor：穿透 QQ 小程序 Ark 卡片与引用回复链，提取真实 BV 号          │
│    • MediaExtractor：ffmpeg 抽音轨 / 关键帧截取，支持自定义路径与自动探测        │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 2. 清洗提纯层 (core/cleaner)                                                  │
│    • text_cleaner：简介去广、时间戳目录挖掘、字幕合并与口癖剔除、评论去噪        │
│    • danmaku_cleaner：自适应密度分桶，提取高能波峰与梗词云                       │
│    • frame_filter：dHash 感知哈希去重 + 黑屏过滤                                │
│    • asr_cleaner：ASR 幻觉/复读剔除、二次元术语纠偏、按标点断句平摊时间戳        │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 3. 门控路由 (core/gating)                                                     │
│    ├─ 模式 A：时间戳目录 + 高字速 → 纯文本纲要（3–8s，零视觉 Token）            │
│    └─ 模式 B：字幕稀疏 / 弹幕高能 → 波峰锚定截帧 + 视觉模型观察（10–25s）        │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 4. 提炼与呈现 (core/perception)                                               │
│    • prompt_builder：教程向 / 梗向两套结构化 System Prompt（防提示词注入）       │
│    • session_store：30 分钟会话记忆，支持自然语言追问                            │
│    • 子代理输出报告 → 注入主模型 → 由主模型以自身人设交付                        │
└──────────────────────────────────────────────────────────────────────────────┘
```

**数据流**：`B站 API → CleanedVideoBundle（强类型数据契约）→ GatingPlan → 结构化报告 → 主模型回复`。所有中间产物都有 dataclass 定义（见 `core/models.py`），便于单独测试与替换。

---

## 环境要求

| 项 | 要求 | 说明 |
| --- | --- | --- |
| AstrBot | `>=4.16,<5` | 在 **4.29.1** 上实测通过 |
| Python | 3.10+ | 使用 AstrBot 自带运行环境即可 |
| 消息平台 | OneBot v11（`aiocqhttp`） | 依赖 QQ 小程序卡片/引用回复的解析 |
| 网络 | 可访问 B 站 API | 中国大陆网络通常无需代理 |
| ffmpeg | 可选（视觉模式与 ASR 必需） | 可装系统包，或用配置项指定绝对路径 |
| Pillow | 随 AstrBot 提供 | 仅用于关键帧去重（延迟导入，缺失时自动跳过） |

---

## 安装

### 方式一：WebUI 插件市场（推荐）

1. 打开 AstrBot WebUI → **插件** → 插件市场；
2. 搜索 `astrbot_plugin_bilibili_watch`，或在「从 URL 安装」中填入本仓库地址；
3. 安装后到插件配置页按需填写参数（见[配置](#配置)）；
4. 重启 AstrBot 或点击插件页的「重载」。

> 从 URL / 压缩包安装时，AstrBot 会读取 `metadata.yaml` 并把插件目录重命名为其中的 `name`，因此仓库名与本插件名不同也不影响安装。

### 方式二：手动安装

```bash
cd <AstrBot>/data/plugins
# 注意：目录名必须显式指定为 metadata.yaml 里的 name
git clone https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution.git astrbot_plugin_bilibili_watch
```

> 手动复制/解压时同样要保证目录名为 `astrbot_plugin_bilibili_watch`，否则 AstrBot 会把它当成另一个插件（配置与数据目录都会变）。

### 方式三：AstrBot 运行在 Docker 中

把插件放进映射到容器的 `data/plugins` 卷：

```yaml
# docker-compose.yml（AstrBot 侧）
services:
  astrbot:
    volumes:
      - ./data:/AstrBot/data          # 插件、配置、数据库都在这里
      # 或单独挂载插件：
      # - ./plugins/astrbot_plugin_bilibili_watch:/AstrBot/data/plugins/astrbot_plugin_bilibili_watch
```

容器内需要 ffmpeg 才能使用视觉/ASR 能力，可在镜像构建时安装，或改用宿主机的 ffmpeg 路径（通过 `ffmpeg_path` 配置项）。

### 安装后自检

插件加载成功时，日志会输出：

```text
[B站之眼evolution] 插件已加载 (版本 1.1.3，智能清洗与双模门控模式)
```

同时 AstrBot 会注册一个 LLM 工具：

```text
Added llm tool: bilibili_watch
```

在群内发送 `/bili状态`，应返回登录状态、ASR 引擎与 FFmpeg 探测结果。

---

## 配置

### 配置优先级

由高到低：

1. **AstrBot WebUI 插件配置页**（写入 `data/config/astrbot_plugin_bilibili_watch_config.json`）；
2. **插件目录下的 `config.json`**（适合 Docker volume 挂载 / 便携部署）；
3. **环境变量**（适合容器注入）；
4. 代码内置默认值。

### 参数说明

| 键 | 面板标签 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `sessdata` | B站登录凭据 SESSDATA | string | `""` | 留空为游客态（360P/480P，字幕接口易报 `-101`）。推荐用 `/bili登录` 扫码，扫码凭据优先于此项。 |
| `asr_provider` | 语音识别服务商 | string | `none` | `none` / `mimo` / `siliconflow`，仅在视频无官方字幕时触发。 |
| `mimo_api_key` | MiMo API Key | string | `""` | `asr_provider=mimo` 时必填。 |
| `siliconflow_api_key` | 硅基流动 API Key | string | `""` | `asr_provider=siliconflow` 时必填。 |
| `vision_provider_id` | 视觉子代理模型 ID | string | `""` | 留空跟随当前会话主模型；主模型不支持图片时建议显式指定一个多模态 Provider。 |
| `ffmpeg_path` | FFmpeg 路径 | string | `""` | 留空按 `PATH → imageio-ffmpeg` 顺序自动探测。 |
| `user_daily_quota` | 每用户每日次数 | int | `20` | `0` 表示不限制。 |
| `quota_exempt_admins` | 管理员豁免次数限制 | bool | `true` | 群管理员与 AstrBot 管理员豁免。 |
| `cooldown_seconds` | 同会话冷却（秒） | int | `15` | 同一会话两次请求的最小间隔，`0` 表示不限制。 |
| `ack_enabled` | 收到视频先回执 | bool | `true` | 先回一句「我去看看这个视频」，缓解等待焦虑。 |
| `ack_template` | 回执文案 | string | `我去看看这个视频，稍等一下～` | 可自定义口吻。 |
| `wait_notice_enabled` | 催更时提示进度 | bool | `true` | 观看期间群友催促时回复已耗时与阶段；`/stop`、`/reset`、`/new`、`/help` 仍正常放行。 |

以下几项**只能**通过 `config.json` 或环境变量设置（不在面板暴露）：

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `mimo_base_url` | `https://api.xiaomimimo.com/v1` | 小米 MiMo 接口地址。 |
| `siliconflow_base_url` | `https://api.siliconflow.cn/v1` | 硅基流动接口地址。 |
| `siliconflow_model` | `FunAudioLLM/SenseVoiceSmall` | 硅基流动 ASR 模型名。 |

### 环境变量

| 变量 | 对应配置键 |
| --- | --- |
| `BILI_SESSDATA` | `sessdata` |
| `ASR_PROVIDER` | `asr_provider` |
| `MIMO_API_KEY` | `mimo_api_key` |
| `SILICONFLOW_API_KEY` | `siliconflow_api_key` |
| `FFMPEG_PATH` | `ffmpeg_path` |
| `VISION_PROVIDER_ID` | `vision_provider_id` |

### `config.json` 示例

把 `config.example.json` 复制为 `config.json` 后填写。**该文件已被 `.gitignore` 忽略，请勿提交真实 Key。**

```json
{
  "sessdata": "",
  "asr_provider": "mimo",
  "mimo_api_key": "sk-your-mimo-key-here",
  "siliconflow_api_key": "",
  "vision_provider_id": "",
  "ffmpeg_path": "",
  "user_daily_quota": 20,
  "quota_exempt_admins": true,
  "cooldown_seconds": 15,
  "ack_enabled": true,
  "ack_template": "我去看看这个视频，稍等一下～",
  "wait_notice_enabled": true
}
```

---

## 使用

### 指令

| 指令 | 别名 | 权限 | 示例 | 说明 |
| --- | --- | --- | --- | --- |
| `/看视频` | `bwatch` | 全员 | `/看视频 BV1xx411c7m9` | 触发完整清洗与双模门控分析。 |
| `/看主页` | `bup` | 全员 | `/看主页 2` | 查询 UP 主等级、签名、粉丝数与近期投稿。 |
| `/bili状态` | `bili_status` | 全员 | `/bili状态` | 查看登录态、画质、ASR 与 FFmpeg 状态。 |
| `/bili登录` | `bili_login` | 管理员 | `/bili登录` | 申请扫码登录二维码（180 秒有效），成功后解锁 1080P。 |
| `/bili注销` | `bili_logout` | 管理员 | `/bili注销` | 清除本地扫码凭据，回退游客态。 |

> 唤醒前缀以 AstrBot 全局配置为准（默认 `/`）。指令需在私聊或群内唤醒后发送。

### 自然语言与卡片触发

- **`@bot` + 链接**：`@小助手 看看这个 https://www.bilibili.com/video/BV1...`，无需斜杠指令。
- **小程序卡片**：群友从 B 站 App「分享到 QQ」发出的卡片，或**引用该卡片再 `@bot`**，插件会遍历消息树提取真实 BV 号。
- **短链**：`https://b23.tv/xxxx` 会自动跟随跳转解析。

### 30 分钟追问

视频处理完成后，会话记忆保留 30 分钟。期间可直接追问：

- 「刚才那个视频里练度要求是啥？」
- 「UP 主置顶评论写了什么？」
- 「那个名场面在几分几秒？」

主模型会基于已提炼的事实作答，不会重复抓取 B 站。

### 登录与画质

| 场景 | 画质 | 字幕/评论 |
| --- | --- | --- |
| 游客态 | 360P / 480P | 易触发 `-101`、`-352` 风控 |
| 扫码登录 / 已配 SESSDATA | 1080P（`qn=80`） | 稳定获取 CC/AI 字幕 |

扫码流程：管理员发送 `/bili登录` → 手机 B 站 App 扫码确认 → 凭据写入 `<AstrBot>/data/plugin_data/astrbot_plugin_bilibili_watch/credentials.json` → 立即生效，无需重启。

二维码在**本地渲染**（`qrcode` 库，写入插件数据目录），含 `qrcode_key` 的登录链接不会发送给任何第三方服务；若环境未安装 `qrcode` 库，则退化为只发送文本链接。

> 建议使用**小号**登录。凭据以明文 JSON 保存在插件数据目录，请勿将 `data/` 提交到公开仓库。

---

## 测试

仓库提供离线单测与在线联调两套脚本。**在线联调所需的 Key 一律通过环境变量传入，不得写入任何文件。**

### 离线测试（不联网、无需 Key）

```bash
python tests/test_cleaners.py    # 清洗、目录挖掘、弹幕分桶、门控路由
python tests/test_asr.py         # ASR 幻觉剔除、术语纠偏、时间戳平摊
```

期望输出包含：

```text
[PASS] All 6 core test cases passed successfully!
```

### 在线联调（需要网络与对应 Key）

```bash
# B 站抓取与清洗链路（无需 Key，公开视频）
python tests/live/test_bili_client_live.py

# 小米 MiMo ASR（合成音频，验证握手）
export MIMO_API_KEY="sk-***"        # Windows: $env:MIMO_API_KEY = "sk-***"
python tests/live/test_mimo_live.py

# 硅基流动多语言 ASR（日语/韩语/中文）
export SILICONFLOW_API_KEY="sk-***"
python tests/live/test_siliconflow_live.py
```

| 脚本 | 依赖 | 覆盖范围 |
| --- | --- | --- |
| `tests/test_cleaners.py` | 无 | 文本清洗、目录提取、弹幕波峰、双模路由 |
| `tests/test_asr.py` | 无（MiMo 部分可选） | ASR 后处理、断句、术语纠偏 |
| `tests/live/test_bili_client_live.py` | 网络 | 真实视频元数据、弹幕、评论、字幕抓取 |
| `tests/live/test_mimo_live.py` | `MIMO_API_KEY` | MiMo ASR 协议握手 |
| `tests/live/test_siliconflow_live.py` | `SILICONFLOW_API_KEY` | SenseVoiceSmall 多语言转录 |

---

## 常见问题

### Q1：群里发消息没反应？

1. 确认平台连接正常：AstrBot 日志出现 `[aiocqhttp]` 消息事件；
2. 确认消息是**唤醒**状态（`@bot` 或以唤醒前缀开头）；
3. 检查是否命中冷却（默认 15 秒/会话）或每日配额（默认 20 次/用户）；
4. 查看 `journalctl` 或 WebUI 日志中 `[B站之眼evolution]` 的输出。

### Q2：提示接口风控（`-412` / `-352`）？

B 站对未登录、无设备指纹的高频请求有 IP 级流控。

- 用 `/bili登录` 绑定（小号即可），风控率显著下降；
- 适当增大 `cooldown_seconds`；
- 避免短时间内连续请求大量视频。

### Q3：视频没有字幕，能听懂台词吗？

可以。把 `asr_provider` 设为 `mimo`（中文/方言）或 `siliconflow`（日语/韩语/多语言），并填写对应 Key，插件会在无官方字幕时自动抽音轨转录（单次最长 5 分钟音频）。

### Q4：提示找不到 ffmpeg？

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y ffmpeg
# 或在配置项 ffmpeg_path 中填写绝对路径
```

视觉截帧与 ASR 抽音轨都依赖 ffmpeg；纯文本模式不依赖。

### Q5：开启 AstrBot「文本转图片」后，公式/emoji 显示异常？

AstrBot 的 t2i 是独立渲染器，与本插件无关：

- **本地渲染**（`t2i_strategy=local`）基于 Pillow，没有公式引擎，`$$...$$` 会原样显示；且需要等宽中文字体与等宽 emoji 字体，否则出现 □ 方框；
- **远程渲染**（`t2i_strategy=remote`）基于无头浏览器，支持 KaTeX 公式与 emoji，官方服务较慢时可自建 [astrbot-t2i-service](https://github.com/AstrBotDevs/astrbot-t2i-service)（`docker run -itd -p 8999:8999 soulter/astrbot-t2i-service:latest`），再在 AstrBot 配置中填写自建地址。

本插件报告中的 Markdown 结构（标题、表格、代码块）在两种渲染方式下都能正常显示。

### Q6：扫码登录一直不成功？

- 二维码 180 秒过期，过期后重新 `/bili登录`；
- 扫码后需在手机端**确认登录**；
- 若网络无法访问 B 站 passport 接口，请检查出口网络。

### Q7：为什么有的视频 3 秒就出报告，有的要 20 多秒？

这是双模门控的设计表现：教程/口播类（字幕完整、字速高）走纯文本模式，3–8 秒；二次元切片/手书/无字幕视频走弹幕波峰截帧 + 视觉模型，10–25 秒。

---

## 隐私与安全

- **凭据存储**：扫码登录的 SESSDATA 等写入 `<AstrBot>/data/plugin_data/astrbot_plugin_bilibili_watch/credentials.json`（明文 JSON），仅本机可读；`/bili注销` 可随时清除。
- **不外传**：插件的网络请求只发往 B 站 API 与你配置的 ASR 服务商；无遥测、无统计上报。
- **日志**：日志中不打印凭据与 Key；但错误堆栈可能包含 URL（含公开参数）。
- **仓库卫生**：`config.json`、`data/`、`credentials.json`、`*.key`、`.env` 已在 `.gitignore` 中忽略。
- **建议**：使用 B 站小号；不要把 `data/` 目录打包分享。

如发现安全问题，请按 [SECURITY.md](SECURITY.md) 的方式私下报告，不要在公开 Issue 中贴出 Key。

---

## 开发

### 目录结构

```text
astrbot_plugin_bilibili_watch/
├── main.py                    # 插件入口：事件钩子、指令、LLM Tool、编排
├── metadata.yaml              # 插件元信息（名称、版本、平台支持）
├── _conf_schema.json          # WebUI 配置面板 schema
├── config.example.json        # 独立配置文件模板
├── core/
│   ├── config.py              # 分层配置（WebUI > config.json > 环境变量）
│   ├── models.py              # 强类型数据契约
│   ├── client/                # B 站客户端、卡片解析、媒体抽帧
│   ├── cleaner/               # 文本/弹幕/字幕/帧去重/ASR 清洗
│   ├── gating/                # 双模门控路由
│   ├── asr/                   # ASR 引擎抽象与两家实现
│   ├── auth/                  # 扫码登录与凭据持久化
│   └── perception/            # Prompt 构造与会话记忆
├── tests/
│   ├── test_cleaners.py       # 离线单测
│   ├── test_asr.py            # 离线单测
│   └── live/                  # 需要网络/Key 的联调脚本
└── .github/workflows/ci.yml   # 离线测试 CI
```

### 扩展指引

- **新增清洗规则**：在 `core/cleaner/` 增加纯函数，返回 `CleanedVideoBundle` 所需字段，并在 `tests/test_cleaners.py` 补用例。
- **调整门控阈值**：`core/gating/router.py` 构造参数（字速阈值、弹幕波峰阈值、截帧数）均可注入，便于按内容类型调参。
- **新增 ASR 引擎**：实现 `core/asr/base.py` 的 `BaseASREngine.transcribe`，在 `main.py::_get_asr_engine` 注册，并在 `_conf_schema.json` 增加 `options`。
- **新增平台**：`metadata.yaml` 的 `support_platforms` 控制适配平台；新增平台需自行实现消息卡片解析。

代码风格、提交规范、测试要求与发布流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 贡献

欢迎 Issue 与 PR。提交前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)；请勿在 Issue/PR 中粘贴 API Key、SESSDATA 或他人隐私信息。

## 许可证

本项目基于 [MIT License](LICENSE) 开源。

## 致谢

- [AcceleratorYue](https://github.com/AcceleratorYue) — 原版插件 [astrbot_plugin_bilibili_eyes](https://github.com/AcceleratorYue/astrbot_plugin_bilibili_eyes) 的作者，本项目的缘起；
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) 提供的插件框架与消息平台抽象；
- 小米 MiMo 与硅基流动 SenseVoiceSmall 提供的 ASR 能力；
- 所有提交 Issue 与 PR 的贡献者。

## 免责声明

本项目仅用于学习与个人使用。请遵守 B 站用户协议与 robots 约定，控制请求频率；因使用本插件产生的账号风控、封禁或其他后果由使用者自行承担。

## 变更记录

见 [CHANGELOG.md](CHANGELOG.md)。
