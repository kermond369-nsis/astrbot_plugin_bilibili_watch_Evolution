# 贡献指南

感谢你愿意为 **B站之眼 · evolution** 提交改进。本文说明开发环境、代码约定与 PR 流程。

## 开始之前

1. 先搜索已有的 [Issues](https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution/issues)，避免重复；
2. 功能类改动建议先开 Issue 讨论方案；
3. **不要**在任何 Issue / PR / 截图中粘贴 API Key、SESSDATA、Cookie、QQ 号等敏感信息。

## 开发环境

```bash
git clone https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution.git
cd astrbot_plugin_bilibili_watch

# 离线测试无需 AstrBot 环境（只依赖标准库）
python tests/test_cleaners.py
python tests/test_asr.py
```

若要在真实 AstrBot 中调试，把本目录克隆/软链到：

```text
<AstrBot>/data/plugins/astrbot_plugin_bilibili_watch
```

然后重启 AstrBot，观察日志关键字 `[B站之眼evolution]`。

## 目录约定

| 目录 | 职责 | 约束 |
| --- | --- | --- |
| `core/client/` | B 站接口、卡片解析、媒体处理 | 网络请求需处理退避；不得在事件循环中阻塞 |
| `core/cleaner/` | 文本/弹幕/字幕/帧/ASR 清洗 | **纯函数**，不得发起网络请求，便于离线单测 |
| `core/gating/` | 双模门控 | 阈值可注入，避免散落魔法数字 |
| `core/asr/` | ASR 引擎抽象 | 新引擎需实现 `BaseASREngine.transcribe` |
| `core/perception/` | Prompt 构造、会话记忆 | 注意防提示词注入 |
| `main.py` | 事件钩子、指令、LLM Tool、编排 | 阻塞操作一律走线程池 |

## 代码风格

- Python 3.10+，文件头使用 `from __future__ import annotations`；
- 优先类型标注，数据契约用 `dataclass`（`core/models.py`）；
- 命名：模块用 `snake_case`，类用 `PascalCase`，常量全大写；
- 注释与文档字符串使用中文，面向使用者/维护者说明「为什么」，而不是复述代码；
- 不要引入重型第三方依赖；确需引入时，在 PR 描述中说明理由与替代方案。

## 提交信息

采用 Conventional Commits 风格：

```text
fix: 修复无字幕视频 ASR 未触发的问题
feat: 支持按 UP 主查询近期投稿
docs: 补充 ffmpeg 安装说明
test: 增加弹幕分桶边界用例
```

## 测试要求

- 改动 `core/cleaner/`、`core/gating/`、`core/asr/` 时，必须补充或更新离线单测；
- 提交前本地跑通：

```bash
python tests/test_cleaners.py
python tests/test_asr.py
```

- 涉及真实接口的联调脚本（`tests/live/`）不要求 CI 通过，但请在本地验证并说明结果；
- **禁止**把 Key 写进测试代码或提交 `config.json`。

## Pull Request 流程

1. Fork 仓库，从 `main` 切出分支：`feat/xxx` 或 `fix/xxx`；
2. 提交改动，确保离线测试通过；
3. 在 PR 描述中说明：动机、改动点、验证方式（命令与输出摘要）、是否影响配置/兼容性；
4. 维护者 review 后合并。

## 版本与发布

### 发布检查清单

- [ ] `python tests/test_cleaners.py`、`python tests/test_asr.py`、`python tests/test_repo_hygiene.py` 全绿
- [ ] `metadata.yaml` 的 `version` 与 `CHANGELOG.md` 一致
- [ ] 仓库内无 `config.json`、`data/`、`credentials.json`、`*.key`、`.env` 等敏感文件
- [ ] README 配置表与 `_conf_schema.json` 一致（CI 的 `test_repo_hygiene.py` 会校验版本与 schema 形状）
- [ ] README 中的示例命令、链接可正常访问

### 发版步骤

1. 更新 `CHANGELOG.md`（新增版本段落）与 `metadata.yaml` 的 `version`；
2. 提交后打 tag：`git tag -a vX.Y.Z -m "vX.Y.Z: 摘要"`；
3. 推送提交与 tag：`git push origin main --tags`；
4. 在 GitHub Releases 中基于该 tag 发布，说明直接引用 CHANGELOG 段落。
