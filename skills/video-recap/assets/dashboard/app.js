"use strict";
/*
 * video-recap 剪辑台 — the chrome: theme, routing, top bar, stage bar, search, clipboard.
 *
 * Strictly read-only. The server parses every artifact (GET /api/overview, /api/run,
 * /api/library, /api/project, /api/search); views.js only lays the rows out. Nothing here
 * writes, generates or renders: an action is one Chinese sentence copied for the assistant.
 * Every piece of project text is escaped before it reaches innerHTML, and data-driven
 * geometry goes through the CSSOM (data-css), because the CSP forbids inline styles.
 */

/* ---- theme: applied before anything renders (the CSP forbids an inline head script) */
const THEME_KEY = "video_recap_dashboard_theme";
const readTheme = () => {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : "auto";
  } catch (_error) {
    return "auto";
  }
};
const S = { theme: readTheme(), overview: null, data: null, seq: 0, palette: { sel: 0, items: [], timer: null, seq: 0 } };
function applyTheme() {
  if (S.theme === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.dataset.theme = S.theme;
}
applyTheme();
const isDark = () => S.theme === "dark" || (S.theme === "auto" && typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches);
function toggleTheme() {
  S.theme = isDark() ? "light" : "dark";
  try { localStorage.setItem(THEME_KEY, S.theme); } catch (_error) { /* private window: this page only */ }
  applyTheme();
  renderTop(parseRoute(location.hash));
}

/* ---- helpers */
const $ = (selector, root) => (root || document).querySelector(selector);
const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;
const mediaUrl = (rel) => `/api/media?path=${enc(rel)}`;
const num = (value) => (Number.isFinite(value) ? String(Math.round(value * 10) / 10) : "—");
function clock(seconds, precise = false) {
  if (!Number.isFinite(seconds)) return "—";
  const sign = seconds < 0 ? "-" : "";
  const total = Math.abs(seconds);
  const minutes = Math.floor(total / 60);
  const rest = total - minutes * 60;
  return `${sign}${minutes}:${precise ? rest.toFixed(1).padStart(4, "0") : String(Math.floor(rest)).padStart(2, "0")}`;
}
function size(bytes) {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${num(bytes / 1024)} KB`;
  return `${num(bytes / 1024 / 1024)} MB`;
}

const IC = {
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2.5 12h2M19.5 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/>',
  moon: '<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10Z"/>',
  alert: '<path d="M12 4 2.8 19.5h18.4L12 4Z"/><path d="M12 10v4.5M12 17.2v.3"/>',
  refresh: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4h-4"/>',
  right: '<path d="m9.5 6 6 6-6 6"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8v.3"/>',
  minus: '<path d="M7 12h10"/>',
};
const icon = (name) => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${IC[name] || ""}</svg>`;

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  let data = null;
  try { data = await response.json(); } catch (_error) { throw new Error("看板服务返回了无法读取的内容"); }
  if (!response.ok) throw new Error((data && data.error) || `HTTP ${response.status}`);
  return data;
}

/* ---- routes: #/  #/run/<rel>[/<view>]  #/library/<rel>[/<tab>]  #/project/<rel>  (+ ?query) */
function parseRoute(hash) {
  const raw = (hash || "").replace(/^#/, "") || "/";
  const [pathPart, qs = ""] = raw.split("?");
  const parts = pathPart.split("/").filter(Boolean);
  const q = new URLSearchParams(qs);
  if (["run", "library", "project"].includes(parts[0]) && parts[1]) {
    let path = parts[1];
    try { path = decodeURIComponent(parts[1]); } catch (_error) { /* keep the raw segment */ }
    return { page: parts[0], path, view: parts[2] || "", q };
  }
  return { page: "overview", path: "", view: "", q };
}
const href = (page, path, view = "", query = "") => `#/${page}/${enc(path)}${view ? `/${view}` : ""}${query ? `?${query}` : ""}`;
function go(hash) {
  if (location.hash === hash) render();
  else location.hash = hash;
}

/* ---- chrome */
function crumbsFor(route) {
  const O = S.overview;
  const crumbs = [`<a class="crumb home" href="#/"><span class="t">总览</span></a>`];
  const D = S.data;
  if (route.page === "overview" || !D) return crumbs.join("");
  const projectPath = route.page === "project" ? D.path : D.project;
  const project = projectPath && O ? O.projects.find((item) => item.path === projectPath) : null;
  if (project) crumbs.push(`<span class="crumb-sep">/</span><a class="crumb" href="${href("project", project.path)}"><span class="t">${esc(project.name)}</span></a>`);
  if (route.page === "run" && D.parent) crumbs.push(`<span class="crumb-sep">/</span><a class="crumb" href="${href("run", D.parent)}"><span class="t mono">${esc(D.parent.split("/").pop())}</span></a>`);
  if (route.page !== "project") crumbs.push(`<span class="crumb-sep">/</span><span class="crumb" aria-current="page"><span class="t">${esc(D.name)}</span></span>`);
  return crumbs.join("");
}

function renderTop(route) {
  $("#topbar").innerHTML = `
    <a class="zs-mark" href="#/" aria-label="回到总览">片</a>
    <a class="wordmark" href="#/"><b>VIDEO RECAP</b><span>剪辑台</span></a>
    <nav class="crumbs" aria-label="位置">${crumbsFor(route)}</nav>
    <span class="spacer"></span>
    <button class="search-trigger" type="button" data-act="search" aria-label="搜索">${icon("search")}<span>搜索旁白、剪辑理由、资源…</span><kbd>⌘K</kbd></button>
    <button class="icon-btn" type="button" data-act="refresh" aria-label="重新读取">${icon("refresh")}</button>
    <button class="icon-btn theme-btn" type="button" data-act="theme" aria-label="${isDark() ? "切换到浅色" : "切换到深色"}">${icon(isDark() ? "sun" : "moon")}</button>`;
}

function renderTabs(route) {
  const bar = $("#stagebar");
  const D = S.data;
  let tabs = [];
  if (route.page === "run" && D) {
    tabs = D.stages.map((stage) => ({ key: stage.key === "home" ? "" : stage.key, label: stage.label, meta: stage.meta, dot: stage.key === "home" ? null : stage.state }));
  } else if (route.page === "library" && D) {
    tabs = [
      { key: "resources", label: "资源", meta: `${D.resources.length} 项` },
      { key: "templates", label: "模板", meta: `${D.templates.length} 版` },
      { key: "samples", label: "样片", meta: `${D.samples.length} 条` },
    ];
  }
  bar.hidden = !tabs.length;
  if (!tabs.length) { bar.innerHTML = ""; return; }
  const current = route.page === "library" ? route.view || "resources" : route.view;
  bar.innerHTML = tabs.map((tab, i) => `${i > 0 && route.page === "run" && i > 1 ? '<span class="tab-sep"></span>' : ""}<a class="tab" data-stage="${tab.key || "home"}" href="${href(route.page, D.path, tab.key)}" ${current === tab.key ? 'aria-current="page"' : ""}>${tab.dot === null || tab.dot === undefined ? "" : `<i class="dot ${esc(tab.dot)}" aria-hidden="true"></i>`}<span class="lbl">${esc(tab.label)}</span>${tab.meta ? `<span class="meta">${esc(tab.meta)}</span>` : ""}</a>`).join("");
  const active = $(".tab[aria-current]", bar);
  if (active) bar.scrollLeft = Math.max(0, active.offsetLeft - bar.clientWidth / 2 + active.clientWidth / 2);
}

function applyCss(root) {
  for (const node of $$("[data-css]", root)) {
    for (const rule of node.dataset.css.split(";")) {
      const at = rule.indexOf(":");
      if (at > 0) node.style.setProperty(rule.slice(0, at).trim(), rule.slice(at + 1).trim());
    }
    node.removeAttribute("data-css");
  }
}

/* ---- render */
async function load(route) {
  if (!S.overview || route.page === "overview") S.overview = await api("/api/overview");
  if (route.page === "overview") return null;
  return api(`/api/${route.page}?path=${enc(route.path)}`);
}

async function render() {
  const seq = ++S.seq;
  const route = parseRoute(location.hash);
  const view = $("#view");
  view.dataset.state = "loading";
  closePalette();
  let data;
  try {
    data = await load(route);
  } catch (error) {
    if (seq !== S.seq) return;
    S.data = null;
    renderTop(route);
    renderTabs(route);
    view.innerHTML = `<div class="wrap"><div class="empty"><h3>读不出这一页</h3><p>${esc(error.message)}</p><a class="btn" href="#/">回到总览</a></div></div>`;
    view.dataset.state = "error";
    return;
  }
  if (seq !== S.seq) return;
  S.data = data;
  renderTop(route);
  renderTabs(route);
  let html;
  try {
    switch (route.page) {
      case "run": html = viewRun(data, route); break;
      case "library": html = viewLibrary(data, route); break;
      case "project": html = viewProject(data); break;
      default: html = viewOverview(S.overview);
    }
  } catch (error) {
    view.innerHTML = `<div class="wrap"><div class="empty"><h3>这一页有数据读不懂</h3><p>${esc(error.message)}</p><a class="btn" href="#/">回到总览</a></div></div>`;
    view.dataset.state = "error";
    return;
  }
  view.innerHTML = html;
  applyCss(view);
  document.title = `${route.page === "overview" ? "总览" : data.name} · 剪辑台 · VIDEO RECAP`;
  bindPlayer();
  view.dataset.route = location.hash || "#/";
  view.dataset.state = "ready";
  focusTarget(route);
}

function focusTarget(route) {
  const id = route.q.get("id");
  const clip = route.q.get("clip");
  const seg = route.q.get("i");
  const node = (id && document.getElementById(`e-${id}`)) || (clip && document.getElementById(`c-${clip}`)) || (seg && document.getElementById(`n-${seg}`));
  if (!node) { scrollTo(0, 0); return; }
  requestAnimationFrame(() => {
    node.scrollIntoView({ block: "center" });
    node.classList.add("flash");
    setTimeout(() => node.classList.remove("flash"), 1300);
  });
}

/* ---- player: a video plus rows / timeline segments that carry data-at / data-end */
function bindPlayer() {
  const video = $("#player");
  if (!video) return;
  const total = Number($("#view [data-total]")?.dataset.total) || 0;
  const head = $("#playhead");
  const hit = $("#tlhit");
  const now = $("#nowTime");
  const marks = $$("[data-at]");
  const place = () => {
    const t = video.currentTime || 0;
    const span = total || video.duration || 1;
    if (head) head.style.setProperty("left", `calc(64px + (100% - 64px) * ${Math.min(1, t / span)})`);
    if (now) now.textContent = `${clock(t, true)} / ${clock(span, true)}`;
    for (const node of marks) node.classList.toggle("on", t >= Number(node.dataset.at) && t < Number(node.dataset.end));
  };
  video.addEventListener("timeupdate", place);
  video.addEventListener("seeked", place);
  if (hit) {
    hit.addEventListener("click", (event) => {
      const box = hit.getBoundingClientRect();
      const span = total || video.duration || 0;
      video.currentTime = Math.max(0, Math.min(span, ((event.clientX - box.left) / box.width) * span));
    });
  }
  place();
}

function seek(seconds) {
  const video = $("#player");
  if (!video || !Number.isFinite(seconds)) return;
  video.currentTime = seconds;
  video.play().catch(() => {});
}

/* ---- clipboard + toast */
async function writeClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_error) {
    const scratch = document.createElement("textarea");
    scratch.className = "clipboard-scratch";
    scratch.value = text;
    scratch.setAttribute("readonly", "");
    document.body.append(scratch);
    scratch.select();
    let copied = false;
    try { copied = document.execCommand("copy"); } catch (_ignored) { copied = false; }
    scratch.remove();
    return copied;
  }
}
function toast(message) {
  const host = $("#toast");
  host.textContent = message;
  host.classList.add("show");
  clearTimeout(host.hideTimer);
  host.hideTimer = setTimeout(() => host.classList.remove("show"), 2400);
}

/* ---- search palette (server-side: GET /api/search?q=) */
function openPalette() {
  const host = $("#palette");
  host.hidden = false;
  host.innerHTML = `<div class="pal"><div class="pal-in">${icon("search")}<input id="palq" placeholder="搜索旁白、剪辑理由、资源、模板…" autocomplete="off" aria-label="搜索"><kbd>esc</kbd></div>
    <div class="pal-res" id="palres" role="listbox"></div>
    <div class="pal-foot"><span>↑↓ 选择</span><span>↵ 打开</span><span>搜索只读，不改任何文件</span></div></div>`;
  const input = $("#palq");
  input.focus();
  input.addEventListener("input", () => { S.palette.sel = 0; scheduleSearch(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      S.palette.sel = Math.max(0, Math.min(S.palette.items.length - 1, S.palette.sel + (event.key === "ArrowDown" ? 1 : -1)));
      paintResults(input.value);
    }
    if (event.key === "Enter" && S.palette.items[S.palette.sel]) { closePalette(); go(S.palette.items[S.palette.sel].href); }
  });
  paintResults("");
}
function closePalette() {
  const host = $("#palette");
  if (!host || host.hidden) return;
  host.hidden = true;
  host.innerHTML = "";
}
function scheduleSearch() {
  clearTimeout(S.palette.timer);
  S.palette.timer = setTimeout(runSearch, 140);
}
async function runSearch() {
  const input = $("#palq");
  if (!input) return;
  const text = input.value.trim();
  const seq = ++S.palette.seq;
  if (!text) { S.palette.items = []; paintResults(""); return; }
  try {
    const data = await api(`/api/search?q=${enc(text)}`);
    if (seq !== S.palette.seq) return;
    S.palette.items = data.hits;
    paintResults(text);
  } catch (error) {
    if (seq === S.palette.seq) $("#palres").innerHTML = `<div class="pal-empty">${esc(error.message)}</div>`;
  }
}
function snippet(hit) {
  const text = hit.snippet;
  return `${esc(text.slice(0, hit.at))}<mark>${esc(text.slice(hit.at, hit.at + hit.len))}</mark>${esc(text.slice(hit.at + hit.len))}`;
}
function paintResults(text) {
  const host = $("#palres");
  if (!host) return;
  if (!text) { host.innerHTML = '<div class="pal-empty">可以搜一句旁白、剪辑理由里的词、资源或模板的 id 与标题</div>'; return; }
  const hits = S.palette.items;
  if (!hits.length) { host.innerHTML = `<div class="pal-empty">没有找到「${esc(text)}」</div>`; return; }
  const groups = [...new Set(hits.map((hit) => hit.group))];
  host.innerHTML = groups.map((group) => `<div class="pal-g">${esc(group)}</div>${hits.map((hit, index) => ({ hit, index })).filter((item) => item.hit.group === group)
    .map(({ hit, index }) => `<a class="pal-item" role="option" href="${esc(hit.href)}" aria-selected="${index === S.palette.sel}"><span class="w">${esc(hit.title)}</span><span class="x">${snippet(hit)}</span></a>`).join("")}`).join("");
  $('.pal-item[aria-selected="true"]', host)?.scrollIntoView({ block: "nearest" });
}

/* ---- events */
async function onClick(event) {
  const node = event.target;
  if (node.id === "palette") { closePalette(); return; }
  if (node.closest(".pal-item")) { closePalette(); return; }
  const copy = node.closest("[data-copy]");
  if (copy) {
    const copied = await writeClipboard(copy.dataset.copy);
    toast(copied ? "已复制，去对话里发送给助手" : "浏览器拒绝了剪贴板，请手动选中复制");
    return;
  }
  const seekTo = node.closest("[data-seek]");
  if (seekTo && !node.closest("a")) { seek(Number(seekTo.dataset.seek)); return; }
  const action = node.closest("[data-act]")?.dataset.act;
  if (action === "search") openPalette();
  else if (action === "theme") toggleTheme();
  else if (action === "refresh") { S.overview = null; render(); }
}
function onKey(event) {
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "");
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); openPalette(); return; }
  if (event.key === "Escape") { closePalette(); return; }
  if (event.key === "/" && !typing && $("#palette").hidden) { event.preventDefault(); openPalette(); }
}

document.addEventListener("click", onClick);
document.addEventListener("keydown", onKey);
window.addEventListener("hashchange", render);
if (typeof matchMedia === "function") matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => { if (S.theme === "auto") renderTop(parseRoute(location.hash)); });
document.addEventListener("DOMContentLoaded", render);
