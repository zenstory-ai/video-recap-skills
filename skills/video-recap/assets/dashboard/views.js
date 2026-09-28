"use strict";
/*
 * video-recap 剪辑台 — the views. Each builder takes server-parsed JSON and returns an
 * HTML string; app.js mounts it, applies data-css and binds the player. Nothing here
 * parses an artifact: a view the server could not read arrives as
 * {status: "unparseable", error, raw} and is shown as one sentence plus the raw text.
 */

const LICENCE = { owned: ["ok", "自有"], licensed: ["ok", "已授权"], unknown: ["warn", "授权未确认"], restricted: ["bad", "受限"] };
const CONSENT = { granted: ["ok", "声音授权已确认"], unknown: ["warn", "声音授权未确认"], denied: ["bad", "声音授权被拒"] };
const TPL_STATUS = { adopted: ["ok", "已采用"], draft: ["off", "草稿"], retired: ["off", "已停用"] };
const BINDING = { ok: ["ok", "已解析"], missing: ["bad", "找不到"], not_adopted: ["bad", "未采用，不会生效"], kind_mismatch: ["bad", "类型不符"], version_required: ["bad", "缺版本"], no_library: ["bad", "没有资源库"], invalid: ["bad", "写法无效"] };
const SEVERITY_CHIP = { danger: ["bad", "必须处理"], todo: ["todo", "等你"], warn: ["warn", "留意"], ok: ["ok", "正常"] };
const ROLE_LABEL = { source_video: "原片", voice: "音色", bgm: "背景音乐", subtitle_font: "字幕字体", packaging_layer: "包装图层", subtitle_style: "字幕样式", packaging: "包装" };
const PROVENANCE = { measured: "实测", fitted: "调出", specified: "指定", unknown: "未知" };
const QC_LEVEL = { ok: ["ok", "通过"], warn: ["warn", "留意"], error: ["bad", "阻断"], unparseable: ["bad", "无法解析"] };
const KIND_LABEL = { bgm: "背景音乐", sfx: "音效", voice: "音色", font: "字体", image: "图片", subtitle_style: "字幕样式", packaging: "包装" };
const MODE_LABEL = { full: "整段", cut: "剪辑" };
const AUDIO_LABEL = { narration: "解说", "source-mix": "原声混音", "adopted-packet-copy": "冻结已采用音轨" };
const GROUP_LABEL = { understanding: "理解", cut: "剪辑", script: "解说", render: "渲染" };
const STAGE_HINT = { understanding: "视频理解与创作 brief", cut: "剪辑计划与剪后母版", narration: "旁白稿与配音", film: "合成成片与时间线", qc: "成片检查", resources: "本次运行用到的资源" };
const RESOURCE_KINDS = ["bgm", "sfx", "voice", "font", "image"];

const statusChip = ([cls, word]) => `<span class="status ${cls}">${icon(cls === "ok" ? "check" : cls === "off" ? "minus" : cls === "todo" ? "right" : "alert")}${esc(word)}</span>`;
const askButton = (ask, primary = false) => (ask ? `<button class="btn sm ${primary ? "primary" : ""}" type="button" data-copy="${esc(ask)}">${icon("copy")}复制给助手</button>` : "");
const head = (title, sub, actions = "") => `<div class="head"><div><h2>${esc(title)}</h2>${sub ? `<div class="sub">${sub}</div>` : ""}</div>${actions ? `<div class="actions">${actions}</div>` : ""}</div>`;
const section = (title, note = "") => `<div class="section-t"><h3>${esc(title)}</h3>${note ? `<span class="n">${esc(note)}</span>` : ""}</div>`;
const empty = (title, text, ask = "") => `<div class="empty"><h3>${esc(title)}</h3><p>${esc(text)}</p>${ask ? `<button class="btn primary" type="button" data-copy="${esc(ask)}">${icon("copy")}复制给助手：${esc(ask)}</button>` : ""}</div>`;
const notice = (text, cls = "", actions = "") => `<div class="notice ${cls}" role="note">${icon(cls === "info" ? "info" : "alert")}<span>${esc(text)}</span>${actions ? `<span class="actions">${actions}</span>` : ""}</div>`;
const rawFallback = (view, file) => `${notice(`${file} 读不出结构：${view.error}。下面按原文显示，其余视图不受影响。`, "danger")}<pre class="raw">${esc(view.raw || "（文件为空或无法读取）")}</pre>`;
const issuesList = (issues) => (issues && issues.length ? `<ul class="issues">${issues.map((item) => `<li class="${item.level === "error" ? "error" : "warn"}">${esc(item.message)}</li>`).join("")}</ul>` : "");
const facts = (rows) => `<dl class="facts">${rows.filter(([, value]) => value !== "" && value != null).map(([key, value]) => `<dt>${esc(key)}</dt><dd>${value}</dd>`).join("")}</dl>`;
const gallery = (items) => (items.length ? `${section("预览", `${items.length} 个文件`)}<div class="gallery">${items.join("")}</div>` : "");
function mediaFigure(media, caption) {
  if (!media || !media.path) return "";
  const src = esc(mediaUrl(media.path));
  const body = media.kind === "image" ? `<img src="${src}" alt="${esc(caption)}" loading="lazy">`
    : media.kind === "audio" ? `<audio src="${src}" controls preload="none"></audio>`
      : `<video src="${src}" controls playsinline preload="metadata"></video>`;
  return `<figure class="card">${body}<figcaption>${esc(caption)} · ${esc(media.name)}${media.size ? ` · ${esc(size(media.size))}` : ""}</figcaption></figure>`;
}

/* ================================================================ overview */

function runRow(run, child = false) {
  const qc = run.blockers ? ["bad", `${run.blockers} 个阻断`] : run.unparseable.length ? ["warn", "有文件无法解析"] : null;
  const nextText = run.next_pause ? `等待 ${run.next_pause.artifact}` : run.has_video ? "已有成片" : run.state_error ? "状态无法解析" : "所需输入已就绪";
  return `<a class="run-row ${child ? "child" : ""}" href="${href("run", run.path)}" data-run="${esc(run.path)}">
    <span class="nm">${esc(run.name)}<small>${esc(run.path)}</small></span>
    <span class="pips" aria-label="阶段">${run.stages.filter((s) => s.key !== "home").map((s) => `<i class="dot ${esc(s.state)}" title="${esc(`${s.label} ${s.meta}`)}"></i>`).join("")}</span>
    <span class="nx">${esc(MODE_LABEL[run.mode] || run.mode || "")} · ${esc(nextText)}</span>
    ${qc ? statusChip(qc) : `<span class="status off">${icon("minus")}无阻断</span>`}</a>`;
}

function runTree(runs, parent, depth = 0) {
  return runs.filter((run) => run.parent === parent).map((run) => runRow(run, depth > 0) + runTree(runs, run.path, depth + 1)).join("");
}

function viewOverview(O) {
  const c = O.counts;
  const counts = O.next_counts;
  const rest = O.attention.length - O.next.length;
  const kpi = (label, value, detail, link) => `<a class="card kpi" href="${link}"><div class="k">${label}</div><div class="v">${value}</div><div class="d">${esc(detail)}</div></a>`;
  const groups = O.projects.map((project) => `<a class="group-h" href="${href("project", project.path)}">${esc(project.name)} <span class="muted">${esc(project.path)}</span>${project.unresolved ? statusChip(["bad", `${project.unresolved} 个绑定不会生效`]) : ""}</a>${runTree(O.runs.filter((run) => run.project === project.path), null) || '<div class="group-h muted">还没有运行</div>'}`);
  const loose = O.runs.filter((run) => !run.project);
  if (loose.length) groups.push(`${O.projects.length ? '<div class="group-h">未归属项目的运行</div>' : ""}${runTree(loose, null)}`);
  return `<div class="wrap" data-view="overview">
  <section class="hero">
    <div class="hero-t">
      <div class="eyebrow">总览 · 只读</div>
      <h1>${esc(O.name)}</h1>
      <div class="meta"><span class="mono">${esc(O.root)}</span></div>
      <div class="kpis">
        ${kpi("运行", c.runs, `${counts.waiting} 个在等你 · ${counts.blocked} 个有阻断`, "#/")}
        ${kpi("项目", c.projects, `${counts.binding} 个绑定问题`, "#/")}
        ${kpi("资源库", c.libraries, `资源 ${c.resources} · 模板 ${c.templates} · 样片 ${c.samples}`, O.libraries[0] ? href("library", O.libraries[0].path) : "#/")}
        ${kpi("需要处理", O.attention.length, `授权 ${counts.licence} · 库错误 ${counts.library_error}`, "#/")}
      </div>
    </div>
    <div class="card next ${esc(O.next_severity)}" id="nextSteps" data-severity="${esc(O.next_severity)}">
      <div class="eyebrow">下一步</div>
      ${O.next.length ? O.next.map((item, i) => `<div class="todo" data-kind="${esc(item.kind)}" data-severity="${esc(item.severity)}"><span class="num ${esc(item.severity)}">${i + 1}</span><div><div class="t">${esc(item.message)}</div><div class="d">${esc(item.title)}</div>
        <div class="acts">${askButton(item.ask, i === 0)}<a class="btn sm ghost" href="${esc(item.href)}">打开</a></div></div></div>`).join("")
        : '<div class="todo"><span class="num">✓</span><div><div class="t">没有待处理的事</div><div class="d">运行都没有卡住，资源库也没有需要确认的授权。</div></div></div>'}
      <p class="foot">${rest > 0 ? `另有 ${rest} 条，见下方「需要注意」。` : ""}看板只读：写稿、剪辑、合成、登记授权都在和助手的对话里确认。</p>
    </div>
  </section>
  ${O.warnings.map((text) => notice(text)).join("")}
  ${section("项目与运行", `${c.projects} 个项目 · ${c.runs} 次运行`)}
  ${O.runs.length || O.projects.length ? `<div class="card runs">${groups.join("")}</div>` : empty("还没有运行", "在 --root 下没有找到 recap_run_manifest.json 或 recap_project.json。")}
  ${section("资源库", "资源、模板与样片")}
  ${O.libraries.length ? `<div class="libs">${O.libraries.map((lib) => `<a class="card lib" href="${href("library", lib.path)}"><span class="nm">${esc(lib.name)}</span><span class="mono muted">${esc(lib.path)}</span>
    <span class="pills"><span class="pill">资源 ${lib.counts.resources}</span><span class="pill">模板 ${lib.counts.templates}</span><span class="pill">样片 ${lib.counts.samples}</span></span>
    <span class="pills">${lib.error_count ? statusChip(["bad", `${lib.error_count} 个错误`]) : statusChip(["ok", "校验通过"])}${lib.warning_count ? statusChip(["warn", `${lib.warning_count} 条提醒`]) : ""}</span></a>`).join("")}</div>`
    : empty("还没有资源库", "资源库根目录放一个 library.json；格式见 references/resource-library.md。")}
  ${O.attention.length ? `${section("需要注意", `${O.attention.length} 条`)}<div class="card alist">${O.attention.map((item) => `<a class="arow" href="${esc(item.href)}">${statusChip(SEVERITY_CHIP[item.severity] || SEVERITY_CHIP.warn)}<span><span class="t">${esc(item.title)}</span><br><span class="m">${esc(item.message)}</span></span><span class="go muted">${icon("right")}</span></a>`).join("")}</div>` : ""}
</div>`;
}

/* ================================================================ project */

function resolvedTo(row) {
  const t = row.target;
  if (!t) return '<span class="muted">—</span>';
  if (t.type === "template") return `${esc(t.title || t.ref)} <span class="mono muted">${esc(t.ref)}</span> ${statusChip(TPL_STATUS[t.status] || ["bad", t.status || "—"])}${t.canvas ? ` <span class="muted small">${num(t.canvas.width)}×${num(t.canvas.height)}</span>` : ""}`;
  return `${esc(t.title || t.ref)} <span class="muted small">${esc(KIND_LABEL[t.kind] || t.kind)}</span> ${statusChip(LICENCE[t.license] || ["bad", t.license || "未填写"])}`;
}

function applicationBlock(A) {
  if (!A) return "";
  if (!A.ok) return notice(A.message, "danger", askButton(`请修复项目绑定：${A.message}`));
  const rows = [...Object.entries(A.env || {}).map(([k, v]) => [k, v]), ...Object.entries(A.arg_updates || {}).map(([k, v]) => [`--${k.replace(/_/g, "-")}`, v])];
  return `<div class="card">${rows.length ? facts(rows.map(([k, v]) => [k, `<span class="mono">${esc(v)}</span>`])) : '<p class="chips muted">绑定为空，运行时不会下发任何设置。</p>'}</div>`;
}

function viewProject(P) {
  const lib = P.library;
  const libLine = !lib ? "" : lib.found ? (lib.rel ? `<a href="${href("library", lib.rel)}">${esc(lib.raw)}</a>` : `${esc(lib.raw)} <span class="muted">（在 --root 之外：只解析绑定，不提供浏览与预览）</span>`) : `${esc(lib.raw ?? "未填写")} ${statusChip(["bad", "未找到 library.json"])}`;
  const broken = P.bindings.filter((row) => row.severity === "danger");
  const rows = P.bindings.map((row) => {
    const target = row.target && lib && lib.rel ? href("library", lib.rel, row.target.type === "template" ? "templates" : "resources", `id=${enc(row.target.ref)}`) : "";
    return `<tr class="${row.severity === "danger" ? "danger" : ""}" data-role="${esc(row.role)}" data-status="${esc(row.status)}"><td>${esc(KIND_LABEL[row.role] || row.role)}<div class="muted small mono">${esc(row.role)}</div></td><td class="mono">${target ? `<a href="${target}">${esc(row.ref)}</a>` : esc(row.ref)}</td><td>${resolvedTo(row)}</td><td>${statusChip(BINDING[row.status] || ["bad", row.status])}${row.status === "ok" ? "" : `<div class="small">${esc(row.message)}</div>`}</td></tr>`;
  }).join("");
  return `<div class="wrap" data-view="project">
    ${head(P.name, `项目 · <span class="mono">${esc(P.path)}</span>`)}
    ${P.error ? notice(`recap_project.json ${P.error}`, "danger") : ""}
    ${broken.length ? notice(`${broken.length} 个绑定不会生效：${broken.map((row) => `${KIND_LABEL[row.role] || row.role} ${row.ref}`).join("、")}`, "danger", askButton(`请修复项目「${P.name}」的绑定：${broken.map((row) => `${row.role} ${row.ref}（${row.message}）`).join("；")}`, true)) : ""}
    <div class="card">${facts([["资源库", libLine], ["schema", P.schema ? `<span class="mono">${esc(P.schema)}</span>` : ""]])}</div>
    ${section("绑定", "只有 adopted 模板能绑定；绑定在对话里改")}
    ${P.bindings.length ? `<div class="card table-card"><table class="rows"><thead><tr><th>角色</th><th>绑定</th><th>解析到</th><th>状态</th></tr></thead><tbody>${rows}</tbody></table></div>` : empty("还没有绑定", "recap_project.json 的 bindings 为空。")}
    ${section("运行时下发的设置", "按 recap.py --project 的规则解析；不含你的环境变量与命令行参数")}
    ${applicationBlock(P.application)}
    ${section("运行", `${P.runs.length} 次`)}
    ${P.runs.length ? `<div class="card runs">${runTree(P.runs, null) || P.runs.map((run) => runRow(run)).join("")}</div>` : empty("还没有运行", "这个项目目录下还没有 recap_run_manifest.json。")}
  </div>`;
}

/* ================================================================ run */

function viewRun(R, route) {
  const pause = R.next_pause;
  const ask = pause ? `请继续 ${R.work_dir} 这次运行：写 ${pause.artifact}（${pause.hint}）。` : "";
  const sub = `${esc(MODE_LABEL[R.mode] || R.mode)} · ${esc(AUDIO_LABEL[R.audio_mode] || R.audio_mode)} · <span class="mono">${esc(R.path)}</span>`;
  const top = head(R.name, sub, askButton(ask, true));
  const stateNote = R.state_error ? notice(`${R.state_error}。阶段状态只按文件是否存在显示。`, "danger") : "";
  let body;
  switch (route.view) {
    case "understanding": body = runUnderstanding(R); break;
    case "cut": body = runCut(R); break;
    case "narration": body = runNarration(R); break;
    case "film": body = runFilm(R); break;
    case "qc": body = runQc(R); break;
    case "resources": body = runResources(R); break;
    default: body = runHome(R);
  }
  return `<div class="wrap" data-view="run-${esc(route.view || "home")}">${top}${stateNote}${body}</div>`;
}

function runHome(R) {
  const video = R.views.film.video;
  const poster = video && video.path
    ? `<div class="poster"><video src="${esc(mediaUrl(video.path))}" controls playsinline preload="metadata"></video></div>`
    : `<div class="poster empty-poster"><span class="ph-note">${esc(video && video.note ? video.note : "还没有成片。合成在对话里确认。")}</span></div>`;
  const stages = R.stages.filter((stage) => stage.key !== "home").map((stage) => `<a class="pl-row" href="${href("run", R.path, stage.key)}"><i class="dot ${esc(stage.state)}" aria-hidden="true"></i><span class="nm">${esc(stage.label)}</span><span class="fx">${esc(stage.meta)}<span class="muted"> · ${esc(STAGE_HINT[stage.key])}</span></span><span class="muted">${icon("right")}</span></a>`).join("");
  const state = R.state || {};
  const src = state.source_video || {};
  const notes = (state.stale_manifest_notes || []).map((text) => notice(text)).join("");
  const children = R.children && R.children.length ? `${section("子运行", `${R.children.length} 个 · 多源素材的逐源工作目录`)}<div class="card runs">${R.children.map((run) => runRow(run)).join("")}</div>` : "";
  return `<div class="ephome"><div>${poster}</div><div>
    ${R.next_pause ? notice(`下一步：等待写入 ${R.next_pause.artifact}（${R.next_pause.hint}）`, "", askButton(`请继续 ${R.work_dir} 这次运行：写 ${R.next_pause.artifact}（${R.next_pause.hint}）。`)) : ""}
    ${notes}
    <div class="card pipeline">${stages}</div>
    ${section("运行信息")}
    <div class="card">${facts([["工作目录", `<span class="mono">${esc(R.work_dir)}</span>`], ["源视频", src.path ? `<span class="mono">${esc(src.path)}</span>` : ""], ["记录来源", src.origin ? esc(src.origin) : ""], ["上级运行", R.parent ? `<a href="${href("run", R.parent)}">${esc(R.parent)}</a>` : ""]])}</div>
    ${children}</div></div>`;
}

function runUnderstanding(R) {
  const state = R.state;
  if (!state) return empty("读不出运行状态", "recap_run_manifest.json 无法解析，其他视图仍可查看。");
  const groups = Object.entries(state.artifacts).map(([key, info]) => `<tr><td>${esc(GROUP_LABEL[key] || key)}</td><td>${info.present.map((name) => `<span class="pill mono">${esc(name)}</span>`).join(" ") || '<span class="muted">—</span>'}</td><td>${info.missing.map((name) => `<span class="pill mono muted">${esc(name)}</span>`).join(" ") || '<span class="muted">—</span>'}</td></tr>`).join("");
  const multi = state.multi_source ? `${section("多源素材", `${state.multi_source.sources.length} 个来源`)}<div class="card table-card"><table class="rows"><thead><tr><th>来源</th><th>文件</th><th>工作目录</th></tr></thead><tbody>${state.multi_source.sources.map((s) => `<tr><td class="mono">${esc(s.source_id)}</td><td class="mono">${esc(s.source_path)}</td><td class="mono">${esc(s.source_work_dir)}</td></tr>`).join("")}</tbody></table></div>` : "";
  return `${section("各阶段产物", "按文件是否存在判断")}<div class="card table-card"><table class="rows"><thead><tr><th>阶段</th><th>已有</th><th>还没有</th></tr></thead><tbody>${groups}</tbody></table></div>
    ${state.storyboards.length ? `${section("故事板")}<div class="pills">${state.storyboards.map((name) => `<span class="pill mono">${esc(name)}</span>`).join("")}</div>` : ""}${multi}`;
}

function timeAxis(total, X) {
  const step = total > 600 ? 60 : total > 120 ? 30 : total > 40 ? 10 : 5;
  const ticks = [];
  for (let t = 0; t <= total + 0.001; t += step) ticks.push(`<span data-css="left:${X(t)}">${clock(t)}</span>`);
  return ticks.join("");
}

function playerBlock(media, emptyText, total) {
  const frame = media && media.path
    ? `<div class="frame"><video id="player" src="${esc(mediaUrl(media.path))}" controls playsinline preload="metadata"></video></div>`
    : `<div class="frame empty-poster"><span class="ph-note">${esc(media && media.note ? media.note : emptyText)}</span></div>`;
  return `<div class="player" data-total="${Number(total) || 0}">${frame}<div class="now"><span>${media && media.path ? esc(media.name) : ""}</span><span id="nowTime">0:00.0 / ${clock(total, true)}</span></div><div class="muted small">浏览器只播放已有文件；重新剪辑或合成在对话里确认。</div></div>`;
}

function runCut(R) {
  const V = R.views.cut;
  if (V.status === "unparseable") return rawFallback(V, "clip_plan_validated.json");
  if (V.status === "pending") return empty("剪辑计划已写，还没校验", "clip_plan.json 已在工作目录里；重复同一条 recap 命令后，cut 会校验并渲染剪后母版。", `请继续 ${R.work_dir} 这次运行，校验并渲染 clip_plan.json。`);
  if (V.status !== "ok") return empty("还没有剪辑计划", "cut 模式第一次暂停时由助手写 clip_plan.json。", `请为 ${R.work_dir} 写 clip_plan.json。`);
  const total = V.total || 1;
  const X = (t) => `${(t / total) * 100}%`;
  const sources = V.sources;
  const rung = (clip, i) => `s${sources.length > 1 ? (sources.indexOf(clip.source_id) % 6) + 1 : (i % 2 ? 3 : 5)}`;
  const bars = V.clips.map((clip, i) => `<button class="sb ${rung(clip, i)}" type="button" data-seek="${clip.output_start}" data-at="${clip.output_start}" data-end="${clip.output_end}" data-css="flex-grow:${Math.max(0.2, clip.output_end - clip.output_start)}" title="${esc(`${clip.id} · ${num(clip.output_end - clip.output_start)} 秒 · ${clip.beat}`)}" aria-label="${esc(`片段 ${clip.id}，${num(clip.output_end - clip.output_start)} 秒`)}">${esc(clip.id)}</button>`).join("");
  const target = V.target && V.target <= total * 1.5 ? `<div class="strip-target" data-css="left:${X(Math.min(V.target, total))}"><b>目标 ${clock(V.target)}</b></div>` : "";
  const rows = V.clips.map((clip) => `<tr id="c-${clip.index}" data-seek="${clip.output_start}" data-at="${clip.output_start}" data-end="${clip.output_end}"><td class="mono">${esc(clip.id)}</td><td class="mono">${clock(clip.output_start, true)}–${clock(clip.output_end, true)}</td><td class="mono hide-m">${clock(clip.source_start, true)}–${clock(clip.source_end, true)}${clip.source_id ? `<div class="muted small">${esc(clip.source_id)}</div>` : ""}</td><td class="mono">${num(clip.output_end - clip.output_start)}s</td><td>${esc(clip.reason)}</td></tr>`).join("");
  return `${V.blocking.map((b) => notice(`${b.code}：${b.message}`, "danger")).join("")}
    <div class="card strip-card"><div class="strip-head"><h3>节奏条</h3><span class="muted small">宽度 = 时长${sources.length > 1 ? "，颜色 = 来源" : ""}；点一格跳到该段 · ${V.clips.length} 段 · ${clock(total)}</span></div>
      <div class="strip"><div class="strip-bars">${bars}</div>${target}<div class="strip-axis">${timeAxis(total, X)}</div></div></div>
    <div class="film">${playerBlock(V.video, "还没有剪后母版 edited_source.mp4。", total)}
      <div class="card table-card"><table class="rows"><thead><tr><th>段</th><th>输出</th><th class="hide-m">原片</th><th>时长</th><th>剪辑理由</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

function runNarration(R) {
  const V = R.views.narration;
  if (V.status === "unparseable") return rawFallback(V, "narration.json");
  if (V.status !== "ok") return empty("还没有旁白稿", "写好 narration.json 后重复同一条 recap 命令即可继续。", `请为 ${R.work_dir} 写 narration.json。`);
  const tts = V.tts;
  const pills = [`${V.segments.length} 段`, `${V.chars} 字`, V.segments.length ? `${clock(V.segments[0].start)}–${clock(V.segments[V.segments.length - 1].end)}` : "", tts && tts.engine ? `配音 ${tts.engine}` : ""].filter(Boolean);
  return `${tts && tts.partial ? notice(`有 ${tts.failures} 段配音失败（partial），成片只适合预览。`, "danger") : ""}${tts && tts.error ? notice(`tts_meta.json ${tts.error}`) : ""}
    <div class="deliv">${pills.map((text) => `<span class="pill">${esc(text)}</span>`).join("")}</div>
    <div class="card narr">${V.segments.map((seg) => `<div class="seg-row" id="n-${seg.index}"><span class="tm">${clock(seg.start, true)}–${clock(seg.end, true)}</span><span class="tx">${esc(seg.text)}</span>${seg.overlaps_speech ? '<span class="tag vo">压原声</span>' : "<span></span>"}</div>`).join("")}</div>`;
}

function runFilm(R) {
  const F = R.views.film;
  const T = F.timeline;
  const total = T.status === "ok" ? T.duration : 0;
  const X = (t) => `${(t / (total || 1)) * 100}%`;
  const lane = (key, label) => `<div class="tl-row" data-lane="${key}"><span class="tl-lab">${label}</span><div class="tl-track">${T.lanes[key].map((seg, i) => `<span class="tl-seg ${key} ${key === "video" && i % 2 ? "b" : ""}" data-at="${seg.start}" data-end="${seg.end}" data-css="left:${X(seg.start)};width:calc(${X(Math.max(0.05, seg.end - seg.start))} - 1px)" title="${esc(`${clock(seg.start, true)}–${clock(seg.end, true)} ${seg.label}`)}">${esc(seg.label)}</span>`).join("")}</div></div>`;
  const timeline = T.status === "ok"
    ? `<section class="card tl"><div class="tl-inner"><div class="tl-row"><span></span><div class="tl-ruler">${timeAxis(total, X)}</div></div>
        ${lane("video", "画面")}${lane("narration", "旁白")}${lane("bgm", "背景音乐")}${lane("subtitles", "字幕")}
        <div class="tl-head" id="playhead"></div><div class="tl-hit" id="tlhit" aria-label="点击跳转"></div></div></section>`
    : T.status === "unparseable" ? rawFallback(T, "timeline.json")
      : `<div class="empty"><p>合成后会生成 timeline.json，这里按画面、旁白、背景音乐、字幕四轨显示。</p></div>`;
  const canvas = T.canvas ? `${num(T.canvas.width)}×${num(T.canvas.height)}${T.canvas.fps ? ` · ${num(T.canvas.fps)}fps` : ""}` : "";
  const pills = [canvas, total ? `时长 ${clock(total, true)}` : "", AUDIO_LABEL[R.audio_mode] || ""].filter(Boolean);
  const ask = F.video ? "" : `请继续 ${R.work_dir} 的运行，完成配音与合成。`;
  return `<div class="film">${playerBlock(F.video, "还没有成片。合成在对话里确认。", total)}
    <div>${pills.length ? `<div class="deliv">${pills.map((text) => `<span class="pill">${esc(text)}</span>`).join("")}</div>` : ""}${ask ? notice("还没有合成成片。", "info", askButton(ask)) : ""}${timeline}</div></div>`;
}

function runQc(R) {
  if (!R.qc.length) return empty("还没有 QC 报告", "合成后会写 final_qc.json、golden_eval.json、assembly_qc.json；MiMo 复核需要 --mimo-qc。");
  const ask = R.blockers ? `请查看 ${R.work_dir} 的 QC 阻断项，修复后重新合成。` : "";
  return `${ask ? notice(`共 ${R.blockers} 个阻断项。`, "danger", askButton(ask, true)) : ""}<div class="qc-grid">${R.qc.map((card) => `<div class="card qc" data-file="${esc(card.file)}" data-level="${esc(card.level)}"><div class="hd"><b>${esc(card.label)}</b>${statusChip(QC_LEVEL[card.level] || ["warn", card.level])}</div>
    <div class="muted small mono">${esc(card.file)}</div><p>${esc(card.text)}</p>
    ${card.findings.length ? `<ul>${card.findings.map((f) => `<li class="${f.blocking ? "blocking" : ""}"><span class="mono small">${esc(f.code)}</span> ${esc(f.message)}</li>`).join("")}</ul>` : ""}</div>`).join("")}</div>`;
}

function runResources(R) {
  const L = R.views.resources;
  if (L.status === "unparseable") return rawFallback(L, "resource_lock.json");
  if (L.status !== "ok") return empty("这次运行没有 resource_lock.json", "资源记录列出本次成片用到的 BGM、音色、字体与包装；运行写出后会显示在这里。");
  const lib = (item) => (item.registry === "library" ? `<span class="mono">${esc(item.library.id)}</span> ${statusChip(LICENCE[item.library.license] || ["off", item.library.license || "—"])}${item.library.consent ? ` ${statusChip(CONSENT[item.library.consent] || ["warn", item.library.consent])}` : ""}`
    : item.registry === "material" ? '<span class="muted">素材库</span>' : item.registry === "unregistered" ? statusChip(["warn", "未登记"]) : '<span class="muted">—</span>');
  const detail = (item) => Object.values(item.detail || {}).filter((v) => v !== null && v !== "" && typeof v !== "object").join(" · ");
  const file = (item) => (item.name ? `<span class="fname" title="${esc(item.path)}">${esc(item.name)}</span><span class="fdir mono" title="${esc(item.path)}">${esc(item.dir)}</span>` : `<span class="muted">${esc(detail(item) || "—")}</span>`);
  return `${L.attention.map((item) => notice(`${item.role ? `${ROLE_LABEL[item.role] || item.role}：` : ""}${item.message || item.code}`, "", askButton(`请处理 ${R.work_dir} 的 resource_lock.json 里 ${item.role || ""} 的问题：${item.message || item.code}`))).join("")}
    <div class="card">${facts([["生成时间", esc(L.generated_at || "")], ["资源库", L.library ? `<span class="mono">${esc(L.library)}</span>` : "未使用"], ["项目", L.project ? esc(L.project.name || L.project.path) : ""]])}</div>
    ${L.templates.length ? `${section("模板", `${L.templates.length} 个`)}<div class="card table-card"><table class="rows"><thead><tr><th>角色</th><th>模板</th><th>状态</th></tr></thead><tbody>${L.templates.map((t) => `<tr><td>${esc(ROLE_LABEL[t.role] || t.role)}</td><td class="mono">${esc(t.id)}@v${esc(t.version)}</td><td>${statusChip(TPL_STATUS[t.status] || ["warn", t.status || "—"])}</td></tr>`).join("")}</tbody></table></div>` : ""}
    ${section("资源", `${L.resources.length} 项`)}
    ${L.resources.length ? `<div class="card table-card"><table class="rows"><thead><tr><th>角色</th><th>文件</th><th class="hide-m">大小</th><th>登记与授权</th></tr></thead><tbody>${L.resources.map((r) => `<tr data-role="${esc(r.role)}" data-registry="${esc(r.registry)}"><td>${esc(ROLE_LABEL[r.role] || r.role)}</td><td class="fcell">${file(r)}</td><td class="hide-m">${esc(size(r.size))}</td><td>${lib(r)}</td></tr>`).join("")}</tbody></table></div>` : empty("没有资源条目", "resource_lock.json 的 resources 为空。")}`;
}

/* ================================================================ library */

function boundBy(list) {
  return list.length ? `<div class="pills">${list.map((use) => `<a class="pill" href="${href("project", use.path)}">${esc(use.name)} · ${esc(KIND_LABEL[use.role] || use.role)}</a>`).join("")}</div>` : '<span class="muted small">没有项目绑定</span>';
}

function resourceCard(res) {
  const licence = LICENCE[res.license.status] || ["bad", res.license.status || "未填写"];
  const ask = res.license.status === "unknown" || res.license.status === "restricted" ? `请帮我确认资源 ${res.id} 的授权状态，并更新 ${res.record}。` : "";
  return `<article class="card asset" id="e-${esc(res.id)}" data-resource="${esc(res.id)}"><div class="asset-h"><div><div class="nm">${esc(res.title)}</div><div class="cat"><span class="mono">${esc(res.id)}</span> · ${esc(KIND_LABEL[res.kind] || res.kind)}</div></div>${statusChip(licence)}</div>
    ${facts([
      ["授权", `${esc(res.license.terms || "")}${res.license.evidence ? ` <span class="muted">（依据：${esc(res.license.evidence)}）</span>` : ""}`],
      ["声音授权", res.consent ? statusChip(CONSENT[res.consent.status] || ["warn", res.consent.status]) : ""],
      ["音色", res.voice ? `<span class="mono">${esc(res.voice.provider || "")}${res.voice.voice_id ? ` · ${esc(res.voice.voice_id)}` : ""}</span>` : ""],
      ["来源", res.origin && (res.origin.creator || res.origin.url) ? esc([res.origin.creator, res.origin.url].filter(Boolean).join(" · ")) : ""],
      ["文件", res.files.map((f) => `<span class="mono">${esc(f.name)}</span> <span class="muted">${esc(f.role)} · ${esc(size(f.size))}</span>`).join("<br>")],
      ["被使用", boundBy(res.bound_by)],
    ])}
    ${res.tags.length ? `<div class="pills">${res.tags.map((tag) => `<span class="pill">${esc(tag)}</span>`).join("")}</div>` : ""}
    ${res.notes ? `<p class="desc">${esc(res.notes)}</p>` : ""}${issuesList(res.issues)}${ask ? `<div>${askButton(ask)}</div>` : ""}</article>`;
}

function paramTable(rows) {
  if (!rows || !rows.length) return "";
  return `<div class="table-card"><table class="rows prm"><thead><tr><th>参数</th><th>值</th><th>来源</th></tr></thead><tbody>${rows.map((row) => `<tr data-param="${esc(row.key)}"><td>${esc(row.label)}</td><td>${row.swatch ? `<i class="swatch" data-css="background:${esc(row.swatch)}"></i>` : ""}<span class="mono">${esc(row.value)}</span>${row.unit ? ` <span class="muted">${esc(row.unit)}</span>` : ""}</td><td>${row.provenance ? `<span class="prov ${esc(row.provenance)}">${esc(PROVENANCE[row.provenance] || row.provenance)}</span>` : '<span class="muted">—</span>'}</td></tr>`).join("")}</tbody></table></div>`;
}

const box = (b) => `left:${b.left}%;top:${b.top}%;width:${b.width}%;height:${b.height}%`;

function templateGeometry(tpl, libPath) {
  const P = tpl.preview;
  if (!P) return "";
  const ratio = `aspect-ratio:${num(P.canvas.width)} / ${num(P.canvas.height)}`;
  if (P.type === "subtitle_style") {
    const L = P.line;
    const over = L.width_px && L.width_px > L.usable_px;
    const canvas = `<div class="mini" data-css="${ratio}" role="img" aria-label="字幕带与最长一行的示意图">
      <span class="side" data-css="left:${P.side}%"></span><span class="side" data-css="right:${P.side}%"></span>
      ${P.band ? `<span class="band" data-css="top:${P.band.top}%;height:${P.band.height}%"></span>` : ""}
      ${L.font_size ? `<span class="line ${over ? "over" : ""}" data-css="bottom:${L.bottom ?? 4}%;font-size:${L.font_size}cqw">${esc(L.text)}</span>` : ""}</div>`;
    const notes = [`画布 ${num(P.canvas.width)}×${num(P.canvas.height)} 等比缩小`, P.band_px ? `字幕带 y ${P.band_px.y_top}–${P.band_px.y_bot}，底对齐，底边距 ${P.margin_v}px` : "未给字幕带，示意放在底部",
      L.width_px ? `一行 ${L.chars} 字 × 字号 = ${L.width_px}px，可用宽度 ${L.usable_px}px（两侧各留 40px）` : ""].filter(Boolean);
    return `<div class="tpl-geo">${canvas}<div class="small"><b>示意图，不是渲染。</b><span class="muted">用每行字数的最长一行核对字幕带与宽度。${esc(notes.join("；"))}</span>${over ? ` ${statusChip(["warn", "超出可用宽度"])}` : ""}</div></div>`;
  }
  const layers = P.layers.map((layer) => (layer.box ? (layer.media ? `<img class="layer" src="${esc(mediaUrl(layer.media.path))}" alt="" data-css="${box(layer.box)}">` : `<span class="layer-box" data-css="${box(layer.box)}">${esc(layer.name)}</span>`) : "")).join("");
  const canvas = `<div class="mini pkg" data-css="${ratio}" role="img" aria-label="包装图层示意图">${layers}${P.safe ? `<span class="safe" data-css="${box(P.safe)}"></span>` : ""}</div>`;
  const list = `<table class="rows"><thead><tr><th>图层</th><th>位置</th><th>图片</th></tr></thead><tbody>${P.layers.map((layer) => `<tr><td>${esc(layer.name)}</td><td class="mono small">${esc(layer.rect_text)}</td><td>${layer.resource ? `<a href="${href("library", libPath, "resources", `id=${enc(layer.resource)}`)}">${esc(layer.title || layer.resource)}</a>` : "—"}${layer.media ? "" : ' <span class="muted small">（不在 --root 内，不预览）</span>'}</td></tr>`).join("")}</tbody></table>`;
  return `<div class="tpl-geo">${canvas}<div class="small"><b>示意图，不是渲染。</b><span class="muted">图层按 rect 摆放在 ${num(P.canvas.width)}×${num(P.canvas.height)} 画布上，青色虚线框是安全区；棋盘格是透明处。</span></div></div><div class="table-card">${list}</div>`;
}

function templateCard(tpl, libPath) {
  const a = tpl.adoption;
  const ask = tpl.status === "draft" ? `请帮我评审模板 ${tpl.ref}，确认后写入采用记录。` : "";
  return `<article class="card asset" id="e-${esc(tpl.ref)}" data-template="${esc(tpl.ref)}"><div class="asset-h"><div><div class="nm">${esc(tpl.title || tpl.id)}</div><div class="cat"><span class="mono">${esc(tpl.ref)}</span> · ${esc(KIND_LABEL[tpl.kind] || tpl.kind)}</div></div>${statusChip(TPL_STATUS[tpl.status] || ["bad", tpl.status || "未填写"])}</div>
    ${facts([["画布", tpl.canvas ? `${num(tpl.canvas.width)}×${num(tpl.canvas.height)}` : ""], ["样片", tpl.samples.map((s) => (s.missing ? `<span class="mono">${esc(s.id)}</span> ${statusChip(["bad", "不存在"])}` : `<a href="${href("library", libPath, "samples", `id=${enc(s.id)}`)}">${esc(s.title || s.id)}</a>`)).join("、")], ["被使用", boundBy(tpl.bound_by)]])}
    ${a ? `<div class="adoption"><b>${esc(a.date || "")} · ${esc(a.by || "")}</b> 采用：<q>${esc(a.statement || "")}</q><div class="muted small">范围：${esc(a.scope || "")}</div></div>` : ""}
    ${templateGeometry(tpl, libPath)}${paramTable(tpl.rows)}
    <details class="rawjson"><summary>原始 JSON</summary><pre class="params">${esc(JSON.stringify(tpl.params, null, 2))}</pre></details>${tpl.notes ? `<p class="desc">${esc(tpl.notes)}</p>` : ""}${issuesList(tpl.issues)}${ask ? `<div>${askButton(ask)}</div>` : ""}</article>`;
}

function sampleCard(sample) {
  return `<article class="card asset" id="e-${esc(sample.id)}" data-sample="${esc(sample.id)}"><div class="asset-h"><div><div class="nm">${esc(sample.title)}</div><div class="cat"><span class="mono">${esc(sample.id)}</span>${sample.canvas ? ` · ${num(sample.canvas.width)}×${num(sample.canvas.height)}` : ""}</div></div>${sample.offline ? statusChip(["warn", "不在本机"]) : ""}</div>
    ${facts([["示范", `<span class="pills">${sample.demonstrates.map((text) => `<span class="pill">${esc(text)}</span>`).join("")}</span>`], ["不能照搬", esc(sample.not_reusable)], ["对应模板", sample.templates.map((ref) => `<span class="mono">${esc(ref)}</span>`).join("、")]])}
    ${issuesList(sample.issues)}</article>`;
}

function viewLibrary(L, route) {
  const tab = route.view || "resources";
  const sub = `资源库 · <span class="mono">${esc(L.path)}</span> · ${L.error_count ? `${L.error_count} 个错误` : "校验通过"}${L.warning_count ? ` · ${L.warning_count} 条提醒` : ""}`;
  let body;
  switch (tab) {
    case "templates": {
      const media = L.templates.flatMap((tpl) => tpl.samples.filter((s) => s.file && s.file.path).map((s) => mediaFigure(s.file, `${tpl.ref} 的样片 ${s.id}`)));
      body = L.templates.length ? `<div class="assets">${L.templates.map((tpl) => templateCard(tpl, L.path)).join("")}</div>${gallery([...new Set(media)])}` : empty("还没有模板", "模板放在 templates/<kind>/<id>/v<version>/template.json。");
      break;
    }
    case "samples":
      body = L.samples.length ? `<div class="assets">${L.samples.map(sampleCard).join("")}</div>${gallery(L.samples.map((s) => mediaFigure(s.file, s.title)).filter(Boolean))}` : empty("还没有样片", "样片放在 samples/<id>/sample.json。");
      break;
    default: {
      const kinds = RESOURCE_KINDS.filter((kind) => L.resources.some((res) => res.kind === kind));
      const media = L.resources.flatMap((res) => res.files.filter((f) => f.media).map((f) => mediaFigure(f.media, `${res.title}（${KIND_LABEL[res.kind] || res.kind}）`)));
      body = L.resources.length ? kinds.map((kind) => `${section(KIND_LABEL[kind] || kind, `${L.resources.filter((res) => res.kind === kind).length} 项`)}<div class="assets">${L.resources.filter((res) => res.kind === kind).map(resourceCard).join("")}</div>`).join("") + gallery(media)
        : empty("还没有资源", "资源放在 resources/<kind>/<id>/resource.json。");
    }
  }
  return `<div class="wrap" data-view="library-${esc(tab)}">${head(L.name, sub)}${L.error ? notice(`library.json ${L.error}`, "danger") : ""}${L.issues.map((item) => notice(`${item.path}：${item.message}`, item.level === "error" ? "danger" : "")).join("")}${body}</div>`;
}
