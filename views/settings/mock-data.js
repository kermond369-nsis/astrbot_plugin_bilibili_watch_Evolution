/* 仅用于本地预览 / 截图（?mock=1）。生产环境由 AstrBot bridge 提供数据。 */

const GROUPS = [
  {
    id: "connection",
    title: "连接与登录",
    desc: "B 站凭据与 ffmpeg。推荐用左侧「登录」页扫码，凭据会优先于这里的手动 SESSDATA。",
    fields: [
      { key: "sessdata", label: "SESSDATA", kind: "secret", hint: "手动粘贴的 B 站 Cookie 凭据。扫码登录成功后无需填写。", placeholder: "留空表示沿用当前值", options: [] },
      { key: "ffmpeg_path", label: "FFmpeg 路径", kind: "string", hint: "留空自动探测：系统 PATH → imageio-ffmpeg。", placeholder: "C:\\ffmpeg\\bin\\ffmpeg.exe", options: [], advanced: true },
    ],
  },
  {
    id: "model",
    title: "模型与实况",
    desc: "视觉子代理与游戏实况判定。留空视觉模型时跟随当前会话主模型。",
    fields: [
      { key: "vision_provider_id", label: "视觉子代理模型", kind: "string", hint: "留空跟随 AstrBot 全局默认模型。", placeholder: "留空 = 全局默认模型", options: [] },
      { key: "gameplay_mode", label: "游戏实况判定", kind: "select", hint: "auto 自动识别；on 强制生成 HUD；off 关闭。", options: [{ value: "auto", label: "自动识别" }, { value: "on", label: "强制开启" }, { value: "off", label: "关闭" }] },
    ],
  },
  {
    id: "asr",
    title: "ASR 语音识别",
    desc: "没有可信字幕时，用音频转录补齐台词。长音频会按块转录。",
    fields: [
      { key: "asr_provider", label: "服务商", kind: "select", hint: "没有可信字幕时才触发。", options: [{ value: "none", label: "禁用" }, { value: "mimo", label: "小米 MiMo" }, { value: "siliconflow", label: "硅基流动" }] },
      { key: "mimo_api_key", label: "MiMo API Key", kind: "secret", hint: "仅服务商为 MiMo 时生效。", placeholder: "留空表示沿用当前值", options: [] },
      { key: "siliconflow_api_key", label: "硅基流动 API Key", kind: "secret", hint: "仅服务商为硅基流动时生效。", placeholder: "留空表示沿用当前值", options: [] },
      { key: "asr_max_seconds", label: "单视频最长转录", kind: "int", hint: "超过此长度不再继续转录。", min: 60, max: 7200, unit: "秒", options: [] },
      { key: "asr_chunk_seconds", label: "分块长度", kind: "int", hint: "每块音频长度，5 分钟一块最稳。", min: 60, max: 600, unit: "秒", options: [] },
      { key: "mimo_base_url", label: "MiMo 接口地址", kind: "string", hint: "一般无需修改。", options: [], advanced: true },
      { key: "siliconflow_base_url", label: "硅基流动接口地址", kind: "string", hint: "一般无需修改。", options: [], advanced: true },
      { key: "siliconflow_model", label: "硅基流动模型名", kind: "string", hint: "默认 SenseVoiceSmall。", options: [], advanced: true },
    ],
  },
  {
    id: "evidence",
    title: "画面证据",
    desc: "场景联表与底栏 HUD 时间带。帧越多证据越全，视觉 Token 也越多。",
    fields: [
      { key: "frame_budget_cap", label: "场景帧上限", kind: "int", hint: "场景联表最多抽多少帧。", min: 6, max: 48, unit: "帧", options: [] },
      { key: "hud_sheet_max", label: "HUD 时间带张数", kind: "int", hint: "每张 32 格，6 张约覆盖 6 分钟。", min: 1, max: 12, unit: "张", options: [] },
      { key: "keep_frame_dirs", label: "帧缓存保留", kind: "int", hint: "保留最近多少个视频的截图目录。", min: 1, max: 50, unit: "个", options: [] },
    ],
  },
  {
    id: "interaction",
    title: "交互与频控",
    desc: "每日额度、冷却和回复文案。",
    fields: [
      { key: "user_daily_quota", label: "每用户每日次数", kind: "int", hint: "0 表示不限制。", min: 0, max: 10000, unit: "次", options: [] },
      { key: "quota_exempt_admins", label: "管理员豁免额度", kind: "bool", hint: "管理员不占用每日次数。", options: [] },
      { key: "cooldown_seconds", label: "同会话冷却", kind: "int", hint: "两次请求的最小间隔。", min: 0, max: 3600, unit: "秒", options: [] },
      { key: "ack_enabled", label: "收到视频先回执", kind: "bool", hint: "先回一句提示。", options: [] },
      { key: "ack_template", label: "回执文案", kind: "text", hint: "可自定义口吻。", placeholder: "我去看看这个视频，稍等一下～", options: [] },
      { key: "wait_notice_enabled", label: "催更时提示进度", kind: "bool", hint: "催促时回复已耗时与阶段。", options: [] },
    ],
  },
  {
    id: "cache",
    title: "缓存与原文",
    desc: "字幕/ASR 原文缓存用于二次查询。",
    fields: [
      { key: "transcript_ttl_hours", label: "原文缓存时长", kind: "int", hint: "过期自动清理。", min: 1, max: 720, unit: "小时", options: [] },
    ],
  },
];

const VALUES = {
  ffmpeg_path: "",
  vision_provider_id: "",
  gameplay_mode: "auto",
  asr_provider: "mimo",
  asr_max_seconds: 1800,
  asr_chunk_seconds: 300,
  mimo_base_url: "https://api.xiaomimimo.com/v1",
  siliconflow_base_url: "https://api.siliconflow.cn/v1",
  siliconflow_model: "FunAudioLLM/SenseVoiceSmall",
  frame_budget_cap: 36,
  hud_sheet_max: 6,
  keep_frame_dirs: 6,
  user_daily_quota: 20,
  quota_exempt_admins: true,
  cooldown_seconds: 15,
  ack_enabled: true,
  ack_template: "我去看看这个视频，稍等一下～",
  wait_notice_enabled: true,
  transcript_ttl_hours: 72,
};

function fakeAvatar() {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96">
    <defs><linearGradient id="a" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#6aa6ff"/><stop offset="1" stop-color="#a06bff"/>
    </linearGradient></defs>
    <rect width="96" height="96" fill="url(#a)"/>
    <circle cx="48" cy="38" r="20" fill="#ffe3d3"/>
    <path d="M26 30c4-16 40-16 44 0-6-5-14-7-22-7s-16 2-22 7Z" fill="#3b3f55"/>
    <circle cx="41" cy="38" r="2.4" fill="#2b2f45"/>
    <circle cx="55" cy="38" r="2.4" fill="#2b2f45"/>
    <path d="M42 47c4 3 8 3 12 0" fill="none" stroke="#c96b6b" stroke-width="2" stroke-linecap="round"/>
    <path d="M22 96c2-20 12-30 26-30s24 10 26 30Z" fill="#ffffff" opacity="0.92"/>
  </svg>`;
  return "data:image/svg+xml;base64," + btoa(svg);
}

const STATUS = {
  version: "1.4.0",
  plugin_name: "astrbot_plugin_bilibili_watch",
  data_dir: "C:\\AstrBot\\data\\plugin_data\\astrbot_plugin_bilibili_watch",
  config_path: "C:\\AstrBot\\data\\config\\astrbot_plugin_bilibili_watch_config.json",
  login: { logged_in: true, source: "qr", sessdata_tail: "****a1b2", account: { uname: "观测者", mid: "22425829", level: 6, vip: true, avatar: fakeAvatar() } },
  asr: { provider: "mimo", configured: true, max_seconds: 1800, chunk_seconds: 300 },
  vision_provider_id: "",
  vision: { mode: "global", provider_id: "openai-responses/gemini-3.8-flash-high", model: "gemini-3.8-flash-high" },
  gameplay_mode: "auto",
  ffmpeg: { path: "ffmpeg", available: true, version: "ffmpeg version 6.0 Copyright (c) 2000-2023 the FFmpeg developers" },
  cache: { total_bytes: 48234496, total_files: 612, frame_videos: 4, transcript_count: 7, items: [] },
  quota: { user_daily_quota: 20, quota_exempt_admins: true, cooldown_seconds: 15 },
  registered_apis: ["/astrbot_plugin_bilibili_watch/status", "/settings", "/settings/save", "/login/qr"],
  server_time: Math.floor(Date.now() / 1000),
};

const CACHE = {
  total_bytes: 48234496,
  total_files: 612,
  frame_videos: 4,
  transcript_count: 7,
  items: [
    { name: "frames", desc: "截图 / 场景联表 / HUD 时间带", bytes: 46137344, files: 592 },
    { name: "transcripts", desc: "字幕 / ASR 原文缓存", bytes: 1856000, files: 7 },
    { name: "qrcode", desc: "扫码登录二维码", bytes: 8200, files: 1 },
    { name: "cache", desc: "ASR 临时音频", bytes: 236000, files: 12 },
  ],
};

const VIDEOS = [
  { title: "【转载|蔚蓝档案】蓝甲赛特塔124层 水伊吹+心【制约解除作战】", bvid: "BV1MwxY63Eu9", gameplay: true, age_seconds: 120, umo: "aiocqhttp:GroupMessage:123456" },
  { title: "9 个冷门 AI 挑战擂主豆包：写程序对打 + 改屎山代码", bvid: "BV1pJnB6iEd7", gameplay: false, age_seconds: 980, umo: "aiocqhttp:GroupMessage:123456" },
];

const DIAG = {
  python: "3.12.8",
  platform: "Windows-11-10.0.26100-SP0",
  cwd: "C:\\AstrBot",
  cache_root: STATUS.data_dir,
  config_path: STATUS.config_path,
  ffmpeg: STATUS.ffmpeg,
  client_user: "admin",
  registered_apis: [
    "/astrbot_plugin_bilibili_watch/status",
    "/astrbot_plugin_bilibili_watch/settings",
    "/astrbot_plugin_bilibili_watch/settings/save",
    "/astrbot_plugin_bilibili_watch/settings/reset",
    "/astrbot_plugin_bilibili_watch/login/qr",
    "/astrbot_plugin_bilibili_watch/login/status",
    "/astrbot_plugin_bilibili_watch/login/account",
    "/astrbot_plugin_bilibili_watch/login/logout",
    "/astrbot_plugin_bilibili_watch/cache",
    "/astrbot_plugin_bilibili_watch/cache/cleanup",
    "/astrbot_plugin_bilibili_watch/videos",
    "/astrbot_plugin_bilibili_watch/diagnostics",
  ],
};

function fakeQr() {
  const cells = [];
  for (let y = 0; y < 21; y += 1) {
    for (let x = 0; x < 21; x += 1) {
      const finder = (x < 7 && y < 7) || (x > 13 && y < 7) || (x < 7 && y > 13);
      const on = finder ? (x % 6 !== 0 && y % 6 !== 0) : (x * 7 + y * 13 + x * y) % 3 === 0;
      if (on) cells.push(`<rect x="${x * 10}" y="${y * 10}" width="10" height="10"/>`);
    }
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 210"><rect width="210" height="210" fill="#fff"/><g fill="#111">${cells.join("")}</g></svg>`;
  return "data:image/svg+xml;base64," + btoa(svg);
}

export function createMockBridge() {
  return {
    ready: async () => ({ pluginName: "astrbot_plugin_bilibili_watch", pageName: "settings", locale: "zh-CN", isDark: false }),
    t: (_key, fallback) => fallback,
    onContext: () => () => {},
    apiGet: async (endpoint) => {
      if (endpoint === "status") return STATUS;
      if (endpoint === "settings") {
        return {
          groups: GROUPS,
          values: VALUES,
          secrets_present: { sessdata: true, mimo_api_key: true, siliconflow_api_key: false },
          config_path: STATUS.config_path,
        };
      }
      if (endpoint === "cache") return CACHE;
      if (endpoint === "videos") return { items: VIDEOS };
      if (endpoint === "diagnostics") return DIAG;
      if (endpoint === "login/account") return { logged_in: true, account: STATUS.login.account, source: "qr" };
      if (endpoint === "login/status") return { state: "waiting", code: 86101, message: "等待扫码" };
      return {};
    },
    apiPost: async (endpoint) => {
      if (endpoint === "login/qr") return { key: "mock", url: "https://passport.bilibili.com/h5-app/passport/login/scan?mock=1", image: fakeQr(), expires_in: 180 };
      if (endpoint === "login/logout") return { cleared: true, message: "已清除扫码凭据", login: { logged_in: false, source: "guest" } };
      if (endpoint === "settings/save") return { saved: true, message: "已保存 1 项设置", values: VALUES, secrets_present: { sessdata: true, mimo_api_key: true } };
      if (endpoint === "settings/reset") return { saved: true, message: "已恢复默认", values: VALUES, secrets_present: {} };
      if (endpoint === "cache/cleanup") return { ok: true, freed_bytes: 46137344, removed_files: 592, cache: CACHE };
      return {};
    },
  };
}
