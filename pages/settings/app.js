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
};

function loadThemePreference() { try { const s = window.localStorage.getItem(THEME_KEY); if (["light","dark","auto"].includes(s)) return s; } catch {} return "auto"; }
function saveThemePreference() { try { window.localStorage.setItem(THEME_KEY, themePreference); } catch {} }
function themeButtonLabel() { return themePreference === "dark" ? "主题：深色" : themePreference === "light" ? "主题：浅色" : "主题：自动"; }
function applyTheme() { let e = themePreference; if (e === "auto" && themeMediaQuery) e = themeMediaQuery.matches ? "dark" : "light"; root.setAttribute("data-theme", e); if (els.toggleThemeBtn) els.toggleThemeBtn.textContent = themeButtonLabel(); }
function cycleTheme() { themePreference = themePreference === "auto" ? "light" : themePreference === "light" ? "dark" : "auto"; saveThemePreference(); applyTheme(); }
function toast(msg, kind = "info") { if (!els.toastLayer) return; const n = document.createElement("div"); n.className = `toast toast-${kind}`; n.textContent = msg; els.toastLayer.appendChild(n); requestAnimationFrame(() => n.classList.add("show")); setTimeout(() => { n.classList.remove("show"); setTimeout(() => n.remove(), 220); }, 2600); }
function val(id) { return document.getElementById(id)?.value ?? ""; }
function checked(id) { return Boolean(document.getElementById(id)?.checked); }
function sortGroups(list) { return [...list].sort((a, b) => (Number(b.config_updated_at || 0) - Number(a.config_updated_at || 0)) || String(a.group_name || a.group_id || "").localeCompare(String(b.group_name || b.group_id || ""), "zh-Hans-CN")); }

function renderGroupList() {
  const kw = String(els.groupSearchInput?.value || "").trim().toLowerCase();
  const visible = sortGroups(groups).filter(g => !kw || `${g.group_name || ""} ${g.group_id || ""}`.toLowerCase().includes(kw));
  els.groupList.innerHTML = "";
  if (!visible.length) { els.groupList.classList.add("empty-hint"); els.groupList.textContent = "未找到群聊。"; return; }
  els.groupList.classList.remove("empty-hint");
  visible.forEach(group => {
    const card = document.createElement("button"); card.type = "button"; card.className = "group-item";
    if (String(currentGroup?.group_info?.group_id || "") === String(group.group_id)) card.classList.add("active");
    card.addEventListener("click", () => loadGroupConfig(group.group_id));
    const avatar = document.createElement("img"); avatar.className = "group-avatar"; avatar.src = group.avatar || ""; avatar.alt = group.group_name || group.group_id; avatar.onerror = () => { avatar.style.display = "none"; };
    const body = document.createElement("span"); body.className = "group-item-body";
    const name = document.createElement("span"); name.className = "group-name"; name.textContent = group.group_name || `群 ${group.group_id}`;
    const meta = document.createElement("span"); meta.className = "group-meta"; const t = Number(group.config_updated_at || 0); meta.textContent = t ? `群号：${group.group_id} · 最近配置：${new Date(t).toLocaleString()}` : `群号：${group.group_id}`;
    body.appendChild(name); body.appendChild(meta); card.appendChild(avatar); card.appendChild(body); els.groupList.appendChild(card);
  });
}

function boolField(id, label, hint, value) {
  const f = document.createElement("div"); f.className = "field field-bool";
  f.innerHTML = `<div><div class="field-label">${label}</div><div class="field-hint">${hint}</div></div><label class="switch"><input id="${id}" type="checkbox" ${value ? "checked" : ""} /><span class="switch-slider"></span></label>`;
  return f;
}
function textField(label, hint, inputHtml) {
  const f = document.createElement("div"); f.className = "field";
  f.innerHTML = `<div class="field-label">${label}</div><div class="field-hint">${hint}</div>${inputHtml}`;
  return f;
}

function renderGroupForm(payload) {
  currentGroup = payload;
  const info = payload.group_info || {};
  const c = payload.config || {};
  els.currentGroupTitle.textContent = info.group_name || `群 ${info.group_id || ""}`;
  els.currentGroupMeta.textContent = info.group_id ? `群号：${info.group_id}` : "请选择左侧群聊";
  els.groupForm.innerHTML = "";
  els.groupForm.classList.remove("empty-hint");

  const sectionTitle = (text) => { const d = document.createElement("div"); d.className = "section-title"; d.textContent = text; return d; };

  els.groupForm.appendChild(sectionTitle("基本"));
  els.groupForm.appendChild(boolField("enabled", "启用该群截图任务", "开启后按间隔截图并推送到该群。", c.enabled === true));
  els.groupForm.appendChild(textField("任务名称", "用于消息模板和日志。", `<input id="nameInput" value="${c.name || "网页截图"}" />`));
  els.groupForm.appendChild(textField("网页地址", "填写完整 URL。", `<input id="urlInput" value="${c.url || ""}" placeholder="https://example.com" />`));
  els.groupForm.appendChild(textField("推送间隔分钟", "按系统时间对齐触发。", `<input id="intervalInput" type="number" min="1" value="${c.interval_minutes ?? 60}" />`));

  els.groupForm.appendChild(sectionTitle("消息"));
  els.groupForm.appendChild(boolField("sendText", "发送截图文字", "开启后截图前附带文本。", c.send_text !== false));
  els.groupForm.appendChild(textField("截图文字", "支持 {name} 和 {url}。留空使用默认。", `<textarea id="screenshotTextInput" rows="3" spellcheck="false">${c.screenshot_text || ""}</textarea>`));
  els.groupForm.appendChild(boolField("mentionAndQuote", "引用和艾特发送人", "手动状态截图时引用并艾特。", Boolean(c.mention_and_quote_sender)));
  els.groupForm.appendChild(boolField("notifyFailure", "失败通知", "定时截图失败时向该群通知。", c.notify_on_failure !== false));

  els.groupForm.appendChild(sectionTitle("截图参数"));
  els.groupForm.appendChild(textField("浏览器尺寸与等待", "宽、高、等待秒、超时秒。", `<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px"><input id="widthInput" type="number" value="${c.viewport_width ?? 1280}" /><input id="heightInput" type="number" value="${c.viewport_height ?? 720}" /><input id="waitInput" type="number" step="0.5" value="${c.wait_seconds ?? 5}" /><input id="timeoutInput" type="number" step="1" value="${c.timeout_seconds ?? 30}" /></div>`));

  renderGroupList();
}

function collectGroupForm() {
  return {
    name: val("nameInput"), enabled: checked("enabled"), url: val("urlInput"),
    interval_minutes: Number(val("intervalInput") || 60),
    send_text: checked("sendText"), screenshot_text: val("screenshotTextInput"),
    mention_and_quote_sender: checked("mentionAndQuote"), notify_on_failure: checked("notifyFailure"),
    viewport_width: Number(val("widthInput") || 1280), viewport_height: Number(val("heightInput") || 720),
    wait_seconds: Number(val("waitInput") || 5), timeout_seconds: Number(val("timeoutInput") || 30),
  };
}

async function refreshGroups(opts = {}) { groups = await api.safePost("settings/groups/refresh", {}); renderGroupList(); if (!opts.silent) toast("群列表已同步", "success"); }
async function loadGroupConfig(groupId) { renderGroupForm(await api.safeGet("settings/group", { group_id: String(groupId || "") })); }
async function saveGroupConfig() {
  const gid = String(currentGroup?.group_info?.group_id || ""); if (!gid) { toast("请先选择群聊", "error"); return; }
  els.saveGroupBtn.disabled = true;
  try { renderGroupForm(await api.safePost("settings/group", { group_id: gid, config: collectGroupForm() })); await refreshGroups({ silent: true }); toast("配置已保存", "success"); }
  catch (e) { toast("保存失败：" + e.message, "error"); } finally { els.saveGroupBtn.disabled = false; }
}
async function resetGroupConfig() {
  const gid = String(currentGroup?.group_info?.group_id || ""); if (!gid) { toast("请先选择群聊", "error"); return; }
  if (!confirm("确定清空该群配置吗？")) return;
  els.resetGroupBtn.disabled = true;
  try { renderGroupForm(await api.safePost("settings/group/reset", { group_id: gid })); await refreshGroups({ silent: true }); toast("已清空", "success"); }
  catch (e) { toast("重置失败：" + e.message, "error"); } finally { els.resetGroupBtn.disabled = false; }
}
async function loadBootstrap() {
  els.groupList.classList.add("empty-hint"); els.groupList.textContent = "群列表同步中…";
  els.groupForm.classList.add("empty-hint"); els.groupForm.textContent = "请从左侧选择一个群聊。";
  const data = await api.safeGet("settings/bootstrap");
  groups = data.groups || [];
  await refreshGroups({ silent: true });
  const first = sortGroups(groups)[0]; if (first) await loadGroupConfig(first.group_id);
}
function bindEvents() {
  els.groupSearchInput?.addEventListener("input", renderGroupList);
  els.refreshGroupsBtn?.addEventListener("click", () => refreshGroups({ silent: false }));
  els.saveGroupBtn?.addEventListener("click", saveGroupConfig);
  els.resetGroupBtn?.addEventListener("click", resetGroupConfig);
  els.toggleThemeBtn?.addEventListener("click", cycleTheme);
}
function init() { applyTheme(); bindEvents(); if (!bridge) { els.groupForm.classList.add("empty-hint"); els.groupForm.textContent = "无法获取页面桥接。"; return; } try { api = createApi(bridge); } catch (e) { els.groupForm.classList.add("empty-hint"); els.groupForm.textContent = "初始化失败：" + e.message; return; } loadBootstrap().catch(e => { els.groupList.classList.add("empty-hint"); els.groupList.textContent = "加载失败：" + e.message; toast("加载失败：" + e.message, "error"); }); }
init();
