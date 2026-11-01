"use strict";
// 群聊情报站控制台：仿 ChatLab 会话式布局。所有数据都以 textContent 写入，避免把群消息当作 HTML 渲染。
const $ = id => document.getElementById(id);
function node(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined && text !== null) n.textContent = String(text); if (cls) n.className = cls; return n; }
const store = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : v; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* 部分嵌入环境禁用本地存储，忽略即可 */ } },
};
const SVGNS = "http://www.w3.org/2000/svg";

const PAGES = {
  overview: ["总览", "群聊记录、关键词转发与 AI 总结的整体运行情况。"],
  forwards: ["转发记录", "关键词命中后的私聊投递，点击任一行查看卡片里的消息。"],
  rules: ["关键词规则", "编辑规则时，右侧按当前草稿实时测试，不必先保存。"],
  summaries: ["总结任务", "每群凑满设定条数自动生成 AI 总结，私发给接收人。"],
  settings: ["插件设置", "功能开关、大肥鱼查群记录、接收人、监控范围、调用预算与断线补录。"],
};
const STATUS = { collecting: ["等待后文", "wait"], pending: ["待总结", "wait"], generating: ["生成中", "run"], ready: ["待发送", "wait"], sending: ["发送中", "run"], done: ["已发送", "ok"], failed: ["失败", "bad"] };
const STATUS_FILTERS = {
  forwards: [["", "全部"], ["failed", "失败"], ["collecting", "等待后文"], ["ready", "待发送"], ["done", "已发送"]],
  summaries: [["", "全部"], ["failed", "失败"], ["pending", "待总结"], ["generating", "生成中"], ["ready", "待发送"], ["done", "已发送"]],
};
const FIELDS = {
  "basic-fields": ["enabled", "silent_mode"],
  "chat-fields": ["chat_search_enabled"],
  "recipient-fields": ["recipients", "recipient_platform_id"],
  "scope-fields": ["group_mode", "groups", "excluded_groups", "platform_ids"],
  "budget-fields": ["summary_input_chars", "summary_max_calls_per_batch", "max_llm_calls_per_day", "summary_timeout", "delivery_message_chars", "retry_limit"],
  "backfill-fields": ["backfill_enabled", "backfill_interval", "backfill_max_hours", "backfill_pages_per_cycle", "backfill_summary_enabled"],
  "rule-fields": ["forward_enabled", "keywords", "match_mode", "exclude_keywords", "case_sensitive"],
  "card-fields": ["forward_format", "forward_context_before", "forward_context_after", "forward_context_wait", "forward_context_minutes"],
  "summary-fields": ["summary_enabled", "summary_format", "summary_threshold", "summary_provider_id", "summary_instruction"],
};
const FIELD_PAGE = { "basic-fields": "settings", "chat-fields": "settings", "recipient-fields": "settings", "scope-fields": "settings", "budget-fields": "settings", "backfill-fields": "settings", "rule-fields": "rules", "card-fields": "rules", "summary-fields": "summaries" };
const KEY_PAGE = Object.fromEntries(Object.entries(FIELDS).flatMap(([box, keys]) => keys.map(k => [k, FIELD_PAGE[box]])));
const SCOPE_KEYS = FIELDS["scope-fields"];
const RULE_KEYS = FIELDS["rule-fields"];
const STACKED = new Set(["groups", "excluded_groups", "platform_ids", "recipients", "keywords", "exclude_keywords", "summary_instruction"]);
const OPTION_LABELS = { group_mode: { all: "全部群聊", allowlist: "仅指定群聊" }, match_mode: { any: "包含任一关键词", all: "必须包含全部关键词" }, forward_format: { card: "QQ 合并转发卡片", text: "普通文字通知" }, summary_format: { image: "排版图片（链接另发文字）", text: "纯文字分段" } };
const OUTLINE = { Image: "图片", Face: "表情", Record: "语音", Video: "视频", File: "文件", At: "@提及", AtAll: "@全体", Reply: "回复", Forward: "合并转发", Json: "卡片", Poke: "戳一戳", Share: "分享", Node: "转发节点", Nodes: "转发节点" };
const LIST = { messages: ["message", "messages"], forwards: ["forward", "jobs"], summaries: ["summary", "jobs"], wsSummaries: ["ws-summary", "jobs"] };
const RANGES = [["7d", "近7天", 7], ["30d", "近30天", 30], ["365d", "近一年", 365], ["all", "全部", 0]];
const WS_TABS = ["insight", "ranking", "messages", "summaries", "files", "settings"];
const INSIGHT_SUBS = ["overview", "time", "types"];
const WEEK = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const RANKS = [
  ["speakers", "发言榜", "message", "条", "发言最多的成员"],
  ["images", "图片榜", "image", "张", "发图最多"],
  ["faces", "表情榜", "smile", "个", "发表情最多"],
  ["night", "夜猫子榜", "moon", "条", "0–6 点发言最多"],
  ["streak", "连续发言", "flame", "天", "最长连续发言天数"],
];
const ICONS = {
  grid: ["M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z"],
  users: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M22 21v-2a4 4 0 0 0-3-3.87 M16 3.13a4 4 0 0 1 0 7.75", "M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8"],
  user: ["M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1"],
  message: ["M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8z"],
  send: ["M22 2 9 15 M22 2l-7 20-6-7-7-6z"],
  filter: ["M22 3H2l8 9.46V19l4 2v-8.54L22 3z"],
  file: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z M14 2v6h6 M8 13h8 M8 17h6"],
  settings: ["M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z"],
  refresh: ["M20 7v5h-5 M4 17v-5h5 M6 7a7 7 0 0 1 12-2l2 3 M18 17a7 7 0 0 1-12 2l-2-3"],
  moon: ["M21 12.8A9 9 0 0 1 11.2 3 9 9 0 1 0 21 12.8z"],
  sun: ["M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v2 M12 20v2 M4.9 4.9l1.4 1.4 M17.7 17.7l1.4 1.4 M2 12h2 M20 12h2 M4.9 19.1l1.4-1.4 M17.7 6.3l1.4-1.4"],
  download: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4 M7 10l5 5 5-5 M12 15V3"],
  search: ["M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M21 21l-4.3-4.3"],
  history: ["M3 12a9 9 0 1 0 3-6.7L3 8 M3 3v5h5 M12 7v5l3 2"],
  spark: ["M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"],
  alert: ["M12 3 2 21h20L12 3z M12 9v5 M12 17v.1"],
  inbox: ["M22 12h-6l-2 3h-4l-2-3H2 M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z"],
  folder: ["M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2z"],
  chevron: ["M6 9l6 6 6-6"],
  bars: ["M3 21h18 M7 17v-7 M12 17V5 M17 17v-4"],
  trophy: ["M8 21h8 M12 17v4 M7 4h10v5a5 5 0 0 1-10 0z M17 6h3v2a3 3 0 0 1-3 3 M7 6H4v2a3 3 0 0 0 3 3"],
  clock: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2"],
  layers: ["M12 3 2 8l10 5 10-5z M2 13l10 5 10-5 M2 17l10 5 10-5"],
  calendar: ["M4 5h16v16H4z M4 10h16 M8 3v4 M16 3v4"],
  image: ["M3 4h18v16H3z M3 16l5-5 4 4 3-3 6 6 M15.5 7.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3"],
  smile: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M8 14s1.5 2 4 2 4-2 4-2 M9 9h.01 M15 9h.01"],
  flame: ["M12 2c1 4 6 6 6 12a6 6 0 0 1-12 0c0-3 2-5 3-6 0 2 1 3 2 3 0-4-1-6 1-9z"],
  bolt: ["M13 2 4 14h7l-1 8 9-12h-7z"],
  percent: ["M19 5 5 19 M7 5a2 2 0 1 0 0 4 2 2 0 0 0 0-4 M17 15a2 2 0 1 0 0 4 2 2 0 0 0 0-4"],
};
const S = {
  page: "overview", config: null, providers: [], groups: [], status: null, dirtyKeys: [], serverChanged: false,
  pages: { messages: 1, forwards: 1, summaries: 1, wsSummaries: 1 }, filters: { forwards: "", summaries: "" }, sessionFilter: "all",
  ws: { key: "", tab: "insight", sub: "overview", range: "all" }, seq: { insight: 0, ranking: 0 }, requests: {}, refreshing: false, lastRefresh: 0,
};
let bridge, toastTimer;

function icon(name) { const svg = document.createElementNS(SVGNS, "svg"); svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("class", "icon"); svg.setAttribute("aria-hidden", "true"); for (const d of ICONS[name] || ICONS.grid) { const p = document.createElementNS(SVGNS, "path"); p.setAttribute("d", d); svg.append(p); } return svg; }
document.querySelectorAll("[data-icon]").forEach(n => n.replaceChildren(icon(n.dataset.icon)));
const fmt = n => Number(n || 0).toLocaleString("zh-CN");
const pad = n => String(n).padStart(2, "0");
const pct = (a, b) => { if (!b) return "0%"; const v = a / b * 100; return (v > 0 && v < 10 ? v.toFixed(1) : Math.round(v)) + "%"; };
function debounce(fn, ms) { let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); }; }

// —— 时间：统一按北京时间显示，列表用相对时间，悬停看完整时间 ——
const BJ = 8 * 3600;
function fullDate(ts) { return ts ? new Date(ts * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "—"; }
function bj(ts) { const d = new Date((ts + BJ) * 1000); return { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, d: d.getUTCDate(), H: d.getUTCHours(), M: d.getUTCMinutes(), day: Math.floor((ts + BJ) / 86400) }; }
function ymd(ts) { if (!ts) return "—"; const p = bj(ts); return `${p.y}/${pad(p.m)}/${pad(p.d)}`; }
function rel(ts) {
  if (!ts) return "—";
  const now = Date.now() / 1000, diff = now - ts, p = bj(ts), hm = `${pad(p.H)}:${pad(p.M)}`;
  if (diff < 0) return -diff < 60 ? `${Math.ceil(-diff)} 秒后` : -diff < 3600 ? `${Math.ceil(-diff / 60)} 分钟后` : hm;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  const days = bj(now).day - p.day;
  if (days === 0) return `${Math.floor(diff / 3600)} 小时前`;
  if (days === 1) return `昨天 ${hm}`;
  if (bj(now).y !== p.y) return `${p.y}-${pad(p.m)}-${pad(p.d)}`;
  return `${p.m}-${pad(p.d)} ${hm}`;
}
function timeNode(ts) { const n = node("time", rel(ts), "time"); n.title = fullDate(ts); if (ts) n.dateTime = new Date(ts * 1000).toISOString(); return n; }
function shortTime(ts) {  // 会话列表右侧的时间：今天显示时分，昨天显示“昨天”，更早显示日期
  if (!ts) return ""; const p = bj(ts), now = bj(Date.now() / 1000);
  if (p.day === now.day) return `${pad(p.H)}:${pad(p.M)}`;
  if (p.day === now.day - 1) return "昨天";
  return p.y === now.y ? `${p.m}/${p.d}` : `${p.y}/${p.m}/${p.d}`;
}
const clock = () => new Date().toLocaleTimeString("zh-CN", { hour12: false });

// —— 通用界面组件 ——
function toast(text, opts = {}) {
  const t = $("toast"); t.className = "toast " + (opts.tone || ""); t.replaceChildren(node("span", text));
  if (opts.action) { const b = node("button", opts.action[0], "toast-action"); b.type = "button"; b.onclick = () => { t.hidden = true; act(b, opts.action[1]); }; t.append(b); }
  t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, opts.action ? 9000 : 4500);
}
function notice(text, tone = "", action = null) {
  const n = $("notice"); if (!text) { n.hidden = true; return; }
  n.className = "notice " + tone; n.replaceChildren(icon("alert"), node("span", text)); n.hidden = false;
  if (action) { const b = node("button", action[0], "text-button"); b.type = "button"; b.onclick = () => act(b, action[1]); n.append(b); }
}
async function act(btn, fn) {
  if (btn) { if (btn.disabled) return; btn.disabled = true; btn.classList.add("busy"); }
  try { return await fn(); } catch (e) { toast(e?.message || "操作失败，请重试", { tone: "bad" }); } finally { if (btn) { btn.disabled = false; btn.classList.remove("busy"); } }
}
function button(text, fn, cls = "text-button") { const b = node("button", text, cls); b.type = "button"; b.onclick = e => { e.stopPropagation(); act(b, fn); }; return b; }
function badge(text, tone = "") { const b = node("span", undefined, "badge " + tone); b.append(node("i"), node("span", text)); return b; }
function statusBadge(status) { const [label, tone] = STATUS[status] || [status, "muted"]; return badge(label, tone); }
function chip(text, cls = "") { return node("span", text, "chip " + cls); }
function empty(box, title, detail = "", action = null) { const n = node("div", undefined, "empty"); const mark = node("span", undefined, "empty-icon"); mark.append(icon("inbox")); n.append(mark, node("strong", title)); if (detail) n.append(node("small", detail)); if (action) n.append(button(action[0], action[1], "button secondary small")); box.replaceChildren(n); }
function table(headings, cls = "") {
  const wrap = node("div", undefined, "table-wrap"), t = node("table", undefined, cls), h = node("thead"), r = node("tr"), body = node("tbody");
  headings.forEach(x => { const [text, c] = Array.isArray(x) ? x : [x, ""]; const th = node("th", text, c); th.scope = "col"; r.append(th); });
  h.append(r); t.append(h, body); wrap.append(t); return { wrap, body };
}
function cell(...children) { const td = node("td"); children.forEach(n => { if (n !== null && n !== undefined) td.append(n instanceof Node ? n : node("span", n)); }); return td; }
function rightCell(...children) { const td = cell(actions(...children.filter(Boolean))); td.className = "right"; return td; }
function actions(...buttons) { const n = node("div", undefined, "row-actions"); n.append(...buttons); return n; }
function clickableRow(open, label) { const tr = node("tr", undefined, "row-link"); tr.tabIndex = 0; tr.setAttribute("aria-label", label); tr.onclick = open; tr.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } }; return tr; }
function segmented(box, options, current, onChange) {
  box.replaceChildren(...options.map(([value, label]) => { const b = node("button", label, "seg" + (value === current ? " active" : "")); b.type = "button"; b.setAttribute("aria-pressed", String(value === current)); b.onclick = () => { if (value !== current) onChange(value); }; return b; }));
}
const HUES = [345, 212, 152, 28, 268, 190];
function hueOf(seed) { let h = 0; for (const c of String(seed)) h = (h * 31 + c.charCodeAt(0)) >>> 0; return HUES[h % HUES.length]; }
// QQ 头像走腾讯公开地址（浏览器直接请求，会向腾讯暴露所查看的 QQ 号）；加载失败时保留首字头像
const QQ_ID = /^\d{5,12}$/;
function qqImg(id, group = false) {
  if (!QQ_ID.test(String(id || ""))) return null;
  const img = node("img"); img.alt = ""; img.loading = "lazy"; img.decoding = "async"; img.referrerPolicy = "no-referrer";
  img.onerror = () => img.remove();
  img.src = group ? `https://p.qlogo.cn/gh/${id}/${id}/100` : `https://q1.qlogo.cn/g?b=qq&nk=${id}&s=100`;
  return img;
}
function avatar(name, id, group = false) { const a = node("span", (String(name || id || "?").trim()[0] || "?"), "avatar"); a.style.setProperty("--hue", hueOf(id || name)); a.setAttribute("aria-hidden", "true"); const img = qqImg(id, group); if (img) a.append(img); return a; }
function outlineText(outline) { return [...String(outline || "").matchAll(/\[(\w+)\]/g)].map(m => `[${OUTLINE[m[1]] || m[1]}]`).join(" "); }
function person(name, id) { const p = node("div", undefined, "person"); const t = node("div", undefined, "person-text"); t.append(node("strong", name || id), node("small", id)); p.append(avatar(name, id), t); return p; }
function groupName(g, multi = multiPlatform()) { const d = node("div", undefined, "group-name"); d.append(node("strong", g.group_name || g.group_id), node("small", g.group_id + (multi && g.platform ? " · " + g.platform : ""))); return d; }
function multiPlatform() { return new Set(S.groups.map(g => g.platform).filter(Boolean)).size > 1; }
function highlight(text, terms, sensitive = S.config?.values.case_sensitive) {
  const frag = document.createDocumentFragment(), value = String(text || "");
  const list = [...new Set((terms || []).filter(Boolean).map(String))].sort((a, b) => b.length - a.length);
  if (!list.length || !value) { frag.append(value); return frag; }
  const fold = s => sensitive ? s : s.toLowerCase(), hay = fold(value), needles = list.map(fold);
  let i = 0, start = 0;
  while (i < value.length) {
    const k = needles.findIndex(n => n && hay.startsWith(n, i));
    if (k < 0) { i++; continue; }
    if (start < i) frag.append(value.slice(start, i));
    frag.append(node("mark", value.slice(i, i + needles[k].length))); i += needles[k].length; start = i;
  }
  if (start < value.length) frag.append(value.slice(start));
  return frag;
}
function outlineChips(outline) { const box = node("span", undefined, "chips inline"); for (const m of String(outline || "").matchAll(/\[(\w+)\]/g)) box.append(chip(OUTLINE[m[1]] || m[1], "soft")); return box.childElementCount ? box : null; }
function snippet(text, outline, terms, cls = "snippet", sensitive) { const d = node("div", undefined, cls); if (text) d.append(highlight(text, terms, sensitive)); else d.append(node("span", "（无文字）", "muted")); const o = outlineChips(outline); if (o) d.append(o); return d; }
function metaGrid(pairs) { const g = node("dl", undefined, "meta-grid"); for (const [k, v] of pairs) { if (v === null || v === undefined || v === "") continue; const w = node("div"), dd = node("dd"); dd.append(v instanceof Node ? v : String(v)); w.append(node("dt", k), dd); g.append(w); } return g; }
function section(title, ...children) { const s = node("section", undefined, "dialog-section"); s.append(node("h3", title), ...children.filter(Boolean)); return s; }
function card(title, note, ...children) { const c = node("section", undefined, "card"), h = node("div", undefined, "card-heading"); h.append(node("h2", title)); if (note) h.append(node("span", note, "muted")); c.append(h, ...children.filter(Boolean)); return c; }
function openDialog(title, parts, buttons = []) {
  $("detail-title").textContent = title; $("detail-body").replaceChildren(...parts);
  $("detail-actions").replaceChildren(...buttons); $("detail-actions").hidden = !buttons.length;
  if (!$("details-dialog").open) $("details-dialog").showModal();
}
$("close-details").onclick = () => $("details-dialog").close();
$("details-dialog").addEventListener("click", e => { if (e.target === $("details-dialog")) $("details-dialog").close(); });
async function confirmAction(title, text, label = "确认", tone = "") {
  const dialog = $("confirm-dialog"); $("confirm-title").textContent = title; $("confirm-body").textContent = text; $("confirm-yes").textContent = label; $("confirm-yes").className = "button " + tone;
  return new Promise(resolve => { const done = yes => { dialog.close(); resolve(yes); }; $("confirm-no").onclick = () => done(false); $("confirm-yes").onclick = () => done(true); dialog.oncancel = e => { e.preventDefault(); done(false); }; dialog.showModal(); $("confirm-no").focus(); });
}
function setConnection(state, label) { $("connection-dot").className = state; $("connection-label").textContent = label; }

// —— 页面切换 ——
async function navigate(page) {
  if (!PAGES[page] && page !== "group") return; S.page = page;
  document.querySelectorAll("[data-view]").forEach(n => { n.hidden = n.dataset.view !== page; });
  document.querySelectorAll("#navigation [data-page]").forEach(n => { const on = n.dataset.page === page; n.classList.toggle("active", on); if (on) n.setAttribute("aria-current", "page"); else n.removeAttribute("aria-current"); });
  renderHeader(); renderSessions(); updateFill();
  window.scrollTo({ top: 0, behavior: "instant" });
  if (page === "rules") schedulePreview();
  if (!bridge) return;
  if (page === "group") await loadWorkspace();
  else if (LIST[page]) await loadList(page);
}
for (const b of document.querySelectorAll("[data-page],[data-go]")) b.onclick = () => act(b, () => navigate(b.dataset.page || b.dataset.go));
for (const b of document.querySelectorAll("[data-open-group]")) b.onclick = () => act(b, () => openGroup(b.dataset.openGroup));
function setSummaryTab(tab) { $("summary-records").hidden = tab !== "records"; $("summary-config").hidden = tab !== "config"; document.querySelectorAll("[data-summary-tab]").forEach(b => { const on = b.dataset.summaryTab === tab; b.classList.toggle("active", on); b.setAttribute("aria-selected", String(on)); }); }
for (const b of document.querySelectorAll("[data-summary-tab]")) b.onclick = () => setSummaryTab(b.dataset.summaryTab);
async function goFailed(kind) { const page = kind === "summary" ? "summaries" : "forwards"; S.filters[page] = "failed"; S.pages[page] = 1; renderStatusFilter(page); if (page === "summaries") setSummaryTab("records"); await navigate(page); }
function renderHeader() {
  const grp = S.page === "group";
  $("page-avatar").hidden = !grp; $("ws-controls").hidden = !grp;
  if (grp) { renderWorkspaceHeader(); return; }
  $("page-title").textContent = PAGES[S.page][0]; $("page-subtitle").textContent = PAGES[S.page][1];
}

// —— 配置草稿 ——
function readInput(key) {
  const f = S.config.schema[key], input = $("field-" + key);
  if (!input) return S.config.values[key];
  if (f.type === "bool") return input.checked;
  if (f.type === "list") return [...new Set(input.value.split(/\r?\n/).map(x => x.trim()).filter(Boolean))];
  if (f.type === "int") return input.value.trim() === "" ? NaN : Number(input.value);
  return input.value.trim();
}
function draft() { return Object.fromEntries(Object.keys(S.config.schema).map(k => [k, readInput(k)])); }
function setInput(key, value) { const f = S.config.schema[key], input = $("field-" + key); if (!input) return; if (f.type === "bool") input.checked = Boolean(value); else input.value = f.type === "list" ? value.join("\n") : value; }
function renderFields() {
  const { schema, values } = S.config;
  for (const [boxId, keys] of Object.entries(FIELDS)) {
    const box = $(boxId); box.replaceChildren();
    for (const key of keys) {
      const f = schema[key]; if (!f) continue;
      const row = node("div", undefined, "field" + (STACKED.has(key) ? " stacked" : "")), text = node("div", undefined, "field-text"), label = node("label", f.description);
      row.id = "row-" + key; label.htmlFor = "field-" + key; text.append(label); if (f.hint) text.append(node("p", f.hint));
      let input, control;
      if (key === "summary_provider_id") {
        input = node("select"); input.append(new Option("跟随来源群聊天模型", ""), ...S.providers.map(p => new Option(p.id, p.id)));
        if (values[key] && !S.providers.some(p => p.id === values[key])) input.append(new Option(values[key] + "（当前不可用）", values[key]));
      } else if (f.options) {
        input = node("select"); f.options.forEach(v => input.append(new Option(OPTION_LABELS[key]?.[v] || v, v)));
      } else if (f.type === "list" || key === "summary_instruction") {
        input = node("textarea"); input.rows = key === "keywords" ? 5 : key === "summary_instruction" ? 4 : 3; input.spellcheck = false;
        input.placeholder = f.type === "list" ? "一行一个" : "填写总结关注点与格式";
      } else if (f.type === "bool") {
        input = node("input"); input.type = "checkbox"; input.setAttribute("role", "switch");
        control = node("span", undefined, "switch"); control.append(input, node("span", undefined, "switch-track"));
      } else {
        input = node("input"); input.type = f.type === "int" ? "number" : "text"; if (f.type === "int") { input.step = "1"; input.inputMode = "numeric"; }
      }
      input.id = "field-" + key;
      if (f.hint) input.setAttribute("aria-describedby", "hint-" + key), text.lastChild.id = "hint-" + key;
      input.addEventListener("input", changed); input.addEventListener("change", changed);
      row.append(text, control || input); box.append(row);
      setInput(key, values[key]);  // 必须在插入文档后赋值：setInput 通过 id 查找元素
    }
  }
  $("summary-hint").textContent = `每群 ${values.summary_threshold} 条 · ${values.summary_provider_id || "跟随来源群模型"}`;
  changed();
}
function syncFieldStates(d) {
  const dim = (key, off) => $("row-" + key)?.classList.toggle("inactive", Boolean(off));
  const cardMode = d.forward_format === "card";
  dim("groups", d.group_mode !== "allowlist");
  ["keywords", "match_mode", "exclude_keywords", "case_sensitive", "forward_format"].forEach(k => dim(k, !d.forward_enabled));
  ["forward_context_before", "forward_context_after", "forward_context_minutes"].forEach(k => dim(k, !d.forward_enabled || !cardMode));
  dim("forward_context_wait", !d.forward_enabled || !cardMode || d.forward_context_after === 0);
  ["summary_format", "summary_threshold", "summary_provider_id", "summary_instruction"].forEach(k => dim(k, !d.summary_enabled));
  ["backfill_interval", "backfill_max_hours", "backfill_pages_per_cycle", "backfill_summary_enabled"].forEach(k => dim(k, !d.backfill_enabled));
}
function changed() {
  if (!S.config) return;
  const d = draft(), saved = S.config.values, schema = S.config.schema;
  S.dirtyKeys = Object.keys(d).filter(k => JSON.stringify(d[k]) !== JSON.stringify(saved[k]));
  const n = S.dirtyKeys.length;
  $("savebar").hidden = !n;
  if (n) {
    $("save-title").textContent = `${n} 项未保存的修改`;
    const names = S.dirtyKeys.slice(0, 3).map(k => schema[k].description).join("、") + (n > 3 ? " 等" : "");
    $("save-detail").textContent = names + (S.serverChanged ? " · 服务器配置已在别处变更，保存时会提示冲突" : " · 切换页面不会丢失");
  }
  const pages = new Set(S.dirtyKeys.map(k => KEY_PAGE[k]));
  document.querySelectorAll("#navigation [data-page]").forEach(b => b.classList.toggle("has-draft", pages.has(b.dataset.page)));
  document.querySelectorAll(".field").forEach(r => r.classList.toggle("edited", S.dirtyKeys.includes(r.id.slice(4))));
  syncFieldStates(d);
  schedulePreview();
}
function keepDraft(skip = []) { const d = draft(); return Object.fromEntries(S.dirtyKeys.filter(k => !skip.includes(k)).map(k => [k, d[k]])); }
function applyConfig(cfg, keep = {}) {
  S.config = cfg; S.serverChanged = false; renderFields();
  for (const [k, v] of Object.entries(keep)) if (k in cfg.values) setInput(k, v);
  changed(); renderScopeSummary(); renderSessions(); renderGroupRulesNote(); if (S.page === "group") renderWorkspaceHeader();
}
$("save").onclick = () => act($("save"), async () => {
  const values = draft();
  for (const [key, f] of Object.entries(S.config.schema)) if (f.type === "int" && !Number.isInteger(values[key])) {
    await navigate(KEY_PAGE[key]); if (KEY_PAGE[key] === "summaries") setSummaryTab("config"); if (SCOPE_KEYS.includes(key)) toggleScope(true);
    $("field-" + key).focus(); throw new Error(`「${f.description}」需要填写整数`);
  }
  const saved = await bridge.apiPost("settings/save", { values, revision: S.config.revision });
  const readback = await bridge.apiGet("settings");
  if (readback.revision !== saved.revision) throw new Error("配置随后又发生了变化，请刷新后检查");
  applyConfig(readback); await refresh(); toast("配置已保存，并已回读确认", { tone: "ok" });
});
$("discard").onclick = () => act($("discard"), async () => {
  if (!await confirmAction("放弃未保存的修改？", "将重新载入服务器当前配置。已经保存的规则和历史记录不受影响。", "放弃修改", "danger")) return;
  applyConfig(await bridge.apiGet("settings")); toast("已重新载入服务器配置");
});
window.addEventListener("beforeunload", e => { if (S.dirtyKeys.length) { e.preventDefault(); e.returnValue = ""; } });

// —— 群聊会话：左栏列表、监控状态、单群排除/恢复（即时生效，带撤销） ——
function groupValue(g) { return JSON.stringify([g.platform || "", g.group_id]); }
function scopeFromValue(value) { if (!value) return {}; const [platform, group_id] = JSON.parse(value); return platform ? { platform, group_id } : { group_id }; }
function selectedGroup(id) { return scopeFromValue($(id).value); }
function populateGroups() {
  const multi = multiPlatform();
  for (const id of ["forward-group", "summary-group"]) {
    const select = $(id), prev = select.value;
    select.replaceChildren(new Option("全部群聊", ""), ...S.groups.map(g => new Option((g.group_name || g.group_id) + (multi ? ` · ${g.platform}` : ""), groupValue(g))));
    if ([...select.options].some(o => o.value === prev)) select.value = prev;
  }
}
function scopeOf(g, v = S.config.values) {
  if (g.joined === false) return ["已离群", "muted", "unmonitored"];
  if (!v.enabled) return ["插件暂停", "warn", "unmonitored"];
  if (v.excluded_groups.includes(g.group_id)) return ["已排除", "bad", "excluded"];
  if (v.platform_ids.length && g.platform && !v.platform_ids.includes(g.platform)) return ["实例未选中", "muted", "unmonitored"];
  if (v.group_mode === "allowlist" && !v.groups.includes(g.group_id)) return ["未监控", "muted", "unmonitored"];
  return ["监控中", "ok", "monitored"];
}
function groupRows() {
  const v = S.config.values, rows = S.groups.map(g => ({ ...g }));
  for (const id of [...v.groups, ...v.excluded_groups]) if (!rows.some(g => g.group_id === id)) rows.push({ platform: "", group_id: id, group_name: id, message_count: 0, pending_count: 0, excluded_count: 0 });
  return rows;
}
function currentGroup() {
  if (!S.ws.key) return null;
  const [platform, group_id] = JSON.parse(S.ws.key), rows = S.config ? groupRows() : S.groups;
  return rows.find(g => g.group_id === group_id && (g.platform || "") === platform) || rows.find(g => g.group_id === group_id) || { platform, group_id, group_name: group_id, message_count: 0, pending_count: 0, excluded_count: 0 };
}
function wsScope() { const g = currentGroup(); return g ? (g.platform ? { platform: g.platform, group_id: g.group_id } : { group_id: g.group_id }) : {}; }
function rangeArgs() { const days = RANGES.find(r => r[0] === S.ws.range)?.[2] || 0; return days ? { since: Math.floor(Date.now() / 1000 - days * 86400) } : {}; }
function totalMessages() { return S.groups.reduce((n, g) => n + (g.message_count || 0), 0); }
function sessionItem(key, name, sub, time, tone, statusLabel, avatarNode) {
  const active = S.page === "group" && S.ws.key === key, b = node("button", undefined, "session" + (active ? " active" : "")), t = node("span", undefined, "session-text"), line = node("span", undefined, "session-line");
  b.type = "button"; if (active) b.setAttribute("aria-current", "page"); b.title = statusLabel ? `${name} · ${statusLabel}` : name;
  line.append(node("strong", name)); if (time) { const tm = node("span", shortTime(time), "session-time"); tm.title = fullDate(time); line.append(tm); }
  t.append(line, node("small", sub));
  b.append(avatarNode, t); if (tone) { const dot = node("i", undefined, "session-dot " + tone); dot.setAttribute("aria-label", statusLabel); b.append(dot); }
  b.onclick = () => act(null, () => openGroup(key));
  return b;
}
function renderSessions() {
  if (!S.config) return;
  const rows = groupRows(), term = $("session-search").value.trim().toLocaleLowerCase(), counts = { all: rows.length, monitored: 0, excluded: 0, unmonitored: 0 };
  rows.forEach(g => { counts[scopeOf(g)[2]]++; });
  segmented($("session-filter"), [["all", `全部 ${counts.all}`], ["monitored", `监控中 ${counts.monitored}`], ["excluded", `已排除 ${counts.excluded}`]], S.sessionFilter, value => { S.sessionFilter = value; renderSessions(); });
  const shown = rows.filter(g => (S.sessionFilter === "all" || scopeOf(g)[2] === S.sessionFilter) && (g.group_name + " " + g.group_id).toLocaleLowerCase().includes(term));
  $("session-count").textContent = shown.length === rows.length ? String(rows.length) : `${shown.length}/${rows.length}`;
  const allAvatar = node("span", undefined, "avatar all"); allAvatar.append(icon("users"));
  const list = [sessionItem("", "全部群聊", `${rows.length} 个群 · ${fmt(totalMessages())} 条`, 0, "", "", allAvatar)];
  for (const g of shown) {
    const [label, tone] = scopeOf(g), lm = g.last_message, preview = lm ? `${lm.sender_name}：${lm.text || outlineText(lm.outline) || "[消息]"}` : `${fmt(g.message_count)} 条消息`;
    list.push(sessionItem(groupValue(g), g.group_name || g.group_id, preview, g.last_time, tone, label, avatar(g.group_name, g.group_id, true)));
  }
  if (!shown.length) list.push(node("p", rows.length ? "没有符合条件的群" : "暂无群聊，请确认机器人在线", "muted session-empty"));
  $("session-list").replaceChildren(...list);
  const cur = S.page === "group" ? (currentGroup()?.group_name || "全部群聊") : "群聊列表";
  $("session-current").textContent = cur;
}
$("session-search").oninput = renderSessions;
$("session-toggle").onclick = () => { const open = !$("sessions").classList.contains("open"); $("sessions").classList.toggle("open", open); $("session-toggle").setAttribute("aria-expanded", String(open)); };
function renderScopeSummary() {
  if (!S.config) return;
  const v = S.config.values, parts = [v.group_mode === "all" ? "监控全部群聊" : `仅监控 ${v.groups.length} 个指定群`];
  if (v.excluded_groups.length) parts.push(`排除 ${v.excluded_groups.length} 个`);
  if (v.platform_ids.length) parts.push(`限定 ${v.platform_ids.length} 个实例`);
  parts.push(v.silent_mode ? "静默模式开启" : "静默模式关闭");
  if (!v.enabled) parts.unshift("插件已暂停");
  $("scope-summary").textContent = parts.join(" · ");
}
function toggleScope(open = $("scope-fields").hidden) { $("scope-fields").hidden = !open; $("scope-toggle").setAttribute("aria-expanded", String(open)); $("scope-toggle").textContent = open ? "收起" : "编辑范围"; }
$("scope-toggle").onclick = () => toggleScope();
async function setGroupScope(g, action, undo = false) {
  if (SCOPE_KEYS.some(k => S.dirtyKeys.includes(k))) { await navigate("settings"); toggleScope(true); throw new Error("监控范围有未保存的修改，请先保存或放弃，再操作单个群"); }
  const name = g.group_name || g.group_id;
  if (action === "exclude" && !undo && !await confirmAction(`排除「${name}」？`, "排除后不再记录这个群的新消息，也不会转发或总结。已有记录会保留，可以随时恢复。", "排除", "danger")) return;
  const cfg = await bridge.apiPost("groups/scope", { group_id: g.group_id, action, revision: S.config.revision });
  applyConfig(cfg, keepDraft(SCOPE_KEYS));
  if (undo) { toast("已撤销", { tone: "ok" }); return; }
  toast(action === "exclude" ? `已排除「${name}」` : `已恢复监控「${name}」`, { tone: "ok", action: ["撤销", () => setGroupScope(g, action === "exclude" ? "include" : "exclude", true)] });
}
function overviewGroups() {
  const box = $("overview-groups"), rows = S.groups.filter(g => g.message_count).slice(0, 6);
  if (!rows.length) { empty(box, "暂无群聊记录", "监控群收到新消息后会显示在这里。"); return; }
  const top = Math.max(...rows.map(g => g.message_count)), { wrap, body } = table(["群聊", "消息量", ["最近消息", "nowrap"]], "compact");
  for (const g of rows) {
    const tr = clickableRow(() => act(null, () => openGroup(groupValue(g))), "打开 " + (g.group_name || g.group_id) + " 的洞察"), bar = node("div", undefined, "meter"), fill = node("i");
    fill.style.width = (g.message_count / top * 100) + "%"; bar.append(fill);
    tr.append(cell(groupName(g)), cell(node("span", fmt(g.message_count), "small-num"), bar), cell(timeNode(g.last_time))); body.append(tr);
  }
  box.replaceChildren(wrap);
}

// —— 群工作区：洞察 · 榜单 · 消息 · 总结 ——
async function openGroup(key, tab) {
  if (S.ws.key !== key) { S.ws.key = key; S.chat.jump = 0; S.chat.key = ""; S.pages.messages = 1; S.pages.wsSummaries = 1; $("message-search").value = ""; $("message-sender").value = ""; }
  if (tab) setWsTab(tab);
  $("sessions").classList.remove("open"); $("session-toggle").setAttribute("aria-expanded", "false");
  await navigate("group");
}
function renderWorkspaceHeader() {
  const g = currentGroup(), av = $("page-avatar");
  for (const t of ["files", "settings"]) { const b = document.querySelector(`[data-ws-tab="${t}"]`); if (b) b.hidden = !g; }  // 「全部群聊」没有群文件和单群设置
  if (!g && ["files", "settings"].includes(S.ws.tab)) setWsTab("insight");
  $("ws-status").replaceChildren(); $("ws-scope").replaceChildren();
  if (!g) {
    av.className = "page-avatar all"; av.replaceChildren(icon("users"));
    $("page-title").textContent = "全部群聊";
    $("page-subtitle").textContent = `QQ · ${S.groups.length} 个群 · 共 ${fmt(totalMessages())} 条记录`;
    return;
  }
  av.className = "page-avatar"; av.textContent = (String(g.group_name || g.group_id).trim()[0] || "?"); av.style.setProperty("--hue", hueOf(g.group_id)); { const gi = qqImg(g.group_id, true); if (gi) av.append(gi); }
  $("page-title").textContent = g.group_name || g.group_id;
  $("page-subtitle").textContent = `QQ · 群聊 · ${g.group_id}` + (multiPlatform() && g.platform ? ` · ${g.platform}` : "") + ` · 共 ${fmt(g.message_count)} 条记录`;
  if (!S.config) return;
  const v = S.config.values, [label, tone, key] = scopeOf(g);
  $("ws-status").append(badge(label, tone));
  if (Object.keys(groupRulesOf(g.group_id)).length) $("ws-status").append(badge("单独规则", "run"));
  if (g.joined !== false && v.enabled && (!v.platform_ids.length || !g.platform || v.platform_ids.includes(g.platform)))
    $("ws-scope").append(key === "monitored" ? button("排除此群", () => setGroupScope(g, "exclude"), "button secondary small danger-text") : button(key === "excluded" ? "恢复监控" : "加入监控", () => setGroupScope(g, "include"), "button small"));
}
function setWsTab(tab) {
  S.ws.tab = tab;
  document.querySelectorAll("[data-ws-tab]").forEach(b => { const on = b.dataset.wsTab === tab; b.classList.toggle("active", on); b.setAttribute("aria-selected", String(on)); });
  for (const t of WS_TABS) $("ws-" + t).hidden = t !== tab;
  $("ws-range").hidden = ["summaries", "files", "settings"].includes(tab);  // 总结任务和群设置不按消息时间筛选
  updateFill();
}
for (const b of document.querySelectorAll("[data-ws-tab]")) b.onclick = () => { setWsTab(b.dataset.wsTab); act(null, loadWorkspace); };
function setInsightSub(sub) {
  S.ws.sub = sub;
  document.querySelectorAll("[data-insight-sub]").forEach(b => { const on = b.dataset.insightSub === sub; b.classList.toggle("active", on); b.setAttribute("aria-selected", String(on)); });
  for (const s of INSIGHT_SUBS) $("insight-" + s).hidden = s !== sub;
}
for (const b of document.querySelectorAll("[data-insight-sub]")) b.onclick = () => setInsightSub(b.dataset.insightSub);
function renderRange() {
  segmented($("ws-range"), RANGES.map(r => [r[0], r[1]]), S.ws.range, value => { S.ws.range = value; S.pages.messages = 1; renderRange(); act(null, loadWorkspace); });
}
async function loadWorkspace(auto = false) {
  if (!bridge || !S.config) return;
  if (S.ws.tab === "insight") return loadInsight();
  if (S.ws.tab === "ranking") return loadRanking();
  if (S.ws.tab === "messages") return loadMessages(auto);
  if (S.ws.tab === "files") { if (!auto) await loadFiles(); return; }  // 群文件要调 QQ 接口，自动刷新不重读
  if (S.ws.tab === "settings") { if (!auto || S.groupRules?.key !== S.ws.key) renderGroupRules(); return; }  // 自动刷新不打断正在编辑的规则
  renderWsSummaryHead(); return loadList("wsSummaries");
}
async function loadPane(kind, box, api, render) {
  const req = ++S.seq[kind];
  if (!box.childElementCount) box.replaceChildren(node("div", "正在载入…", "muted pad"));
  box.classList.add("loading");
  try { const d = await bridge.apiGet(api, { ...wsScope(), ...rangeArgs() }); if (req === S.seq[kind]) render(d); }
  finally { if (req === S.seq[kind]) box.classList.remove("loading"); }
}
function loadInsight() { return loadPane("insight", $("ws-insight"), "insight", renderInsight); }
function loadRanking() { return loadPane("ranking", $("ws-ranking"), "ranking", renderRanking); }
function noDataText() { return S.ws.range === "all" ? "监控群收到新消息后，这里会显示统计。" : "换个时间范围试试。"; }

function renderInsight(d) {
  const g = currentGroup(), panes = INSIGHT_SUBS.map(s => $("insight-" + s));
  if (!d.total) { panes.forEach(p => empty(p, "这个时间范围内没有消息", noDataText())); return; }
  panes[0].replaceChildren(heroCard(d, g));
  panes[1].replaceChildren(...timeCards(d));
  panes[2].replaceChildren(typesCard(d));
}
function heroCard(d, g) {
  const hero = node("section", undefined, "hero"), top = node("div", undefined, "hero-top"), main = node("div", undefined, "hero-main"), meta = node("div", undefined, "hero-meta");
  const tag = node("span"); tag.append(icon("users"), node("span", g ? "QQ · 群聊" : `QQ · 全部 ${S.groups.length} 个群`));
  const range = node("span"); range.append(icon("calendar"), node("span", `${ymd(d.first_time)} - ${ymd(d.last_time)}`));
  meta.append(tag, range); main.append(node("h2", g ? (g.group_name || g.group_id) : "全部群聊", "hero-title"), meta);
  const nums = node("div", undefined, "hero-nums");
  for (const [v, label] of [[d.total, "消息总数"], [d.span_days, "跨度天数"], [d.active_days, "活跃天数"], [d.daily_avg, "日均消息"]]) { const c = node("div", undefined, "hero-num"); c.append(node("strong", fmt(v)), node("span", label)); nums.append(c); }
  top.append(main, nums);
  hero.append(top, node("div", "ACTIVITY HEATMAP", "eyebrow"), heatmap(d.days), node("div", "KEY METRICS", "eyebrow"), metrics(d));
  return hero;
}
const DAY_MS = 86400000;
const isoToMs = s => Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10));
const msToIso = t => new Date(t).toISOString().slice(0, 10);
const mondayIndex = t => (new Date(t).getUTCDay() + 6) % 7;
function heatmap(days) {
  const wrap = node("div", undefined, "heat");
  if (!days.length) return wrap;
  const counts = new Map(days.map(r => [r.day, r.n])), first = isoToMs(days[0].day), last = isoToMs(days[days.length - 1].day), max = Math.max(1, ...days.map(r => r.n));
  let start = first - mondayIndex(first) * DAY_MS;
  const end = last + (6 - mondayIndex(last)) * DAY_MS;
  let weeks = Math.round((end - start + DAY_MS) / (7 * DAY_MS)), cut = false;
  if (weeks > 53) { start = end + DAY_MS - 53 * 7 * DAY_MS; weeks = 53; cut = true; }  // 热力图最多显示最近 53 周
  const labels = node("div", undefined, "heat-days"), scroll = node("div", undefined, "heat-scroll"), months = node("div", undefined, "heat-months"), grid = node("div", undefined, "heat-grid");
  WEEK.forEach((w, i) => labels.append(node("span", i % 2 ? "" : w)));
  labels.prepend(node("span", ""));
  let lastLabel = -9, prevMonth = -1;
  for (let w = 0; w < weeks; w++) {
    const ws = start + w * 7 * DAY_MS, month = new Date(ws).getUTCMonth();
    if (month !== prevMonth) { if (w - lastLabel >= 3) { const m = node("span", `${month + 1}月`); m.style.gridColumn = `${w + 1} / span 3`; months.append(m); lastLabel = w; } prevMonth = month; }
    for (let i = 0; i < 7; i++) {
      const t = ws + i * DAY_MS, c = node("i");
      if (t < first || t > last) { c.className = "heat-cell blank"; grid.append(c); continue; }
      const day = msToIso(t), n = counts.get(day) || 0;
      c.className = "heat-cell l" + (n ? Math.min(4, Math.ceil(n / max * 4)) : 0); c.title = `${day}（${WEEK[i]}）：${fmt(n)} 条`; grid.append(c);
    }
  }
  scroll.append(months, grid);
  const legend = node("div", undefined, "heat-legend");
  legend.append(node("span", "1"), node("i", undefined, "heat-scale"), node("span", fmt(max)));
  if (cut) legend.prepend(node("span", "仅显示最近 53 周 ·", "muted"));
  const body = node("div", undefined, "heat-body"); body.append(labels, scroll);
  wrap.append(body, legend);
  return wrap;
}
function metrics(d) {
  const t = Object.fromEntries((d.types || []).map(x => [x.key, x.n])), total = d.total, wd = d.busiest_weekday, bd = d.busiest_day, ph = d.peak_hour;
  const items = [
    ["message", "blue", fmt(d.daily_avg), "条", "日均消息", `共 ${fmt(d.span_days)} 天`],
    ["image", "pink", fmt(t.image || 0), "张", "图片消息", `占全部消息 ${pct(t.image || 0, total)}`],
    ["calendar", "amber", wd === null || wd === undefined ? "—" : WEEK[wd], "", "最活跃星期", wd === null || wd === undefined ? "" : `${fmt(d.weekdays[wd])} 条消息`],
    ["sun", "green", pct(d.weekend, total), "", "周末活跃度", "周六、周日消息占比"],
    ["flame", "red", bd ? bd.day.slice(5).replace("-", "/") : "—", "", "最活跃日期", bd ? `${fmt(bd.n)} 条消息` : ""],
    ["moon", "indigo", fmt(d.late_night), "条", "深夜聊天", `0–6 点 · 占 ${pct(d.late_night, total)}`],
    ["bolt", "amber", fmt(d.longest_streak), "天", "连续打卡", "最长连续活跃"],
    ["percent", "cyan", pct(d.active_rate, 1), "", "活跃率", "有消息的天数占比"],
    ["user", "violet", fmt(d.members), "人", "成员数", "范围内发过言的成员"],
    ["clock", "pink", ph === null || ph === undefined ? "—" : `${ph}:00`, "", "最活跃时段", ph === null || ph === undefined ? "" : `${fmt(d.hours[ph])} 条消息`],
  ];
  const grid = node("div", undefined, "metrics");
  for (const [ic, tone, value, unit, label, sub] of items) {
    const m = node("div", undefined, "metric"), mark = node("span", undefined, "metric-icon tone-" + tone), body = node("div", undefined, "metric-body"), val = node("div", undefined, "metric-value tone-" + tone);
    mark.append(icon(ic)); val.append(node("strong", value)); if (unit) val.append(node("small", unit));
    body.append(val, node("span", label, "metric-label")); if (sub) body.append(node("span", sub, "metric-sub"));
    m.append(mark, body); grid.append(m);
  }
  return grid;
}
function fillDays(days) {
  if (!days.length) return [];
  const counts = new Map(days.map(r => [r.day, r.n])), out = [];
  let t = isoToMs(days[0].day); const end = isoToMs(days[days.length - 1].day);
  if ((end - t) / DAY_MS > 400) t = end - 400 * DAY_MS;  // 趋势图最多 401 天
  for (; t <= end; t += DAY_MS) { const day = msToIso(t); out.push({ day, n: counts.get(day) || 0 }); }
  return out;
}
function timeCards(d) {
  const ph = d.peak_hour, wd = d.busiest_weekday, series = fillDays(d.days), bd = d.busiest_day;
  const hours = card("24 小时分布", "北京时间 · 按小时统计", barChart({ labels: d.hours.map((_, i) => String(i)), values: d.hours, highlight: [ph], tips: d.hours.map((n, i) => [`${i}:00–${i + 1}:00`, `${fmt(n)} 条消息`]), caption: `高峰 ${ph}:00–${ph + 1}:00 · 深夜 0–6 点共 ${fmt(d.late_night)} 条`, aria: "24 小时消息分布" }));
  const week = card("星期分布", "周一在前", barChart({ labels: WEEK, values: d.weekdays, highlight: [wd], caption: `最活跃：${WEEK[wd]} · 周末占 ${pct(d.weekend, d.total)}`, aria: "星期消息分布" }));
  const two = node("div", undefined, "charts-2"); two.append(hours, week);
  const trend = card("每日趋势", `${fmt(series.length)} 天`, barChart({ labels: series.map(r => r.day.slice(5)), values: series.map(r => r.n), highlight: bd ? [series.findIndex(r => r.day === bd.day)] : [], tips: series.map(r => [r.day, `${fmt(r.n)} 条消息`]), caption: `日均 ${fmt(d.daily_avg)} 条` + (bd ? ` · 最多 ${bd.day} ${fmt(bd.n)} 条` : ""), aria: "每日消息趋势" }));
  return [two, trend];
}
function typesCard(d) {
  const rows = [...(d.types || [])].sort((a, b) => b.n - a.n), max = Math.max(1, ...rows.map(r => r.n)), list = node("div", undefined, "type-list");
  for (const r of rows) {
    const row = node("div", undefined, "type-row" + (r.n ? "" : " zero")), bar = node("div", undefined, "type-bar"), fill = node("i"), n = node("strong", fmt(r.n), "num");
    fill.style.width = (r.n / max * 100) + "%"; bar.append(fill);
    row.append(node("span", r.label, "type-label"), bar, n, node("span", pct(r.n, d.total), "type-pct")); list.append(row);
  }
  return card("消息类型", `共 ${fmt(d.total)} 条 · 按包含的类型统计，一条消息可同时计入多类，占比相加可能超过 100%`, list);
}
function niceMax(v) { if (v <= 4) return 4; const p = 10 ** Math.floor(Math.log10(v)), n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }
function barChart({ labels, values, highlight = [], tips = null, caption = "", aria = "" }) {
  const W = 640, H = 220, L = 46, R = 10, B = 180, T = 16, n = values.length, max = niceMax(Math.max(1, ...values)), step = (W - L - R) / Math.max(1, n);
  const svg = document.createElementNS(SVGNS, "svg"), total = values.reduce((a, b) => a + b, 0), box = node("div", undefined, "chart"), tip = node("div", undefined, "chart-tip");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", `${aria}，共 ${total} 条`);
  const add = (tag, attrs, text) => { const e = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text !== undefined) e.textContent = text; svg.append(e); return e; };
  for (let i = 0; i <= 4; i++) { const y = B - (B - T) * i / 4; add("line", { x1: L, y1: y, x2: W - R, y2: y, class: "chart-grid" + (i ? "" : " base") }); add("text", { x: L - 8, y: y + 4, "text-anchor": "end", class: "chart-label" }, fmt(max * i / 4)); }
  const every = Math.max(1, Math.ceil(n / 12)), showValues = n <= 14;
  tip.hidden = true;
  values.forEach((v, i) => {
    const w = Math.max(1.5, Math.min(44, step * .62)), x = L + step * i + (step - w) / 2, h = Math.max(v ? 2 : 0, v / max * (B - T)), hot = highlight.includes(i);
    const hit = add("rect", { x: L + step * i, y: T, width: step, height: B - T, class: "chart-hit" });
    add("rect", { x, y: B - h, width: w, height: h, rx: Math.min(5, w / 2), class: "chart-bar" + (hot ? " hot" : "") });
    if (i % every === 0) add("text", { x: x + w / 2, y: 204, "text-anchor": "middle", class: "chart-label" + (hot ? " strong" : "") }, labels[i]);
    if (showValues && v) add("text", { x: x + w / 2, y: B - h - 7, "text-anchor": "middle", class: "chart-value" }, fmt(v));
    hit.addEventListener("mouseenter", () => { const [a, b] = tips ? tips[i] : [labels[i], `${fmt(v)} 条消息`]; tip.replaceChildren(node("strong", a), node("span", b)); tip.hidden = false; tip.style.left = ((x + w / 2) / W * 100) + "%"; tip.style.top = ((B - h) / H * 100) + "%"; });
    hit.addEventListener("mouseleave", () => { tip.hidden = true; });
  });
  box.append(svg, tip); if (caption) box.append(node("div", caption, "chart-caption"));
  return box;
}
function renderRanking(d) {
  const box = $("ws-ranking");
  if (!d.total) { empty(box, "这个时间范围内没有消息", noDataText()); return; }
  const grid = node("div", undefined, "rank-grid");
  for (const [key, title, ic, unit, desc] of RANKS) {
    const items = d.lists?.[key] || [], top = Math.max(1, ...items.map(i => i.n)), list = node("ol", undefined, "rank-list");
    items.forEach((it, i) => {
      const li = node("li"), row = node("button", undefined, "rank-row"), who = node("span", undefined, "person-text"), val = node("span", undefined, "rank-val"), num = node("span", undefined, "rank-num"), bar = node("span", undefined, "rank-bar"), fill = node("i");
      row.type = "button"; row.title = `查看 ${it.sender_name || it.sender_id} 的消息`; row.onclick = () => act(null, () => filterSender(it.sender_id));
      who.append(node("strong", it.sender_name || it.sender_id), node("small", key === "streak" ? `活跃 ${fmt(it.active_days)} 天 · ${it.sender_id}` : it.sender_id));
      num.append(node("strong", fmt(it.n)), node("small", unit)); fill.style.width = (it.n / top * 100) + "%"; bar.append(fill); val.append(num, bar);
      row.append(node("span", String(i + 1), "rank-no" + (i < 3 ? " top" + (i + 1) : "")), avatar(it.sender_name, it.sender_id), who, val);
      li.append(row); list.append(li);
    });
    const c = node("section", undefined, "card rank-card"), h = node("div", undefined, "card-heading"), t = node("h2");
    t.append(icon(ic), node("span", title)); h.append(t, node("span", desc, "muted"));
    c.append(h, items.length ? list : node("p", "暂无数据", "muted pad")); grid.append(c);
  }
  box.replaceChildren(node("p", `范围内共 ${fmt(d.total)} 条消息 · 点击成员查看他的消息`, "muted pane-note"), grid);
}
async function filterSender(senderId) { $("message-sender").value = senderId; S.pages.messages = 1; S.chat.jump = 0; setWsTab("messages"); await loadMessages(); }
function renderWsSummaryHead() {
  if (!S.config) return;
  const g = currentGroup(), th = (g && groupRulesOf(g.group_id).summary_threshold) || S.config.values.summary_threshold;
  if (g) {
    $("ws-summary-pending").textContent = `${fmt(g.pending_count || 0)} / ${fmt(th)} 条`;
    $("ws-summary-progress").textContent = "待凑批，满额后自动生成一批总结" + (g.excluded_count ? `；另有 ${fmt(g.excluded_count)} 条旧记录尚未计入` : "");
  } else {
    $("ws-summary-pending").textContent = fmt(S.status?.pending_message_count) + " 条";
    $("ws-summary-progress").textContent = `全部群待凑批（每群满 ${fmt(th)} 条生成一批）`;
  }
}
$("ws-summary-config").onclick = () => act($("ws-summary-config"), async () => { await navigate("summaries"); setSummaryTab("config"); });

// —— 单群规则：转发规则、总结规则可按群单独设置；没开启的部分跟随全局 ——
const GROUP_RULE_SECTIONS = [
  ["forward", "转发规则", "开启后本群按下面的规则转发，不再跟随全局「关键词规则」。", ["forward_enabled", "keywords", "match_mode", "exclude_keywords", "case_sensitive", "forward_format", "forward_context_before", "forward_context_after", "forward_context_wait", "forward_context_minutes"]],
  ["summary", "总结规则", "开启后本群按下面的规则总结，不再跟随全局「总结设置」。", ["summary_enabled", "summary_format", "summary_threshold", "summary_provider_id", "summary_instruction"]],
];
S.groupRules = { key: "" };
function groupRulesOf(id) { return S.config?.values.group_rules?.[id] || {}; }
function grField(key, value, on) {
  const f = S.config.schema[key], row = node("div", undefined, "field" + (STACKED.has(key) ? " stacked" : "")), text = node("div", undefined, "field-text"), label = node("label", f.description);
  label.htmlFor = "gr-" + key; text.append(label); if (f.hint) text.append(node("p", f.hint));
  let input, control;
  if (key === "summary_provider_id") {
    input = node("select"); input.append(new Option("跟随来源群聊天模型", ""), ...S.providers.map(p => new Option(p.id, p.id)));
    if (value && !S.providers.some(p => p.id === value)) input.append(new Option(value + "（当前不可用）", value));
  } else if (f.options) {
    input = node("select"); f.options.forEach(o => input.append(new Option(OPTION_LABELS[key]?.[o] || o, o)));
  } else if (f.type === "list" || key === "summary_instruction") {
    input = node("textarea"); input.rows = key === "keywords" ? 4 : 3; input.spellcheck = false; input.placeholder = f.type === "list" ? "一行一个" : "填写总结关注点与格式";
  } else if (f.type === "bool") {
    input = node("input"); input.type = "checkbox"; input.setAttribute("role", "switch");
    control = node("span", undefined, "switch"); control.append(input, node("span", undefined, "switch-track"));
  } else {
    input = node("input"); input.type = f.type === "int" ? "number" : "text"; if (f.type === "int") { input.step = "1"; input.inputMode = "numeric"; }
  }
  input.id = "gr-" + key; input.disabled = !on;
  if (f.type === "bool") input.checked = Boolean(value); else input.value = f.type === "list" ? value.join("\n") : value;
  if (key === "keywords") input.addEventListener("input", () => grTest());
  row.append(text, control || input); return row;
}
function grRead(key) {
  const f = S.config.schema[key], input = $("gr-" + key);
  if (f.type === "bool") return input.checked;
  if (f.type === "list") return [...new Set(input.value.split(/\r?\n/).map(x => x.trim()).filter(Boolean))];
  if (f.type === "int") { const n = Number(input.value); if (input.value.trim() === "" || !Number.isInteger(n)) throw new Error(`${f.description} 需要填写整数`); return n; }
  return input.value.trim();
}
function grOn(sec) { return Boolean($("gr-on-" + sec)?.checked); }
function grDraft() {
  const rules = {};
  for (const [sec, , , keys] of GROUP_RULE_SECTIONS) if (grOn(sec)) for (const k of keys) rules[k] = grRead(k);
  return rules;
}
let grSeq = 0;
const grTest = debounce(async () => {
  const ta = $("gr-test"), res = $("gr-test-result"); if (!ta || !res || !bridge) return;
  const text = ta.value;
  if (!text.trim()) { res.replaceChildren(node("span", "输入一段消息后自动测试，不会发送消息，也不调用 AI。", "muted")); return; }
  const seq = ++grSeq, values = { ...S.config.values };
  try {
    if (grOn("forward")) for (const k of GROUP_RULE_SECTIONS[0][3]) values[k] = grRead(k);
    const r = await bridge.apiPost("match/preview", { text, values });
    if (seq !== grSeq) return;
    const head = node("div", undefined, "preview-head"), body = node("div", undefined, "preview-text");
    head.append(r.hits.length ? badge("命中 " + r.hits.length + " 个关键词", "ok") : badge("未命中", "muted"), chip(grOn("forward") ? "按本群规则（含未保存）" : "跟随全局规则", "soft"));
    if (!values.forward_enabled) head.append(chip("转发已关闭，实际不会转发", "soft"));
    body.append(highlight(text, r.hits, values.case_sensitive)); res.replaceChildren(head, body);
  } catch (e) { if (seq === grSeq) res.replaceChildren(node("div", e?.message || "测试失败", "error-text")); }
}, 400);
function renderGroupRules() {
  const box = $("ws-settings"), g = currentGroup();
  S.groupRules.key = S.ws.key;
  if (!g || !S.config) { empty(box, "请先选择一个群", "单群规则按群设置，在左侧群列表里选一个群。"); return; }
  const v = S.config.values, own = groupRulesOf(g.group_id), name = g.group_name || g.group_id, parts = [];
  parts.push(node("div", `这里设置「${name}」自己的转发规则和总结规则。没开启的部分跟随全局设置；接收人、调用预算和断线补录只有全局设置。修改只作用于之后的新消息，已经生成的任务不变。`, "info"));
  for (const [sec, title, hint, keys] of GROUP_RULE_SECTIONS) {
    const on = keys.some(k => k in own), card = node("section", undefined, "card"), head = node("div", undefined, "card-heading"), ht = node("div");
    ht.append(node("h2", title), node("p", hint, "heading-note"));
    const sw = node("label", undefined, "gr-toggle"), state = node("span", on ? "单独设置" : "跟随全局"), cb = node("input"), track = node("span", undefined, "switch");
    cb.type = "checkbox"; cb.id = "gr-on-" + sec; cb.checked = on; cb.setAttribute("role", "switch");
    track.append(cb, node("span", undefined, "switch-track")); sw.append(state, track); head.append(ht, sw);
    const body = node("div", undefined, "card-body gr-body" + (on ? "" : " off"));
    for (const k of keys) body.append(grField(k, k in own ? own[k] : v[k], on));
    cb.onchange = () => {
      body.classList.toggle("off", !cb.checked); state.textContent = cb.checked ? "单独设置" : "跟随全局";
      body.querySelectorAll("input,select,textarea").forEach(i => { i.disabled = !cb.checked; });
      if (sec === "forward") grTest();
    };
    card.append(head, body);
    if (sec === "forward") {
      const test = node("div", undefined, "card-body gr-test-box"), ta = node("textarea"), res = node("div", undefined, "preview-result");
      ta.id = "gr-test"; ta.rows = 3; ta.placeholder = "粘贴一段消息，测试本群的转发规则（包括还没保存的修改）"; res.id = "gr-test-result"; ta.oninput = () => grTest();
      test.append(node("strong", "规则测试", "gr-test-label"), ta, res); card.append(test);
    }
    parts.push(card);
  }
  const bar = node("div", undefined, "gr-actions");
  if (Object.keys(own).length) bar.append(button("全部恢复跟随全局", resetGroupRules, "button secondary"));
  bar.append(button("保存本群规则", saveGroupRules, "button"));
  parts.push(bar);
  box.replaceChildren(...parts); grTest();
}
async function saveGroupRules() {
  const g = currentGroup(); if (!g) return;
  const rules = grDraft(), name = g.group_name || g.group_id, has = Object.keys(rules).length > 0;
  const cfg = await bridge.apiPost("groups/rules", { group_id: g.group_id, rules: has ? rules : null, revision: S.config.revision });
  applyConfig(cfg, keepDraft()); renderGroupRules();
  toast(has ? `已保存「${name}」的单独规则，之后的新消息按它处理` : `「${name}」已恢复跟随全局`, { tone: "ok" });
}
async function resetGroupRules() {
  const g = currentGroup(); if (!g) return;
  const name = g.group_name || g.group_id;
  if (!await confirmAction(`「${name}」恢复跟随全局？`, "会删除这个群的单独转发规则和总结规则，之后的新消息按全局设置处理。", "恢复跟随全局", "danger")) return;
  const cfg = await bridge.apiPost("groups/rules", { group_id: g.group_id, rules: null, revision: S.config.revision });
  applyConfig(cfg, keepDraft()); renderGroupRules(); toast("已恢复跟随全局", { tone: "ok" });
}
function renderGroupRulesNote() {
  if (!S.config) return;
  const rules = S.config.values.group_rules || {}, nameOf = id => S.groups.find(g => g.group_id === id)?.group_name || id;
  for (const [id, sec] of [["rules-group-note", 0], ["summary-group-note", 1]]) {
    const el = $(id); if (!el) continue;
    const [, title, , keys] = GROUP_RULE_SECTIONS[sec], ids = Object.keys(rules).filter(gid => keys.some(k => k in rules[gid]));
    el.hidden = !ids.length;
    el.textContent = ids.length ? `${ids.map(nameOf).join("、")} 使用单独的${title}，不受这里影响；可在该群工作区的「设置」标签修改。` : "";
  }
}

// —— 群文件：打开「文件」标签时实时向 QQ 读取，不保存；下载链接临时获取 ——
S.files = { key: "", path: [], seq: 0 };
function fmtSize(n) {
  n = Number(n) || 0; if (n < 1024) return n + " B";
  const units = ["KB", "MB", "GB", "TB"]; let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
  return (n >= 100 ? n.toFixed(0) : n >= 10 ? n.toFixed(1) : n.toFixed(2)) + " " + units[i];
}
function attachList(m) { try { const a = JSON.parse(m.attachments || "[]"); return Array.isArray(a) ? a.filter(x => x && x.name) : []; } catch { return []; } }
function fileCard(f) { const c = node("span", undefined, "file-card"); c.title = f.name; c.append(icon("file"), node("span", f.name, "file-name")); if (f.size) c.append(node("small", fmtSize(f.size))); return c; }
function filesNode(m) { const a = attachList(m); if (!a.length) return null; const box = node("div", undefined, "bubble-files"); a.forEach(f => box.append(fileCard(f))); return box; }
function numCell(v) { const td = cell(v); td.className = "num"; return td; }
async function loadFiles(reset = false) {
  const g = currentGroup(), box = $("ws-files");
  if (!g) { empty(box, "请先选择一个群", "群文件按群查看，在左侧群列表里选一个群。"); return; }
  if (reset || S.files.key !== S.ws.key) { S.files.key = S.ws.key; S.files.path = []; box.replaceChildren(); }
  const folder = S.files.path[S.files.path.length - 1], seq = ++S.files.seq, args = { group_id: g.group_id };
  if (g.platform) args.platform = g.platform;
  if (folder) args.folder_id = folder.id;
  if (!box.childElementCount) box.replaceChildren(node("div", "正在向 QQ 读取群文件…", "muted pad"));
  box.classList.add("loading");
  try {
    const d = await bridge.apiGet("files/list", args);
    if (seq === S.files.seq) renderFiles(d);
  } catch (e) {
    if (seq !== S.files.seq) return;
    const card = node("section", undefined, "card"); box.replaceChildren(card);
    empty(card, "读取群文件失败", e?.message || "QQ 可能未连接，请稍后重试。", ["重试", () => loadFiles()]);
  } finally { if (seq === S.files.seq) box.classList.remove("loading"); }
}
function renderFiles(d) {
  const box = $("ws-files"), card = node("section", undefined, "card"), bar = node("div", undefined, "toolbar files-bar"), crumbs = node("nav", undefined, "crumbs");
  crumbs.setAttribute("aria-label", "文件夹路径");
  const levels = [{ id: "", name: "群文件" }, ...S.files.path];
  levels.forEach((lv, i) => {
    if (i) crumbs.append(node("span", "/", "crumb-sep"));
    if (i === levels.length - 1) crumbs.append(node("strong", lv.name));
    else crumbs.append(button(lv.name, () => { S.files.path = S.files.path.slice(0, i); return loadFiles(); }, "text-button"));
  });
  const u = d.usage, info = node("span", undefined, "muted");
  info.textContent = `${fmt(d.folders.length)} 个文件夹 · ${fmt(d.files.length)} 个文件`
    + (u ? ` · 全群共 ${fmt(u.file_count)} 个` + (u.total_space ? `，已用 ${fmtSize(u.used_space)} / ${fmtSize(u.total_space)}` : "") : "");
  bar.append(crumbs, node("span", undefined, "toolbar-spacer"), info, button("刷新", () => loadFiles(), "button secondary small"));
  card.append(bar, node("p", "列表实时从 QQ 读取，插件不保存文件；点一个文件获取临时下载链接，链接过一段时间会失效。", "search-hint muted"));
  if (d.truncated) card.append(node("p", `这个文件夹文件较多，只显示最新的 ${fmt(d.files.length)} 个。`, "search-hint muted"));
  const list = node("div"); card.append(list); box.replaceChildren(card);
  if (!d.folders.length && !d.files.length) { empty(list, S.files.path.length ? "文件夹是空的" : "这个群还没有群文件", "QQ 返回的列表为空。"); return; }
  const { wrap, body } = table(["名称", ["大小", "num"], "上传者", ["时间", "nowrap"], ["下载次数", "num"], ["", "right"]], "files-table");
  for (const f of d.folders) {
    const tr = clickableRow(() => act(null, () => { S.files.path.push({ id: f.folder_id, name: f.name }); return loadFiles(); }), `打开文件夹 ${f.name}`);
    const nm = node("div", undefined, "file-cell"), ic = node("span", undefined, "file-icon folder"); ic.append(icon("folder")); nm.append(ic, node("strong", f.name));
    tr.append(cell(nm), numCell(f.count ? fmt(f.count) + " 个" : "—"), cell(node("span", f.creator_name || "—", "muted")),
      cell(f.create_time ? timeNode(f.create_time) : node("span", "—", "muted")), numCell(""), rightCell(node("span", "打开 →", "muted")));
    body.append(tr);
  }
  const now = Date.now() / 1000;
  for (const f of d.files) {
    const tr = clickableRow(() => act(null, () => downloadFile(f)), `下载 ${f.name}`);
    const nm = node("div", undefined, "file-cell"), ic = node("span", undefined, "file-icon"), txt = node("div", undefined, "file-text");
    ic.append(icon("file")); txt.append(node("strong", f.name));
    if (f.dead_time) txt.append(node("small", (f.dead_time < now ? "已过期 · " : "临时文件，到期 ") + ymd(f.dead_time), f.dead_time < now ? "error-text" : "muted"));
    nm.append(ic, txt);
    tr.append(cell(nm), numCell(fmtSize(f.size)), cell(node("span", f.uploader_name || f.uploader || "—", "muted")),
      cell(f.upload_time ? timeNode(f.upload_time) : node("span", "—", "muted")), numCell(fmt(f.download_times)), rightCell(button("下载", () => downloadFile(f), "button secondary small")));
    body.append(tr);
  }
  list.replaceChildren(wrap);
}
async function downloadFile(f) {
  const g = currentGroup(); if (!g) return;
  const args = { group_id: g.group_id, file_id: f.file_id, name: f.name || "" };  // 后端据此给链接加上文件名
  if (f.busid) args.busid = f.busid; if (g.platform) args.platform = g.platform;
  const { url } = await bridge.apiGet("files/url", args);
  if (!/^https?:\/\//i.test(String(url || ""))) throw new Error("QQ 返回的下载链接无效");
  // 插件页在沙盒里（只有 allow-downloads，没有 allow-popups），不能开新窗口；
  // 在本页直接跳到下载链接，QQ 返回 Content-Disposition: attachment，浏览器只下载、不离开页面
  const a = document.createElement("a"); a.href = url; a.rel = "noreferrer"; a.download = f.name || ""; document.body.append(a); a.click(); a.remove();
  toast(`正在下载「${f.name}」；浏览器没有开始下载的话，复制链接到浏览器打开（链接会过期）`, { tone: "ok", action: ["复制链接", () => copyLink(url, f.name)] });
}
// 面板多半是 http 访问，没有 navigator.clipboard；退回 execCommand，再不行就弹出可选中的链接让用户手动复制
async function copyLink(url, name) {
  if (await copyText(url)) { toast("已复制下载链接", { tone: "ok" }); return; }
  const input = node("input", undefined, "link-input"); input.type = "text"; input.readOnly = true; input.value = url;
  input.onfocus = () => input.select();
  openDialog("复制下载链接", [node("p", `浏览器不允许自动复制。链接已选中，按 Ctrl+C（Mac 按 ⌘+C）复制，再粘贴到新标签页打开即可下载「${name}」。链接会过期，失效后重新点一次下载。`, "muted"), input]);
  input.focus(); input.select();
}
async function copyText(text) {
  try { if (window.isSecureContext && navigator.clipboard) { await navigator.clipboard.writeText(text); return true; } } catch { /* 沙盒或权限拦截，走下面的老办法 */ }
  const ta = node("textarea"); ta.value = text; ta.readOnly = true; ta.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0";
  document.body.append(ta); ta.select();
  let ok = false; try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove(); return ok;
}

// —— 列表：消息、转发、总结 ——
function searchTerms() { return $("message-search").value.trim().split(/\s+/).filter(Boolean); }
function messageFiltered() { return Boolean($("message-search").value.trim() || $("message-sender").value.trim() || S.ws.range !== "all"); }
async function loadList(name) {
  const [prefix, api] = LIST[name], box = $(prefix + "-list"), args = { page: S.pages[name] };
  if (name === "messages") Object.assign(args, wsScope(), rangeArgs(), { search: $("message-search").value.trim(), sender: $("message-sender").value.trim() });
  else if (name === "wsSummaries") Object.assign(args, wsScope(), { kind: "summary" });
  else Object.assign(args, selectedGroup(prefix + "-group"), { kind: name === "forwards" ? "match" : "summary", status: S.filters[name] });
  for (const k of Object.keys(args)) if (args[k] === "" || args[k] === undefined) delete args[k];
  const req = (S.requests[name] || 0) + 1; S.requests[name] = req; box.classList.add("loading");
  try {
    const data = await bridge.apiGet(api, args);
    if (S.requests[name] !== req) return;
    const last = Math.max(1, Math.ceil(data.total / data.page_size));
    if (!data.items.length && data.page > last) { S.pages[name] = last; return loadList(name); }
    if (name === "messages") { messageTable(box, data.items); updateFill(); }
    else if (name === "forwards") forwardTable(box, data.items);
    else summaryTable(box, data.items, name === "summaries" && S.filters.summaries);
    pagination($(prefix + "-pager"), data, name);
  } finally { if (S.requests[name] === req) box.classList.remove("loading"); }
}
function pagination(box, data, name) {
  const total = Math.max(1, Math.ceil(data.total / data.page_size));
  if (data.total <= data.page_size) { box.replaceChildren(node("span", `共 ${fmt(data.total)} 条`, "muted")); return; }
  const prev = button("上一页", async () => { S.pages[name]--; await loadList(name); }, "button secondary small"), next = button("下一页", async () => { S.pages[name]++; await loadList(name); }, "button secondary small");
  prev.disabled = data.page <= 1; next.disabled = data.page >= total;
  box.replaceChildren(node("span", `共 ${fmt(data.total)} 条 · 第 ${data.page} / ${total} 页`, "muted"), prev, next);
}
function messageTable(box, items) {
  if (!items.length) { const f = messageFiltered(); empty(box, "没有找到消息", f ? "调整关键词、发言人或时间范围后再试。" : "监控群收到新消息后会出现在这里。", f && ($("message-search").value || $("message-sender").value) ? ["清除筛选", resetMessages] : null); return; }
  const all = !S.ws.key, terms = searchTerms(), { wrap, body } = table(all ? ["发言人", "消息内容", "群聊", ["时间", "nowrap"]] : ["发言人", "消息内容", ["时间", "nowrap"]], "messages-table");
  for (const m of items) {
    const tr = clickableRow(() => act(null, () => jumpTo(m.id)), `${m.sender_name} 的消息，跳到聊天位置`);
    tr.append(cell(person(m.sender_name, m.sender_id)), cell(snippet(m.text, m.outline, terms, "snippet", false), filesNode(m)));
    if (all) tr.append(cell(node("span", m.group_name, "muted ellipsis")));
    tr.append(cell(timeNode(m.source_time))); body.append(tr);
  }
  box.replaceChildren(wrap);
}
function statusCell(j) {
  const td = cell(statusBadge(j.status));
  if (j.last_error) td.append(node("small", j.last_error, "error-text"));
  else if (j.next_at > Date.now() / 1000 && !["failed", "done"].includes(j.status)) td.append(node("small", (j.status === "collecting" ? "后文等待至 " : "下次尝试 ") + rel(j.next_at)));
  return td;
}
function retryButton(j) { return j.status === "failed" ? button("重试", () => retry(j), "button secondary small") : null; }
function forwardTable(box, items) {
  if (!items.length) { empty(box, S.filters.forwards ? "没有该状态的转发任务" : "暂无转发记录", S.filters.forwards ? "换个状态筛选看看。" : "配置接收人和关键词后，命中的新消息会在这里出现。", S.filters.forwards ? null : ["编辑关键词规则", () => navigate("rules")]); return; }
  const { wrap, body } = table([["任务", "nowrap"], "命中内容", "来源群", "状态", ["时间", "nowrap"], ["", "right"]]);
  for (const j of items) {
    const tr = clickableRow(() => showJob(j.id), `转发任务 ${j.id}，查看详情`), what = node("div", undefined, "hit-cell"), chips = node("div", undefined, "chips");
    (j.hits || []).forEach(h => chips.append(chip(h)));
    if (chips.childElementCount) what.append(chips);
    if (j.preview?.text) { const line = node("div", undefined, "snippet one"); if (j.preview.sender_name) line.append(node("b", j.preview.sender_name + "：")); line.append(highlight(j.preview.text, j.hits)); what.append(line); }
    tr.append(cell(node("strong", "#" + j.id), node("small", `${j.record_count} 条 · ${j.delivery_mode === "card" ? "卡片" : "文字"}`)), cell(what), cell(node("span", j.group_name, "ellipsis")), statusCell(j), cell(timeNode(j.created_at)), rightCell(retryButton(j)));
    body.append(tr);
  }
  box.replaceChildren(wrap);
}
function summaryTable(box, items, filtered = false) {
  if (!items.length) { empty(box, filtered ? "没有该状态的总结任务" : "暂无总结任务", filtered ? "换个状态筛选看看。" : "每个群凑满设定条数后会自动创建一批总结。"); return; }
  const { wrap, body } = table([["任务", "nowrap"], "总结预览", "来源群", "状态", ["时间", "nowrap"], ["", "right"]]);
  for (const j of items) {
    const tr = clickableRow(() => showJob(j.id), `总结任务 ${j.id}，查看详情`), preview = j.preview?.text ? node("div", j.preview.text, "snippet two") : node("span", "尚未生成", "muted");
    const st = statusCell(j); st.append(node("small", `AI ${j.generation_calls} 次 · 已发 ${j.delivery_progress} 段`));
    tr.append(cell(node("strong", "#" + j.id), node("small", `${j.record_count} 条消息`)), cell(preview), cell(node("span", j.group_name, "ellipsis")), st, cell(timeNode(j.created_at)), rightCell(retryButton(j)));
    body.append(tr);
  }
  box.replaceChildren(wrap);
}
function recentJobs(items) {
  const box = $("recent-jobs");
  if (!items.length) { empty(box, "暂无任务", "配置接收 QQ 后，命中消息和凑齐的群消息会创建任务。", ["前往插件设置", () => navigate("settings")]); return; }
  const { wrap, body } = table(["任务", "内容", "状态", ["时间", "nowrap"]], "compact");
  for (const j of items) {
    const tr = clickableRow(() => showJob(j.id), `任务 ${j.id}，查看详情`), title = node("div", undefined, "job-title"), kind = node("span", undefined, "kind " + j.kind);
    kind.append(icon(j.kind === "match" ? "send" : "spark")); title.append(kind, node("strong", `#${j.id}`), node("small", j.group_name));
    const text = j.preview?.text ? (j.kind === "match" && j.preview.sender_name ? j.preview.sender_name + "：" : "") + j.preview.text : (j.kind === "match" ? "关键词转发" : "AI 总结");
    tr.append(cell(title), cell(node("div", text, "snippet one")), cell(statusBadge(j.status)), cell(timeNode(j.created_at))); body.append(tr);
  }
  box.replaceChildren(wrap);
}
function renderStatusFilter(page) { segmented($(page === "forwards" ? "forward-status" : "summary-status"), STATUS_FILTERS[page], S.filters[page], value => { S.filters[page] = value; S.pages[page] = 1; renderStatusFilter(page); act(null, () => loadList(page)); }); }
renderStatusFilter("forwards"); renderStatusFilter("summaries");
for (const [id, page] of [["forward-group", "forwards"], ["summary-group", "summaries"]]) $(id).onchange = () => act(null, async () => { S.pages[page] = 1; await loadList(page); });

const queryMessages = () => act(null, () => { S.pages.messages = 1; S.chat.jump = 0; return loadMessages(); });
const queryMessagesSoon = debounce(queryMessages, 350);
function resetMessages() { $("message-search").value = ""; $("message-sender").value = ""; return queryMessages(); }
for (const id of ["message-search", "message-sender"]) { $(id).oninput = queryMessagesSoon; $(id).onkeydown = e => { if (e.key === "Enter") queryMessages(); }; }
$("reset-messages").onclick = resetMessages;
$("export").onclick = () => act($("export"), async () => { await bridge.download("export", wsScope(), "群聊记录.jsonl"); toast("已开始下载：导出所选群的全部记录，不受搜索条件和时间范围影响"); });

// —— 消息：仿 QQ 的气泡聊天流（默认）和表格两种视图；有搜索词时列出结果，点一条跳到聊天位置 ——
const VIEW_KEY = "group-digest-msg-view";
S.msgView = store.get(VIEW_KEY, "chat") === "table" ? "table" : "chat";
S.chat = { key: "", items: [], hasOlder: false, hasNewer: false, focus: 0, jump: 0, busy: false };
function renderMessageView() {
  segmented($("message-view"), [["chat", "聊天"], ["table", "表格"]], S.msgView, value => {
    S.msgView = value; store.set(VIEW_KEY, value); S.chat.jump = 0; S.pages.messages = 1; renderMessageView(); act(null, () => loadMessages());
  });
}
renderMessageView();
function searching() { return Boolean($("message-search").value.trim()); }
function chatScope() { const a = { ...wsScope(), ...rangeArgs() }, sender = $("message-sender").value.trim(); if (sender) a.sender = sender; return a; }
function chatKey() { return JSON.stringify([wsScope(), S.ws.range, $("message-sender").value.trim(), S.chat.jump]); }
async function loadMessages(auto = false) {
  if (!S.chat.jump && (searching() || S.msgView === "table")) { S.chat.key = ""; return loadList("messages"); }
  if (auto && S.chat.key === chatKey()) return chatAppendNewer();  // 自动刷新只追加新消息，不打断阅读位置
  const key = chatKey(), req = (S.requests.chat || 0) + 1, args = { ...chatScope(), limit: 60 };
  S.requests.chat = req; if (S.chat.jump) args.around = S.chat.jump;
  $("message-list").classList.add("loading");
  try {
    const d = await bridge.apiGet("messages/timeline", args);
    if (S.requests.chat !== req) return;
    Object.assign(S.chat, { key, items: d.items, hasOlder: d.has_older, hasNewer: d.has_newer, focus: d.focus || 0 });
    renderChat(S.chat.jump ? "focus" : "bottom");
  } finally { if (S.requests.chat === req) $("message-list").classList.remove("loading"); }
}
function chatFetch(extra) { return bridge.apiGet("messages/timeline", { ...chatScope(), ...extra }); }
async function chatAppendNewer() {
  const last = S.chat.items[S.chat.items.length - 1];
  if (S.chat.hasNewer || !last) return;
  const d = await chatFetch({ after: last.id, limit: 100 });
  if (!d.items.length || S.chat.items[S.chat.items.length - 1] !== last) return;
  const box = chatBox(), atBottom = !box || box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  S.chat.items = S.chat.items.concat(d.items); S.chat.hasNewer = d.has_newer;
  if (S.chat.items.length > 800) { S.chat.items = S.chat.items.slice(-800); S.chat.hasOlder = true; }
  renderChat(atBottom ? "bottom" : "keep");
}
async function chatOlder() {
  const first = S.chat.items[0];
  if (S.chat.busy || !S.chat.hasOlder || !first) return;
  S.chat.busy = true;
  try {
    const d = await chatFetch({ before: first.id, limit: 60 });
    if (S.chat.items[0] !== first) return;
    S.chat.items = d.items.concat(S.chat.items); S.chat.hasOlder = d.has_older; renderChat("prepend");
  } finally { S.chat.busy = false; }
}
async function chatNewer() {
  const last = S.chat.items[S.chat.items.length - 1];
  if (S.chat.busy || !S.chat.hasNewer || !last) return;
  S.chat.busy = true;
  try {
    const d = await chatFetch({ after: last.id, limit: 60 });
    if (S.chat.items[S.chat.items.length - 1] !== last) return;
    S.chat.items = S.chat.items.concat(d.items); S.chat.hasNewer = d.has_newer; renderChat("keep");
  } finally { S.chat.busy = false; }
}
function jumpTo(id) { S.chat.jump = id; return loadMessages(); }
function leaveJump() { S.chat.jump = 0; return loadMessages(); }
function chatBox() { return $("message-list").querySelector(".chat"); }
// 聊天视图像 QQ 一样铺满窗口，只保留聊天框自己的滚动条（窄屏仍按页面滚动）
function updateFill() { document.body.classList.toggle("chat-fill", S.page === "group" && S.ws.tab === "messages" && Boolean(chatBox())); }
function chatStamp(ts) {
  const p = bj(ts), now = bj(Date.now() / 1000), hm = `${pad(p.H)}:${pad(p.M)}`;
  if (p.day === now.day) return "今天 " + hm;
  if (p.day === now.day - 1) return "昨天 " + hm;
  return (p.y === now.y ? "" : p.y + "年") + `${p.m}月${p.d}日 ${hm}`;
}
function renderChat(mode) {
  const c = S.chat, old = chatBox(), oldHeight = old ? old.scrollHeight : 0, oldTop = old ? old.scrollTop : 0;
  const wrap = node("div", undefined, "chat-wrap"), box = node("div", undefined, "chat");
  box.setAttribute("role", "log"); box.setAttribute("aria-label", "聊天记录");
  if (c.jump) {
    const bar = node("div", undefined, "chat-bar"), back = searching() ? "← 返回搜索结果" : S.msgView === "table" ? "← 返回列表" : "回到最新消息";
    bar.append(node("span", searching() ? "已跳到这条消息所在位置，可以上下翻看前后的聊天" : "正在查看这条消息附近的聊天"), button(back, leaveJump, "button secondary small"));
    wrap.append(bar);
  }
  wrap.append(box); $("message-list").replaceChildren(wrap); updateFill();
  if (!c.items.length) {
    empty(box, "没有消息", messageFiltered() ? "换个发言人或时间范围试试。" : "监控群收到新消息后会出现在这里。");
    $("message-pager").replaceChildren(); return;
  }
  if (c.hasOlder) { const m = node("div", undefined, "chat-more"); m.append(button("加载更早的消息", chatOlder, "button secondary small")); box.append(m); }
  else box.append(node("div", "没有更早的消息了", "chat-end"));
  const me = new Set((S.config?.values.recipients || []).map(String)), all = !S.ws.key, terms = c.jump && searching() ? searchTerms() : [];
  let prev = null, focusEl = null;
  for (const m of c.items) {
    const gap = !prev || m.source_time - prev.source_time > 600 || bj(m.source_time).day !== bj(prev.source_time).day;
    if (gap) box.append(node("div", chatStamp(m.source_time), "chat-divider"));
    // 同一人 5 分钟内连发的消息合成一组，只显示一次头像和昵称
    const cont = !gap && prev.sender_id === m.sender_id && prev.group_id === m.group_id && m.source_time - prev.source_time < 300;
    const mine = me.has(String(m.sender_id)), row = node("div", undefined, "chat-row" + (cont ? " cont" : "") + (mine ? " me" : "") + (m.id === c.focus ? " focus" : ""));
    const side = node("div", undefined, "chat-side"), main = node("div", undefined, "chat-main"), who = () => act(null, () => filterSender(m.sender_id));
    if (!cont) { const av = avatar(m.sender_name, m.sender_id); av.title = `只看 ${m.sender_name || m.sender_id} 的消息`; av.onclick = who; side.append(av); }
    const p = bj(m.source_time), hm = `${pad(p.H)}:${pad(p.M)}`;
    if (!cont) {
      // 每组消息的昵称旁显示发送时间；自己的消息不显示昵称，只显示时间
      const head = node("div", undefined, "chat-head");
      if (!mine || all) {
        const name = node("button", undefined, "chat-name"); name.type = "button"; name.title = `${m.sender_id} · 只看此人的消息`; name.onclick = who;
        if (!mine) name.append(node("span", m.sender_name || m.sender_id));
        if (all) name.append(chip(m.group_name, "soft"));
        head.append(name);
      }
      const t = node("time", hm, "chat-time"); t.title = fullDate(m.source_time); head.append(t);
      main.append(head);
    } else {
      // 连发的后续消息：悬停时在头像位置显示时间
      const t = node("time", hm, "chat-hover-time"); t.title = fullDate(m.source_time); side.append(t);
    }
    const bubble = node("div", undefined, "bubble" + (m.text ? "" : " media")); bubble.title = fullDate(m.source_time);
    const fl = filesNode(m);
    if (m.text) { bubble.append(terms.length ? highlight(m.text, terms, false) : m.text); const o = outlineChips(m.outline); if (o) bubble.append(o); }
    else if (!fl) bubble.append(outlineText(m.outline) || "[消息]");
    if (fl) { bubble.append(fl); bubble.classList.remove("media"); }
    main.append(bubble); row.append(side, main); box.append(row);
    if (m.id === c.focus) focusEl = row;
    prev = m;
  }
  if (c.hasNewer) { const m = node("div", undefined, "chat-more"); m.append(button("加载更新的消息", chatNewer, "button secondary small")); box.append(m); }
  $("message-pager").replaceChildren(node("span", `已载入 ${fmt(c.items.length)} 条 · 滚到顶部会自动加载更早的消息`, "muted"));
  if (mode === "bottom") box.scrollTop = box.scrollHeight;
  else if (mode === "prepend") box.scrollTop = box.scrollHeight - oldHeight + oldTop;
  else if (mode === "keep") box.scrollTop = oldTop;
  else if (mode === "focus" && focusEl) box.scrollTop = focusEl.offsetTop - box.clientHeight / 2 + focusEl.offsetHeight / 2;
  box.onscroll = () => { if (box.scrollTop < 60 && c.hasOlder && !c.busy) act(null, chatOlder); };
}

// —— 任务详情 ——
async function showJob(id) {
  openDialog(`任务 #${id}`, [node("div", "正在载入…", "muted pad")]);
  try { renderJob(await bridge.apiGet("jobs/detail", { id })); } catch (e) { openDialog(`任务 #${id}`, [node("div", e?.message || "载入失败", "error-text pad")]); }
}
function renderJob(j) {
  const match = j.kind === "match", now = Date.now() / 1000, unit = j.delivery_mode === "card" ? " 张" : " 段";
  const parts = [metaGrid([["来源群", j.group_name], ["状态", statusBadge(j.status)], ["消息数量", j.record_count + " 条"], match ? ["投递形式", j.delivery_mode === "card" ? "QQ 合并转发卡片" : "文字通知"] : ["AI 调用", j.generation_calls + " 次"], ["已投递", j.delivery_progress + unit], ["创建时间", fullDate(j.created_at)], ["完成时间", j.completed_at ? fullDate(j.completed_at) : null], ["下次尝试", j.next_at > now && !["done", "failed"].includes(j.status) ? fullDate(j.next_at) : null], ["失败原因", j.last_error || null]])];
  if (match) {
    if (j.hits.length) { const c = node("div", undefined, "chips"); j.hits.forEach(h => c.append(chip(h))); parts.push(section("命中关键词", c)); }
    const list = node("ol", undefined, "msg-list");
    for (const r of j.records) {
      const li = node("li", undefined, "msg" + (r.matched ? " matched" : "")), head = node("div", undefined, "msg-head");
      head.append(avatar(r.sender_name, r.sender_id), node("strong", r.sender_name), timeNode(r.source_time)); if (r.matched) head.append(chip("命中", "hit"));
      li.append(head, snippet(r.text, r.outline, r.matched ? j.hits : [], "msg-body")); list.append(li);
    }
    parts.push(section(j.delivery_mode === "card" ? "卡片包含的消息" : "转发的消息", j.records.length ? list : node("p", "原消息记录不可用。", "muted"), j.records.length < j.record_count ? node("p", `仅显示前 ${j.records.length} 条。`, "muted") : null));
  } else {
    parts.push(section("总结内容", node("div", j.result || "尚未生成总结。", "detail-content" + (j.result ? "" : " muted"))));
  }
  const buttons = [];
  if (j.status === "failed") buttons.push(button("重试任务", () => retry(j), "button"));
  buttons.push(button("查看来源群消息", async () => { $("details-dialog").close(); await openGroup(groupValue(j), "messages"); }, "button secondary"));
  openDialog(`${match ? "关键词转发" : "AI 总结"} #${j.id}`, parts, buttons);
}
async function retry(j) {
  const text = j.kind === "match" ? "按当前配置重新投递这条转发。" : "已有总结只重试发送；尚未生成的总结会再次调用模型，可能产生费用。";
  if (!await confirmAction(`重试任务 #${j.id}？`, text, "重试任务")) return;
  await bridge.apiPost("jobs/retry", { id: j.id }); if ($("details-dialog").open) $("details-dialog").close();
  toast("重试请求已接受，后台正在处理", { tone: "ok" }); await refresh();
}

// —— 规则测试：按当前草稿实时匹配，不保存 ——
let previewSeq = 0;
const runPreview = debounce(async () => {
  const text = $("preview-text").value, box = $("preview-result");
  if (!text.trim()) { box.replaceChildren(node("span", "输入或粘贴一段消息，结果会自动显示。", "muted")); return; }
  const seq = ++previewSeq, d = draft(), values = { ...S.config.values }, usingDraft = RULE_KEYS.some(k => S.dirtyKeys.includes(k));
  for (const k of RULE_KEYS) values[k] = d[k];
  try { const r = await bridge.apiPost("match/preview", { text, values }); if (seq === previewSeq) renderPreview(text, r.hits, values, usingDraft); }
  catch (e) { if (seq === previewSeq) box.replaceChildren(node("div", e?.message || "测试失败", "error-text")); }
}, 400);
function schedulePreview() { if (S.config && bridge) runPreview(); }
$("preview-text").oninput = schedulePreview;
function renderPreview(text, hits, v, usingDraft) {
  const head = node("div", undefined, "preview-head"), fold = s => v.case_sensitive ? s : s.toLowerCase();
  head.append(hits.length ? badge("命中 " + hits.length + " 个关键词", "ok") : badge("未命中", "muted"));
  if (usingDraft) head.append(chip("按未保存的规则", "soft"));
  const notes = [];
  if (!hits.length) {
    const ex = v.exclude_keywords.find(w => fold(text).includes(fold(w)));
    if (!v.keywords.length) notes.push("尚未填写关键词。");
    else if (ex) notes.push(`包含排除词「${ex}」，因此不转发。`);
    else if (v.match_mode === "all") notes.push("当前要求同时包含全部关键词。");
  }
  if (!v.forward_enabled) notes.push("关键词转发当前已关闭，实际不会转发。");
  const out = [head, node("div", undefined, "preview-text")];
  out[1].append(highlight(text, hits, v.case_sensitive));
  notes.forEach(n => out.push(node("p", n, "muted")));
  $("preview-result").replaceChildren(...out);
}

// —— 总览 ——
function stat(name, value, label, sub, tone, go) { const b = node("button", undefined, "stat"); b.type = "button"; b.onclick = () => act(b, go); const mark = node("span", undefined, "stat-icon " + name); mark.append(icon(name)); const body = node("div", undefined, "stat-body"); body.append(node("span", label, "stat-label"), node("strong", fmt(value)), node("span", sub, "stat-sub " + (tone || ""))); b.append(mark, body); return b; }
function renderStatus(s) {
  S.status = s; $("version").textContent = "v" + s.version;
  const failed = s.jobs.failed || 0, pending = Object.entries(s.jobs).filter(([k]) => !["done", "failed"].includes(k)).reduce((n, [, v]) => n + v, 0), byKind = s.failed_by_kind || {};
  const budget = S.config?.values.max_llm_calls_per_day;
  $("stats").replaceChildren(
    stat("message", s.message_count, "累计消息", `今日 ${fmt(s.messages_today)} 条 · 昨日 ${fmt(s.messages_yesterday)} 条`, "", () => openGroup("", "messages")),
    stat("users", s.joined_group_count || s.group_count, "已加入群聊", `${s.group_count} 个群有记录`, "", () => openGroup("", "insight")),
    stat("file", pending, "进行中任务", failed ? `${failed} 个失败，点击查看` : "没有失败任务", failed ? "bad" : "ok", () => failed ? goFailed(byKind.match ? "match" : "summary") : navigate("forwards")),
    stat("spark", s.llm_calls_today, "今日 AI 调用", `昨日 ${fmt(s.llm_calls_yesterday)} 次${budget ? ` · 每日上限 ${fmt(budget)}` : ""}`, budget && s.llm_calls_today >= budget * .8 ? "warn" : "", () => navigate("summaries")));
  const offline = s.backfill_health === "等待QQ连接" || Object.values(s.group_directory_health || {}).some(x => String(x).includes("离线"));
  const worker = x => [String(x || "未启动"), x === "运行中" ? "ok" : "bad"];
  const lines = [["记录与任务", s.enabled ? ["已启用", "ok"] : ["已暂停", "warn"]], ["QQ 连接", offline ? ["未连接", "bad"] : ["在线", "ok"]], ["总结队列", worker(s.workers.generate)], ["投递队列", worker(s.workers.deliver)], ["接收人", s.recipient_count ? [s.recipient_count + " 位", "ok"] : ["未配置", "warn"]], ["自动补录", [s.backfill_health || "等待检查", /异常|失败/.test(s.backfill_health || "") ? "bad" : "ok"]], ["群聊响应", [s.silent_mode ? "静默模式" : "正常响应", "muted"]]];
  $("runtime").replaceChildren(...lines.map(([label, [value, tone]]) => { const n = node("div", undefined, "runtime-line"); n.append(node("span", label), badge(value, tone)); return n; }));
  for (const [kind, id] of [["match", "badge-forwards"], ["summary", "badge-summaries"]]) { const n = byKind[kind] || 0; $(id).hidden = !n; $(id).textContent = n; $(id).title = `${n} 个失败任务`; }
  $("summary-pending").textContent = fmt(s.pending_message_count) + " 条";
  $("summary-progress").textContent = `待凑批（每群满 ${s.threshold} 条生成一批）` + (s.summary_excluded ? `，另有 ${fmt(s.summary_excluded)} 条旧记录尚未计入` : "");
  $("include-summary-history").hidden = !s.summary_excluded;
  if (S.page === "group" && S.ws.tab === "summaries") renderWsSummaryHead();
  const workerError = [s.workers.generate, s.workers.deliver].find(x => String(x).startsWith("后台异常"));
  if (s.ingest_error) notice(s.ingest_error, "bad");
  else if (offline) notice("QQ 当前未连接，暂时无法接收和转发新消息。请在 NapCat 扫码登录后刷新。", "bad");
  else if (workerError) notice("后台任务出现异常：" + workerError, "bad");
  else if (failed) notice(`有 ${failed} 个失败任务，可以查看原因并重试。`, "bad", ["查看失败任务 →", () => goFailed(byKind.match ? "match" : "summary")]);
  else if (!s.recipient_count) notice("尚未配置接收 QQ：当前只记录消息，不调用 AI、不发送消息。", "warn", ["设置接收人 →", () => navigate("settings")]);
  else if (!s.enabled) notice("插件已暂停，历史记录与任务仍保留。", "warn");
  else notice("");
}
function renderActivity(rows) {
  const box = $("activity");
  if (!rows.some(r => r.count)) { empty(box, "近 7 天暂无消息记录"); return; }
  const total = rows.reduce((n, r) => n + r.count, 0);
  box.replaceChildren(barChart({ labels: rows.map((r, i) => i === rows.length - 1 ? "今天" : r.day.slice(5)), values: rows.map(r => r.count), highlight: [rows.length - 1], tips: rows.map(r => [r.day, `${fmt(r.count)} 条消息`]), caption: `7 天共 ${fmt(total)} 条 · 日均 ${fmt(Math.round(total / 7))} 条`, aria: "近 7 天消息数量" }));
}
function renderBackfill(s) {
  const rows = s.backfill || [], box = $("backfill-status"), errors = rows.filter(r => r.last_error).length, inserted = rows.reduce((n, r) => n + (r.inserted_count || 0), 0), last = Math.max(0, ...rows.map(r => r.last_success || 0));
  const parts = [s.backfill_health || "等待检查", `${rows.length} 个群`, `累计补回 ${fmt(inserted)} 条`];
  if (last) parts.push("最近成功 " + rel(last)); if (errors) parts.push(`${errors} 个群待重试`);
  $("backfill-summary").textContent = parts.join(" · "); $("backfill-strip").classList.toggle("warn", Boolean(errors));
  if (!rows.length) { empty(box, "尚无补录检查进度", "启用自动补录后，会通过已连接的 QQ 核对各群消息。"); return; }
  const { wrap, body } = table(["群聊", "历史已核对至", ["累计补回", "num"], "状态"], "compact");
  for (const r of rows) {
    const g = S.groups.find(x => x.platform === r.platform && x.group_id === r.group_id) || { group_id: r.group_id, group_name: r.group_id, platform: r.platform }, tr = node("tr"), n = cell(fmt(r.inserted_count)); n.className = "num";
    const st = r.last_error ? badge("待重试：" + r.last_error, "bad") : r.upper_bound ? badge("翻页处理中", "run") : r.truncated_hours ? badge("已完成，较早缺口超出回溯上限", "warn") : badge("已核对", "ok");
    tr.append(cell(groupName(g)), cell(r.completed_to ? timeNode(r.completed_to) : "—"), n, cell(st)); body.append(tr);
  }
  box.replaceChildren(wrap);
}
$("backfill-toggle").onclick = () => { const open = $("backfill-status").hidden; $("backfill-status").hidden = !open; $("backfill-toggle").setAttribute("aria-expanded", String(open)); $("backfill-toggle").textContent = open ? "收起" : "展开各群进度"; };
$("run-backfill").onclick = () => act($("run-backfill"), async () => { await bridge.apiPost("backfill/run", {}); toast("已开始检查补录，稍后刷新查看进度"); await refresh(); });
$("include-summary-history").onclick = () => act($("include-summary-history"), async () => {
  if (S.dirtyKeys.length) throw new Error("请先保存或放弃当前配置修改，再选择补总结范围");
  const preview = await bridge.apiGet("summary/history");
  if (!preview.count) { toast("没有尚未计入的旧记录"); return; }
  if (!await confirmAction("将旧记录计入总结？", `监控范围内有 ${preview.count} 条旧记录未计入。确认后按每群 ${S.config.values.summary_threshold} 条排队调用 AI，并发送给当前配置的 ${S.config.values.recipients.length} 位接收人，可能产生模型费用。`, "计入并开始排队")) return;
  const result = await bridge.apiPost("summary/history", { through_id: preview.through_id, revision: S.config.revision });
  toast(`已将 ${result.count} 条旧记录计入，达到每群阈值后自动总结`, { tone: "ok" }); await refresh();
});

// —— 刷新、主题与自动刷新 ——
async function refresh(auto = false) {
  if (S.refreshing) return; S.refreshing = true;
  try {
    // 自动刷新只取当前页面需要的数据：总览图表和近期任务仅在总览页刷新
    const overview = !auto || S.page === "overview";
    const [settings, status, groups, activity, jobs, providers] = await Promise.all([bridge.apiGet("settings"), bridge.apiGet("status"), bridge.apiGet("groups"), overview ? bridge.apiGet("activity") : null, overview ? bridge.apiGet("jobs", { page: 1 }) : null, auto && S.config ? null : bridge.apiGet("providers")]);
    if (providers) S.providers = providers;
    S.groups = groups;
    // 没有草稿时才重新渲染表单，避免打断正在编辑的字段。
    if (!S.config || (!S.dirtyKeys.length && (settings.revision !== S.config.revision || providers))) applyConfig(settings);
    else if (settings.revision !== S.config.revision && !S.serverChanged) { S.serverChanged = true; changed(); }
    S.lastRefresh = Date.now();
    renderStatus(status); renderBackfill(status); populateGroups(); renderSessions(); overviewGroups(); renderScopeSummary();
    if (activity) renderActivity(activity);
    if (jobs) recentJobs(jobs.items.slice(0, 6));
    if (S.page === "group") { renderWorkspaceHeader(); await loadWorkspace(auto); }
    else if (LIST[S.page]) await loadList(S.page);
    setConnection("online", "已连接管理面板"); $("last-refresh").textContent = "更新于 " + clock();
  } catch (e) {
    setConnection("offline", auto ? "连接中断，稍后自动重试" : "连接失败"); throw e;
  } finally { S.refreshing = false; }
}
$("refresh").onclick = () => act($("refresh"), () => refresh());
// 默认深色（ChatLab 同款）；只有手动切换后才记住偏好。v1.6 换了存储键，旧版自动写入的“浅色”不再生效。
const THEME_KEY = "group-digest-theme-v2";
function applyTheme(light, persist = false) {
  document.body.classList.toggle("light", light);
  $("theme").replaceChildren(icon(light ? "moon" : "sun"), node("span", light ? "切换深色模式" : "切换浅色模式"));
  if (persist) store.set(THEME_KEY, light ? "light" : "dark");
}
applyTheme(store.get(THEME_KEY, "dark") === "light");
$("theme").onclick = () => applyTheme(!document.body.classList.contains("light"), true);
$("auto-refresh").checked = store.get("group-digest-auto-refresh", "1") === "1";
$("auto-refresh").onchange = () => store.set("group-digest-auto-refresh", $("auto-refresh").checked ? "1" : "0");
function autoRefresh() { if (!$("auto-refresh").checked || document.hidden || document.querySelector("dialog[open]") || !bridge || !S.config) return; refresh(true).catch(() => {}); }
setInterval(autoRefresh, 30000);
document.addEventListener("visibilitychange", () => { if (!document.hidden && Date.now() - S.lastRefresh > 30000) autoRefresh(); });

(async () => {
  setWsTab("insight"); setInsightSub("overview"); renderRange();
  await navigate("overview");
  try {
    bridge = window.AstrBotPluginView;
    if (!bridge) throw new Error("请从 AstrBot 左侧「群聊情报站 → 控制台」打开。");
    await bridge.ready(); await refresh();
  } catch (e) { setConnection("offline", "连接失败"); notice(e?.message || "无法连接面板", "bad"); }
})();
