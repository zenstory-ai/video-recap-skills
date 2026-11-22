#!/usr/bin/env python3
"""Atomic canonical timeline.json v3 -> MP4/WAV renderer with auto-applied mechanical invariants."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from _audio import (
    CODEC_PEAK_HEADROOM_DB,
    build_loudnorm_filter,
    correct_aac_true_peak,
    default_bridge,
    ducking_expression,
    measure_loudness,
)
from _common import (
    color_tag_args,
    filter_script_args,
    frame_count,
    has_tool,
    load_json,
    parse_fps,
    probe_media,
    resolve_source,
    run_cmd,
    timeline_video_spans,
)
from _subtitles import write_ass, write_srt

__all__ = ["main", "render_timeline", "validate_timeline_physical"]

_EDGE_FADE_S = 0.012
_VIDEO_OV_EXTS = {".mov", ".webm", ".mp4", ".mkv", ".gif"}
_SCALE_MATRIX = {"bt709": "bt709", "bt470bg": "bt470", "smpte170m": "smpte170m", "fcc": "fcc", "smpte240m": "smpte240m", "bt2020nc": "bt2020"}
_BT709 = {"colorspace": "bt709", "color_primaries": "bt709", "color_trc": "bt709", "color_range": "tv"}
_BT709_SETPARAMS = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"


def _even(val: float) -> int:
    return max(2, int(round(val)) - int(round(val)) % 2)


def _esc_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "'\\\\\\''")


def _color_conv(tags: dict[str, Any]) -> str:
    if tags.get("from_rgb"):
        return "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
    in_m = _SCALE_MATRIX.get(str(tags.get("colorspace") or ""))
    if in_m:
        return f"scale=in_color_matrix={in_m}:in_range={tags.get('color_range', 'tv')}:out_color_matrix=bt709:out_range=tv,format=yuv420p"
    return "format=yuv420p"


def _continuous_join(prev: dict[str, Any], nxt: dict[str, Any]) -> bool:
    return prev["source"] == nxt["source"] and abs(prev["out"] - nxt["in"]) <= 0.05


def _narration_spans(timeline: dict[str, Any], base_dir: Path | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, seg in enumerate((timeline.get("audio") or {}).get("narration") or ()):
        fref = seg.get("file") or seg.get("source")
        if not fref:
            raise ValueError(f"Narration segment {seg.get('id', idx)} missing 'file'")
        wav = resolve_source(timeline, str(fref), base_dir)
        if not wav.is_file():
            raise FileNotFoundError(f"Narration audio not found: {wav}")
        dur = float(seg.get("duration") or probe_media(wav)["duration_s"])
        at = float(seg.get("at", 0.0))
        out.append({"id": str(seg.get("id") or f"n{idx + 1}"), "path": wav, "at": at, "end": round(at + dur, 4), "duration": dur, "gain": float(seg.get("gain", 1.0))})
    return sorted(out, key=lambda row: row["at"])


def validate_timeline_physical(timeline: dict[str, Any], *, base_dir: Path | None = None, strict: bool = False) -> dict[str, Any]:
    """Fail-fast on deterministic physical impossibilities; soft-warn on timing overlaps unless strict=True."""
    spans, total_dur = timeline_video_spans(timeline)
    if not spans:
        raise ValueError("timeline.json has empty 'video' track")
    source_info: dict[str, dict[str, Any]] = {}
    for span in spans:
        src_path = resolve_source(timeline, span["source"], base_dir)
        key = str(src_path)
        if key not in source_info:
            source_info[key] = probe_media(src_path)
        if span["in"] < 0 or span["out"] > source_info[key]["duration_s"] + 0.15:
            raise ValueError(f"Clip {span['id']} [{span['in']}, {span['out']}] exceeds source duration {source_info[key]['duration_s']:.3f}s")

    narrs = _narration_spans(timeline, base_dir)
    warnings: list[str] = []
    for i in range(1, len(narrs)):
        if narrs[i]["at"] < narrs[i - 1]["end"] - 0.02:
            warnings.append(f"Narration overlap: {narrs[i - 1]['id']} ({narrs[i - 1]['end']:.3f}s) > {narrs[i]['id']} ({narrs[i]['at']:.3f}s)")
    if narrs and narrs[-1]["end"] > total_dur + 0.15:
        warnings.append(f"Narration {narrs[-1]['id']} ends at {narrs[-1]['end']:.3f}s past video end {total_dur:.3f}s")

    keep_wins: list[tuple[float, float]] = []
    for span in spans:
        for kw in span["clip"].get("keep_windows") or ():
            ks, ke = max(span["in"], float(kw[0])), min(span["out"], float(kw[1]))
            if ke > ks:
                keep_wins.append((span["timeline_start"] + ks - span["in"], span["timeline_start"] + ke - span["in"]))
    for narr in narrs:
        for ks, ke in keep_wins:
            if narr["at"] < ke - 0.02 and narr["end"] > ks + 0.02:
                warnings.append(f"Narration {narr['id']} [{narr['at']:.2f}, {narr['end']:.2f}] collides with keep_window [{ks:.2f}, {ke:.2f}]")
    if strict and warnings:
        raise ValueError("; ".join(warnings))
    return {"spans": spans, "duration_s": total_dur, "source_info": source_info, "narration": narrs, "warnings": warnings}


def _fit_scale_filter(tw: int, th: int, fit: str, focus: Any) -> str:
    if str(fit or "contain") == "cover":
        fx, fy = (float(focus[0]), float(focus[1])) if isinstance(focus, (list, tuple)) and len(focus) == 2 else (0.5, 0.5)
        fx, fy = max(0.0, min(1.0, fx)), max(0.0, min(1.0, fy))
        return f"scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th}:(iw-ow)*{fx:.3f}:(ih-oh)*{fy:.3f},setsar=1"
    return f"scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2,setsar=1"


def _clip_vfilter(label_in: str, label_out: str, span: dict[str, Any], sinfo: dict[str, Any],
                  cw: int, ch: int, fps_str: str, n_frames: int, backdrop: dict[str, Any],
                  viewport: dict[str, Any] | None) -> str:
    src_fps = float(parse_fps(sinfo.get("fps_rational") or sinfo.get("fps", 30.0)))
    half = 0.5 / src_fps if src_fps > 0 else 0.0
    t_in = max(0.0, span["in"] - half)
    t_out = max(t_in + 0.001, span["out"] - half)
    crop = span["clip"].get("crop")
    crop_f = f"crop={int(crop['w'])}:{int(crop['h'])}:{int(crop['x'])}:{int(crop['y'])}," if crop else ""
    cf = _color_conv(sinfo.get("color_tags") or {})
    core = f"trim=start={t_in:.6f}:end={t_out:.6f},setpts=PTS-STARTPTS,fps={fps_str},tpad=stop_mode=clone:stop=-1,trim=end_frame={n_frames},setpts=PTS-STARTPTS"
    bkind = str((backdrop or {}).get("kind") or "none")
    fit_mode = str((viewport or {}).get("fit") or "contain")
    focus = span["clip"].get("focus")

    if bkind in {"blur", "color"} and viewport:
        tag = label_out.strip("[]")
        vx, vy = int(round(float(viewport.get("x", 0.0)) * cw)), int(round(float(viewport.get("y", 0.0)) * ch))
        vw, vh = _even(float(viewport.get("w", 1.0)) * cw), _even(float(viewport.get("h", 1.0)) * ch)
        fg = f"{crop_f}{_fit_scale_filter(vw, vh, fit_mode, focus)},{cf}"
        if bkind == "blur":
            sigma = float(backdrop.get("blur_sigma", 20))
            return (
                f"{label_in}{core},split=2[{tag}_bs][{tag}_fs];"
                f"[{tag}_bs]scale={cw}:{ch}:force_original_aspect_ratio=increase,crop={cw}:{ch},boxblur={sigma:.1f}:2,setsar=1,{cf}[{tag}_bg];"
                f"[{tag}_fs]{fg}[{tag}_fg];"
                f"[{tag}_bg][{tag}_fg]overlay={vx}:{vy}:shortest=1,{_BT709_SETPARAMS}{label_out}"
            )
        col = str(backdrop.get("color") or "#000000")
        return (
            f"color=c={col}:s={cw}x{ch}:r={fps_str}:d={span['duration']:.6f},format=yuv420p[{tag}_bg];"
            f"{label_in}{core},{fg}[{tag}_fg];"
            f"[{tag}_bg][{tag}_fg]overlay={vx}:{vy}:shortest=1,{_BT709_SETPARAMS}{label_out}"
        )
    return f"{label_in}{core},{crop_f}{_fit_scale_filter(cw, ch, fit_mode, focus)},{cf},{_BT709_SETPARAMS}{label_out}"


def _overlay_filters(cur_v: str, overlays: Sequence[dict[str, Any]], tl: dict[str, Any], base_dir: Path | None,
                     cw: int, ch: int, inputs: list[str], parts: list[str]) -> str:
    for idx, ov in enumerate(overlays):
        src_p = resolve_source(tl, str(ov["source"]), base_dir)
        if not src_p.is_file():
            raise FileNotFoundError(f"Overlay asset not found: {src_p}")
        is_video = src_p.suffix.lower() in _VIDEO_OV_EXTS
        in_i = inputs.count("-i")
        if is_video:
            if src_p.suffix.lower() == ".webm":
                inputs.extend(["-c:v", "libvpx-vp9", "-i", str(src_p)])
            else:
                inputs.extend(["-i", str(src_p)])
        else:
            inputs.extend(["-loop", "1", "-i", str(src_p)])
        rect = ov.get("rect") or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        ox, oy = int(round(float(rect.get("x", 0.0)) * cw)), int(round(float(rect.get("y", 0.0)) * ch))
        ow, oh = _even(float(rect.get("w", 1.0)) * cw), _even(float(rect.get("h", 1.0)) * ch)
        full_canvas = ox == 0 and oy == 0 and ow == cw and oh == ch
        s0, s1 = float(ov.get("start", 0.0)), float(ov.get("end", 1.0))
        dur = max(0.05, s1 - s0)
        scale_f = f"scale={cw}:{ch}" if full_canvas else f"scale={ow}:{oh}:force_original_aspect_ratio=decrease"
        chain = [f"[{in_i}:v]format=rgba", scale_f, f"trim=duration={dur:.4f},setpts=PTS-STARTPTS+{s0:.4f}/TB"]
        if float(ov.get("fade_in", 0.0)) > 0:
            chain.append(f"fade=t=in:st={s0:.4f}:d={min(float(ov['fade_in']), dur / 2):.4f}:alpha=1")
        if float(ov.get("fade_out", 0.0)) > 0:
            fo = min(float(ov["fade_out"]), dur / 2)
            chain.append(f"fade=t=out:st={max(s0, s1 - fo):.4f}:d={fo:.4f}:alpha=1")
        parts.append(",".join(chain) + f"[ov_{idx}]")
        y_expr = f"{oy}+max(0,1-(t-{s0:.4f})/0.25)*24" if ov.get("motion") == "slide_up" else str(oy)
        nxt_v = f"[v_ov_{idx}]"
        parts.append(f"{cur_v}[ov_{idx}]overlay=x={ox}:y='{y_expr}':enable='between(t,{s0:.4f},{s1:.4f})':eof_action=pass:shortest=0{nxt_v}")
        cur_v = nxt_v
    return cur_v


def _delay_bus(in_i: int, at_s: float, gain: float, total_dur: float, lbl: str) -> str:
    ms = max(0, int(round(at_s * 1000)))
    return f"[{in_i}:a:0]aformat=sample_rates=48000:channel_layouts=stereo,volume={gain:.4f},adelay={ms}|{ms},apad=whole_dur={total_dur:.4f},atrim=0:{total_dur:.4f}{lbl}"


def _build_audio_graph(tl: dict[str, Any], base_dir: Path | None, spans: Sequence[dict[str, Any]],
                       source_info: dict[str, dict[str, Any]], src_to_idx: dict[str, int],
                       narrs: Sequence[dict[str, Any]], total_dur: float, inputs: list[str]) -> tuple[list[str], bool]:
    a_parts, a_concat, buses = [], [], []
    has_audio = False
    for idx, span in enumerate(spans):
        spath = str(resolve_source(tl, span["source"], base_dir))
        in_i, sinfo = src_to_idx[spath], source_info[spath]
        albl = f"[aseg_{idx}]"
        keep = str(span["clip"].get("audio", "keep")) != "mute" and bool(sinfo.get("has_audio"))
        gain = float(span["clip"].get("audio_gain", 1.0))
        if keep and gain > 0:
            has_audio = True
            fi = 0.0 if (idx > 0 and _continuous_join(spans[idx - 1], span)) else min(_EDGE_FADE_S, span["duration"] / 2)
            fo = 0.0 if (idx + 1 < len(spans) and _continuous_join(span, spans[idx + 1])) else min(_EDGE_FADE_S, span["duration"] / 2)
            ch = [f"[{in_i}:a:0]atrim=start={span['in']:.6f}:end={span['out']:.6f}", "asetpts=PTS-STARTPTS", "aformat=sample_rates=48000:channel_layouts=stereo"]
            if fi > 0:
                ch.append(f"afade=t=in:st=0:d={fi:.4f}")
            if fo > 0:
                ch.append(f"afade=t=out:st={max(0.0, span['duration'] - fo):.4f}:d={fo:.4f}")
            if abs(gain - 1.0) > 1e-4:
                ch.append(f"volume={gain:.4f}")
            a_parts.append(",".join(ch) + albl)
        else:
            a_parts.append(f"anullsrc=r=48000:cl=stereo:d={span['duration']:.6f}{albl}")
        a_concat.append(albl)

    duck = (tl.get("mix") or {}).get("duck") or {}
    fade_s = float(duck.get("fade_s", 0.25))
    bridge_s = float(duck.get("bridge_s", default_bridge(fade_s)))
    if has_audio:
        orig_lbl = a_concat[0] if len(a_concat) == 1 else "[orig_cat]"
        if len(a_concat) > 1:
            a_parts.append(f"{''.join(a_concat)}concat=n={len(a_concat)}:v=0:a=1[orig_cat]")
        if narrs:
            dw = [(n["at"], n["end"], float(duck.get("under_narration", 0.18))) for n in narrs]
            a_parts.append(f"{orig_lbl}volume='{ducking_expression(dw, 1.0, fade_s, bridge_s)}':eval=frame[orig_duck]")
            buses.append("[orig_duck]")
        else:
            buses.append(orig_lbl)
    else:
        a_parts.clear()

    for idx, narr in enumerate(narrs):
        has_audio = True
        in_i = inputs.count("-i")
        inputs.extend(["-i", str(narr["path"])])
        lbl = f"[narr_{idx}]"
        a_parts.append(_delay_bus(in_i, narr["at"], narr["gain"], total_dur, lbl))
        buses.append(lbl)

    audio_sec = tl.get("audio") or {}
    for idx, bgm in enumerate(audio_sec.get("bgm") or ()):
        bpath = resolve_source(tl, str(bgm["source"]), base_dir)
        if not bpath.is_file():
            raise FileNotFoundError(f"BGM file not found: {bpath}")
        has_audio = True
        in_i = inputs.count("-i")
        inputs.extend((["-stream_loop", "-1"] if bgm.get("loop", True) else []) + ["-i", str(bpath)])
        b_in, b_at, b_out = float(bgm.get("in", 0.0)), float(bgm.get("at", 0.0)), float(bgm.get("out", total_dur))
        b_dur, b_gain = max(0.05, b_out - b_at), float(bgm.get("gain", 0.25))
        ch = [f"[{in_i}:a:0]atrim=start={b_in:.4f}:duration={b_dur:.4f}", "asetpts=PTS-STARTPTS", "aformat=sample_rates=48000:channel_layouts=stereo"]
        if float(bgm.get("fade_in", 0.0)) > 0:
            ch.append(f"afade=t=in:st=0:d={float(bgm['fade_in']):.4f}")
        if float(bgm.get("fade_out", 0.0)) > 0:
            fo = float(bgm["fade_out"])
            ch.append(f"afade=t=out:st={max(0.0, b_dur - fo):.4f}:d={fo:.4f}")
        if b_at > 0:
            ms = int(round(b_at * 1000))
            ch.append(f"adelay={ms}|{ms}")
        if narrs:
            bw = [(n["at"], n["end"], min(b_gain, float(duck.get("bgm_under_narration", 0.12)))) for n in narrs]
            ch.append(f"volume='{ducking_expression(bw, b_gain, fade_s, bridge_s)}':eval=frame")
        else:
            ch.append(f"volume={b_gain:.4f}")
        ch.append(f"apad=whole_dur={total_dur:.4f},atrim=0:{total_dur:.4f}")
        lbl = f"[bgm_{idx}]"
        a_parts.append(",".join(ch) + lbl)
        buses.append(lbl)

    for idx, sfx in enumerate(audio_sec.get("sfx") or ()):
        xpath = resolve_source(tl, str(sfx["source"]), base_dir)
        if not xpath.is_file():
            raise FileNotFoundError(f"SFX file not found: {xpath}")
        has_audio = True
        in_i = inputs.count("-i")
        inputs.extend(["-i", str(xpath)])
        lbl = f"[sfx_{idx}]"
        a_parts.append(_delay_bus(in_i, float(sfx.get("at", 0.0)), float(sfx.get("gain", 0.8)), total_dur, lbl))
        buses.append(lbl)

    if not buses:
        a_parts.append(f"anullsrc=r=48000:cl=stereo:d={total_dur:.6f}[amix_raw]")
    elif len(buses) == 1:
        a_parts.append(f"{buses[0]}anull[amix_raw]")
    else:
        a_parts.append(f"{''.join(buses)}amix=inputs={len(buses)}:duration=first:normalize=0[amix_raw]")
    return a_parts, has_audio


def render_timeline(
    timeline: dict[str, Any],
    out_path: str | Path,
    *,
    base_dir: Path | None = None,
    preview: bool = False,
    audio_only: bool = False,
    time_range: tuple[float, float] | None = None,
    work_dir: Path | None = None,
    out_srt: str | Path | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Compile canonical `timeline.json` v3 into an MP4 (or WAV for `audio_only`)."""
    if not has_tool("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH")
    pre = validate_timeline_physical(timeline, base_dir=base_dir, strict=strict)
    spans, total_dur, source_info, narrs = pre["spans"], pre["duration_s"], pre["source_info"], pre["narration"]

    canvas = dict(timeline.get("canvas") or {})
    first = source_info[str(resolve_source(timeline, spans[0]["source"], base_dir))]
    cw, ch = _even(float(canvas.get("width") or first.get("width") or 1280)), _even(float(canvas.get("height") or first.get("height") or 720))
    if preview and max(cw, ch) > 640:
        scale = 640.0 / max(cw, ch)
        cw, ch = _even(cw * scale), _even(ch * scale)
    fps_frac = parse_fps(canvas.get("fps") or first.get("fps_rational") or 30)
    fps_str = f"{fps_frac.numerator}/{fps_frac.denominator}"

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vr_render_") as tmp_s:
        tmp = Path(work_dir) if work_dir else Path(tmp_s)
        tmp.mkdir(parents=True, exist_ok=True)

        inputs: list[str] = []
        src_to_idx: dict[str, int] = {}
        for span in spans:
            sp = str(resolve_source(timeline, span["source"], base_dir))
            if sp not in src_to_idx:
                src_to_idx[sp] = inputs.count("-i")
                inputs.extend(["-i", sp])

        v_parts, v_concat, cur_v = [], [], ""
        if not audio_only:
            for idx, span in enumerate(spans):
                sp = str(resolve_source(timeline, span["source"], base_dir))
                vlbl = f"[vseg_{idx}]"
                v_parts.append(_clip_vfilter(
                    f"[{src_to_idx[sp]}:v:0]", vlbl, span, source_info[sp], cw, ch, fps_str,
                    frame_count(span["duration"], fps_frac), canvas.get("backdrop") or {}, canvas.get("viewport"),
                ))
                v_concat.append(vlbl)
            cur_v = v_concat[0] if len(v_concat) == 1 else "[vcat]"
            if len(v_concat) > 1:
                v_parts.append(f"{''.join(v_concat)}concat=n={len(v_concat)}:v=1:a=0[vcat]")
            cur_v = _overlay_filters(cur_v, timeline.get("overlays") or (), timeline, base_dir, cw, ch, inputs, v_parts)
            subs = timeline.get("subtitles") or []
            if subs:
                st = timeline.get("subtitle_style") or {}
                ass_p = write_ass(subs, {"width": cw, "height": ch}, tmp / "subtitles.ass", style=st, safe_y=st.get("safe_y"))
                if out_srt:
                    write_srt(subs, out_srt, strip_trailing_punct=bool(st.get("strip_trailing_punct")))
                v_parts.append(f"{cur_v}ass='{_esc_path(ass_p)}'[vsub]")
                cur_v = "[vsub]"

        a_parts, has_audio = _build_audio_graph(timeline, base_dir, spans, source_info, src_to_idx, narrs, total_dur, inputs)
        mix_cfg = timeline.get("mix") or {}
        t_lufs, t_tp = float(mix_cfg.get("target_lufs", -14.0)), float(mix_cfg.get("true_peak_dbtp", -1.5))
        init_tp = round(t_tp - CODEC_PEAK_HEADROOM_DB, 2)
        measured = measure_loudness(inputs=inputs, filter_complex=";".join(a_parts), audio_map="[amix_raw]",
                                    work_dir=tmp, target_lufs=t_lufs, true_peak=init_tp) if (has_audio and not preview) else None
        ln_f, ln_plan = build_loudnorm_filter(measured, target_lufs=t_lufs, true_peak=init_tp if not preview else t_tp)
        full_fc = ";".join([*v_parts, *a_parts, f"[amix_raw]{ln_f}[aout]" if has_audio else "[amix_raw]anull[aout]"])
        f_args, f_script = filter_script_args("filter_complex", full_fc, tmp)
        range_args = ["-ss", f"{max(0.0, time_range[0]):.4f}", "-t", f"{max(0.05, time_range[1] - time_range[0]):.4f}"] if time_range else []

        try:
            if audio_only:
                run_cmd(["ffmpeg", "-y", "-hide_banner", *inputs, *f_args, "-map", "[aout]", *range_args, "-ar", "48000", "-ac", "2", str(out)], check=True)
                return {"output": str(out), "audio_only": True, "duration_s": total_dur, "loudness_plan": ln_plan, "warnings": pre["warnings"]}
            enc = ["-c:v", "libx264", "-preset", "ultrafast" if preview else "medium",
                   "-crf", "28" if preview else "18", "-pix_fmt", "yuv420p", *color_tag_args(_BT709)]
            dur_args = [] if time_range else ["-t", f"{total_dur:.4f}"]
            run_cmd([
                "ffmpeg", "-y", "-hide_banner", *inputs, *f_args, "-map", cur_v, "-map", "[aout]", *range_args,
                *enc, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart", *dur_args, str(out),
            ], check=True)
        finally:
            if f_script is not None:
                f_script.unlink(missing_ok=True)

        aac_report: dict[str, Any] = {}
        if has_audio and not preview and time_range is None:
            def _reencode_audio(new_tp: float) -> None:
                new_ln, _ = build_loudnorm_filter(measured, target_lufs=t_lufs, true_peak=new_tp)
                re_args, re_script = filter_script_args("filter_complex", ";".join([*a_parts, f"[amix_raw]{new_ln}[aout]"]), tmp)
                vid_idx = inputs.count("-i")
                cand = out.with_name(f".{out.stem}.reenc{out.suffix}")
                try:
                    run_cmd([
                        "ffmpeg", "-y", "-hide_banner", *inputs, "-i", str(out), *re_args,
                        "-map", f"{vid_idx}:v:0", "-map", "[aout]", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                        "-movflags", "+faststart", "-t", f"{total_dur:.4f}", str(cand),
                    ], check=True)
                    os.replace(cand, out)
                finally:
                    cand.unlink(missing_ok=True)
                    if re_script is not None:
                        re_script.unlink(missing_ok=True)
            aac_report = correct_aac_true_peak(out, _reencode_audio, target_tp=t_tp, initial_target=init_tp)

    return {"output": str(out), "canvas": {"width": cw, "height": ch, "fps": fps_str}, "duration_s": total_dur, "preview": preview, "loudness_plan": ln_plan, "aac_true_peak": aac_report, "warnings": pre["warnings"]}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render canonical timeline.json v3 to MP4 or WAV.")
    parser.add_argument("timeline", help="Path to timeline.json.")
    parser.add_argument("--out", required=True, help="Output MP4 (or WAV with --audio-only).")
    parser.add_argument("--preview", action="store_true", help="Fast low-res draft render.")
    parser.add_argument("--audio-only", action="store_true", help="Render mixed audio only.")
    parser.add_argument("--range", dest="time_range", help="Optional render slice START:END seconds.")
    parser.add_argument("--work-dir", help="Optional scratch directory for temp files.")
    parser.add_argument("--out-srt", help="Optional path to also export subtitles.srt.")
    parser.add_argument("--strict", action="store_true", help="Fail on soft timing warnings (narration/keep_window overlaps).")
    args = parser.parse_args(argv)

    tl_path = Path(args.timeline).resolve()
    tr = tuple(float(x) for x in args.time_range.split(":", 1)) if args.time_range else None
    result = render_timeline(
        load_json(tl_path), args.out, base_dir=tl_path.parent, preview=args.preview,
        audio_only=args.audio_only, time_range=tr,
        work_dir=Path(args.work_dir) if args.work_dir else None, out_srt=args.out_srt, strict=args.strict,
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
