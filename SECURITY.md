# 安全策略

## 支持范围

| 版本 | 支持 |
| --- | --- |
| 1.1.x | ✅ 接收安全报告 |
| < 1.1 | ❌ 请先升级 |

## 报告方式

**不要在公开 Issue 中披露漏洞或敏感信息。**

请通过 GitHub 的 [Private vulnerability reporting](https://github.com/kermond369-nsis/astrbot_plugin_bilibili_watch_Evolution/security/advisories/new) 提交，或给维护者发私信。报告中请包含：

- 受影响的版本与提交；
- 复现步骤（可脱敏）；
- 影响范围（例如：凭据泄露、任意文件读取、SSRF）；
- 建议的修复方向（可选）。

## 本插件处理哪些敏感数据

| 数据 | 存放位置 | 说明 |
| --- | --- | --- |
| B 站 SESSDATA / 扫码凭据 | `<AstrBot>/data/plugin_data/astrbot_plugin_bilibili_watch/credentials.json` | 明文 JSON，权限由文件系统决定；`/bili注销` 可清除 |
| 配置中的 API Key | AstrBot 插件配置或 `config.json` | 由 AstrBot 配置系统或文件权限保护 |
| 视频元数据/字幕/弹幕 | 内存（30 分钟会话记忆） | 不落盘，进程重启即清空 |
| 关键帧截图 | `<AstrBot>/data/plugin_data/astrbot_plugin_bilibili_watch/frames/` | 临时文件，可随时删除 |
| 登录二维码 | `<AstrBot>/data/plugin_data/astrbot_plugin_bilibili_watch/qrcode/login_qr.png` | 本地渲染，不经过第三方二维码服务 |

## 已知边界与建议

- 本插件不提供鉴权层，面板/配置的访问控制依赖 AstrBot 自身的账号体系；
- 扫码登录建议使用 B 站小号；
- 若部署在多人可写的主机上，应限制插件目录权限，避免凭据被读取；
- 插件不会把数据发送到 B 站与所配置 ASR 服务商之外的任何地址。
