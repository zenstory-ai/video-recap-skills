"use strict";
/*
 * video-recap 审片台 — minimal, human-first read-only viewer.
 * Only GET requests are issued; zero parameter clutter.
 */

const THEME_KEY = "video_recap_dashboard_theme";
const readTheme = () => {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : "auto";
  } catch (_error) {
    return "auto";
  }
};

const S = {
  theme: readTheme(),
  overview: null,
  data: null,
  activePage: "",
  activePath: "",
  videoMode: "final",
  seq: 0,
  palette: { sel: 0, items: [], timer: null, seq: 0 },
};

function applyTheme() {
  if (S.theme === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.dataset.theme = S.theme;
}
applyTheme();

const isDark = () =>
  S.theme === "dark" ||
  (S.theme === "auto" && typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches);

function toggleTheme() {
  S.theme = isDark() ? "light" : "dark";
  try { localStorage.setItem(THEME_KEY, S.theme); } catch (_error) { /* private mode */ }
  applyTheme();
  renderTop();
}

/* ---- DOM & formatting helpers */
const $ = (selector, root) => (root || document).querySelector(selector);
const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;
const mediaUrl = (rel) => `/api/media?path=${enc(rel)}`;
const num = (value) => (Number.isFinite(value) ? String(Math.round(value * 10) / 10) : "—");

function clock(seconds) {
  if (!Number.isFinite(seconds)) return "—";
  const sign = seconds < 0 ? "-" : "";
  const total = Math.max(0, Math.round(Math.abs(seconds)));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${sign}${minutes}:${String(rest).padStart(2, "0")}`;
}

const IC = {
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2.5 12h2M19.5 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/>',
  moon: '<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10Z"/>',
  refresh: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4h-4"/>',
  alert: '<path d="M12 4 2.8 19.5h18.4L12 4Z"/><path d="M12 10v4.5M12 17.2v.3"/>',
};
const icon = (name) => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${IC[name] || ""}</svg>`;

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  let data = null;
  try { data = await response.json(); } catch (_error) { throw new Error("看板服务返回了无法读取的内容"); }
  if (!response.ok) throw new Error((data && data.error) || `HTTP ${response.status}`);
  return data;
}

/* ---- routing */
function parseRoute(hash) {
  const raw = (hash || "").replace(/^#/, "") || "/";
  const [pathPart, qs = ""] = raw.split("?");
  const parts = pathPart.split("/").filter(Boolean);
  const q = new URLSearchParams(qs);
  if (["run", "library"].includes(parts[0]) && parts[1]) {
    let path = parts[1];
    try { path = decodeURIComponent(parts[1]); } catch (_error) { /* keep raw */ }
    return { page: parts[0], path, view: parts[2] || "", q };
  }
  if (parts[0] === "runs") return { page: "runs", path: "", view: "", q };
  if (parts[0] === "libraries") return { page: "libraries", path: "", view: "", q };
  return { page: "home", path: "", view: "", q };
}

const href = (page, path, view = "", query = "") =>
  `#/${page}/${enc(path)}${view ? `/${view}` : ""}${query ? `?${query}` : ""}`;

function go(hash) {
  if (location.hash === hash) render();
  else location.hash = hash;
}

function topRuns(overview) {
  if (!overview || !overview.runs) return [];
  const list = overview.runs.filter((r) => !r.internal);
  return [
    ...list.filter((r) => r.has_video),
    ...list.filter((r) => !r.has_video),
  ];
}

function resolveTarget(route, overview) {
  const runs = topRuns(overview);
  const allRuns = overview.runs || [];
  if (route.page === "run" || route.page === "library") {
    return { page: route.page, path: route.path, view: route.view, q: route.q };
  }
  if (route.page === "libraries") {
    const first = overview.libraries[0];
    return first ? { page: "library", path: first.path, view: "", q: route.q } : { page: "libraries-empty", path: "", view: "", q: route.q };
  }
  if (runs.length || allRuns.length) {
    const first = runs[0] || allRuns[0];
    return { page: "run", path: first.path, view: "", q: route.q };
  }
  if (overview.libraries.length) {
    return { page: "library", path: overview.libraries[0].path, view: "", q: route.q };
  }
  return { page: "runs-empty", path: "", view: "", q: route.q };
}

/* ---- top chrome */
function renderTop() {
  const O = S.overview;
  const hasLib = Boolean(O && O.libraries && O.libraries.length > 0);
  const onLib = S.activePage.startsWith("librar");

  const nav = hasLib
    ? `<nav class="nav-seg" aria-label="页面切换">
        <a class="nav-tab" href="#/runs" ${!onLib ? 'aria-current="page"' : ""}>成片</a>
        <a class="nav-tab" href="#/libraries" ${onLib ? 'aria-current="page"' : ""}>素材库</a>
      </nav>`
    : `<div class="top-spacer"></div>`;

  $("#topbar").innerHTML = `
    <a class="brand" href="#/">
      <span class="zs-mark">片</span>
      <span class="brand-title">审片台</span>
    </a>
    ${nav}
    <div class="top-actions">
      <button class="search-trigger" type="button" data-act="search" aria-label="搜索">${icon("search")}<span>搜索</span></button>
      <button class="icon-btn" type="button" data-act="refresh" title="刷新" aria-label="刷新">${icon("refresh")}</button>
      <button class="icon-btn" type="button" data-act="theme" title="切换深浅色" aria-label="${isDark() ? "浅色模式" : "深色模式"}">${icon(isDark() ? "sun" : "moon")}</button>
    </div>`;
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

/* ---- main render cycle */
async function render() {
  const seq = ++S.seq;
  const rawRoute = parseRoute(location.hash);
  const view = $("#view");
  closePalette();
  closeLightbox();

  try {
    if (!S.overview) S.overview = await api("/api/overview");
  } catch (error) {
    if (seq !== S.seq) return;
    S.activePage = "error";
    renderTop();
    view.innerHTML = `<div class="page-wrap"><div class="empty"><h3>无法读取目录</h3><p>${esc(error.message)}</p></div></div>`;
    return;
  }
  if (seq !== S.seq) return;

  const target = resolveTarget(rawRoute, S.overview);

  // Switching 解说词 / 镜头 inside the same video keeps <video> playing uninterrupted.
  if (
    target.page === "run" &&
    S.activePage === "run" &&
    S.activePath === target.path &&
    S.data &&
    $("#inspectorHost")
  ) {
    const tab = pickPanelTab(S.data, target.view);
    $("#inspectorHost").innerHTML = renderSidePanel(S.data, tab);
    applyCss($("#inspectorHost"));
    refreshPlayerMarks();
    view.dataset.route = location.hash || "#/";
    focusTarget(target);
    return;
  }

  if (
    target.page === "library" &&
    S.activePage === "library" &&
    S.activePath === target.path &&
    S.data
  ) {
    view.innerHTML = viewLibrary(S.overview, S.data, target);
    applyCss(view);
    view.dataset.route = location.hash || "#/";
    view.dataset.state = "ready";
    focusTarget(target);
    return;
  }

  view.dataset.state = "loading";
  let detail = null;
  if (target.page === "run" || target.page === "library") {
    try {
      detail = await api(`/api/${target.page}?path=${enc(target.path)}`);
    } catch (error) {
      if (seq !== S.seq) return;
      S.activePage = target.page;
      S.activePath = "";
      S.data = null;
      renderTop();
      view.innerHTML = `<div class="page-wrap"><div class="empty"><h3>无法打开该内容</h3><p>${esc(error.message)}</p><a class="btn" href="#/">返回</a></div></div>`;
      view.dataset.state = "error";
      return;
    }
  }
  if (seq !== S.seq) return;

  S.activePage = target.page;
  S.activePath = target.path;
  S.data = detail;
  S.videoMode = "final";
  renderTop();

  let html = "";
  try {
    if (target.page === "run") html = viewStudio(S.overview, detail, target);
    else if (target.page === "library") html = viewLibrary(S.overview, detail, target);
    else if (target.page === "libraries-empty") html = `<div class="page-wrap">${empty("暂无素材库", "当前目录下没有 library.json。")}</div>`;
    else html = `<div class="page-wrap">${empty("暂无成片", "生成成片后会自动在这里显示。")}</div>`;
  } catch (error) {
    view.innerHTML = `<div class="page-wrap"><div class="empty"><h3>显示异常</h3><p>${esc(error.message)}</p></div></div>`;
    view.dataset.state = "error";
    return;
  }

  view.innerHTML = html;
  applyCss(view);
  const titleLabel = detail ? detail.title || detail.name : "审片台";
  document.title = `${titleLabel} · 审片台`;
  bindPlayer();
  view.dataset.route = location.hash || "#/";
  view.dataset.state = "ready";
  focusTarget(target);
}

function focusTarget(route) {
  const id = route.q.get("id");
  const clip = route.q.get("clip");
  const seg = route.q.get("i");
  const node =
    (id && document.getElementById(`e-${id}`)) ||
    (clip !== null && clip !== "" && document.getElementById(`c-${clip}`)) ||
    (seg !== null && seg !== "" && document.getElementById(`n-${seg}`));
  if (!node) return;
  requestAnimationFrame(() => {
    node.scrollIntoView({ block: "center", behavior: "smooth" });
    node.classList.add("flash");
    if (node.dataset.seek !== undefined) seek(Number(node.dataset.seek), false);
    setTimeout(() => node.classList.remove("flash"), 1300);
  });
}

/* ---- synced player */
let playerPlaceFn = null;

function refreshPlayerMarks() {
  if (typeof playerPlaceFn === "function") playerPlaceFn();
}

function bindPlayer() {
  playerPlaceFn = null;
  const video = $("#player");
  if (!video) return;
  const place = () => {
    const t = video.currentTime || 0;
    for (const node of $$("[data-at]")) {
      const active = t >= Number(node.dataset.at) && t < Number(node.dataset.end);
      node.classList.toggle("on", active);
    }
  };
  playerPlaceFn = place;
  video.addEventListener("timeupdate", place);
  video.addEventListener("seeked", place);
  place();
}

function seek(seconds, autoplay = true) {
  const video = $("#player");
  if (!video || !Number.isFinite(seconds)) return;
  video.currentTime = Math.max(0, seconds);
  if (autoplay) video.play().catch(() => {});
}

function switchVideoSource(mode) {
  if (!S.data || S.videoMode === mode) return;
  const finalMedia = S.data.views?.film?.video;
  const cutMedia = S.data.views?.cut?.video;
  const nextMedia = mode === "cut" ? cutMedia : finalMedia;
  const video = $("#player");
  if (!video || !nextMedia || !nextMedia.path) return;
  const prevTime = video.currentTime || 0;
  const wasPaused = video.paused;
  S.videoMode = mode;
  video.src = mediaUrl(nextMedia.path);
  video.addEventListener(
    "loadedmetadata",
    () => {
      video.currentTime = Math.min(prevTime, video.duration || prevTime);
      if (!wasPaused) video.play().catch(() => {});
    },
    { once: true }
  );
  for (const btn of $$("[data-video-mode]")) btn.classList.toggle("on", btn.dataset.videoMode === mode);
}

/* ---- lightbox & search */
function openLightbox(src, caption) {
  const host = $("#lightbox");
  host.hidden = false;
  host.innerHTML = `<div class="lb-box"><img src="${esc(src)}" alt="${esc(caption)}"><div class="lb-cap"><span>${esc(caption)}</span><button class="btn sm" type="button" data-act="close-lb">关闭</button></div></div>`;
}
function closeLightbox() {
  const host = $("#lightbox");
  if (!host || host.hidden) return;
  host.hidden = true;
  host.innerHTML = "";
}

function openPalette() {
  const host = $("#palette");
  host.hidden = false;
  host.innerHTML = `<div class="pal"><div class="pal-in">${icon("search")}<input id="palq" placeholder="搜索解说词、镜头描述、素材名称…" autocomplete="off" aria-label="搜索"></div>
    <div class="pal-res" id="palres" role="listbox"></div></div>`;
  const input = $("#palq");
  input.focus();
  input.addEventListener("input", () => { S.palette.sel = 0; clearTimeout(S.palette.timer); S.palette.timer = setTimeout(runSearch, 130); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      S.palette.sel = Math.max(0, Math.min(S.palette.items.length - 1, S.palette.sel + (event.key === "ArrowDown" ? 1 : -1)));
      paintResults(input.value);
    }
    if (event.key === "Enter" && S.palette.items[S.palette.sel]) {
      closePalette();
      go(S.palette.items[S.palette.sel].href);
    }
  });
  paintResults("");
}

function closePalette() {
  const host = $("#palette");
  if (!host || host.hidden) return;
  host.hidden = true;
  host.innerHTML = "";
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
  if (!text) { host.innerHTML = '<div class="pal-empty">输入关键词即可跳转到对应台词、镜头或素材</div>'; return; }
  const hits = S.palette.items;
  if (!hits.length) { host.innerHTML = `<div class="pal-empty">未找到「${esc(text)}」</div>`; return; }
  host.innerHTML = hits
    .map((hit, index) => `<a class="pal-item" role="option" href="${esc(hit.href)}" aria-selected="${index === S.palette.sel}"><span class="w">${esc(hit.title)}</span><span class="x">${snippet(hit)}</span></a>`)
    .join("");
  $('.pal-item[aria-selected="true"]', host)?.scrollIntoView({ block: "nearest" });
}

/* ---- global events */
function onClick(event) {
  const node = event.target;
  if (node.id === "palette" || node.closest(".pal-item")) { closePalette(); return; }
  if (node.id === "lightbox" || node.closest('[data-act="close-lb"]')) { closeLightbox(); return; }

  const zoom = node.closest("[data-zoom-src]");
  if (zoom) {
    openLightbox(zoom.dataset.zoomSrc, zoom.dataset.zoomCaption || "");
    return;
  }

  const vmode = node.closest("[data-video-mode]");
  if (vmode) { switchVideoSource(vmode.dataset.videoMode); return; }

  const seekTo = node.closest("[data-seek]");
  if (seekTo && !node.closest("a")) {
    seek(Number(seekTo.dataset.seek), true);
    return;
  }

  const action = node.closest("[data-act]")?.dataset.act;
  if (action === "search") openPalette();
  else if (action === "theme") toggleTheme();
  else if (action === "refresh") {
    S.overview = null;
    S.activePath = "";
    render();
  }
}

function onKey(event) {
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "");
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openPalette();
    return;
  }
  if (event.key === "Escape") {
    closeLightbox();
    closePalette();
    return;
  }
  if (event.key === "/" && !typing && $("#palette").hidden) {
    event.preventDefault();
    openPalette();
  }
}

document.addEventListener("click", onClick);
document.addEventListener("keydown", onKey);
window.addEventListener("hashchange", render);
if (typeof matchMedia === "function") {
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => {
    if (S.theme === "auto") renderTop();
  });
}
document.addEventListener("DOMContentLoaded", render);
