"""Shared stdlib + ffprobe/ffmpeg utilities for video-recap atomic primitives."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence

__all__ = [
    "color_tag_args",
    "color_tag_filter",
    "filter_script_args",
    "frame_count",
    "has_tool",
    "load_json",
    "output_color_tags",
    "parse_fps",
    "probe_media",
    "resolve_source",
    "run_cmd",
    "sanitize_api_error",
    "sanitize_error",
    "save_json",
    "timeline_video_spans",
]

_STANDARD_FPS = (
    (23.976, Fraction(24000, 1001)), (24.0, Fraction(24, 1)), (25.0, Fraction(25, 1)),
    (29.97, Fraction(30000, 1001)), (30.0, Fraction(30, 1)), (50.0, Fraction(50, 1)),
    (59.94, Fraction(60000, 1001)), (60.0, Fraction(60, 1)),
)
_DATA_URL_RE = re.compile(r"data:[a-zA-Z0-9.+/-]+;base64,[A-Za-z0-9+/=_-]+", re.IGNORECASE)
_KEY_RE = re.compile(r"\b(?:tp|sk)-[A-Za-z0-9_-]{12,}\b")
_COLOR_FIELDS = (("color_space", "colorspace"), ("color_primaries", "color_primaries"), ("color_transfer", "color_trc"))
_UNTAGGED_COLOR = {None, "", "unknown", "unspecified", "reserved", "N/A"}
_RGB_PREFIXES = ("rgb", "bgr", "gbr", "argb", "abgr", "0rgb", "0bgr", "x2rgb", "x2bgr", "pal8", "bayer_")
_KNOWN_COLOR = {
    "colorspace": {"bt709", "fcc", "bt470bg", "smpte170m", "smpte240m", "ycgco", "bt2020nc", "bt2020c", "smpte2085", "chroma-derived-nc", "chroma-derived-c", "ictcp"},
    "color_primaries": {"bt709", "bt470m", "bt470bg", "smpte170m", "smpte240m", "film", "bt2020", "smpte428", "smpte431", "smpte432"},
    "color_trc": {"bt709", "bt470m", "bt470bg", "smpte170m", "smpte240m", "linear", "log100", "log316", "iec61966-2-4", "bt1361e", "iec61966-2-1", "bt2020-10", "bt2020-12", "smpte2084", "smpte428", "arib-std-b67"},
}
_TRC_ALIAS = {"bt470m": "gamma22", "bt470bg": "gamma28"}


def has_tool(name: str) -> str | None:
    return shutil.which(name)


def run_cmd(cmd: Sequence[str], *, timeout: float | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(p) for p in cmd], stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:500]
        raise RuntimeError(f"Command failed ({result.returncode}): {cmd[0]}: {detail}")
    return result


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path: str | Path, payload: Any) -> Path:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=dest.parent,
            prefix=f".{dest.stem}.", suffix=".tmp", delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, dest)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
    return dest


def sanitize_api_error(text: Any, extra_secrets: Sequence[str] = ()) -> str:
    cleaned = _DATA_URL_RE.sub("<redacted-data-url>", str(text or ""))
    cleaned = _KEY_RE.sub("<redacted-key>", cleaned)
    for secret in extra_secrets:
        if secret:
            cleaned = cleaned.replace(str(secret), "<redacted-key>")
    return cleaned.strip()[:400]


sanitize_error = sanitize_api_error


def parse_fps(value: Any, default: Fraction = Fraction(30, 1)) -> Fraction:
    if isinstance(value, Fraction):
        return value if value > 0 else default
    if isinstance(value, int) and not isinstance(value, bool):
        return Fraction(value, 1) if 0 < value <= 240 else default
    if isinstance(value, str):
        text = value.strip()
        if "/" in text:
            num_s, den_s = text.split("/", 1)
            try:
                num, den = int(num_s), int(den_s)
                if num > 0 and den > 0:
                    return Fraction(num, den)
            except ValueError:
                pass
        if text.isdigit():
            iv = int(text)
            return Fraction(iv, 1) if 0 < iv <= 240 else default
        try:
            value = float(text)
        except ValueError:
            return default
    try:
        flt = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(flt) or not (0 < flt <= 240):
        return default
    if abs(flt - round(flt)) < 1e-6:
        return Fraction(int(round(flt)), 1)
    nearest_nom, nearest_frac = min(_STANDARD_FPS, key=lambda item: abs(flt - item[0]))
    if abs(flt - nearest_nom) <= 0.05:
        return nearest_frac
    return Fraction(flt).limit_denominator(1001)


def frame_count(duration_s: float, fps: Fraction) -> int:
    dur = float(duration_s)
    return 0 if dur <= 0 else max(1, int(round(dur * float(fps))))


def filter_script_args(flag: str, expr: str, work_dir: Path, threshold: int = 4096) -> tuple[list[str], Path | None]:
    if len(expr.encode("utf-8")) <= threshold:
        return [f"-{flag}", expr], None
    script_flag = {"filter_complex": "-filter_complex_script", "vf": "-filter_script:v", "af": "-filter_script:a"}.get(flag, f"-/{flag}")
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / f".filter_{flag}_{os.getpid()}.txt"
    path.write_text(expr, encoding="utf-8")
    return [script_flag, str(path)], path


def resolve_source(timeline: dict[str, Any], ref: str, base_dir: str | Path | None = None) -> Path:
    raw = (timeline.get("sources") or {}).get(ref, ref)
    candidate = Path(str(raw))
    return candidate if (candidate.is_absolute() or base_dir is None) else (Path(base_dir) / candidate).resolve()


def timeline_video_spans(timeline: dict[str, Any]) -> tuple[list[dict[str, Any]], float]:
    spans: list[dict[str, Any]] = []
    cursor = 0.0
    for idx, clip in enumerate(timeline.get("video") or ()):
        src_in, src_out = float(clip["in"]), float(clip["out"])
        dur = src_out - src_in
        if dur <= 0:
            raise ValueError(f"Video clip {clip.get('id', idx)} has non-positive duration")
        spans.append({
            "index": idx, "id": clip.get("id", f"v{idx + 1}"), "source": clip["source"],
            "in": src_in, "out": src_out, "duration": dur,
            "timeline_start": round(cursor, 6), "timeline_end": round(cursor + dur, 6), "clip": clip,
        })
        cursor += dur
    return spans, round(cursor, 6)


def _parse_ratio(value: Any) -> float | None:
    if value in (None, "", "0:1", "0/1", "N/A"):
        return None
    left, sep, right = str(value).replace("/", ":").partition(":")
    try:
        num, den = float(left), (float(right) if sep else 1.0)
    except ValueError:
        return None
    return num / den if num > 0 and den > 0 else None


def _stream_rotation(stream: dict[str, Any]) -> int:
    for raw in (stream.get("tags", {}).get("rotate"), stream.get("rotation")):
        if raw not in (None, ""):
            return int(round(float(raw))) % 360
    for item in stream.get("side_data_list") or ():
        if item.get("rotation") not in (None, ""):
            return int(round(float(item["rotation"]))) % 360
    return 0


def output_color_tags(stream: dict[str, Any]) -> dict[str, Any]:
    declared = {opt: stream.get(key) for key, opt in _COLOR_FIELDS}
    from_rgb = declared["colorspace"] == "gbr" or str(stream.get("pix_fmt") or "").startswith(_RGB_PREFIXES)
    if from_rgb:
        declared["colorspace"] = "bt709"
    if all(v in _UNTAGGED_COLOR or v == "bt709" for v in declared.values()):
        tags: dict[str, Any] = {opt: "bt709" for _, opt in _COLOR_FIELDS}
    else:
        tags = {opt: v for opt, v in declared.items() if v in _KNOWN_COLOR[opt]}
    tags["color_range"] = "tv" if from_rgb else ("pc" if stream.get("color_range") == "pc" else "tv")
    if from_rgb:
        tags["from_rgb"] = True
    return tags


def color_tag_filter(tags: dict[str, Any]) -> str:
    parts = [f"{opt}={tags[opt]}" for _, opt in _COLOR_FIELDS if opt in tags]
    parts.append(f"range={tags.get('color_range', 'tv')}")
    setparams = "setparams=" + ":".join(parts)
    if tags.get("from_rgb"):
        return f"scale=out_color_matrix=bt709:out_range={tags.get('color_range', 'tv')},format=yuv420p,{setparams}"
    return setparams


def color_tag_args(tags: dict[str, Any]) -> list[str]:
    args: list[str] = []
    for _, opt in _COLOR_FIELDS:
        if opt in tags:
            val = _TRC_ALIAS.get(tags[opt], tags[opt]) if opt == "color_trc" else tags[opt]
            args.extend([f"-{opt}", str(val)])
    return [*args, "-color_range", str(tags.get("color_range", "tv"))]


def _pos_float(val: Any) -> float | None:
    try:
        flt = float(val)
        return flt if math.isfinite(flt) and flt > 0 else None
    except (TypeError, ValueError):
        return None


def probe_media(path: str | Path) -> dict[str, Any]:
    media_path = Path(path)
    if not media_path.is_file():
        raise FileNotFoundError(f"Media file not found: {media_path}")
    if not has_tool("ffprobe"):
        raise RuntimeError("ffprobe not found on PATH")
    res = run_cmd([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(media_path),
    ], check=True)
    raw = json.loads(res.stdout)
    fmt, streams = raw.get("format") or {}, raw.get("streams") or []
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    sub_streams = [
        {"index": s.get("index"), "codec_name": s.get("codec_name"), "language": (s.get("tags") or {}).get("language")}
        for s in streams if s.get("codec_type") == "subtitle"
    ]
    duration_s = next(
        (v for cand in (fmt.get("duration"), (v_stream or {}).get("duration"), (a_stream or {}).get("duration")) if (v := _pos_float(cand)) is not None),
        0.0,
    )

    info: dict[str, Any] = {
        "path": str(media_path), "format_name": fmt.get("format_name"),
        "size_bytes": media_path.stat().st_size, "duration_s": round(duration_s, 4),
        "has_video": v_stream is not None, "has_audio": a_stream is not None,
        "subtitle_streams": sub_streams,
    }
    if v_stream is not None:
        coded_w, coded_h = int(v_stream.get("width") or 0), int(v_stream.get("height") or 0)
        sar_str, dar_str = v_stream.get("sample_aspect_ratio") or "1:1", v_stream.get("display_aspect_ratio") or ""
        sar, dar, rot = _parse_ratio(sar_str) or 1.0, _parse_ratio(dar_str), _stream_rotation(v_stream)
        disp_w = coded_w * sar if _parse_ratio(sar_str) else (coded_h * dar if dar else float(coded_w))
        disp_h = float(coded_h)
        if rot in {90, 270}:
            disp_w, disp_h = disp_h, disp_w
        w, h = max(1, int(round(disp_w))), max(1, int(round(disp_h)))
        avg_r = v_stream.get("avg_frame_rate")
        fps_frac = parse_fps(avg_r if avg_r not in (None, "", "0/0", "0/1") else v_stream.get("r_frame_rate"))
        info.update({
            "width": w, "height": h, "coded_width": coded_w, "coded_height": coded_h,
            "rotation": rot, "sar": sar_str, "dar": dar_str or f"{w}:{h}",
            "fps": round(float(fps_frac), 3), "fps_rational": f"{fps_frac.numerator}/{fps_frac.denominator}",
            "codec_name": v_stream.get("codec_name"), "pix_fmt": v_stream.get("pix_fmt"),
            "color_tags": output_color_tags(v_stream),
        })
    if a_stream is not None:
        info.update({
            "audio_codec": a_stream.get("codec_name"),
            "sample_rate": int(a_stream.get("sample_rate") or 0),
            "channels": int(a_stream.get("channels") or 0),
            "audio_duration_s": round(_pos_float(a_stream.get("duration")) or duration_s, 4),
        })
    return info
