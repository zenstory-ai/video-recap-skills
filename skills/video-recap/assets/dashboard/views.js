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
  image: "图形",
  subtitle_style: "字幕样式",
  packaging: "画面包装",
  production_reference: "剪辑参考",
};

const CATEGORY_LABEL = {
  "urban-suspense": "都市悬疑",
  "ancient-emotional": "古风情感",
  "high-energy": "燃系竞技",
  "warm-narrative": "温情叙事",
  whoosh: "转场呼啸",
  impact: "情绪重音",
  riser: "蓄力抬升",
  accent: "节奏提示",
  "steady-narrator": "沉稳叙事",
  "crisp-female": "清亮女声",
  "cinematic-male": "电影感男声",
  "news-brief": "资讯播报",
  portrait: "竖屏 9:16",
  landscape: "横屏 16:9",
  reference: "剪辑参考",
};

const PROVIDER_LABEL = {
  "mimo-tts": "MiMo 语音",
  "fish-audio": "Fish Audio",
  "index-tts": "IndexTTS",
};

const catLabel = (raw, fallback = "默认分类") =>
  (raw && (CATEGORY_LABEL[raw] || raw)) || fallback;

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
  const studioVp =
    P.studio && P.studio.viewport
      ? `<span class="studio-vp" data-css="${boxCss(P.studio.viewport)}">主画面视窗</span>`
      : "";
  const layers = (P.layers || [])
    .map((layer) =>
      layer.box
        ? layer.media
          ? `<img class="layer" src="${esc(mediaUrl(layer.media.path))}" alt="" data-css="${boxCss(layer.box)}">`
          : `<span class="layer-box" data-css="${boxCss(layer.box)}">${esc(layer.name)}</span>`
        : ""
    )
    .join("");
  const cls = P.studio ? `mini studio-mini ${P.studio.backdrop === "blurred_frame" ? "blurred" : ""}` : "mini pkg";
  return `<div class="${cls}" data-css="${ratio}" role="img" aria-label="包装预览">${studioVp}${layers}</div>`;
}

function renderFilterChips(libPath, tabKey, groups, activeCat) {
  if (groups.length <= 1) return "";
  const allChip = `<a class="chip-btn ${!activeCat ? "on" : ""}" href="${href("library", libPath, tabKey)}">全部<span>${groups.reduce((n, g) => n + g.items.length, 0)}</span></a>`;
  const chips = groups
    .map(
      (g) =>
        `<a class="chip-btn ${activeCat === g.key ? "on" : ""}" href="${href("library", libPath, tabKey, `cat=${enc(g.key)}`)}">${esc(g.label)}<span>${g.items.length}</span></a>`
    )
    .join("");
  return `<div class="filter-bar" role="navigation" aria-label="分类筛选">${allChip}${chips}</div>`;
}

function groupItems(items, keyFn, labelFn) {
  const map = new Map();
  for (const item of items) {
    const key = keyFn(item) || "default";
    if (!map.has(key)) map.set(key, { key, label: labelFn(key, item), items: [] });
    map.get(key).items.push(item);
  }
  return Array.from(map.values());
}

function renderGroupedSections(groups, activeCat, cardFn, gridClass = "res-grid") {
  const shown = activeCat ? groups.filter((g) => g.key === activeCat) : groups;
  if (!shown.length) return empty("暂无匹配条目", "切换上方分类查看其他素材。");
  if (shown.length === 1 && groups.length === 1) {
    return `<div class="${gridClass}">${shown[0].items.map(cardFn).join("")}</div>`;
  }
  return `<div class="cat-stack">${shown
    .map(
      (g) => `<section class="cat-group">
        <div class="cat-head"><h2>${esc(g.label)}</h2><span class="cat-count">${g.items.length}</span></div>
        <div class="${gridClass}">${g.items.map(cardFn).join("")}</div>
      </section>`
    )
    .join("")}</div>`;
}

function renderAudioPreview(res) {
  const mediaFile = (res.files || []).find((f) => f.media && f.media.path && f.media.kind === "audio");
  if (!mediaFile) return "";
  return `<audio class="res-audio" src="${esc(mediaUrl(mediaFile.media.path))}" controls preload="metadata"></audio>`;
}

function renderBgmCard(res) {
  const vocalBadge =
    res.has_vocal === false
      ? '<span class="tag ok">纯音乐</span>'
      : res.has_vocal === true
        ? '<span class="tag warn">含人声</span>'
        : `<span class="tag">${esc(catLabel(res.category, "配乐"))}</span>`;
  const dur = res.measured && Number.isFinite(res.measured.duration_sec) ? clock(res.measured.duration_sec) : "";
  const bpm = Number.isFinite(res.bpm) ? `${Math.round(res.bpm)} BPM` : "";
  const lufs =
    res.measured && Number.isFinite(res.measured.lufs_i) ? `${res.measured.lufs_i.toFixed(1)} LUFS` : "";
  const metaParts = [dur, bpm, lufs].filter(Boolean).join(" · ");
  const chips = [...(res.moods || []), ...(res.scene_fit || []), ...(res.tags || [])].slice(0, 4);

  return `<article class="card res-card" id="e-${esc(res.id)}">
    <div class="rc-top">
      <b class="rc-title">${esc(res.title)}</b>
      ${vocalBadge}
    </div>
    ${metaParts ? `<div class="rc-meta">${esc(metaParts)}</div>` : ""}
    ${chips.length ? `<div class="chip-row">${chips.map((c) => `<span class="meta-chip">${esc(c)}</span>`).join("")}</div>` : ""}
    ${res.notes ? `<p class="small muted">${esc(res.notes)}</p>` : ""}
    ${renderAudioPreview(res)}
  </article>`;
}

function renderSfxCard(res) {
  const peak = Number.isFinite(res.peak_offset_ms) ? `瞬态峰值 ${Math.round(res.peak_offset_ms)}ms` : "";
  const dur =
    res.measured && Number.isFinite(res.measured.duration_sec) ? `${res.measured.duration_sec.toFixed(2)}s` : "";
  const metaParts = [res.tone, dur, peak].filter(Boolean).join(" · ");
  const chips = [...(res.scene_fit || []), ...(res.tags || [])].slice(0, 3);

  return `<article class="card res-card compact" id="e-${esc(res.id)}">
    <div class="rc-top">
      <b class="rc-title">${esc(res.title)}</b>
      <span class="tag">${esc(catLabel(res.category, "音效"))}</span>
    </div>
    ${metaParts ? `<div class="rc-meta">${esc(metaParts)}</div>` : ""}
    ${chips.length ? `<div class="chip-row">${chips.map((c) => `<span class="meta-chip">${esc(c)}</span>`).join("")}</div>` : ""}
    ${res.notes ? `<p class="small muted">${esc(res.notes)}</p>` : ""}
    ${renderAudioPreview(res)}
  </article>`;
}

function renderVoiceCard(res) {
  const provider = res.voice && res.voice.provider ? PROVIDER_LABEL[res.voice.provider] || res.voice.provider : "语音合成";
  const speed = Number.isFinite(res.recommended_speed) ? `推荐 ${res.recommended_speed}x` : "";
  const metaParts = [provider, res.persona || res.gender, speed].filter(Boolean).join(" · ");
  const chips = [...(res.scene_fit || []), ...(res.tags || [])].slice(0, 4);
  const audio = renderAudioPreview(res);

  return `<article class="card res-card" id="e-${esc(res.id)}">
    <div class="rc-top">
      <b class="rc-title">${esc(res.title)}</b>
      <span class="tag voice">${esc(catLabel(res.category, "解说音色"))}</span>
    </div>
    ${metaParts ? `<div class="rc-meta">${esc(metaParts)}</div>` : ""}
    ${chips.length ? `<div class="chip-row">${chips.map((c) => `<span class="meta-chip">${esc(c)}</span>`).join("")}</div>` : ""}
    ${res.notes ? `<p class="small muted">${esc(res.notes)}</p>` : ""}
    ${audio || '<div class="small muted">云端预置音色（无需本地参考音频）</div>'}
  </article>`;
}

function renderAssetCard(res) {
  const mediaFile = (res.files || []).find((f) => f.media && f.media.path);
  let preview = "";
  if (mediaFile && mediaFile.media.kind === "image") {
    const src = mediaUrl(mediaFile.media.path);
    preview = `<button class="img-btn" type="button" data-zoom-src="${esc(src)}" data-zoom-caption="${esc(res.title)}"><img src="${esc(src)}" alt="${esc(res.title)}" loading="lazy"></button>`;
  }
  const chips = (res.tags || []).slice(0, 4);
  return `<article class="card res-card" id="e-${esc(res.id)}">
    <div class="rc-top">
      <b class="rc-title">${esc(res.title)}</b>
      <span class="tag">${esc(KIND_LABEL[res.kind] || res.kind)}</span>
    </div>
    ${chips.length ? `<div class="chip-row">${chips.map((c) => `<span class="meta-chip">${esc(c)}</span>`).join("")}</div>` : ""}
    ${res.notes ? `<p class="small muted">${esc(res.notes)}</p>` : ""}
    ${preview}
  </article>`;
}

function renderTemplateCard(tpl) {
  const mini = renderMiniPreview(tpl);
  const canvasText = tpl.canvas ? `${tpl.canvas.width}×${tpl.canvas.height}` : "";
  const summary = [
    canvasText,
    ...(tpl.rows || []).slice(0, 3).map((r) => `${r.label} ${r.value}${r.unit ? r.unit : ""}`),
  ]
    .filter(Boolean)
    .join(" · ");
  return `<article class="card tpl-card" id="e-${esc(tpl.ref)}">
    ${mini ? `<div class="tpl-mini">${mini}</div>` : ""}
    <div class="tpl-info">
      <div class="rc-top">
        <b class="rc-title">${esc(tpl.title || tpl.id)}</b>
        <span class="tag">${esc(KIND_LABEL[tpl.kind] || tpl.kind)}</span>
      </div>
      ${summary ? `<div class="rc-meta">${esc(summary)}</div>` : ""}
      ${tpl.notes ? `<p class="small muted">${esc(tpl.notes)}</p>` : ""}
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
      <div class="rc-top">
        <b class="rc-title">${esc(sample.title)}</b>
        <span class="tag">参考样片</span>
      </div>
      ${sample.demonstrates && sample.demonstrates.length ? `<div class="chip-row">${sample.demonstrates.map((d) => `<span class="meta-chip">${esc(d)}</span>`).join("")}</div>` : ""}
      ${sample.not_reusable ? `<p class="small muted">边界说明：${esc(sample.not_reusable)}</p>` : ""}
    </div>
  </article>`;
}

function renderEpisodeRow(ep) {
  const dur = Number.isFinite(ep.duration_sec) ? clock(ep.duration_sec) : "";
  const stats = [dur, ep.resolution].filter(Boolean).join(" · ");
  const badge = ep.ep_no ? `E${String(ep.ep_no).padStart(2, "0")}` : "单集";
  return `<div class="ep-row" id="e-${esc(ep.material_id)}">
    <span class="ep-badge">${esc(badge)}</span>
    <div class="ep-main">
      <div class="ep-head">
        <b class="ep-title">${esc(ep.episode_title)}</b>
        ${stats ? `<span class="rc-meta">${esc(stats)}</span>` : ""}
      </div>
      <p class="small muted">${esc(ep.synopsis)}</p>
    </div>
  </div>`;
}

function renderSeriesCard(series) {
  const tags = [series.era, series.genre, `${series.episode_count} 集`].filter(Boolean);
  const chars = (series.characters || []).slice(0, 6);
  return `<section class="card series-card">
    <div class="series-head">
      <div class="series-title-row">
        <h2>${esc(series.title)}</h2>
        <div class="chip-row">${tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
      </div>
      ${series.synopsis ? `<p class="small muted">${esc(series.synopsis)}</p>` : ""}
      ${chars.length ? `<div class="chip-row"><span class="small muted">主要人物：</span>${chars.map((c) => `<span class="meta-chip">${esc(c)}</span>`).join("")}</div>` : ""}
    </div>
    <div class="ep-list">${series.episodes.map(renderEpisodeRow).join("")}</div>
  </section>`;
}

function renderMaterialsView(L, activeCat) {
  const M = L.materials || { series: [], standalone: [] };
  const groups = [];
  for (const s of M.series || []) {
    groups.push({ key: s.series_id, label: s.title, items: s.episodes, series: s });
  }
  if (M.standalone && M.standalone.length) {
    groups.push({ key: "standalone", label: "单集原片", items: M.standalone, standalone: true });
  }
  const chips = renderFilterChips(L.path, "materials", groups, activeCat);
  const shown = activeCat ? groups.filter((g) => g.key === activeCat) : groups;
  const cards = shown
    .map((g) =>
      g.series
        ? renderSeriesCard(g.series)
        : `<section class="card series-card">
            <div class="series-head"><h2>单集原片</h2></div>
            <div class="ep-list">${g.items.map(renderEpisodeRow).join("")}</div>
          </section>`
    )
    .join("");
  return `${chips}<div class="cat-stack">${cards}</div>`;
}

function renderStylesView(L, activeCat, targetId) {
  const publicVisuals = (L.resources || []).filter(
    (r) =>
      (r.kind === "font" || r.kind === "image") &&
      (r.visibility !== "internal" || r.id === targetId)
  );
  const portraitTpls = (L.templates || []).filter(
    (t) => t.kind !== "production_reference" && t.canvas && t.canvas.height > t.canvas.width
  );
  const landscapeTpls = (L.templates || []).filter(
    (t) => t.kind !== "production_reference" && (!t.canvas || t.canvas.width >= t.canvas.height)
  );
  const refTpls = (L.templates || []).filter((t) => t.kind === "production_reference");
  const samples = L.samples || [];

  const groups = [];
  if (portraitTpls.length) groups.push({ key: "portrait", label: "竖屏样式 9:16", items: portraitTpls, type: "tpl" });
  if (landscapeTpls.length) groups.push({ key: "landscape", label: "横屏样式 16:9", items: landscapeTpls, type: "tpl" });
  if (refTpls.length) groups.push({ key: "reference", label: "剪辑方法参考", items: refTpls, type: "tpl" });
  if (samples.length) groups.push({ key: "samples", label: "参考样片", items: samples, type: "sample" });
  if (publicVisuals.length) groups.push({ key: "assets", label: "字体与图形资产", items: publicVisuals, type: "asset" });

  const chips = renderFilterChips(L.path, "styles", groups, activeCat);
  const shown = activeCat ? groups.filter((g) => g.key === activeCat) : groups;
  const html = shown
    .map((g) => {
      const inner =
        g.type === "sample"
          ? `<div class="tpl-list">${g.items.map(renderSampleCard).join("")}</div>`
          : g.type === "asset"
            ? `<div class="res-grid">${g.items.map(renderAssetCard).join("")}</div>`
            : `<div class="res-grid">${g.items.map(renderTemplateCard).join("")}</div>`;
      return `<section class="cat-group">
        <div class="cat-head"><h2>${esc(g.label)}</h2><span class="cat-count">${g.items.length}</span></div>
        ${inner}
      </section>`;
    })
    .join("");
  return `${chips}<div class="cat-stack">${html}</div>`;
}

function resolveLibraryTab(L, sections, route) {
  const targetId = route.q.get("id") || "";
  const available = new Set(sections.map((s) => s.key));
  if (targetId) {
    const res = (L.resources || []).find((r) => r.id === targetId);
    if (res) {
      if ((res.kind === "bgm" || res.kind === "sfx" || res.kind === "voice") && available.has(res.kind)) {
        return res.kind;
      }
      if (available.has("styles")) return "styles";
    }
    if ((L.templates || []).some((t) => t.ref === targetId) && available.has("styles")) return "styles";
    if ((L.samples || []).some((s) => s.id === targetId) && available.has("styles")) return "styles";
    const M = L.materials || { series: [], standalone: [] };
    const inMat =
      (M.standalone || []).some((e) => e.material_id === targetId) ||
      (M.series || []).some((s) => (s.episodes || []).some((e) => e.material_id === targetId));
    if (inMat && available.has("materials")) return "materials";
  }
  if (route.view === "templates" || route.view === "samples") {
    if (available.has("styles")) return "styles";
  }
  if (route.view === "resources") {
    for (const k of ["bgm", "sfx", "voice", "styles"]) {
      if (available.has(k)) return k;
    }
  }
  return available.has(route.view) ? route.view : sections[0].key;
}

function viewLibrary(O, L, route) {
  const targetId = route.q.get("id") || "";
  const activeCat = route.q.get("cat") || "";
  const matCount = L.materials && L.materials.total_count ? L.materials.total_count : 0;
  const bgmList = (L.resources || []).filter((r) => r.kind === "bgm" && (r.visibility !== "internal" || r.id === targetId));
  const sfxList = (L.resources || []).filter((r) => r.kind === "sfx" && (r.visibility !== "internal" || r.id === targetId));
  const voiceList = (L.resources || []).filter((r) => r.kind === "voice" && (r.visibility !== "internal" || r.id === targetId));
  const visualAssets = (L.resources || []).filter(
    (r) => (r.kind === "font" || r.kind === "image") && (r.visibility !== "internal" || r.id === targetId)
  );
  const styleCount = (L.templates || []).length + (L.samples || []).length + visualAssets.length;

  const sections = [];
  if (matCount > 0) sections.push({ key: "materials", label: "原片剧目", count: matCount });
  if (bgmList.length) sections.push({ key: "bgm", label: "配乐", count: bgmList.length });
  if (sfxList.length) sections.push({ key: "sfx", label: "音效", count: sfxList.length });
  if (voiceList.length) sections.push({ key: "voice", label: "音色", count: voiceList.length });
  if (styleCount > 0) sections.push({ key: "styles", label: "样式与样片", count: styleCount });

  if (!sections.length) {
    return `<div class="page-wrap">${empty("素材库为空", "尚未添加原片、配乐、音效、音色或样式模板。")}</div>`;
  }

  const active = resolveLibraryTab(L, sections, route);
  const switcher =
    sections.length > 1
      ? `<div class="nav-seg" role="tablist" aria-label="素材库分类">
          ${sections
            .map(
              (s) =>
                `<a class="nav-tab" href="${href("library", L.path, s.key)}" ${s.key === active ? 'aria-current="page"' : ""}>${esc(s.label)}<span class="tab-badge">${s.count}</span></a>`
            )
            .join("")}
        </div>`
      : "";

  let body = "";
  if (active === "materials") {
    body = renderMaterialsView(L, activeCat);
  } else if (active === "bgm") {
    const groups = groupItems(bgmList, (r) => r.category || "bgm", (k) => catLabel(k, "精选配乐"));
    body = `${renderFilterChips(L.path, "bgm", groups, activeCat)}${renderGroupedSections(groups, activeCat, renderBgmCard)}`;
  } else if (active === "sfx") {
    const groups = groupItems(sfxList, (r) => r.category || "sfx", (k) => catLabel(k, "通用音效"));
    body = `${renderFilterChips(L.path, "sfx", groups, activeCat)}${renderGroupedSections(groups, activeCat, renderSfxCard, "sfx-grid")}`;
  } else if (active === "voice") {
    const groups = groupItems(voiceList, (r) => r.category || "voice", (k) => catLabel(k, "解说音色"));
    body = `${renderFilterChips(L.path, "voice", groups, activeCat)}${renderGroupedSections(groups, activeCat, renderVoiceCard)}`;
  } else {
    body = renderStylesView(L, activeCat, targetId);
  }

  return `<div class="page-wrap" data-view="library-${esc(active)}">
    <div class="page-bar">
      <h1>${esc(L.name)}</h1>
      ${switcher}
    </div>
    ${L.error ? notice(`library.json ${L.error}`) : ""}
    ${body}
  </div>`;
}
