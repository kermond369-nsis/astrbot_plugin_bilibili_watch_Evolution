"""可视化设置页的字段规格与校验（纯函数，无 AstrBot / 网络依赖）。

页面不直接读 `_conf_schema.json`，而是由这里给出「分组 + 控件类型 + 范围 + 说明」，
服务端再用同一份规格做白名单校验，保证页面与真实配置不会漂移。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SECRET_CLEAR = "__clear__"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str  # bool | int | string | text | select | secret
    default: Any = ""
    hint: str = ""
    options: tuple[tuple[str, str], ...] = ()
    min_value: int | None = None
    max_value: int | None = None
    unit: str = ""
    placeholder: str = ""
    advanced: bool = False

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "hint": self.hint,
            "options": [{"value": value, "label": label} for value, label in self.options],
            "min": self.min_value,
            "max": self.max_value,
            "unit": self.unit,
            "placeholder": self.placeholder,
            "advanced": self.advanced,
        }


@dataclass(frozen=True)
class Group:
    id: str
    title: str
    desc: str
    fields: tuple[Field, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "desc": self.desc,
            "fields": [item.to_dict() for item in self.fields],
        }


GROUPS: tuple[Group, ...] = (
    Group(
        id="connection",
        title="连接与登录",
        desc="B 站凭据与 ffmpeg。推荐用左侧「登录」页扫码，凭据会优先于这里的手动 SESSDATA。",
        fields=(
            Field(
                key="sessdata",
                label="SESSDATA",
                kind="secret",
                hint="手动粘贴的 B 站 Cookie 凭据。扫码登录成功后无需填写；两者同时存在时扫码凭据优先。",
                placeholder="留空表示沿用当前值",
            ),
            Field(
                key="ffmpeg_path",
                label="FFmpeg 路径",
                kind="string",
                default="",
                hint="留空自动探测：系统 PATH → imageio-ffmpeg。填写时请给可执行文件绝对路径。",
                placeholder="例如 C:\\ffmpeg\\bin\\ffmpeg.exe",
                advanced=True,
            ),
        ),
    ),
    Group(
        id="model",
        title="模型与实况",
        desc="视觉子代理与游戏实况判定。留空视觉模型时跟随 AstrBot 全局默认对话模型。",
        fields=(
            Field(
                key="vision_provider_id",
                label="视觉子代理模型",
                kind="string",
                default="",
                hint="留空跟随 AstrBot 全局默认对话模型；填写 Provider ID 可强制指定。",
                placeholder="留空 = 全局默认模型",
            ),
            Field(
                key="gameplay_mode",
                label="游戏实况判定",
                kind="select",
                default="auto",
                hint="auto 按标题/标签/分区自动识别；on 强制生成底栏 HUD 时间带；off 关闭游戏分支。",
                options=(("auto", "自动识别"), ("on", "强制开启"), ("off", "关闭")),
            ),
        ),
    ),
    Group(
        id="asr",
        title="ASR 语音识别",
        desc="没有可信字幕时，用音频转录补齐台词。长音频会按块转录，单块失败不影响其它块。",
        fields=(
            Field(
                key="asr_provider",
                label="服务商",
                kind="select",
                default="none",
                hint="none 禁用；mimo 小米 MiMo；siliconflow 硅基流动 SenseVoiceSmall。",
                options=(("none", "禁用"), ("mimo", "小米 MiMo"), ("siliconflow", "硅基流动")),
            ),
            Field(
                key="mimo_api_key",
                label="MiMo API Key",
                kind="secret",
                hint="仅服务商为 MiMo 时生效。",
                placeholder="留空表示沿用当前值",
            ),
            Field(
                key="siliconflow_api_key",
                label="硅基流动 API Key",
                kind="secret",
                hint="仅服务商为硅基流动时生效。",
                placeholder="留空表示沿用当前值",
            ),
            Field(
                key="mimo_base_url",
                label="MiMo 接口地址",
                kind="string",
                default="https://api.xiaomimimo.com/v1",
                hint="一般无需修改；使用自建代理或兼容接口时才改。",
                advanced=True,
            ),
            Field(
                key="siliconflow_base_url",
                label="硅基流动接口地址",
                kind="string",
                default="https://api.siliconflow.cn/v1",
                hint="一般无需修改；使用自建代理或兼容接口时才改。",
                advanced=True,
            ),
            Field(
                key="siliconflow_model",
                label="硅基流动模型名",
                kind="string",
                default="FunAudioLLM/SenseVoiceSmall",
                hint="默认 SenseVoiceSmall；替换模型时需保证音频接口协议一致。",
                advanced=True,
            ),
            Field(
                key="asr_max_seconds",
                label="单视频最长转录",
                kind="int",
                default=1800,
                hint="超过此长度不再继续转录，避免长视频费用失控。",
                min_value=60,
                max_value=7200,
                unit="秒",
            ),
            Field(
                key="asr_chunk_seconds",
                label="分块长度",
                kind="int",
                default=300,
                hint="每块音频长度。MiMo 等接口对单次上传有大小限制，5 分钟一块最稳。",
                min_value=60,
                max_value=600,
                unit="秒",
            ),
        ),
    ),
    Group(
        id="evidence",
        title="画面证据",
        desc="场景联表与底栏 HUD 时间带。帧越多证据越全，视觉 Token 也越多。",
        fields=(
            Field(
                key="frame_budget_cap",
                label="场景帧上限",
                kind="int",
                default=36,
                hint="场景联表最多抽多少帧；游戏实况会按片长加权后截断。",
                min_value=6,
                max_value=48,
                unit="帧",
            ),
            Field(
                key="hud_sheet_max",
                label="HUD 时间带张数",
                kind="int",
                default=6,
                hint="每张 32 格，张数越多覆盖越全；对 6 分钟视频，6 张约覆盖全片。",
                min_value=1,
                max_value=12,
                unit="张",
            ),
            Field(
                key="keep_frame_dirs",
                label="帧缓存保留",
                kind="int",
                default=6,
                hint="保留最近多少个视频的截图目录，超出后自动清理最旧的。",
                min_value=1,
                max_value=50,
                unit="个",
            ),
        ),
    ),
    Group(
        id="interaction",
        title="交互与频控",
        desc="每日额度、冷却和回复文案。回执与催更提示只影响聊天体验，不影响分析。",
        fields=(
            Field(
                key="user_daily_quota",
                label="每用户每日次数",
                kind="int",
                default=20,
                hint="0 表示不限制；管理员是否豁免见下一项。",
                min_value=0,
                max_value=10000,
                unit="次",
            ),
            Field(
                key="quota_exempt_admins",
                label="管理员豁免额度",
                kind="bool",
                default=True,
                hint="开启后群管理员与 AstrBot 管理员不占用每日次数。",
            ),
            Field(
                key="cooldown_seconds",
                label="同会话冷却",
                kind="int",
                default=15,
                hint="同一会话两次请求的最小间隔，0 表示不限制。",
                min_value=0,
                max_value=3600,
                unit="秒",
            ),
            Field(
                key="ack_enabled",
                label="收到视频先回执",
                kind="bool",
                default=True,
                hint="收到视频后先回一句提示，避免群友等待焦虑。",
            ),
            Field(
                key="ack_template",
                label="回执文案",
                kind="text",
                default="我去看看这个视频，稍等一下～",
                hint="仅在「收到视频先回执」开启时生效，可自定义口吻。",
                placeholder="我去看看这个视频，稍等一下～",
            ),
            Field(
                key="wait_notice_enabled",
                label="催更时提示进度",
                kind="bool",
                default=True,
                hint="观看期间群友催促时回复已耗时与当前阶段；/stop、/reset 等仍会放行。",
            ),
        ),
    ),
    Group(
        id="cache",
        title="缓存与原文",
        desc="字幕/ASR 原文缓存用于二次查询，过期会自动清理；截图缓存见「缓存与诊断」页。",
        fields=(
            Field(
                key="transcript_ttl_hours",
                label="原文缓存时长",
                kind="int",
                default=72,
                hint="字幕/ASR 原文在磁盘保留多少小时，供 bilibili_transcript 二次查询。",
                min_value=1,
                max_value=720,
                unit="小时",
            ),
        ),
    ),
)

ALL_FIELDS: tuple[Field, ...] = tuple(item for group in GROUPS for item in group.fields)
FIELD_MAP: dict[str, Field] = {item.key: item for item in ALL_FIELDS}
SECRET_KEYS: set[str] = {item.key for item in ALL_FIELDS if item.kind == "secret"}

# 这些字段清空会让功能不可用，服务端兜底拒绝
REQUIRED_NON_EMPTY: set[str] = {
    "ack_template",
    "mimo_base_url",
    "siliconflow_base_url",
    "siliconflow_model",
}


def spec_payload() -> list[dict]:
    return [group.to_dict() for group in GROUPS]


def coerce_value(item: Field, raw: Any) -> tuple[Any, str | None]:
    """把页面传来的一项值转成合法类型。返回 (值, 错误信息)。"""
    if item.kind == "bool":
        if isinstance(raw, bool):
            return raw, None
        if isinstance(raw, (int, float)) and raw in (0, 1):
            return bool(raw), None
        if isinstance(raw, str):
            lowered = raw.strip().lower()
            if lowered in ("true", "1", "on", "yes"):
                return True, None
            if lowered in ("false", "0", "off", "no"):
                return False, None
        return None, f"{item.label} 必须是布尔值"

    if item.kind == "int":
        if isinstance(raw, bool):
            return None, f"{item.label} 必须是整数"
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            return None, f"{item.label} 必须是整数"
        if item.min_value is not None and value < item.min_value:
            return None, f"{item.label} 不能小于 {item.min_value}"
        if item.max_value is not None and value > item.max_value:
            return None, f"{item.label} 不能大于 {item.max_value}"
        return value, None

    if item.kind == "select":
        value = str(raw or "").strip()
        allowed = {option for option, _ in item.options}
        if value not in allowed:
            return None, f"{item.label} 取值不合法"
        return value, None

    if item.kind == "secret":
        if raw == SECRET_CLEAR:
            return "", None
        if raw is None:
            return None, None
        value = str(raw).strip()
        if not value:
            return None, None  # 空串 = 保持原值
        if len(value) > 4096:
            return None, f"{item.label} 过长"
        return value, None

    # string / text
    value = "" if raw is None else str(raw).strip()
    if len(value) > 4000:
        return None, f"{item.label} 过长"
    return value, None


def validate_values(
    payload: Any,
    current: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """白名单校验页面提交的 settings。

    - 只接受 spec 里声明过的键；
    - secret 空串表示保持原值，`__clear__` 表示清空；
    - 返回 (待保存的增量, 错误列表)。
    """
    if not isinstance(payload, dict):
        return {}, ["请求体必须是对象"]
    current = current or {}
    unknown = [key for key in payload if key not in FIELD_MAP]
    if unknown:
        return {}, [f"未知配置项：{', '.join(sorted(unknown)[:6])}"]

    values: dict[str, Any] = {}
    errors: list[str] = []
    for key, raw in payload.items():
        item = FIELD_MAP[key]
        value, error = coerce_value(item, raw)
        if error:
            errors.append(error)
            continue
        if value is None:
            continue
        if key in REQUIRED_NON_EMPTY and item.kind in ("string", "text") and not str(value).strip():
            errors.append(f"{item.label} 不能为空")
            continue
        if item.kind != "secret" and current.get(key) == value:
            continue
        values[key] = value
    return values, errors


def public_values(current: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bool]]:
    """返回可安全下发到页面的值：secret 只告诉页面「是否已配置」。"""
    values: dict[str, Any] = {}
    secrets_present: dict[str, bool] = {}
    for item in ALL_FIELDS:
        if item.kind == "secret":
            secrets_present[item.key] = bool(str(current.get(item.key) or "").strip())
            continue
        value = current.get(item.key, item.default)
        values[item.key] = value
    return values, secrets_present


def apply_defaults(current: dict[str, Any], keys: list[str] | None = None) -> dict[str, Any]:
    """生成「恢复默认」的增量：默认跳过 secret，避免误清空 Key。"""
    values: dict[str, Any] = {}
    targets = set(keys) if keys else None
    for item in ALL_FIELDS:
        if item.kind == "secret":
            continue
        if targets is not None and item.key not in targets:
            continue
        values[item.key] = item.default
    return values


def mask_tail(value: str, keep: int = 4) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= keep:
        return "****"
    return "****" + value[-keep:]
