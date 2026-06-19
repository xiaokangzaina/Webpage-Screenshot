import { createApi } from "./api.js";

const bridge = window.AstrBotPluginPage;
let api = null;
const root = document.documentElement;
const themeMediaQuery = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
const THEME_KEY = "webpage-screenshot-theme";
let themePreference = loadThemePreference();
let groups = [];
let currentGroup = null;

const els = {
  groupList: document.getElementById("groupList"),
  groupForm: document.getElementById("groupForm"),
  groupSearchInput: document.getElementById("groupSearchInput"),
  currentGroupTitle: document.getElementById("currentGroupTitle"),
  currentGroupMeta: document.getElementById("currentGroupMeta"),
  toastLayer: document.getElementById("toastLayer"),
  toggleThemeBtn: document.getElementById("toggleThemeBtn"),
  refreshGroupsBtn: document.getElementById("refreshGroupsBtn"),
  resetGroupBtn: document.getElementById("resetGroupBtn"),
  saveGroupBtn: document.getElementById("saveGroupBtn"),
  totalGroupsMetric: document.getElementById("totalGroupsMetric"),
  enabledGroupsMetric: document.getElementById("enabledGroupsMetric"),
  currentIntervalMetric: document.getElementById("currentIntervalMetric"),
  currentUrlMetric: document.getElementById("currentUrlMetric"),
  groupCountPill: document.getElementById("groupCountPill"),
  enabledBadge: document.getElementById("enabledBadge"),
  previewUrl: document.getElementById("previewUrl"),
  previewName: document.getElementById("previewName"),
  previewText: document.getElementById("previewText"),
  previewSize: document.getElementById("previewSize"),
  previewWait: document.getElementById("previewWait"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function loadThemePreference() {
  try {
    const saved = window.localStorage.getItem(THEME_KEY);
    if (["light", "dark", "auto"].includes(saved)) return saved;
  } catch {}
  return "auto";
}
function saveThemePreference() { try { window.localStorage.setItem(THEME_KEY, themePreference); } catch {} }
async function persistThemePreference() {
  saveThemePreference();
  if (!api) return;
  try { await api.safePost("settings/theme", { theme: themePreference }); }
  catch (e) { toast("主题已本地保存，远端保存失败：" + e.message, "error"); }
}
function themeButtonLabel() { return themePreference === "dark" ? "主题：深色" : themePreference === "light" ? "主题：浅色" : "主题：自动"; }
function applyTheme() {
  let effective = themePreference;
  if (effective === "auto" && themeMediaQuery) effective = themeMediaQuery.matches ? "dark" : "light";
  root.setAttribute("data-theme", effective);
  if (els.toggleThemeBtn) els.toggleThemeBtn.textContent = themeButtonLabel();
}
async function cycleTheme() {
  themePreference = themePreference === "auto" ? "light" : themePreference === "light" ? "dark" : "auto";
  applyTheme();
  await persistThemePreference();
}

function toast(msg, kind = "info") {
  if (!els.toastLayer) return;
  const node = document.createElement("div");
  node.className = `toast toast-${kind}`;
  node.textContent = msg;
  els.toastLayer.appendChild(node);
  requestAnimationFrame(() => node.classList.add("show"));
  setTimeout(() => { node.classList.remove("show"); setTimeout(() => node.remove(), 240); }, 2800);
}
function val(id) { return document.getElementById(id)?.value ?? ""; }
function checked(id) { return Boolean(document.getElementById(id)?.checked); }
function sortGroups(list) {
  return [...list].sort((a, b) =>
    (Number(b.config_updated_at || 0) - Number(a.config_updated_at || 0)) ||
    String(a.group_name || a.group_id || "").localeCompare(String(b.group_name || b.group_id || ""), "zh-Hans-CN")
  );
}
function domainOf(url) {
  try { return new URL(url).hostname || url; } catch { return String(url || "未配置"); }
}

function updateMetrics(config = currentGroup?.config || {}) {
  const configuredCount = groups.filter(g => Number(g.config_updated_at || 0) > 0).length;
  els.totalGroupsMetric.textContent = String(groups.length || 0);
  els.enabledGroupsMetric.textContent = String(configuredCount || 0);
  els.groupCountPill.textContent = String(groups.length || 0);
  els.currentIntervalMetric.textContent = currentGroup ? `${Number(config.interval_minutes || 60)} 分钟` : "--";
  els.currentUrlMetric.textContent = currentGroup ? domainOf(config.url) : "未选择";
  els.enabledBadge.textContent = currentGroup ? (config.enabled ? "已启用" : "未启用") : "未选择";
  els.enabledBadge.classList.toggle("on", Boolean(currentGroup && config.enabled));
}

function renderGroupList() {
  const keyword = String(els.groupSearchInput?.value || "").trim().toLowerCase();
  const visible = sortGroups(groups).filter(g => !keyword || `${g.group_name || ""} ${g.group_id || ""}`.toLowerCase().includes(keyword));
  els.groupList.innerHTML = "";
  els.groupList.classList.remove("skeleton");
  if (!visible.length) {
    els.groupList.classList.add("empty-state");
    els.groupList.textContent = keyword ? "没有匹配的群聊。" : "暂无可配置群聊。";
    updateMetrics();
    return;
  }
  els.groupList.classList.remove("empty-state");
  visible.forEach(group => {
    const active = String(currentGroup?.group_info?.group_id || "") === String(group.group_id);
    const time = Number(group.config_updated_at || 0);
    const card = document.createElement("button");
    card.type = "button";
    card.className = `group-item${active ? " active" : ""}`;
    card.addEventListener("click", () => loadGroupConfig(group.group_id));
    card.innerHTML = `
      <span class="group-avatar-wrap"><img class="group-avatar" src="${escapeHtml(group.avatar || "")}" alt="" /></span>
      <span class="group-item-body">
        <span class="group-line"><b>${escapeHtml(group.group_name || `群 ${group.group_id}`)}</b>${time ? "<em>已配置</em>" : ""}</span>
        <span class="group-meta">群号：${escapeHtml(group.group_id)}${time ? ` · ${new Date(time).toLocaleString()}` : ""}</span>
      </span>`;
    const img = card.querySelector("img");
    img.onerror = () => { img.remove(); card.querySelector(".group-avatar-wrap").textContent = "群"; };
    els.groupList.appendChild(card);
  });
  updateMetrics();
}

function sectionTitle(icon, title, desc) {
  const node = document.createElement("div");
  node.className = "section-title";
  node.innerHTML = `<span>${icon}</span><div><b>${title}</b><small>${desc}</small></div>`;
  return node;
}
function boolField(id, label, hint, value) {
  const field = document.createElement("div");
  field.className = "field-card field-bool";
  field.innerHTML = `
    <div><div class="field-label">${escapeHtml(label)}</div><div class="field-hint">${escapeHtml(hint)}</div></div>
    <label class="switch"><input id="${id}" type="checkbox" ${value ? "checked" : ""} /><span class="switch-slider"></span></label>`;
  field.querySelector("input")?.addEventListener("change", updateLivePreview);
  return field;
}
function inputField(label, hint, inputHtml, wide = false) {
  const field = document.createElement("div");
  field.className = `field-card${wide ? " wide" : ""}`;
  field.innerHTML = `<div class="field-label">${label}</div><div class="field-hint">${hint}</div>${inputHtml}`;
  field.querySelectorAll("input, textarea").forEach(node => node.addEventListener("input", updateLivePreview));
  return field;
}
function numberGrid(config) {
  return `
    <div class="number-grid">
      <label><span>宽度</span><input id="widthInput" type="number" min="320" value="${Number(config.viewport_width ?? 1280)}" /></label>
      <label><span>高度</span><input id="heightInput" type="number" min="240" value="${Number(config.viewport_height ?? 720)}" /></label>
      <label><span>等待秒数</span><input id="waitInput" type="number" step="0.5" min="0" value="${Number(config.wait_seconds ?? 5)}" /></label>
      <label><span>超时秒数</span><input id="timeoutInput" type="number" step="1" min="1" value="${Number(config.timeout_seconds ?? 30)}" /></label>
    </div>`;
}

function renderGroupForm(payload) {
  currentGroup = payload;
  const info = payload.group_info || {};
  const config = payload.config || {};
  els.currentGroupTitle.textContent = info.group_name || `群 ${info.group_id || ""}`;
  els.currentGroupMeta.textContent = info.group_id ? `群号：${info.group_id}` : "请选择左侧群聊";
  els.groupForm.innerHTML = "";
  els.groupForm.classList.remove("empty-state");

  els.groupForm.appendChild(sectionTitle("⚙️", "基础任务", "控制该群是否启用、截图网页和推送频率。"));
  els.groupForm.appendChild(boolField("enabled", "启用该群截图任务", "开启后按间隔截图并推送到该群。", config.enabled === true));
  els.groupForm.appendChild(inputField("任务名称", "用于消息模板和日志识别。", `<input id="nameInput" value="${escapeHtml(config.name || "网页截图")}" />`));
  els.groupForm.appendChild(inputField("网页地址", "填写完整 URL，例如 https://example.com。", `<input id="urlInput" value="${escapeHtml(config.url || "")}" placeholder="https://example.com" />`, true));
  els.groupForm.appendChild(inputField("推送间隔", "按系统时间对齐触发。", `<div class="unit-input"><input id="intervalInput" type="number" min="1" value="${Number(config.interval_minutes ?? 60)}" /><span>分钟</span></div>`));

  els.groupForm.appendChild(sectionTitle("💬", "消息行为", "配置截图前文本、失败通知和手动状态截图行为。"));
  els.groupForm.appendChild(boolField("sendText", "发送截图文字", "开启后截图前附带文本。", config.send_text !== false));
  els.groupForm.appendChild(inputField("截图文字", "支持 {name} 和 {url}。留空使用默认。", `<textarea id="screenshotTextInput" rows="4" spellcheck="false">${escapeHtml(config.screenshot_text || "")}</textarea>`, true));
  els.groupForm.appendChild(boolField("mentionAndQuote", "引用和艾特发送人", "手动状态截图时引用并艾特。", Boolean(config.mention_and_quote_sender)));
  els.groupForm.appendChild(boolField("notifyFailure", "失败通知", "定时截图失败时向该群通知。", config.notify_on_failure !== false));

  els.groupForm.appendChild(sectionTitle("🖥️", "渲染参数", "调整 Chromium 浏览器窗口、等待和超时时间。"));
  els.groupForm.appendChild(inputField("浏览器尺寸与等待", "宽、高、等待秒、超时秒。", numberGrid(config), true));

  updateLivePreview();
  renderGroupList();
}

function collectGroupForm() {
  return {
    name: val("nameInput"),
    enabled: checked("enabled"),
    url: val("urlInput"),
    interval_minutes: Number(val("intervalInput") || 60),
    send_text: checked("sendText"),
    screenshot_text: val("screenshotTextInput"),
    mention_and_quote_sender: checked("mentionAndQuote"),
    notify_on_failure: checked("notifyFailure"),
    viewport_width: Number(val("widthInput") || 1280),
    viewport_height: Number(val("heightInput") || 720),
    wait_seconds: Number(val("waitInput") || 5),
    timeout_seconds: Number(val("timeoutInput") || 30),
  };
}

function updateLivePreview() {
  if (!currentGroup) { updateMetrics(); return; }
  const config = collectGroupForm();
  const url = config.url || "https://example.com";
  const name = config.name || "网页截图";
  const text = config.screenshot_text || `${name} 网页截图`;
  els.previewUrl.textContent = domainOf(url);
  els.previewName.textContent = name;
  els.previewText.textContent = config.send_text ? text.replaceAll("{name}", name).replaceAll("{url}", url) : "已关闭截图文字，将仅发送图片。";
  els.previewSize.textContent = `${config.viewport_width || 1280} × ${config.viewport_height || 720}`;
  els.previewWait.textContent = `等待 ${config.wait_seconds || 0}s · 超时 ${config.timeout_seconds || 30}s`;
  updateMetrics(config);
}

async function refreshGroups(opts = {}) {
  els.refreshGroupsBtn.disabled = true;
  try {
    groups = await api.safePost("settings/groups/refresh", {});
    renderGroupList();
    if (!opts.silent) toast("群列表已同步", "success");
  } finally {
    els.refreshGroupsBtn.disabled = false;
  }
}
async function loadGroupConfig(groupId) { renderGroupForm(await api.safeGet("settings/group", { group_id: String(groupId || "") })); }
async function saveGroupConfig() {
  const gid = String(currentGroup?.group_info?.group_id || "");
  if (!gid) { toast("请先选择群聊", "error"); return; }
  els.saveGroupBtn.disabled = true;
  try {
    renderGroupForm(await api.safePost("settings/group", { group_id: gid, config: collectGroupForm() }));
    await refreshGroups({ silent: true });
    toast("配置已保存", "success");
  } catch (e) {
    toast("保存失败：" + e.message, "error");
  } finally {
    els.saveGroupBtn.disabled = false;
  }
}
async function resetGroupConfig() {
  const gid = String(currentGroup?.group_info?.group_id || "");
  if (!gid) { toast("请先选择群聊", "error"); return; }
  if (!confirm("确定清空该群配置吗？")) return;
  els.resetGroupBtn.disabled = true;
  try {
    renderGroupForm(await api.safePost("settings/group/reset", { group_id: gid }));
    await refreshGroups({ silent: true });
    toast("已清空", "success");
  } catch (e) {
    toast("重置失败：" + e.message, "error");
  } finally {
    els.resetGroupBtn.disabled = false;
  }
}
async function loadBootstrap() {
  els.groupList.classList.add("skeleton");
  els.groupList.textContent = "群列表同步中…";
  els.groupForm.classList.add("empty-state");
  els.groupForm.textContent = "请从左侧选择一个群聊。";
  const data = await api.safeGet("settings/bootstrap");
  const savedTheme = data.config?.page_theme;
  if (["auto", "light", "dark"].includes(savedTheme)) {
    themePreference = savedTheme;
    saveThemePreference();
    applyTheme();
  }
  groups = data.groups || [];
  await refreshGroups({ silent: true });
  const first = sortGroups(groups)[0];
  if (first) await loadGroupConfig(first.group_id);
  else updateMetrics();
}
function bindEvents() {
  els.groupSearchInput?.addEventListener("input", renderGroupList);
  els.refreshGroupsBtn?.addEventListener("click", () => refreshGroups({ silent: false }));
  els.saveGroupBtn?.addEventListener("click", saveGroupConfig);
  els.resetGroupBtn?.addEventListener("click", resetGroupConfig);
  els.toggleThemeBtn?.addEventListener("click", cycleTheme);
  themeMediaQuery?.addEventListener?.("change", applyTheme);
}
function init() {
  applyTheme();
  bindEvents();
  if (!bridge) {
    els.groupForm.classList.add("empty-state");
    els.groupForm.textContent = "无法获取 AstrBot 页面桥接。";
    return;
  }
  try { api = createApi(bridge); }
  catch (e) { els.groupForm.classList.add("empty-state"); els.groupForm.textContent = "初始化失败：" + e.message; return; }
  loadBootstrap().catch(e => {
    els.groupList.classList.add("empty-state");
    els.groupList.textContent = "加载失败：" + e.message;
    toast("加载失败：" + e.message, "error");
  });
}

init();
