"use strict";
/*
 * video-recap 审片台 — simple, uncluttered views:
 *   1. 成片 (Video list + clean native video player + 解说词 / 镜头)
 *   2. 素材库 (Audio/image previews, clean visual style cards, sample videos)
 */

const KIND_LABEL = {
  bgm: "配乐",
  sfx: "音效",
  voice: "音色",
  font: "字体",
  image: "图片",
  subtitle_style: "字幕样式",
  packaging: "画面包装",
  production_reference: "剪辑参考",
};

const empty = (title, text) =>
  `<div class="empty"><h3>${esc(title)}</h3><p>${esc(text)}</p></div>`;

const notice = (text) =>
  `<div class="notice danger" role="note">${icon("alert")}<span>${esc(text)}</span></div>`;

/* ============================================================ 1. 成片 */

function renderRunItem(run, activePath) {
  const active = run.path === activePath;
  const dur = Number.isFinite(run.duration) ? clock(run.duration) : "";
  const err = run.blockers > 0 || Boolean(run.state_error);
  return `<a class="rail-item ${active ? "active" : ""}" href="${href("run", run.path)}">
    <span class="ri-title">${err ? '<i class="dot danger" aria-hidden="true"></i>' : ""}${esc(run.title || run.name)}</span>
    ${dur ? `<span class="ri-dur">${esc(dur)}</span>` : ""}
  </a>`;
}

function renderRunRail(O, activePath) {
  const all = topRuns(O);
  const ready = all.filter((r) => r.has_video);
  const drafts = all.filter((r) => !r.has_video);

  if (!ready.length && !drafts.length) {
    return `<div class="rail-empty">暂无成片</div>`;
  }

  // When finished videos exist, show them cleanly and tuck unfinished work dirs into a quiet fold.
  const mainList = (ready.length ? ready : drafts).map((r) => renderRunItem(r, activePath)).join("");
  const draftOpen = drafts.some((r) => r.path === activePath) ? " open" : "";
  const draftHtml =
    ready.length && drafts.length
      ? `<details class="rail-more"${draftOpen}>
          <summary>未完成草稿（${drafts.length}）</summary>
          <div class="rail-sublist">${drafts.map((r) => renderRunItem(r, activePath)).join("")}</div>
        </details>`
      : "";

  return `<div class="rail-list">${mainList}${draftHtml}</div>`;
}

function hasNarrationContent(R) {
  const V = R.views.narration;
  return V.status === "ok" && V.segments && V.segments.length > 0;
}

function hasCutContent(R) {
  const V = R.views.cut;
  return (V.status === "ok" && V.clips && V.clips.length > 0) || Boolean(R.storyboards && R.storyboards.length > 0);
}

function pickPanelTab(R, requested) {
  const hasNarr = hasNarrationContent(R) || R.views.narration.status === "unparseable";
  const hasCut = hasCutContent(R) || R.views.cut.status === "unparseable";
  if (requested === "cut" && hasCut) return "cut";
  if (requested === "narration" && hasNarr) return "narration";
  if (hasNarr) return "narration";
  if (hasCut) return "cut";
  return "narration";
}

function renderVideoStage(R) {
  const finalMedia = R.views.film.video;
  const cutMedia = R.views.cut.video;
  const hasFinal = Boolean(finalMedia && finalMedia.path);
  const hasCut = Boolean(cutMedia && cutMedia.path);
  const activeMedia = hasFinal ? finalMedia : hasCut ? cutMedia : null;

  if (!activeMedia) {
    const msg =
      finalMedia && finalMedia.note
        ? finalMedia.note
        : R.next_pause
          ? `当前进度：${R.next_pause.hint}`
          : "该目录尚未生成可播放的视频文件。";
    return `<div class="theater-wrap"><div class="theater-empty"><h3>尚未生成视频</h3><p>${esc(msg)}</p></div></div>`;
  }

  return `<div class="theater-wrap">
    <div class="theater"><video id="player" src="${esc(mediaUrl(activeMedia.path))}#t=0.1" controls playsinline preload="metadata"></video></div>
  </div>`;
}

function renderScriptList(R) {
  const V = R.views.narration;
  if (V.status === "unparseable") return `<div class="insp-pad">${notice(`解说词文件无法读取：${V.error}`)}</div>`;
  if (!V.segments || !V.segments.length) {
    return `<div class="insp-pad">${empty("暂无解说词", R.audio_mode === "source-mix" ? "本片为纯原声剪辑，不包含旁白解说。" : "尚未生成解说词。")}</div>`;
  }
  return `<div class="script-list">${V.segments
    .map(
      (seg) => `<div class="script-row" id="n-${seg.index}" data-seek="${seg.start}" data-at="${seg.start}" data-end="${seg.end}">
      <span class="sr-time">${clock(seg.start)}</span>
      <p class="sr-text">${esc(seg.text)}</p>
    </div>`
    )
    .join("")}</div>`;
}

function cleanClipReason(raw) {
  if (!raw) return "";
  const tokens = String(raw)
    .split(/[|·]/)
    .map((s) => s.trim())
    .filter(Boolean);
  const cleaned = tokens
    .filter((s) => !/^(?:[a-zA-Z]?\d+|cold_open|setup|turn|climax|payoff|hook|bridge|ending|escalation|aftermath)$/i.test(s))
    .filter((s) => !/^(?:POV|视角|入点|出点|in|out)\s*[:=：]/i.test(s))
    .map((s) => s.replace(/^[a-zA-Z0-9_-]+\s*:\s*/, "").trim())
    .filter(Boolean);
  return (cleaned.length ? cleaned : tokens).join(" · ");
}

function renderShotList(R) {
  const V = R.views.cut;
  const storyboards = R.storyboards || [];
  const sbHtml = storyboards.length
    ? `<div class="sb-section">
        <div class="sub-h">画面缩略一览</div>
        <div class="sb-grid">${storyboards
          .map(
            (sb) => `<button class="sb-thumb" type="button" data-zoom-src="${esc(mediaUrl(sb.media.path))}" data-zoom-caption="${esc(`${R.title || R.name} · ${sb.label}`)}">
            <img src="${esc(mediaUrl(sb.media.path))}" alt="${esc(sb.label)}" loading="lazy">
            <span>${esc(sb.label)}</span>
          </button>`
          )
          .join("")}</div>
      </div>`
    : "";

  if (V.status === "unparseable") return `<div class="insp-pad">${notice(`剪辑镜头文件无法读取：${V.error}`)}</div>${sbHtml}`;
  if (V.status !== "ok" || !V.clips || !V.clips.length) {
    return sbHtml || `<div class="insp-pad">${empty("整段原片", "本片保留原视频完整连续画面，未做拆镜剪辑。")}</div>`;
  }

  const rows = V.clips
    .map((clip, idx) => {
      const text = cleanClipReason(clip.reason);
      const order = String(idx + 1).padStart(2, "0");
      return `<div class="clip-row" id="c-${clip.index}" data-seek="${clip.output_start}" data-at="${clip.output_start}" data-end="${clip.output_end}">
        <span class="cr-num">${order}</span>
        <div class="cr-main">
          <span class="cr-time">${clock(clip.output_start)} – ${clock(clip.output_end)}</span>
          ${text ? `<p class="cr-reason">${esc(text)}</p>` : ""}
        </div>
      </div>`;
    })
    .join("");

  return `<div class="clip-list">${rows}</div>${sbHtml}`;
}

function renderSidePanel(R, activeTab) {
  const hasNarr = hasNarrationContent(R) || R.views.narration.status === "unparseable";
  const hasCut = hasCutContent(R) || R.views.cut.status === "unparseable";

  if (hasNarr && hasCut) {
    const tab = activeTab === "cut" ? "cut" : "narration";
    const head = `<div class="insp-tabs">
      <a class="insp-tab ${tab === "narration" ? "on" : ""}" href="${href("run", R.path, "narration")}">解说词</a>
      <a class="insp-tab ${tab === "cut" ? "on" : ""}" href="${href("run", R.path, "cut")}">镜头</a>
    </div>`;
    return `${head}<div class="insp-body">${tab === "cut" ? renderShotList(R) : renderScriptList(R)}</div>`;
  }

  if (hasCut && !hasNarr) {
    return `<div class="insp-single-head">镜头</div><div class="insp-body">${renderShotList(R)}</div>`;
  }

  return `<div class="insp-single-head">解说词</div><div class="insp-body">${renderScriptList(R)}</div>`;
}

function collectRunError(R) {
  if (R.state_error) return R.state_error;
  const failedQc = (R.qc || []).filter((c) => c.level === "error");
  if (failedQc.length) {
    const details = failedQc
      .flatMap((c) => (c.findings && c.findings.length ? c.findings.map((f) => f.message) : [c.text]))
      .filter(Boolean);
    return details.join("；") || "成片存在未通过的检查项";
  }
  return "";
}

function viewStudio(O, R, route) {
  const activeTab = pickPanelTab(R, route.view);
  const hasFinal = Boolean(R.views.film.video && R.views.film.video.path);
  const hasCut = Boolean(R.views.cut.video && R.views.cut.video.path);
  const switcher =
    hasFinal && hasCut
      ? `<div class="src-switch">
          <button class="src-btn on" type="button" data-video-mode="final">成片</button>
          <button class="src-btn" type="button" data-video-mode="cut">无解说原剪</button>
        </div>`
      : "";
  const errorBanner = collectRunError(R);

  return `<div class="studio" data-view="studio">
    <aside class="rail" aria-label="成片列表">${renderRunRail(O, R.path)}</aside>
    <section class="stage">
      <div class="stage-head">
        <h1>${esc(R.title || R.name)}</h1>
        ${switcher}
      </div>
      ${errorBanner ? notice(errorBanner) : ""}
      <div class="studio-grid">
        ${renderVideoStage(R)}
        <div class="card inspector" id="inspectorHost">${renderSidePanel(R, activeTab)}</div>
      </div>
    </section>
  </div>`;
}

/* =========================================================== 2. 素材库 */

const boxCss = (b) => `left:${b.left}%;top:${b.top}%;width:${b.width}%;height:${b.height}%`;

function renderMiniPreview(tpl) {
  const P = tpl.preview;
  if (!P) return "";
  const ratio = `aspect-ratio:${num(P.canvas.width)} / ${num(P.canvas.height)}`;
  if (P.type === "subtitle_style") {
    const L = P.line;
    const lineCss = [
      `bottom:${L.bottom ?? 4}%`,
      L.font_size ? `font-size:${L.font_size}cqw` : "",
      L.color ? `--line-c:${L.color}` : "",
    ]
      .filter(Boolean)
      .join(";");
    return `<div class="mini" data-css="${ratio}" role="img" aria-label="字幕预览">
      ${P.band ? `<span class="band" data-css="top:${P.band.top}%;height:${P.band.height}%"></span>` : ""}
      ${L.font_size ? `<span class="line" data-css="${esc(lineCss)}">${esc(L.text)}</span>` : ""}
    </div>`;
  }
  const layers = P.layers
    .map((layer) =>
      layer.box
        ? layer.media
          ? `<img class="layer" src="${esc(mediaUrl(layer.media.path))}" alt="" data-css="${boxCss(layer.box)}">`
          : `<span class="layer-box" data-css="${boxCss(layer.box)}">${esc(layer.name)}</span>`
        : ""
    )
    .join("");
  return `<div class="mini pkg" data-css="${ratio}" role="img" aria-label="包装预览">${layers}</div>`;
}

function renderResourceCard(res) {
  const mediaFile = (res.files || []).find((f) => f.media && f.media.path);
  let preview = "";
  if (mediaFile) {
    const m = mediaFile.media;
    const src = mediaUrl(m.path);
    if (m.kind === "audio") {
      preview = `<audio class="res-audio" src="${esc(src)}" controls preload="none"></audio>`;
    } else if (m.kind === "image") {
      preview = `<button class="img-btn" type="button" data-zoom-src="${esc(src)}" data-zoom-caption="${esc(res.title)}"><img src="${esc(src)}" alt="${esc(res.title)}" loading="lazy"></button>`;
    }
  }
  return `<article class="card res-card" id="e-${esc(res.id)}">
    <div class="rc-top">
      <b class="rc-title">${esc(res.title)}</b>
      <span class="tag">${esc(KIND_LABEL[res.kind] || res.kind)}</span>
    </div>
    ${res.notes ? `<p class="small muted">${esc(res.notes)}</p>` : ""}
    ${preview}
  </article>`;
}

function renderTemplateCard(tpl) {
  const mini = renderMiniPreview(tpl);
  const summary = (tpl.rows || [])
    .slice(0, 3)
    .map((r) => `${r.label} ${r.value}${r.unit ? r.unit : ""}`)
    .join(" · ");
  return `<article class="card tpl-card" id="e-${esc(tpl.ref)}">
    ${mini ? `<div class="tpl-mini">${mini}</div>` : ""}
    <div class="tpl-info">
      <div class="rc-top">
        <b class="rc-title">${esc(tpl.title || tpl.id)}</b>
        <span class="tag">${esc(KIND_LABEL[tpl.kind] || tpl.kind)}</span>
      </div>
      ${summary ? `<div class="small muted">${esc(summary)}</div>` : ""}
      ${tpl.notes ? `<p class="small">${esc(tpl.notes)}</p>` : ""}
    </div>
  </article>`;
}

function renderSampleCard(sample) {
  if (!sample.file || !sample.file.path) {
    return `<article class="card res-card" id="e-${esc(sample.id)}">
      <div class="rc-top">
        <b class="rc-title">${esc(sample.title)}</b>
        <span class="tag">参考样片</span>
      </div>
      ${sample.demonstrates && sample.demonstrates.length ? `<p class="small muted">参考点：${esc(sample.demonstrates.join("、"))}</p>` : ""}
    </article>`;
  }
  return `<article class="card sample-card" id="e-${esc(sample.id)}">
    <div class="sample-video"><video src="${esc(mediaUrl(sample.file.path))}#t=0.1" controls playsinline preload="metadata"></video></div>
    <div class="tpl-info">
      <b class="rc-title">${esc(sample.title)}</b>
      ${sample.demonstrates && sample.demonstrates.length ? `<p class="small muted">参考点：${esc(sample.demonstrates.join("、"))}</p>` : ""}
    </div>
  </article>`;
}

function viewLibrary(O, L, route) {
  const sections = [];
  if (L.resources.length) sections.push({ key: "resources", label: "音色与配乐" });
  if (L.templates.length) sections.push({ key: "templates", label: "画面与字幕样式" });
  if (L.samples.length) sections.push({ key: "samples", label: "参考样片" });

  if (!sections.length) {
    return `<div class="page-wrap">${empty("素材库为空", "尚未添加音色、配乐、样式或样片。")}</div>`;
  }

  const active = sections.some((s) => s.key === route.view) ? route.view : sections[0].key;
  const switcher =
    sections.length > 1
      ? `<div class="nav-seg" role="tablist">
          ${sections
            .map(
              (s) =>
                `<a class="nav-tab" href="${href("library", L.path, s.key)}" ${s.key === active ? 'aria-current="page"' : ""}>${esc(s.label)}</a>`
            )
            .join("")}
        </div>`
      : "";

  const body =
    active === "templates"
      ? `<div class="res-grid">${L.templates.map(renderTemplateCard).join("")}</div>`
      : active === "samples"
        ? `<div class="tpl-list">${L.samples.map(renderSampleCard).join("")}</div>`
        : `<div class="res-grid">${L.resources.map(renderResourceCard).join("")}</div>`;

  return `<div class="page-wrap" data-view="library-${esc(active)}">
    <div class="page-bar">
      <h1>${esc(L.name)}</h1>
      ${switcher}
    </div>
    ${L.error ? notice(`library.json ${L.error}`) : ""}
    ${body}
  </div>`;
}
