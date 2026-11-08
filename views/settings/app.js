/* 逐渐完善的预知眼 · 可视化设置页
 * 通过 window.AstrBotPluginView bridge 调用插件后端；无构建步骤、无外部依赖。
 */

const t = (key, fallback) => {
  try {
    return window.__bridge?.t?.(key, fallback) ?? fallback;
  } catch {
    return fallback;
  }
};

const NAV_META = {
  overview: {
    title: "概览",
    desc: "插件运行状态、登录情况与最近观看。",
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></svg>',
  },
  login: {
    title: "登录",
    desc: "扫码绑定 B 站账号，解锁 1080P 与更稳的字幕/评论接口。",
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="8" r="4"/><path d="M4.5 20c1.6-3.4 4.2-5 7.5-5s5.9 1.6 7.5 5"/></svg>',
  },
  cache: {
    title: "缓存",
    desc: "截图、场景联表、HUD 时间带与原文缓存的占用和清理。",
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7h16M6 7v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/><path d="M10 11v6M14 11v6"/></svg>',
  },
  diagnostics: {
    title: "诊断",
    desc: "运行环境、接口注册与路径信息，用于排查问题。",
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 12h4l2.5-6 4 12 2.5-6H21"/></svg>',
  },
};

const GROUP_ICONS = {
  connection: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 7V4M15 7V4M7 7h10v4a5 5 0 0 1-10 0V7ZM12 16v5"/></svg>',
  model: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="6" y="6" width="12" height="12" rx="3"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/></svg>',
  asr: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="9" y="2.5" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/></svg>',
  evidence: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="18" height="14" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="m5 17 4.5-4.5 3 3L16 12l3 3"/></svg>',
  interaction: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 5.5h16v10H9l-5 4v-14Z"/><path d="M8 10h8M8 13h5"/></svg>',
  cache: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3 3 7.5 12 12l9-4.5L12 3Z"/><path d="m3 12 9 4.5 9-4.5M3 16.5 12 21l9-4.5"/></svg>',
};

const state = {
  bridge: null,
  context: null,
  status: null,
  groups: [],
  values: {},
  secretsPresent: {},
  original: {},
  secretsOriginal: {},
  dirty: new Set(),
  active: "overview",
  login: { key: "", url: "", image: "", timer: null, countdown: null, expiresAt: 0, state: "idle" },
  cache: null,
  videos: [],
  diagnostics: null,
  saving: false,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function fmtBytes(bytes) {
  const n = Number(bytes || 0);
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = n / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[index]}`;
}

function fmtAge(seconds) {
  const s = Math.max(0, Number(seconds || 0));
  if (s < 60) return `${s} 秒前`;
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  return `${Math.floor(s / 3600)} 小时前`;
}

function fmtTime(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds || 0)));
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function toast(message, kind = "ok") {
  const box = $("#toasts");
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  box.appendChild(node);
  window.setTimeout(() => {
    node.style.opacity = "0";
    node.style.transform = "translateY(6px)";
    window.setTimeout(() => node.remove(), 200);
  }, 4200);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      area.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

function showAlert(message) {
  const node = $("#alert");
  if (!message) {
    node.hidden = true;
    node.textContent = "";
    return;
  }
  node.hidden = false;
  node.textContent = message;
}

function confirmDialog(title, body, okText = "确认") {
  return new Promise((resolve) => {
    const modal = $("#modal");
    $("#modal-title").textContent = title;
    $("#modal-body").textContent = body;
    const ok = $("#modal-ok");
    ok.textContent = okText;
    modal.hidden = false;
    const close = (value) => {
      modal.hidden = true;
      ok.onclick = null;
      modal.onclick = null;
      resolve(value);
    };
    ok.onclick = () => close(true);
    modal.onclick = (event) => {
      if (event.target.dataset.close === "1") close(false);
    };
    window.setTimeout(() => ok.focus(), 10);
  });
}

async function apiGet(endpoint, params = {}) {
  return state.bridge.apiGet(endpoint, params);
}

async function apiPost(endpoint, body = {}) {
  return state.bridge.apiPost(endpoint, body);
}

/* ------------------------------------------------------------------ boot */

async function boot() {
  let bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
  if (!bridge) {
    const params = new URLSearchParams(location.search);
    const localPreview = ["127.0.0.1", "localhost", "::1"].includes(location.hostname);
    if (params.has("mock") && localPreview) {
      const module = await import("./mock-data.js");
      bridge = module.createMockBridge();
      document.documentElement.dataset.mock = "1";
    }
  }
  if (!bridge) {
    showAlert("没有检测到 AstrBot 视图桥接。请从 AstrBot WebUI 的插件详情页打开本页面。");
    $("#page-title").textContent = "无法加载";
    return;
  }
  state.bridge = bridge;
  window.__bridge = bridge;
  state.context = await bridge.ready();
  applyTheme(state.context?.isDark);
  bridge.onContext?.((ctx) => {
    applyTheme(ctx?.isDark);
    renderAll();
  });

  await refreshAll();
  await loadAccount();
  bindShell();
  showView("overview");
}

function applyTheme(isDark) {
  document.documentElement.dataset.theme = isDark ? "dark" : "light";
}

async function refreshAll() {
  showAlert("");
  try {
    const [status, settings, cache, videos] = await Promise.all([
      apiGet("status"),
      apiGet("settings"),
      apiGet("cache").catch(() => null),
      apiGet("videos").catch(() => ({ items: [] })),
    ]);
    state.status = status;
    state.groups = settings.groups || [];
    state.values = settings.values || {};
    state.secretsPresent = settings.secrets_present || {};
    state.original = structuredCloneSafe(state.values);
    state.secretsOriginal = structuredCloneSafe(state.secretsPresent);
    state.dirty.clear();
    state.cache = cache || state.cache;
    state.videos = videos?.items || [];
    renderAll();
    if (state.active === "login") {
      loadAccount();
    }
  } catch (error) {
    showAlert(`加载失败：${error?.message || error}`);
  }
}

function structuredCloneSafe(value) {
  try {
    return structuredClone(value);
  } catch {
    return JSON.parse(JSON.stringify(value));
  }
}

/* ------------------------------------------------------------------ shell */

function bindShell() {
  $("#btn-reload").addEventListener("click", async () => {
    if (state.dirty.size) {
      const ok = await confirmDialog("放弃未保存的更改？", `当前有 ${state.dirty.size} 项修改尚未保存，刷新会丢弃它们。`, "刷新");
      if (!ok) return;
    }
    await refreshAll();
    toast("已刷新");
  });
  $("#btn-save").addEventListener("click", saveSettings);
  $("#btn-save-2").addEventListener("click", saveSettings);
  $("#btn-discard").addEventListener("click", () => {
    state.values = structuredCloneSafe(state.original);
    state.secretsPresent = structuredCloneSafe(state.secretsOriginal);
    state.dirty.clear();
    renderAll();
    toast("已放弃未保存的更改");
  });
  $("#nav").addEventListener("click", (event) => {
    const button = event.target.closest("[data-nav]");
    if (button) showView(button.dataset.nav);
  });
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      if (state.dirty.size) saveSettings();
    }
  });
  window.addEventListener("beforeunload", () => {
    stopLoginTimers();
  });
}

function navItems() {
  const items = [
    { id: "overview", ...NAV_META.overview },
    { id: "login", ...NAV_META.login },
  ];
  state.groups.forEach((group) => {
    items.push({
      id: `group-${group.id}`,
      group,
      title: group.title,
      desc: group.desc,
      icon: GROUP_ICONS[group.id] || GROUP_ICONS.model,
    });
  });
  items.push({ id: "cache", ...NAV_META.cache });
  items.push({ id: "diagnostics", ...NAV_META.diagnostics });
  return items;
}

function renderNav() {
  const nav = $("#nav");
  const items = navItems();
  nav.innerHTML = items
    .map((item, index) => {
      if (index === 2) {
        return `<div class="nav-sep">配置</div>` + navButton(item);
      }
      return navButton(item);
    })
    .join("");
}

function navButton(item) {
  const active = state.active === item.id ? " active" : "";
  const count = item.group ? groupDirtyCount(item.group) : 0;
  const badge = count ? `<span class="badge-count">${count}</span>` : "";
  return `<button class="nav-item${active}" type="button" data-nav="${item.id}" title="${esc(item.title)}">
    <span class="icon">${item.icon}</span><span>${esc(item.title)}</span>${badge}
  </button>`;
}

function groupDirtyCount(group) {
  return group.fields.filter((field) => state.dirty.has(field.key)).length;
}

function showView(id) {
  state.active = id;
  $$(".view").forEach((node) => {
    node.hidden = node.dataset.view !== id && !(id.startsWith("group-") && node.dataset.view === id);
  });
  const item = navItems().find((entry) => entry.id === id);
  $("#page-title").textContent = item?.title || "设置";
  $("#page-desc").textContent = item?.desc || "";
  renderNav();
  updateSaveBar();
  if (id === "login") {
    renderLogin();
    loadAccount();
  }
  if (id === "cache") {
    loadCache();
  }
  if (id === "diagnostics") {
    loadDiagnostics();
  }
}

function renderAll() {
  renderNav();
  renderOverview();
  renderSettings();
  renderLogin();
  renderCache();
  renderDiagnostics();
  renderSidebar();
  updateLoginPill();
  updateSaveBar();
  showView(state.active);
}

function renderSidebar() {
  $("#side-version").textContent = `v${state.status?.version || "—"}`;
  const path = state.status?.data_dir || "";
  $("#side-path").textContent = path;
  $("#side-path").title = path;
  const dot = $("#side-dot");
  dot.classList.toggle("warn", !state.status?.login?.logged_in);
}

function updateLoginPill() {
  const pill = $("#login-pill");
  const login = state.status?.login;
  if (!login) {
    pill.className = "pill";
    pill.textContent = "读取中…";
    return;
  }
  if (login.logged_in) {
    const source = { qr: "扫码", config: "配置" }[login.source] || "";
    pill.className = "pill ok";
    pill.textContent = login.account?.uname ? `已登录 · ${login.account.uname}` : `已登录${source ? ` · ${source}` : ""}`;
  } else {
    pill.className = "pill warn";
    pill.textContent = "游客模式";
  }
}

/* ------------------------------------------------------------------ overview */

function renderOverview() {
  const host = $("#view-overview");
  if (!state.status) {
    host.innerHTML = `<div class="card"><div class="empty">正在读取插件状态…</div></div>`;
    return;
  }
  const status = state.status;
  const login = status.login || {};
  const asr = status.asr || {};
  const ffmpeg = status.ffmpeg || {};
  const cache = status.cache || {};
  const stats = [
    {
      k: "登录状态",
      v: login.logged_in ? (login.account?.uname || "已登录") : "游客",
      ok: login.logged_in,
      s: login.logged_in
        ? `来源：${{ qr: "扫码凭据", config: "配置 SESSDATA" }[login.source] || login.source}${login.sessdata_tail ? ` · ${login.sessdata_tail}` : ""}`
        : "未登录只能取 360P/480P，字幕接口也更易风控",
    },
    {
      k: "ASR 语音识别",
      v: asr.provider === "none" ? "已禁用" : asr.provider === "mimo" ? "小米 MiMo" : "硅基流动",
      ok: asr.configured,
      s: asr.configured ? `单视频上限 ${asr.max_seconds}s · 每块 ${asr.chunk_seconds}s` : "没有配置 Key，无字幕时只能靠评论与画面",
    },
    {
      k: "FFmpeg",
      v: ffmpeg.available ? "可用" : "未找到",
      ok: ffmpeg.available,
      s: ffmpeg.version || ffmpeg.path || "视觉证据与 ASR 抽音轨依赖 ffmpeg",
    },
    {
      k: "视觉子代理",
      v:
        status.vision?.mode === "override"
          ? status.vision.provider_id || "已指定"
          : status.vision?.model || status.vision?.provider_id || "跟随全局默认模型",
      muted: status.vision?.mode !== "override",
      s:
        status.vision?.mode === "override"
          ? "设置页指定了独立 Provider"
          : "跟随 AstrBot 全局默认对话模型（可在「模型与实况」覆盖）",
    },
    {
      k: "帧缓存",
      v: fmtBytes(cache.total_bytes),
      s: `${cache.frame_videos || 0} 个视频 · ${cache.total_files || 0} 个文件`,
    },
    {
      k: "游戏实况",
      v: { auto: "自动识别", on: "强制开启", off: "关闭" }[status.gameplay_mode] || status.gameplay_mode,
      s: "开启时会额外生成底栏 HUD 时间带",
    },
  ];

  host.innerHTML = `
    <div class="stat-grid">
      ${stats.map(statCard).join("")}
    </div>

    <div class="card">
      <div class="card-head">
        <div><h2>最近观看</h2><p>会话记忆 30 分钟；这里只展示标题与时间，不含分析正文。</p></div>
        <div class="card-actions"><button class="btn small ghost" data-action="refresh-videos" type="button">刷新</button></div>
      </div>
      ${renderVideosTable()}
    </div>

    <div class="card">
      <div class="card-head">
        <div><h2>运行信息</h2><p>排查问题时优先看这里。</p></div>
        <div class="card-actions">
          <button class="btn small ghost" data-action="copy" data-copy="${esc(status.data_dir || "")}" type="button">复制数据目录</button>
        </div>
      </div>
      <dl class="kv">
        <dt>插件版本</dt><dd>v${esc(status.version || "—")}</dd>
        <dt>数据目录</dt><dd><code>${esc(status.data_dir || "—")}</code></dd>
        <dt>配置文件</dt><dd><code>${esc(status.config_path || "—")}</code>${status.config_path ? `<button class="btn link copy" type="button" data-action="copy" data-copy="${esc(status.config_path)}">复制</button>` : ""}</dd>
        <dt>接口</dt><dd>${(status.registered_apis || []).length || "—"} 个已注册</dd>
        <dt>服务时间</dt><dd>${status.server_time ? new Date(status.server_time * 1000).toLocaleString() : "—"}</dd>
      </dl>
    </div>
  `;

  host.querySelectorAll("[data-action]").forEach((node) => {
    node.addEventListener("click", handleAction);
  });
  host.querySelectorAll("[data-nav-jump]").forEach((node) => {
    node.addEventListener("click", () => showView(node.dataset.navJump));
  });
}

function statCard(item) {
  const cls = item.ok === true ? "ok" : item.ok === false ? "warn" : item.muted ? "muted" : "";
  return `<div class="stat">
    <div class="k">${esc(item.k)}</div>
    <div class="v ${cls}">${esc(item.v)}</div>
    <div class="s">${esc(item.s || "")}</div>
  </div>`;
}

function renderVideosTable() {
  if (!state.videos.length) {
    return `<div class="empty"><div class="big">🕒</div>还没有看过的视频。去群里发一条 B 站链接试试。</div>`;
  }
  return `<div class="table-wrap"><table class="table">
    <thead><tr><th>标题</th><th>BV</th><th class="col-kind">类型</th><th class="col-session">会话</th><th>时间</th></tr></thead>
    <tbody>
      ${state.videos.map((item) => `<tr>
        <td class="t-title" title="${esc(item.title)}">${esc(item.title || "（无标题）")}</td>
        <td><code>${esc(item.bvid || "")}</code></td>
        <td class="col-kind">${item.gameplay ? '<span class="tag brand">游戏实况</span>' : '<span class="tag">普通</span>'}</td>
        <td class="col-session"><span class="mono">${esc((item.umo || "").slice(0, 28))}</span></td>
        <td>${fmtAge(item.age_seconds)}</td>
      </tr>`).join("")}
    </tbody>
  </table></div>`;
}

/* ------------------------------------------------------------------ settings */

function renderSettings() {
  const host = $("#settings-views");
  if (!state.groups.length) {
    host.innerHTML = `<div class="card"><div class="empty">正在读取配置项…</div></div>`;
    return;
  }
  host.innerHTML = state.groups.map(renderGroup).join("");
  host.querySelectorAll("[data-action]").forEach((node) => node.addEventListener("click", handleAction));
  host.querySelectorAll("[data-field]").forEach((input) => {
    input.addEventListener("input", onFieldInput);
    input.addEventListener("change", onFieldInput);
  });
}

function renderGroup(group) {
  const normal = group.fields.filter((field) => !field.advanced);
  const advanced = group.fields.filter((field) => field.advanced);
  const dirty = groupDirtyCount(group);
  return `<section class="view" data-view="group-${group.id}" ${state.active === `group-${group.id}` ? "" : "hidden"}>
    <div class="card form-group" data-group="${group.id}">
      <div class="group-head">
        <div><h2>${esc(group.title)}</h2><p>${esc(group.desc || "")}</p></div>
        <div class="card-actions">
          ${dirty ? `<span class="tag warn">${dirty} 项待保存</span>` : ""}
          <button class="btn small ghost" type="button" data-action="reset-group" data-group="${group.id}">恢复本组默认</button>
        </div>
      </div>
      <div class="fields">
        ${normal.map((field) => renderField(field)).join("")}
      </div>
      ${advanced.length ? `<details class="advanced"><summary>高级选项（${advanced.length}）</summary>
        <div class="fields">${advanced.map((field) => renderField(field)).join("")}</div>
      </details>` : ""}
    </div>
  </section>`;
}

function renderField(field) {
  const id = `f_${field.key}`;
  const hint = field.hint ? `<div class="hint">${esc(field.hint)}</div>` : "";
  const error = `<div class="error" data-error="${field.key}" hidden></div>`;

  if (field.kind === "bool") {
    const checked = Boolean(state.values[field.key]);
    return `<div class="field wide">
      <div class="bool-row">
        <div class="bool-text"><b>${esc(field.label)}</b><small>${esc(field.hint || "")}</small></div>
        <label class="switch" title="${esc(field.label)}">
          <input type="checkbox" id="${id}" data-field="${field.key}" ${checked ? "checked" : ""} />
          <span></span>
        </label>
      </div>
      ${error}
    </div>`;
  }

  if (field.kind === "secret") {
    const present = Boolean(state.secretsPresent[field.key]);
    return `<div class="field">
      <div class="label-row"><label for="${id}">${esc(field.label)}</label>
        <span class="tag ${present ? "ok" : ""}" data-secret-state="${field.key}">${present ? "已配置" : "未配置"}</span>
      </div>
      <div class="secret-box">
        <div class="secret-top">
          <span class="state" data-secret-note="${field.key}">${present ? "留空表示保持当前值" : "尚未设置"}</span>
          ${present ? `<button class="btn link" type="button" data-action="clear-secret" data-key="${field.key}">清除</button>` : ""}
        </div>
        <input type="password" id="${id}" data-field="${field.key}" data-kind="secret" autocomplete="new-password"
          placeholder="${esc(field.placeholder || "输入新值")}" />
      </div>
      ${hint}${error}
    </div>`;
  }

  if (field.kind === "select") {
    const options = (field.options || []).map((opt) =>
      `<option value="${esc(opt.value)}" ${String(state.values[field.key]) === String(opt.value) ? "selected" : ""}>${esc(opt.label)}</option>`
    ).join("");
    return `<div class="field">
      <label for="${id}">${esc(field.label)}</label>
      <select id="${id}" data-field="${field.key}">${options}</select>
      ${hint}${error}
    </div>`;
  }

  if (field.kind === "text") {
    return `<div class="field wide">
      <label for="${id}">${esc(field.label)}</label>
      <textarea id="${id}" data-field="${field.key}" placeholder="${esc(field.placeholder || "")}">${esc(state.values[field.key] || "")}</textarea>
      ${hint}${error}
    </div>`;
  }

  if (field.kind === "int") {
    const value = state.values[field.key];
    const range = field.min != null && field.max != null ? `范围 ${field.min}–${field.max}` : "";
    return `<div class="field">
      <label for="${id}">${esc(field.label)}</label>
      <div class="input-wrap">
        <input type="number" id="${id}" data-field="${field.key}" data-kind="int"
          value="${value ?? ""}" min="${field.min ?? ""}" max="${field.max ?? ""}" step="1" inputmode="numeric" />
        ${field.unit ? `<span class="suffix">${esc(field.unit)}</span>` : ""}
      </div>
      ${hint}${range ? `<div class="hint">${range}</div>` : ""}${error}
    </div>`;
  }

  return `<div class="field">
    <label for="${id}">${esc(field.label)}</label>
    <input type="text" id="${id}" data-field="${field.key}" value="${esc(state.values[field.key] ?? "")}"
      placeholder="${esc(field.placeholder || "")}" />
    ${hint}${error}
  </div>`;
}

function onFieldInput(event) {
  const input = event.target;
  const key = input.dataset.field;
  if (!key) return;
  const field = findField(key);
  if (!field) return;
  let value;
  if (field.kind === "bool") value = input.checked;
  else if (field.kind === "int") value = input.value === "" ? "" : Number(input.value);
  else value = input.value;

  if (field.kind === "secret") {
    const note = document.querySelector(`[data-secret-note="${key}"]`);
    if (note) note.textContent = input.value ? "保存后将替换当前值" : (state.secretsPresent[key] ? "留空表示保持当前值" : "尚未设置");
    if (input.value) state.dirty.add(key);
    else if (!state.secretsPresent[key]) state.dirty.delete(key);
    else state.dirty.delete(key);
    clearFieldError(key);
    updateDirtyUI();
    return;
  }

  if (field.kind === "int" && value !== "") {
    if ((field.min != null && value < field.min) || (field.max != null && value > field.max)) {
      setFieldError(key, `取值需在 ${field.min}–${field.max} 之间`);
    } else {
      clearFieldError(key);
    }
  } else if (field.kind === "int" && value === "") {
    setFieldError(key, "不能为空");
  } else if (field.kind === "text" && field.key === "ack_template" && !String(value).trim()) {
    setFieldError(key, "回执文案不能为空");
  } else {
    clearFieldError(key);
  }

  state.values[key] = value;
  if (isDirty(field, value)) state.dirty.add(key);
  else state.dirty.delete(key);
  updateDirtyUI();
}

function isDirty(field, value) {
  const original = state.original[field.key];
  if (field.kind === "int") return Number(value) !== Number(original);
  if (field.kind === "bool") return Boolean(value) !== Boolean(original);
  return String(value ?? "") !== String(original ?? "");
}

function findField(key) {
  for (const group of state.groups) {
    const found = group.fields.find((field) => field.key === key);
    if (found) return found;
  }
  return null;
}

function setFieldError(key, message) {
  const node = document.querySelector(`[data-error="${key}"]`);
  const input = document.querySelector(`[data-field="${key}"]`);
  if (node) {
    node.hidden = false;
    node.textContent = message;
  }
  if (input) input.classList.add("invalid");
}

function clearFieldError(key) {
  const node = document.querySelector(`[data-error="${key}"]`);
  const input = document.querySelector(`[data-field="${key}"]`);
  if (node) {
    node.hidden = true;
    node.textContent = "";
  }
  if (input) input.classList.remove("invalid");
}

function updateDirtyUI() {
  const count = state.dirty.size;
  updateSaveBar();
  const items = navItems();
  const buttons = $$("#nav [data-nav]");
  buttons.forEach((button) => {
    const item = items.find((entry) => entry.id === button.dataset.nav);
    const badge = item?.group ? groupDirtyCount(item.group) : 0;
    let node = button.querySelector(".badge-count");
    if (badge) {
      if (!node) {
        node = document.createElement("span");
        node.className = "badge-count";
        button.appendChild(node);
      }
      node.textContent = String(badge);
    } else if (node) {
      node.remove();
    }
  });
  const groupHead = document.querySelector(`[data-group="${state.active.replace("group-", "")}"] .group-head .tag.warn`);
  if (groupHead) {
    const group = state.groups.find((entry) => `group-${entry.id}` === state.active);
    if (group && groupDirtyCount(group) === 0) groupHead.remove();
  }
  $("#dirty-note").textContent = count ? `有 ${count} 项未保存的更改` : "";
}

function updateSaveBar() {
  const count = state.dirty.size;
  const bar = $("#savebar");
  bar.hidden = count === 0;
  $("#btn-save").disabled = count === 0 || state.saving;
  $("#btn-save-2").disabled = count === 0 || state.saving;
  $("#btn-save").textContent = state.saving ? "保存中…" : "保存更改";
  $("#btn-save-2").textContent = state.saving ? "保存中…" : "保存更改";
}

function collectPayload() {
  const values = {};
  for (const key of state.dirty) {
    const field = findField(key);
    if (!field) continue;
    if (field.kind === "secret") {
      const input = document.querySelector(`[data-field="${key}"]`);
      const raw = input?.value?.trim() || "";
      if (raw) values[key] = raw;
      else if (input?.dataset.cleared === "1") values[key] = "__clear__";
      continue;
    }
    values[key] = state.values[key];
  }
  return values;
}

function validateDirty() {
  let ok = true;
  for (const key of state.dirty) {
    const field = findField(key);
    if (!field || field.kind === "secret") continue;
    const value = state.values[key];
    if (field.kind === "int") {
      if (value === "" || value === null || Number.isNaN(Number(value))) {
        setFieldError(key, "不能为空");
        ok = false;
      } else if (
        (field.min != null && Number(value) < field.min) ||
        (field.max != null && Number(value) > field.max)
      ) {
        setFieldError(key, `取值需在 ${field.min}–${field.max} 之间`);
        ok = false;
      }
    }
    if (field.kind === "text" && field.key === "ack_template" && !String(value || "").trim()) {
      setFieldError(key, "回执文案不能为空");
      ok = false;
    }
    if (
      field.kind === "string" &&
      ["mimo_base_url", "siliconflow_base_url", "siliconflow_model"].includes(key) &&
      !String(value || "").trim()
    ) {
      setFieldError(key, "不能为空");
      ok = false;
    }
  }
  return ok;
}

async function saveSettings() {
  if (!state.dirty.size || state.saving) return;
  if (!validateDirty()) {
    toast("有字段未通过校验，请先修正", "err");
    return;
  }
  const values = collectPayload();
  if (!Object.keys(values).length) {
    state.dirty.clear();
    updateDirtyUI();
    return;
  }
  state.saving = true;
  updateSaveBar();
  try {
    const result = await apiPost("settings/save", { values });
    if (result.status === "error") throw new Error(result.message || "保存失败");
    if (result.values) {
      state.values = result.values;
      state.original = structuredCloneSafe(result.values);
    }
    if (result.secrets_present) {
      state.secretsPresent = result.secrets_present;
      state.secretsOriginal = structuredCloneSafe(result.secrets_present);
    }
    state.dirty.clear();
    toast(result.message || "已保存");
    await refreshAll();
  } catch (error) {
    showAlert(`保存失败：${error?.message || error}`);
    toast(`保存失败：${error?.message || error}`, "err");
  } finally {
    state.saving = false;
    updateSaveBar();
  }
}

async function resetGroup(groupId) {
  const group = state.groups.find((entry) => entry.id === groupId);
  if (!group) return;
  const keys = group.fields.filter((field) => field.kind !== "secret").map((field) => field.key);
  const ok = await confirmDialog("恢复本组默认", `将把「${group.title}」的 ${keys.length} 项恢复为默认值（密钥不受影响）。`, "恢复默认");
  if (!ok) return;
  try {
    const result = await apiPost("settings/reset", { keys });
    if (result.status === "error") throw new Error(result.message);
    state.values = result.values || state.values;
    state.original = structuredCloneSafe(state.values);
    state.dirty.clear();
    toast(result.message || "已恢复默认");
    await refreshAll();
  } catch (error) {
    toast(`恢复失败：${error?.message || error}`, "err");
  }
}

/* ------------------------------------------------------------------ login */

function renderLogin() {
  const host = $("#view-login");
  const login = state.status?.login || {};
  const account = login.account;
  const loggedIn = Boolean(login.logged_in);
  const qr = state.login;

  const avatarInner = account?.avatar
    ? `<img src="${esc(account.avatar)}" alt="${esc(account?.uname || "B站头像")}" />`
    : esc((account?.uname || "B").slice(0, 1).toUpperCase());
  const accountHtml = loggedIn
    ? `<div class="account-card">
        <div class="avatar">${avatarInner}</div>
        <div class="account-meta">
          <h3>${esc(account?.uname || "已登录")}</h3>
          <p>UID ${esc(account?.mid || "—")} · LV${esc(account?.level ?? "—")} ${account?.vip ? "· 大会员" : ""}</p>
          <p>凭据来源：${login.source === "qr" ? "扫码登录" : "配置文件 SESSDATA"} ${login.sessdata_tail ? `· ${esc(login.sessdata_tail)}` : ""}</p>
        </div>
        <div class="account-side">
          <span class="tag ok">1080P 已解锁</span>
          <button class="btn small ghost" type="button" data-action="logout">退出登录</button>
        </div>
      </div>`
    : `<div class="account-card">
        <div class="avatar" style="background:var(--panel-3);color:var(--muted)">?</div>
        <div class="account-meta">
          <h3>游客模式</h3>
          <p>只能取 360P/480P，字幕与评论接口更容易触发风控。</p>
        </div>
        <div class="account-side"><span class="tag warn">未登录</span></div>
      </div>`;

  const qrHtml = qr.image
    ? `<img id="qr-image" src="${qr.image}" alt="B站登录二维码" />`
    : `<div class="empty" style="padding:40px 12px">${qr.key ? "二维码图片不可用，请使用下方链接" : "点击下方按钮生成二维码"}</div>`;

  const mask = qr.state === "confirmed" ? `<div class="mask">登录成功</div>` :
    qr.state === "expired" ? `<div class="mask">二维码已过期<br/>请刷新</div>` : "";

  host.innerHTML = `
    <div class="login-grid">
      <div class="card">
        <div class="card-head">
          <div><h2>账号</h2><p>扫码成功后凭据写入插件数据目录，优先于设置里的手动 SESSDATA。</p></div>
          <div class="card-actions">
            ${loggedIn ? `<button class="btn small" type="button" data-action="start-login">重新扫码</button>` : ""}
          </div>
        </div>
        ${accountHtml}
        <div class="section-title" style="margin-top:18px">登录能带来什么</div>
        <ul class="login-tips" style="margin-top:12px">
          <li><span class="tick">✓</span><span><b>1080P 画面证据</b>：技能卡、费用数字、结算界面才看得清。</span></li>
          <li><span class="tick">✓</span><span><b>更稳的字幕/评论接口</b>：游客态常见 -101 / -352 风控。</span></li>
          <li><span class="tick">✓</span><span><b>扫码凭据优先</b>：不会覆盖你在设置里手填的 SESSDATA，退出登录后自动回退。</span></li>
          <li><span class="tick">!</span><span><b>建议使用小号</b>：凭据以明文 JSON 保存在插件的 data 目录，请勿打包分享。</span></li>
        </ul>
      </div>

      <div class="card">
        <div class="card-head"><div><h2>扫码登录</h2><p>使用哔哩哔哩手机客户端扫描。</p></div></div>
        <div class="qr-panel">
          <div class="qr-frame">${qrHtml}${mask}</div>
          <div class="qr-status ${qr.state === "confirmed" ? "ok" : qr.state === "expired" || qr.state === "error" ? "warn" : ""}" id="qr-status">
            ${esc(loginQrStatusText())}
          </div>
          <div class="card-actions">
            <button class="btn primary" type="button" data-action="start-login">${qr.key ? "刷新二维码" : "生成登录二维码"}</button>
          </div>
          ${qr.url ? `<div class="qr-url" id="qr-url">${esc(qr.url)}</div>
            <div class="card-actions"><button class="btn small ghost" type="button" data-action="copy" data-copy="${esc(qr.url)}">复制链接</button></div>` : ""}
        </div>
      </div>
    </div>
  `;

  host.querySelectorAll("[data-action]").forEach((node) => node.addEventListener("click", handleAction));
}

function loginQrStatusText() {
  const qr = state.login;
  if (!qr.key) return "尚未生成二维码";
  if (qr.state === "confirmed") return "登录成功，正在刷新账号信息…";
  if (qr.state === "expired") return "二维码已过期，请刷新";
  if (qr.state === "error") return qr.message || "轮询失败，请重试";
  if (qr.state === "scanned") return "已扫码，请在手机上确认登录";
  const left = Math.max(0, Math.ceil(qr.expiresAt - Date.now() / 1000));
  return `等待扫码 · 剩余 ${left}s`;
}

async function startLogin() {
  stopLoginTimers();
  state.login = { key: "", url: "", image: "", timer: null, countdown: null, expiresAt: 0, state: "loading", message: "" };
  renderLogin();
  try {
    const result = await apiPost("login/qr");
    if (result.status === "error") throw new Error(result.message);
    state.login.key = result.key;
    state.login.url = result.url || "";
    state.login.image = result.image || "";
    state.login.state = "waiting";
    state.login.expiresAt = Date.now() / 1000 + Number(result.expires_in || 180);
    renderLogin();
    state.login.countdown = window.setInterval(() => {
      if (Date.now() / 1000 >= state.login.expiresAt) {
        state.login.state = "expired";
        stopLoginTimers(false);
        renderLogin();
        return;
      }
      const node = $("#qr-status");
      if (node) node.textContent = loginQrStatusText();
    }, 1000);
    state.login.timer = window.setInterval(pollLogin, 2500);
  } catch (error) {
    state.login.state = "error";
    state.login.message = error?.message || String(error);
    renderLogin();
    toast(`申请二维码失败：${error?.message || error}`, "err");
  }
}

async function pollLogin() {
  const key = state.login.key;
  if (!key) return;
  try {
    const result = await apiGet("login/status", { key });
    if (result.status === "error") {
      throw new Error(result.message || "轮询失败");
    }
    if (result.state === "confirmed") {
      state.login.state = "confirmed";
      stopLoginTimers();
      toast("登录成功，1080P 已解锁");
      await refreshAll();
      loadAccount();
      renderLogin();
      return;
    }
    if (result.state === "expired") {
      state.login.state = "expired";
      state.login.message = result.message;
      stopLoginTimers();
      renderLogin();
      return;
    }
    if (result.state === "scanned") {
      state.login.state = "scanned";
    }
    const node = $("#qr-status");
    if (node) node.textContent = loginQrStatusText();
  } catch (error) {
    const node = $("#qr-status");
    if (node) node.textContent = `轮询失败：${error?.message || error}`;
  }
}

function stopLoginTimers(clear = true) {
  if (state.login.timer) window.clearInterval(state.login.timer);
  if (state.login.countdown) window.clearInterval(state.login.countdown);
  state.login.timer = null;
  state.login.countdown = null;
  if (clear) state.login.key = state.login.key || "";
}

async function loadAccount() {
  if (!state.status?.login?.logged_in) return;
  try {
    const result = await apiGet("login/account");
    if (result.account) {
      state.status.login.account = result.account;
      state.status.login.source = result.source || state.status.login.source;
      updateLoginPill();
      if (state.active === "login") renderLogin();
      if (state.active === "overview") renderOverview();
    }
  } catch {
    /* 静默失败：不阻塞页面 */
  }
}

async function logout() {
  const ok = await confirmDialog("退出登录", "将清除本机保存的扫码凭据。若设置里手填了 SESSDATA，会回退到该凭据。", "退出登录");
  if (!ok) return;
  try {
    const result = await apiPost("login/logout");
    if (result.status === "error") throw new Error(result.message);
    toast("已清除扫码凭据");
    await refreshAll();
  } catch (error) {
    toast(`退出失败：${error?.message || error}`, "err");
  }
}

/* ------------------------------------------------------------------ cache */

async function loadCache() {
  try {
    state.cache = await apiGet("cache");
    renderCache();
  } catch (error) {
    toast(`读取缓存失败：${error?.message || error}`, "err");
  }
}

function renderCache() {
  const host = $("#view-cache");
  const cache = state.cache;
  if (!cache) {
    host.innerHTML = `<div class="card"><div class="empty">正在统计缓存…</div></div>`;
    return;
  }
  const total = Math.max(1, cache.total_bytes);
  const rows = (cache.items || []).map((item) => {
    const pct = Math.min(100, Math.round((item.bytes / total) * 100));
    return `<div class="cache-row">
      <div class="name">${esc(cacheLabel(item.name))}<small>${esc(item.desc || "")}</small></div>
      <div class="bar"><i style="width:${pct}%"></i></div>
      <div class="size">${fmtBytes(item.bytes)} · ${item.files} 个文件</div>
    </div>`;
  }).join("");

  host.innerHTML = `
    <div class="card">
      <div class="card-head">
        <div><h2>缓存占用</h2><p>截图与原文缓存都可以随时删除，不影响插件配置与登录凭据。</p></div>
        <div class="card-actions">
          <button class="btn small ghost" type="button" data-action="refresh-cache">刷新</button>
          <button class="btn small danger" type="button" data-action="cleanup-all">清理全部缓存</button>
        </div>
      </div>
      <div class="cache-list">${rows || '<div class="empty">暂无缓存</div>'}</div>
      <div class="cache-total"><span>合计 ${cache.total_files} 个文件</span><strong>${fmtBytes(cache.total_bytes)}</strong></div>
      <div class="card-actions" style="margin-top:14px">
        <button class="btn small ghost" type="button" data-action="cleanup" data-target="frames">清理截图</button>
        <button class="btn small ghost" type="button" data-action="cleanup" data-target="transcripts">清理原文</button>
        <button class="btn small ghost" type="button" data-action="cleanup" data-target="qrcode">清理二维码</button>
        <button class="btn small ghost" type="button" data-action="cleanup" data-target="cache">清理临时音频</button>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><div><h2>缓存说明</h2><p>为什么删了也不影响已生成的报告？</p></div></div>
      <dl class="kv">
        <dt>frames</dt><dd>场景联表、HUD 时间带、按时间点截取的整帧；默认只保留最近 ${state.values.keep_frame_dirs || 6} 个视频。</dd>
        <dt>transcripts</dt><dd>字幕/ASR 原文，供 <code>bilibili_transcript</code> 二次查询；过期时间由「缓存与原文」页控制。</dd>
        <dt>qrcode</dt><dd>扫码登录时本地渲染的二维码 PNG，用完即删，通常为空。</dd>
        <dt>cache</dt><dd>ASR 分块抽出的临时 mp3，转录完成后就会删除，异常中断时可能残留。</dd>
      </dl>
    </div>
  `;
  host.querySelectorAll("[data-action]").forEach((node) => node.addEventListener("click", handleAction));
}

function cacheLabel(name) {
  return { frames: "截图与证据图", transcripts: "字幕/ASR 原文", qrcode: "登录二维码", cache: "ASR 临时音频" }[name] || name;
}

async function cleanupCache(target) {
  const label = target === "all" ? "全部缓存" : cacheLabel(target);
  const ok = await confirmDialog("清理缓存", `将删除${label}。已生成的报告与配置不受影响，确定继续？`, "清理");
  if (!ok) return;
  try {
    const result = await apiPost("cache/cleanup", { target });
    if (result.status === "error") throw new Error(result.message);
    state.cache = result.cache || state.cache;
    renderCache();
    toast(`已释放 ${fmtBytes(result.freed_bytes)}（${result.removed_files} 个文件）`);
  } catch (error) {
    toast(`清理失败：${error?.message || error}`, "err");
  }
}

/* ------------------------------------------------------------------ diagnostics */

async function loadDiagnostics() {
  try {
    state.diagnostics = await apiGet("diagnostics");
    renderDiagnostics();
  } catch (error) {
    toast(`读取诊断信息失败：${error?.message || error}`, "err");
  }
}

function renderDiagnostics() {
  const host = $("#view-diagnostics");
  const info = state.diagnostics;
  if (!info) {
    host.innerHTML = `<div class="card"><div class="empty">正在读取运行环境…</div></div>`;
    return;
  }
  const rows = [
    ["插件版本", `v${state.status?.version || "—"}`],
    ["Python", info.python],
    ["平台", info.platform],
    ["工作目录", info.cwd],
    ["数据目录", info.cache_root],
    ["配置文件", info.config_path],
    ["FFmpeg", info.ffmpeg?.available ? info.ffmpeg.version : info.ffmpeg?.version || "未找到"],
    ["Dashboard 用户", info.client_user || "—"],
  ];
  host.innerHTML = `
    <div class="card">
      <div class="card-head">
        <div><h2>运行环境</h2><p>反馈问题时请附上这一页（不含任何密钥）。</p></div>
        <div class="card-actions"><button class="btn small ghost" type="button" data-action="copy-diagnostics">复制诊断信息</button></div>
      </div>
      <dl class="kv">
        ${rows.map(([key, value]) => `<dt>${esc(key)}</dt><dd><code>${esc(value ?? "—")}</code></dd>`).join("")}
      </dl>
    </div>
    <div class="card">
      <div class="card-head"><div><h2>已注册接口</h2><p>插件向 Dashboard 注册的 Web API 路由。</p></div></div>
      ${(info.registered_apis || []).length
        ? `<div class="table-wrap"><table class="table"><tbody>${info.registered_apis.map((route) => `<tr><td><code>${esc(route)}</code></td></tr>`).join("")}</tbody></table></div>`
        : '<div class="empty">没有注册任何接口</div>'}
    </div>
    <div class="card">
      <div class="card-head"><div><h2>恢复默认</h2><p>把所有非密钥配置恢复为默认值；密钥不会被清空。</p></div></div>
      <button class="btn danger" type="button" data-action="reset-all">恢复全部默认值</button>
    </div>
  `;
  host.querySelectorAll("[data-action]").forEach((node) => node.addEventListener("click", handleAction));
}

/* ------------------------------------------------------------------ actions */

async function handleAction(event) {
  const node = event.currentTarget;
  const action = node.dataset.action;
  if (action === "copy") {
    const text = node.dataset.copy || "";
    const ok = await copyText(text);
    toast(ok ? "已复制" : "复制失败，请手动选择", ok ? "ok" : "err");
    return;
  }
  if (action === "refresh-videos") {
    try {
      const result = await apiGet("videos");
      state.videos = result.items || [];
      renderOverview();
      toast("已刷新");
    } catch (error) {
      toast(`刷新失败：${error?.message || error}`, "err");
    }
    return;
  }
  if (action === "reset-group") {
    await resetGroup(node.dataset.group);
    return;
  }
  if (action === "clear-secret") {
    const key = node.dataset.key;
    const input = document.querySelector(`[data-field="${key}"]`);
    if (input) {
      input.value = "";
      input.dataset.cleared = "1";
      input.placeholder = "保存后将清除";
    }
    const note = document.querySelector(`[data-secret-note="${key}"]`);
    if (note) note.textContent = "保存后将清除当前值";
    state.dirty.add(key);
    updateDirtyUI();
    return;
  }
  if (action === "start-login") {
    await startLogin();
    return;
  }
  if (action === "logout") {
    await logout();
    return;
  }
  if (action === "refresh-cache") {
    await loadCache();
    return;
  }
  if (action === "cleanup") {
    await cleanupCache(node.dataset.target);
    return;
  }
  if (action === "cleanup-all") {
    await cleanupCache("all");
    return;
  }
  if (action === "copy-diagnostics") {
    const info = state.diagnostics || {};
    const text = [
      `version: ${state.status?.version || ""}`,
      `python: ${info.python || ""}`,
      `platform: ${info.platform || ""}`,
      `data: ${info.cache_root || ""}`,
      `config: ${info.config_path || ""}`,
      `ffmpeg: ${info.ffmpeg?.version || ""}`,
      `apis: ${(info.registered_apis || []).join(", ")}`,
    ].join("\n");
    try {
      const ok = await copyText(text);
      toast(ok ? "诊断信息已复制" : "复制失败", ok ? "ok" : "err");
    } catch {
      toast("复制失败", "err");
    }
    return;
  }
  if (action === "reset-all") {
    const ok = await confirmDialog("恢复全部默认值", "所有非密钥配置将恢复默认值，密钥不受影响。", "恢复默认");
    if (!ok) return;
    try {
      const result = await apiPost("settings/reset", {});
      if (result.status === "error") throw new Error(result.message);
      toast(result.message || "已恢复默认");
      await refreshAll();
    } catch (error) {
      toast(`恢复失败：${error?.message || error}`, "err");
    }
  }
}

/* ------------------------------------------------------------------ start */
boot().catch((error) => {
  showAlert(`页面初始化失败：${error?.message || error}`);
  console.error(error);
});
