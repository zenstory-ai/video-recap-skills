"""Declarative studio packaging engine (9:16 portrait_cinema / portrait_stage & 16:9 landscape_cinema)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from artifacts import file_identity
from audio_automation import coalesce_duck_windows
from audio_mix import _seg_place_window
from packaging_fonts import resolve_font_family
from packaging_plates import (
    render_callout_plate,
    render_card_plate,
    render_editorial_paper_strip_plate,
    render_landscape_scrim_plate,
    render_portrait_cinema_frame_plate,
    render_portrait_stage_overlay_plate,
    render_progress_bar_plate,
    render_title_mark_plate,
)

PACKAGING_PLAN = "packaging_plan.json"
PACKAGING_SCHEMA = "video-recap.packaging_plan.v1"
PRESETS = ("portrait_cinema", "portrait_stage", "landscape_cinema")


def _escape_subtitle_filter_path(path) -> str:
    text = str(path).replace("\\", "/")
    for raw, escaped in (
        ("\\", "\\\\"),
        (":", "\\:"),
        ("'", "\\'"),
        (",", "\\,"),
        ("[", "\\["),
        ("]", "\\]"),
    ):
        text = text.replace(raw, escaped)
    return text


def _even(val: float | int) -> int:
    return max(2, int(round(val)) // 2 * 2)


def _rects_intersect(a: dict, b: dict, pad: int = 4) -> bool:
    return not (
        a["x"] + a["width"] + pad <= b["x"]
        or b["x"] + b["width"] + pad <= a["x"]
        or a["y"] + a["height"] + pad <= b["y"]
        or b["y"] + b["height"] + pad <= a["y"]
    )


def _times_overlap(s1: float, e1: float, s2: float, e2: float, tol: float = 0.08) -> bool:
    return min(e1, e2) - max(s1, s2) > tol


def has_packaging_plan(work_dir: str | Path | None) -> bool:
    return work_dir is not None and (Path(work_dir) / PACKAGING_PLAN).is_file()


def load_raw_packaging_plan(work_dir: str | Path | None) -> dict | None:
    if not has_packaging_plan(work_dir):
        return None
    path = Path(work_dir) / PACKAGING_PLAN
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"{PACKAGING_PLAN} 顶层必须是 JSON 对象")
    schema = data.get("schema", PACKAGING_SCHEMA)
    if schema != PACKAGING_SCHEMA:
        raise RuntimeError(f"{PACKAGING_PLAN} schema 不受支持: {schema!r}")
    preset = str(data.get("preset") or "").strip()
    if preset not in PRESETS:
        raise RuntimeError(f"{PACKAGING_PLAN} preset 必须是 {PRESETS} 之一，当前为 {preset!r}")
    return data


def resolve_output_canvas(work_dir: str | Path | None, input_canvas: dict) -> dict:
    """Return the effective output canvas once packaging_plan.json is considered."""
    plan = load_raw_packaging_plan(work_dir)
    if plan is None:
        return dict(input_canvas)
    preset = plan["preset"]
    declared = plan.get("canvas") or {}
    if preset in {"portrait_cinema", "portrait_stage"}:
        w = _even(declared.get("width", 1080))
        h = _even(declared.get("height", 1920))
    else:
        w = _even(declared.get("width", input_canvas["width"]))
        h = _even(declared.get("height", input_canvas["height"]))
    return {
        "width": w,
        "height": h,
        "fps": float(input_canvas.get("fps") or 25.0),
        "storage_width": w,
        "storage_height": h,
        "rotation": 0,
        "sample_aspect_ratio": "1:1",
        "display_aspect_ratio": f"{w}:{h}",
    }


def subtitle_overrides_for_plan(work_dir: str | Path | None, canvas: dict) -> dict:
    """Return preset-calibrated ASS style overrides when packaging_plan.json is active."""
    plan = load_raw_packaging_plan(work_dir)
    if plan is None:
        return {}
    preset = plan["preset"]
    w, h = int(canvas["width"]), int(canvas["height"])
    sub_cfg = plan.get("subtitle_style") or {}
    overrides: dict = {
        "play_res_x": w,
        "play_res_y": h,
        "glow": float(sub_cfg.get("glow", 3.2)),
        "fade_ms": int(sub_cfg.get("fade_ms", 70)),
        "bridge_gap_seconds": float(sub_cfg.get("bridge_gap_seconds", 0.34)),
        "highlight_color": str(sub_cfg.get("highlight_color") or "#FFD24C"),
    }
    if sub_cfg.get("font_name"):
        overrides["font_name"] = str(sub_cfg["font_name"])
    elif "SUBTITLE_FONT_NAME" not in os.environ:
        family, font_path = resolve_font_family("sans_heavy")
        if family:
            overrides["font_name"] = family
        if font_path and "SUBTITLE_FONT_FILE" not in os.environ:
            overrides["font_file"] = font_path
    if preset == "portrait_cinema":
        footer_h = int(plan.get("footer_height", round(188 * h / 1920)))
        overrides.update({
            "font_size": int(sub_cfg.get("font_size", round(52 * w / 1080))),
            "primary_color": str(sub_cfg.get("primary_color") or "#FFE294"),
            "outline_color": str(sub_cfg.get("outline_color") or "#100D0B"),
            "outline": float(sub_cfg.get("outline", round(3.0 * w / 1080, 2))),
            "shadow": float(sub_cfg.get("shadow", round(1.3 * w / 1080, 2))),
            "margin_l": int(sub_cfg.get("margin_l", round(52 * w / 1080))),
            "margin_r": int(sub_cfg.get("margin_r", round(52 * w / 1080))),
            "margin_v": int(sub_cfg.get("margin_v", max(26, round(footer_h * 0.31)))),
            "max_chars": int(sub_cfg.get("max_chars", 16)),
        })
    elif preset == "portrait_stage":
        overrides.update({
            "font_size": int(sub_cfg.get("font_size", round(54 * w / 1080))),
            "primary_color": str(sub_cfg.get("primary_color") or "#FFE294"),
            "outline_color": str(sub_cfg.get("outline_color") or "#0B0E14"),
            "outline": float(sub_cfg.get("outline", round(3.2 * w / 1080, 2))),
            "shadow": float(sub_cfg.get("shadow", round(1.4 * w / 1080, 2))),
            "margin_l": int(sub_cfg.get("margin_l", round(52 * w / 1080))),
            "margin_r": int(sub_cfg.get("margin_r", round(52 * w / 1080))),
            "margin_v": int(sub_cfg.get("margin_v", round(396 * h / 1920))),
            "max_chars": int(sub_cfg.get("max_chars", 16)),
        })
    else:  # landscape_cinema
        card_mode = str(plan.get("subtitle_card_mode") or "cinema_glow")
        if card_mode == "editorial_paper":
            card_h = int(plan.get("card_height", round(100 * h / 1080)))
            card_bot = int(plan.get("card_bottom_margin", round(46 * h / 1080)))
            font_sz = int(sub_cfg.get("font_size", round(46 * h / 1080)))
            overrides.update({
                "font_size": font_sz,
                "primary_color": str(sub_cfg.get("primary_color") or "#1E1916"),
                "highlight_color": str(sub_cfg.get("highlight_color") or "#B8431E"),
                "outline_color": str(sub_cfg.get("outline_color") or "#F2EDE7"),
                "outline": float(sub_cfg.get("outline", 0.0)),
                "shadow": float(sub_cfg.get("shadow", 0.0)),
                "glow": 0.0,
                "dialogue_outline_color": "#0E0E10",
                "dialogue_outline": round(3.2 * h / 1080, 2),
                "dialogue_shadow": round(1.5 * h / 1080, 2),
                "margin_l": int(sub_cfg.get("margin_l", round(220 * w / 1920))),
                "margin_r": int(sub_cfg.get("margin_r", round(220 * w / 1920))),
                "margin_v": int(sub_cfg.get("margin_v", card_bot + max(10, round((card_h - font_sz * 1.18) / 2)))),
                "max_chars": int(sub_cfg.get("max_chars", 22)),
            })
        else:
            overrides.update({
                "font_size": int(sub_cfg.get("font_size", round(48 * h / 1080))),
                "primary_color": str(sub_cfg.get("primary_color") or "#FFE294"),
                "outline_color": str(sub_cfg.get("outline_color") or "#0E0E12"),
                "outline": float(sub_cfg.get("outline", round(3.0 * h / 1080, 2))),
                "shadow": float(sub_cfg.get("shadow", round(1.4 * h / 1080, 2))),
                "margin_l": int(sub_cfg.get("margin_l", round(92 * w / 1920))),
                "margin_r": int(sub_cfg.get("margin_r", round(92 * w / 1920))),
                "margin_v": int(sub_cfg.get("margin_v", round(54 * h / 1080))),
                "max_chars": int(sub_cfg.get("max_chars", 20)),
            })
    return overrides


def _viewport_for_plan(plan: dict, canvas: dict) -> tuple[dict, dict]:
    """Return (viewport_rect, extra_meta) for the preset."""
    preset = plan["preset"]
    w, h = int(canvas["width"]), int(canvas["height"])
    if preset == "portrait_cinema":
        hh = int(plan.get("header_height", round(184 * h / 1920)))
        fh = int(plan.get("footer_height", round(188 * h / 1920)))
        vh = _even(h - hh - fh)
        return {"x": 0, "y": hh, "width": w, "height": vh}, {"header_h": hh, "footer_h": h - hh - vh}
    if preset == "portrait_stage":
        aspect = str(plan.get("viewport_aspect") or "4:3")
        ratio_map = {"16:9": 9 / 16, "16:10": 10 / 16, "4:3": 3 / 4, "1:1": 1.0}
        vh = _even(plan.get("viewport_height") or (w * ratio_map.get(aspect, 3 / 4)))
        default_vy = 584 if vh <= round(w * 0.68) else 436
        vy = int(plan.get("viewport_y", round(default_vy * h / 1920)))
        return {"x": 0, "y": vy, "width": w, "height": vh}, {"aspect": aspect}
    return {"x": 0, "y": 0, "width": w, "height": h}, {}


def _place_timed_plate(
    preset: str,
    canvas: dict,
    viewport: dict,
    size: tuple[int, int],
    slot: str,
    *,
    is_card: bool = False,
) -> dict:
    """Compute {x, y, width, height} for a timed callout or card in a named safe slot."""
    w, h = int(canvas["width"]), int(canvas["height"])
    pw, ph = size
    sx = round(38 * (w / (1080.0 if preset != "landscape_cinema" else 1920.0)))
    vx, vy, vw, vh = viewport["x"], viewport["y"], viewport["width"], viewport["height"]

    if preset == "portrait_stage":
        if is_card:
            if slot == "upper_right":
                x = max(sx, vx + vw - pw - sx)
                y = max(round(96 * h / 1920), vy - ph - round(14 * h / 1920))
            elif slot == "stage_lower":
                x = (w - pw) // 2
                y = vy + vh + round(20 * h / 1920)
            elif slot in {"right", "lower_right"}:
                x = max(sx, vx + vw - pw - sx)
                y = vy + vh - ph - round(16 * h / 1920)
            else:
                x = sx
                y = vy + vh - ph - round(16 * h / 1920)
        else:
            if slot == "stage_upper":
                x = (w - pw) // 2
                y = max(round(100 * h / 1920), vy - ph - round(16 * h / 1920))
            elif slot == "upper_right":
                x = max(sx, w - pw - round(84 * w / 1080))
                y = vy + round(18 * h / 1920)
            elif slot == "upper_left":
                x = sx
                y = vy + round(18 * h / 1920)
            else:  # stage_lower
                x = (w - pw) // 2
                y = vy + vh + round(20 * h / 1920)
    elif preset == "portrait_cinema":
        if is_card:
            x = sx if slot not in {"right", "upper_right"} else max(sx, w - pw - round(88 * w / 1080))
            y = vy + vh - ph - round(26 * h / 1920)
        else:
            x = sx if slot == "upper_left" else max(sx, w - pw - round(76 * w / 1080))
            y = vy + round(28 * h / 1920)
    else:  # landscape_cinema
        if is_card:
            x = sx if slot not in {"right", "upper_right"} else max(sx, w - pw - sx)
            y = max(round(44 * h / 1080), h - ph - round(252 * h / 1080))
        else:
            x = max(sx, w - pw - sx) if slot != "upper_left" else sx
            y = round(48 * h / 1080)

    x = max(0, min(w - pw, int(x)))
    y = max(0, min(h - ph, int(y)))
    return {"x": x, "y": y, "width": pw, "height": ph}


def prepare_studio_packaging(
    work_dir: str | Path,
    input_canvas: dict,
    video_duration: float,
    tts_segments: list[dict],
) -> dict | None:
    """Validate packaging_plan.json, render @2x PNG plates, and return compiled studio spec."""
    plan = load_raw_packaging_plan(work_dir)
    if plan is None:
        return None
    root = Path(work_dir)
    assets_dir = root / "packaging_assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    canvas = resolve_output_canvas(root, input_canvas)
    w, h = canvas["width"], canvas["height"]
    preset = plan["preset"]
    scale = (w / 1080.0) if preset != "landscape_cinema" else (h / 1080.0)
    viewport, meta = _viewport_for_plan(plan, canvas)
    sub_overrides = subtitle_overrides_for_plan(root, canvas)

    static_plates: list[dict] = []
    timed_plates: list[dict] = []
    collision_items: list[dict] = []
    blocking_codes: list[str] = []

    if preset == "portrait_cinema":
        frame_meta = render_portrait_cinema_frame_plate(
            assets_dir / "frame_portrait_cinema.png",
            canvas,
            header_h=meta["header_h"],
            footer_h=meta["footer_h"],
            header=plan.get("header") or {},
            theme_color=str(plan.get("theme_color") or "#16110E"),
        )
        static_plates.append({"name": "portrait_cinema_frame", **frame_meta, "rect": {"x": 0, "y": 0, "width": w, "height": h}})
    elif preset == "portrait_stage":
        backdrop_mode = str(plan.get("backdrop_mode") or "blurred_frame")
        stage_meta = render_portrait_stage_overlay_plate(
            assets_dir / "stage_portrait_overlay.png",
            canvas,
            viewport=viewport,
            header=plan.get("header") or {},
            backdrop_mode=backdrop_mode,
        )
        static_plates.append({"name": "portrait_stage_overlay", **stage_meta, "rect": {"x": 0, "y": 0, "width": w, "height": h}})
    else:
        card_mode = str(plan.get("subtitle_card_mode") or "cinema_glow")
        scrim_meta = render_landscape_scrim_plate(assets_dir / "landscape_scrim.png", canvas)
        static_plates.append({"name": "landscape_scrim", **scrim_meta, "rect": {"x": 0, "y": 0, "width": w, "height": h}})
        if card_mode == "editorial_paper":
            card_w = int(plan.get("card_width", round(w * 0.79)))
            card_h = int(plan.get("card_height", round(100 * h / 1080)))
            card_bot = int(plan.get("card_bottom_margin", round(46 * h / 1080)))
            strip_meta = render_editorial_paper_strip_plate(
                assets_dir / "editorial_paper_strip.png", width=card_w, height=card_h
            )
            pad = strip_meta["pad"]
            rect = {
                "x": max(0, (w - card_w) // 2 - pad),
                "y": max(0, h - card_bot - card_h - pad),
                "width": strip_meta["width"],
                "height": strip_meta["height"],
            }
            narr_windows = [
                (max(0.0, s - 0.14), min(float(video_duration), e + 0.16), 0.0)
                for s, e in map(_seg_place_window, tts_segments)
                if e > s
            ]
            for idx, (ws, we, _) in enumerate(coalesce_duck_windows(narr_windows, bridge=0.65)):
                if we - ws >= 0.35:
                    timed_plates.append({
                        "name": f"editorial_strip_{idx}",
                        "path": strip_meta["path"],
                        "rect": rect,
                        "image_size": {"width": strip_meta["width"], "height": strip_meta["height"]},
                        "start": round(ws, 3),
                        "end": round(we, 3),
                        "fade": 0.18,
                    })

    # Optional calligraphic title_mark
    title_mark = plan.get("title_mark")
    if isinstance(title_mark, dict) and str(title_mark.get("title") or "").strip():
        tm_meta = render_title_mark_plate(assets_dir / "title_mark.png", title_mark, scale=scale)
        tm_x = int(title_mark.get("x", round(36 * scale)))
        tm_y = int(title_mark.get("y", viewport["y"] + round(24 * scale)))
        tm_rect = {
            "x": max(0, min(w - tm_meta["width"], tm_x)),
            "y": max(0, min(h - tm_meta["height"], tm_y)),
            "width": tm_meta["width"],
            "height": tm_meta["height"],
        }
        s0 = max(0.0, float(title_mark.get("start", 0.15)))
        e0 = min(float(video_duration), float(title_mark.get("end", min(6.0, video_duration * 0.35))))
        if e0 - s0 >= 0.6:
            entry = {
                "name": "title_mark_open",
                "path": tm_meta["path"],
                "rect": tm_rect,
                "image_size": {"width": tm_meta["width"], "height": tm_meta["height"]},
                "start": round(s0, 3),
                "end": round(e0, 3),
                "fade": 0.32,
                "motion": "slide_left",
            }
            timed_plates.append(entry)
            collision_items.append(entry)
        reprise = float(title_mark.get("reprise_tail_seconds", 0.0))
        if reprise >= 1.2 and video_duration - reprise > e0 + 1.5:
            rs = round(video_duration - reprise, 3)
            re_end = round(max(rs + 0.8, video_duration - 0.15), 3)
            entry_r = {
                "name": "title_mark_reprise",
                "path": tm_meta["path"],
                "rect": tm_rect,
                "image_size": {"width": tm_meta["width"], "height": tm_meta["height"]},
                "start": rs,
                "end": re_end,
                "fade": 0.32,
                "motion": "slide_left",
            }
            timed_plates.append(entry_r)
            collision_items.append(entry_r)

    # Timed cards (opening_card + cards)
    raw_cards = []
    if isinstance(plan.get("opening_card"), dict):
        raw_cards.append({"kind": "opening_card", **plan["opening_card"]})
    for c in plan.get("cards") or ():
        if isinstance(c, dict):
            raw_cards.append(c)

    for idx, card in enumerate(raw_cards):
        cs = max(0.0, float(card.get("start", 0.15)))
        ce = min(float(video_duration), float(card.get("end", cs + 3.2)))
        if ce - cs < 0.5:
            continue
        c_meta = render_card_plate(assets_dir / f"card_{idx:02d}.png", card, scale=scale)
        side = str(card.get("side") or ("right" if card.get("kind") == "data_card" else "left"))
        rect = _place_timed_plate(
            preset, canvas, viewport, (c_meta["width"], c_meta["height"]), side, is_card=True
        )
        card_motion = (
            "rise_up" if side == "stage_lower"
            else ("slide_right" if side in {"right", "lower_right", "upper_right"} else "slide_left")
        )
        entry = {
            "name": f"card_{idx:02d}_{card.get('kind', 'card')}",
            "path": c_meta["path"],
            "rect": rect,
            "image_size": {"width": c_meta["width"], "height": c_meta["height"]},
            "start": round(cs, 3),
            "end": round(ce, 3),
            "fade": float(card.get("fade", 0.25)),
            "motion": card_motion,
        }
        timed_plates.append(entry)
        collision_items.append(entry)

    # Timed callouts (花字)
    auto_slots = (
        ("stage_lower", "upper_right", "upper_left")
        if preset == "portrait_stage"
        else (("upper_right", "upper_left") if preset == "portrait_cinema" else ("upper_right", "upper_left"))
    )
    for idx, callout in enumerate(plan.get("callouts") or ()):
        if not isinstance(callout, dict):
            continue
        clean_txt = str(callout.get("text") or "").strip().rstrip("，。！？、；：,.!?;:")
        if not clean_txt:
            continue
        if len(clean_txt) > 16:
            blocking_codes.append("packaging_callout_too_long")
        cs = max(0.0, float(callout.get("start", 0.0)))
        ce = min(float(video_duration), float(callout.get("end", cs + 3.2)))
        if ce - cs < 0.5:
            continue
        co_meta = render_callout_plate(assets_dir / f"callout_{idx:02d}.png", callout, scale=scale)
        requested_slot = str(callout.get("slot") or "auto")
        candidates = (
            [auto_slots[(idx + shift) % len(auto_slots)] for shift in range(len(auto_slots))]
            if requested_slot == "auto"
            else [requested_slot]
        )
        chosen_rect = None
        chosen_slot = candidates[0]
        for cand_slot in candidates:
            cand_rect = _place_timed_plate(
                preset, canvas, viewport, (co_meta["width"], co_meta["height"]), cand_slot, is_card=False
            )
            if not any(
                _times_overlap(cs, ce, prev["start"], prev["end"]) and _rects_intersect(cand_rect, prev["rect"])
                for prev in collision_items
            ):
                chosen_rect = cand_rect
                chosen_slot = cand_slot
                break
        if chosen_rect is None:
            chosen_rect = _place_timed_plate(
                preset, canvas, viewport, (co_meta["width"], co_meta["height"]), candidates[0], is_card=False
            )
        co_motion = (
            "slide_left" if chosen_slot == "upper_left"
            else ("slide_right" if chosen_slot == "upper_right" else "rise_up")
        )
        entry = {
            "name": f"callout_{idx:02d}",
            "path": co_meta["path"],
            "rect": chosen_rect,
            "image_size": {"width": co_meta["width"], "height": co_meta["height"]},
            "start": round(cs, 3),
            "end": round(ce, 3),
            "fade": float(callout.get("fade", 0.24)),
            "motion": co_motion,
        }
        timed_plates.append(entry)
        collision_items.append(entry)

    # Subtitle band vs timed card/callout collision & mutual collision checks
    sub_margin_v = int(sub_overrides["margin_v"])
    sub_font_sz = int(sub_overrides["font_size"])
    sub_rect = {
        "x": int(sub_overrides["margin_l"]),
        "y": max(0, h - sub_margin_v - int(sub_font_sz * 2.4)),
        "width": max(1, w - int(sub_overrides["margin_l"]) - int(sub_overrides["margin_r"])),
        "height": int(sub_font_sz * 2.4),
    }
    has_subs = bool(tts_segments) or (root / "original_subtitles.json").is_file()
    collisions = []
    for i, a in enumerate(collision_items):
        if has_subs and _rects_intersect(a["rect"], sub_rect, pad=2):
            collisions.append({"a": a["name"], "b": "subtitle_safe_band"})
        for b in collision_items[i + 1:]:
            if _times_overlap(a["start"], a["end"], b["start"], b["end"]) and _rects_intersect(a["rect"], b["rect"]):
                collisions.append({"a": a["name"], "b": b["name"]})
    if collisions:
        blocking_codes.append("packaging_overlay_collision")

    progress_plate = None
    if bool(plan.get("progress_bar", preset == "portrait_stage")):
        pb_h = max(3, round(4 * scale))
        progress_plate = render_progress_bar_plate(assets_dir / "progress_bar.png", w, pb_h)
        progress_plate["y"] = min(h - pb_h, viewport["y"] + viewport["height"] - pb_h)

    default_backdrop = "blurred_frame" if preset == "portrait_stage" else "cover"
    return {
        "plan_path": str((root / PACKAGING_PLAN).resolve()),
        "preset": preset,
        "canvas": canvas,
        "viewport": viewport,
        "backdrop_mode": str(plan.get("backdrop_mode") or default_backdrop),
        "color_grade": str(plan.get("color_grade") or "").strip(),
        "source_crop_top_ratio": max(0.0, min(0.30, float(plan.get("source_crop_top_ratio", 0.0)))),
        "source_crop_bottom_ratio": max(0.0, min(0.35, float(plan.get("source_crop_bottom_ratio", 0.0)))),
        "focus_x": max(0.0, min(1.0, float(plan.get("focus_x", 0.5)))),
        "focus_y": max(0.0, min(1.0, float(plan.get("focus_y", 0.42)))),
        "static_plates": static_plates,
        "timed_plates": timed_plates,
        "progress_plate": progress_plate,
        "subtitle_overrides": sub_overrides,
        "video_duration": float(video_duration),
        "qc": {
            "preset": preset,
            "viewport": viewport,
            "subtitle_band": sub_rect,
            "static_plate_count": len(static_plates),
            "timed_plate_count": len(timed_plates),
            "collisions": collisions,
            "blocking_codes": sorted(set(blocking_codes)),
        },
    }


_COLOR_GRADE_FILTERS = {
    "warm_drama": "eq=contrast=1.04:saturation=1.07:brightness=0.01",
    "crisp_cinema": "eq=contrast=1.05:saturation=1.06",
    "cool_thriller": "eq=contrast=1.06:saturation=0.95:brightness=-0.01",
    "arena_punch": "eq=contrast=1.07:saturation=1.12",
}


def compose_studio_video_filter(
    studio: dict,
    tail_filters: list[str],
    *,
    extra_layers: list[dict] = (),
    mask_filter: str | None = None,
) -> str:
    """Build a single-pass FFmpeg -vf graph for studio packaging + subtitles + output normalization."""
    w, h = studio["canvas"]["width"], studio["canvas"]["height"]
    fps = max(1.0, float(studio["canvas"].get("fps") or 25.0))
    vp = studio["viewport"]
    vw, vh, vx, vy = vp["width"], vp["height"], vp["x"], vp["y"]
    fx, fy = studio["focus_x"], studio["focus_y"]
    dur = max(0.1, float(studio["video_duration"]))
    scale_ref = (w / 1080.0) if studio["preset"] != "landscape_cinema" else (h / 1080.0)

    pre_ops = []
    if mask_filter:
        pre_ops.append(mask_filter)
    ct, cb = studio["source_crop_top_ratio"], studio["source_crop_bottom_ratio"]
    if ct > 0.0 or cb > 0.0:
        keep = max(0.2, 1.0 - ct - cb)
        pre_ops.append(f"crop=iw:trunc(ih*{keep:.4f}/2)*2:0:trunc(ih*{ct:.4f}/2)*2")

    graph: list[str] = []
    src_label = "in"
    if pre_ops:
        graph.append(f"[in]{','.join(pre_ops)}[src0]")
        src_label = "src0"

    preset = studio["preset"]
    fg_polish = []
    if w >= 720:
        fg_polish.append("unsharp=3:3:0.32:3:3:0.0")
    grade_filt = _COLOR_GRADE_FILTERS.get(studio.get("color_grade") or "")
    if grade_filt:
        fg_polish.append(grade_filt)
    fg_suffix = ("," + ",".join(fg_polish)) if fg_polish else ""

    fit_crop = (
        f"scale={vw}:{vh}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={vw}:{vh}:'(iw-{vw})*{fx:.3f}':'(ih-{vh})*{fy:.3f}'{fg_suffix},setsar=1"
    )
    bw, bh = _even(max(64, w // 4)), _even(max(64, h // 4))
    blur_bg = (
        f"scale={bw}:{bh}:force_original_aspect_ratio=increase:flags=bilinear,"
        f"crop={bw}:{bh},boxblur=10:2,eq=brightness=-0.18:saturation=1.10:contrast=1.04,"
        f"scale={w}:{h}:flags=bicubic,setsar=1"
    )

    if preset == "portrait_cinema":
        if studio.get("backdrop_mode") == "blurred_frame":
            fit_contain = (
                f"scale={vw}:{vh}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"scale=trunc(iw/2)*2:trunc(ih/2)*2{fg_suffix},setsar=1"
            )
            graph.append(f"[{src_label}]split=2[bg_in][fg_in]")
            graph.append(f"[bg_in]{blur_bg}[bg]")
            graph.append(f"[fg_in]{fit_contain}[fg]")
            graph.append(f"[bg][fg]overlay=x='{vx}+({vw}-w)/2':y='{vy}+({vh}-h)/2'[st0]")
        else:
            graph.append(f"[{src_label}]{fit_crop},pad={w}:{h}:{vx}:{vy}:color=black[st0]")
    elif preset == "portrait_stage":
        if studio["backdrop_mode"] == "blurred_frame":
            graph.append(f"[{src_label}]split=2[bg_in][fg_in]")
            graph.append(f"[bg_in]{blur_bg}[bg]")
            graph.append(f"[fg_in]{fit_crop}[fg]")
            graph.append(f"[bg][fg]overlay=x={vx}:y={vy}[st0]")
        else:
            graph.append(f"[{src_label}]{fit_crop},pad={w}:{h}:{vx}:{vy}:color=#0D1017[st0]")
    else:  # landscape_cinema
        graph.append(f"[{src_label}]{fit_crop}[st0]")

    cur = "st0"
    idx = 0
    for sp in studio["static_plates"]:
        lbl = f"sp{idx}"
        out = f"v{idx}"
        idx += 1
        graph.append(
            f"movie=filename='{_escape_subtitle_filter_path(sp['path'])}',format=rgba[{lbl}]"
        )
        graph.append(f"[{cur}][{lbl}]overlay=x={sp['rect']['x']}:y={sp['rect']['y']}[{out}]")
        cur = out

    if studio["progress_plate"] is not None:
        pb = studio["progress_plate"]
        lbl = f"sp{idx}"
        out = f"v{idx}"
        idx += 1
        graph.append(
            f"movie=filename='{_escape_subtitle_filter_path(pb['path'])}',"
            f"loop=loop=-1:size=1:start=0,setpts=N/({fps:.4f}*TB),trim=duration={dur + 0.1:.3f},format=rgba[{lbl}]"
        )
        graph.append(
            f"[{cur}][{lbl}]overlay=x='-w+w*min(1,max(0,t/{dur:.3f}))':y={pb['y']}:eof_action=pass[{out}]"
        )
        cur = out

    for layer in extra_layers:
        lbl = f"sp{idx}"
        out = f"v{idx}"
        idx += 1
        r = layer["rect"]
        graph.append(
            f"movie=filename='{_escape_subtitle_filter_path(layer['path'])}',"
            f"scale={r['width']}:{r['height']},format=rgba[{lbl}]"
        )
        graph.append(f"[{cur}][{lbl}]overlay=x={r['x']}:y={r['y']}[{out}]")
        cur = out

    slide_px = max(8, round(22 * scale_ref))
    rise_px = max(6, round(14 * scale_ref))
    for tp in studio["timed_plates"]:
        lbl = f"tp{idx}"
        out = f"v{idx}"
        idx += 1
        s, e = tp["start"], tp["end"]
        fade = min(float(tp.get("fade", 0.24)), max(0.05, (e - s) * 0.4))
        r = tp["rect"]
        motion = tp.get("motion") or "none"
        ease = f"pow(1-min(1,max(0,(t-{s:.3f})/0.18)),2)"
        if motion == "slide_left":
            ox, oy = f"'{r['x']}-{slide_px}*{ease}'", str(r["y"])
        elif motion == "slide_right":
            ox, oy = f"'{r['x']}+{slide_px}*{ease}'", str(r["y"])
        elif motion == "rise_up":
            ox, oy = str(r["x"]), f"'{r['y']}+{rise_px}*{ease}'"
        else:
            ox, oy = str(r["x"]), str(r["y"])
        graph.append(
            f"movie=filename='{_escape_subtitle_filter_path(tp['path'])}',"
            f"loop=loop=-1:size=1:start=0,setpts=N/({fps:.4f}*TB),trim=duration={e + 0.08:.3f},format=rgba,"
            f"fade=t=in:st={s:.3f}:d={fade:.3f}:alpha=1,"
            f"fade=t=out:st={max(s, e - fade):.3f}:d={fade:.3f}:alpha=1[{lbl}]"
        )
        graph.append(
            f"[{cur}][{lbl}]overlay=x={ox}:y={oy}:eof_action=pass:enable='between(t,{s:.3f},{e:.3f})'[{out}]"
        )
        cur = out

    if tail_filters:
        graph.append(f"[{cur}]{','.join(tail_filters)}[out]")
    else:
        graph.append(f"[{cur}]null[out]")
    return ";".join(graph)


def studio_timeline_segments(studio: dict | None, duration_s: float) -> list[dict]:
    """Convert studio plates into normalized timeline.json image segments."""
    if studio is None:
        return []
    w, h = studio["canvas"]["width"], studio["canvas"]["height"]
    segments: list[dict] = []

    def _to_seg(path: str, rect: dict, size: dict, start: float, end: float) -> dict:
        fit = min(w / size["width"], h / size["height"])
        return {
            "source_path": path,
            "timeline_start": round(float(start), 4),
            "timeline_end": round(float(end), 4),
            "scale": {
                "x": round(rect["width"] / (size["width"] * fit), 6),
                "y": round(rect["height"] / (size["height"] * fit), 6),
            },
            "position": {
                "x": round((rect["x"] + rect["width"] / 2 - w / 2) / (w / 2), 6),
                "y": round((h / 2 - rect["y"] - rect["height"] / 2) / (h / 2), 6),
            },
        }

    for sp in studio["static_plates"]:
        segments.append(
            _to_seg(sp["path"], sp["rect"], {"width": sp["width"], "height": sp["height"]}, 0.0, duration_s)
        )
    for tp in studio["timed_plates"]:
        segments.append(_to_seg(tp["path"], tp["rect"], tp["image_size"], tp["start"], tp["end"]))
    return segments


def studio_packaging_settings(work_dir: str | Path | None) -> dict | None:
    """Audit dictionary for assembly_manifest.json when packaging_plan.json is present."""
    if not has_packaging_plan(work_dir):
        return None
    path = Path(work_dir) / PACKAGING_PLAN
    plan = load_raw_packaging_plan(work_dir) or {}
    return {
        "artifact": PACKAGING_PLAN,
        "present": True,
        "identity": file_identity(path),
        "preset": plan.get("preset"),
        "canvas": plan.get("canvas"),
        "callouts": len(plan.get("callouts") or ()),
        "cards": len(plan.get("cards") or ()) + (1 if isinstance(plan.get("opening_card"), dict) else 0),
    }
