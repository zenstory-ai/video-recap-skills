"""SRT/ASS subtitle parsing, semantic balanced wrapping, and canvas-scaled ASS rendering."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

__all__ = [
    "parse_srt",
    "wrap_subtitle_text",
    "write_ass",
    "write_srt",
]

_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$")
_ASS_COLOR_RE = re.compile(r"^&H([0-9a-fA-F]{6}|[0-9a-fA-F]{8})&?$", re.IGNORECASE)
_SRT_TIME_RE = re.compile(r"(?:(\d{1,2}):)?(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(?:(\d{1,2}):)?(\d{2}):(\d{2})[,.](\d{3})")
_TRAILING_PUNCT = "。，、；：,.;:"
_CLAUSE_PUNCT = set("，、；：,;！？!?。")
_SOFT_BREAK_HEAD = set("但却而在把被让用从到和与又就才也")
_REF_W, _REF_H = 1280.0, 720.0


def seconds_to_srt_time(seconds: float) -> str:
    """Format seconds as `HH:MM:SS,mmm` using Decimal flooring."""
    total_ms = int(Decimal(str(max(0.0, float(seconds)))) * 1000)
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def seconds_to_ass_time(seconds: float) -> str:
    """Format seconds as ASS `H:MM:SS.cc`."""
    cs_total = int(round(max(0.0, float(seconds)) * 100))
    h, rem = divmod(cs_total, 360_000)
    m, rem = divmod(rem, 6_000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def normalize_ass_color(value: Any, default: str = "&H00FFFFFF") -> str:
    """Convert `#RRGGBB[AA]` or `&HAABBGGRR` into canonical `&HAABBGGRR`."""
    raw = str(value or "").strip()
    if not raw:
        return default
    m_hex = _HEX_COLOR_RE.match(raw)
    if m_hex:
        rgb, alpha_hex = m_hex.group(1).upper(), m_hex.group(2)
        ass_alpha = f"{255 - int(alpha_hex, 16):02X}" if alpha_hex else "00"
        return f"&H{ass_alpha}{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}"
    m_ass = _ASS_COLOR_RE.match(raw)
    if m_ass:
        digits = m_ass.group(1).upper()
        return f"&H00{digits}" if len(digits) == 6 else f"&H{digits}"
    return raw


def escape_ass_text(text: str) -> str:
    return (
        str(text).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        .replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\N")
    )


def _display_width(text: str) -> float:
    return sum(1.0 if ord(ch) > 0x7F else 0.55 for ch in text)


def _tokenize_atomic(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+(?:[.'%/:+-][A-Za-z0-9]+)*[%％]?|\s+|.", text)


def _clean_line(line: str, strip_trailing_punct: bool) -> str:
    s = line.strip()
    return s.rstrip(_TRAILING_PUNCT).strip() if strip_trailing_punct else s


def wrap_subtitle_text(
    text: str,
    max_chars: int = 16,
    *,
    strip_trailing_punct: bool = True,
) -> str:
    """Wrap CJK/Latin subtitle text with semantic balanced 2-line preference, keeping numbers intact."""
    clean = _clean_line(re.sub(r"\s+", " ", str(text or "").strip()), strip_trailing_punct)
    if not clean or max_chars <= 0 or _display_width(clean) <= max_chars:
        return clean

    tokens = _tokenize_atomic(clean)
    total_w = _display_width(clean)
    best: tuple[float, str, str] | None = None
    for idx in range(1, len(tokens)):
        left = _clean_line("".join(tokens[:idx]), strip_trailing_punct)
        right = _clean_line("".join(tokens[idx:]), strip_trailing_punct)
        if not left or not right:
            continue
        wl, wr = _display_width(left), _display_width(right)
        if wl > max_chars or wr > max_chars:
            continue
        score = abs(wl - wr) / max(total_w, 1.0)
        prev_tok, next_tok = tokens[idx - 1], tokens[idx]
        if prev_tok and prev_tok[-1] in _CLAUSE_PUNCT:
            score -= 0.65
        elif prev_tok.isspace() or next_tok.isspace():
            score -= 0.35
        elif next_tok and next_tok[0] in _SOFT_BREAK_HEAD:
            score -= 0.12
        if best is None or score < best[0]:
            best = (score, left, right)
    if best is not None:
        return f"{best[1]}\n{best[2]}"

    lines: list[str] = []
    buf = ""
    for tok in tokens:
        if not buf and tok.isspace():
            continue
        if _display_width(buf + tok) <= max_chars or not buf:
            buf += tok
        else:
            lines.append(_clean_line(buf, strip_trailing_punct))
            buf = "" if tok.isspace() else tok
    if buf.strip():
        lines.append(_clean_line(buf, strip_trailing_punct))
    return "\n".join(line for line in lines if line)


def _parse_ts(h: str | None, m: str, s: str, ms: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(path: str | Path) -> list[dict[str, Any]]:
    """Parse an SRT or WebVTT file into `[{"start", "end", "text"}]`."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    cues: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\s*\r?\n", raw.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines or lines[0].upper().startswith("WEBVTT"):
            continue
        ts_idx = next((i for i, line in enumerate(lines) if _SRT_TIME_RE.search(line)), None)
        match = _SRT_TIME_RE.search(lines[ts_idx]) if ts_idx is not None else None
        if not match or ts_idx is None:
            continue
        start, end = _parse_ts(*match.groups()[:4]), _parse_ts(*match.groups()[4:])
        text = "\n".join(lines[ts_idx + 1 :]).strip()
        if text and end > start:
            cues.append({"start": round(start, 4), "end": round(end, 4), "text": text})
    return cues


def write_srt(
    cues: Sequence[dict[str, Any]],
    out_path: str | Path,
    *,
    strip_trailing_punct: bool = False,
) -> Path:
    """Write cues to UTF-8 SRT file."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    idx = 0
    for cue in cues:
        text, start, end = str(cue.get("text") or "").strip(), float(cue["start"]), float(cue["end"])
        if not text or end <= start:
            continue
        if strip_trailing_punct:
            text = text.rstrip(_TRAILING_PUNCT)
        idx += 1
        lines.extend([str(idx), f"{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}", text, ""])
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def write_ass(
    cues: Sequence[dict[str, Any]],
    canvas: dict[str, Any],
    out_path: str | Path,
    *,
    style: dict[str, Any] | None = None,
    safe_y: Sequence[float] | None = None,
) -> Path:
    """Generate canvas-scaled ASS subtitles with balanced wrapping, fade-in, and optional glow."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cfg = dict(style or {})
    cw, ch = int(canvas.get("width", 1280)), int(canvas.get("height", 720))
    max_chars = int(cfg.get("max_chars", 16 if ch >= cw else 22))
    kx, ky = cw / _REF_W, ch / _REF_H

    margin_l = int(round(float(cfg.get("margin_l", 40)) * kx))
    margin_r = int(round(float(cfg.get("margin_r", 40)) * kx))
    band = safe_y or cfg.get("safe_y")
    margin_v = (
        max(8, int(round(ch * max(0.0, 1.0 - float(band[1])))))
        if band and len(band) == 2
        else int(round(float(cfg.get("margin_v", 48)) * ky))
    )
    usable_w = max(1.0, cw - margin_l - margin_r)
    req_font = float(cfg.get("font_size", 46))
    font_size = max(14, int(min(req_font * (ky if "font_size" not in cfg else 1.0), usable_w / max(1, max_chars))))
    font_name = str(cfg.get("font_name") or "PingFang SC")
    primary = normalize_ass_color(cfg.get("primary_color"), "&H00FFFFFF")
    outline_col = normalize_ass_color(cfg.get("outline_color"), "&H00000000")
    outline, shadow, glow = round(float(cfg.get("outline", 2.5)), 2), round(float(cfg.get("shadow", 1.0)), 2), float(cfg.get("glow", 0.0))
    strip_punct = bool(cfg.get("strip_trailing_punct", True))

    lines = [
        "[Script Info]", "ScriptType: v4.00+", "WrapStyle: 0", "ScaledBorderAndShadow: yes",
        f"PlayResX: {cw}", f"PlayResY: {ch}", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font_name},{font_size},{primary},&H000000FF,{outline_col},&H64000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_l},{margin_r},{margin_v},1",
        f"Style: Dialogue,{font_name},{font_size},&H00E8E8E8,&H000000FF,{outline_col},&H64000000,0,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_l},{margin_r},{margin_v},1",
    ]
    if glow > 0:
        lines.append(
            f"Style: Glow,{font_name},{font_size},&H96000000,&H000000FF,&H88000000,&H00000000,-1,0,0,0,100,100,0,0,1,{round(max(3.6, outline + 2.0), 2)},0,2,{margin_l},{margin_r},{margin_v},1"
        )
    lines.extend(["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"])

    for cue in cues:
        raw_text, start, end = str(cue.get("text") or "").strip(), float(cue["start"]), float(cue["end"])
        if not raw_text or end <= start:
            continue
        wrapped = escape_ass_text(wrap_subtitle_text(raw_text, max_chars, strip_trailing_punct=strip_punct))
        style_name = "Dialogue" if cue.get("style") == "dialogue" else "Default"
        s_ts, e_ts = seconds_to_ass_time(start), seconds_to_ass_time(end)
        if glow > 0:
            lines.append(f"Dialogue: 0,{s_ts},{e_ts},Glow,,0,0,0,,{{\\blur{glow:.1f}\\fad(90,70)}}{wrapped}")
            lines.append(f"Dialogue: 1,{s_ts},{e_ts},{style_name},,0,0,0,,{{\\fad(90,70)}}{wrapped}")
        else:
            lines.append(f"Dialogue: 0,{s_ts},{e_ts},{style_name},,0,0,0,,{{\\fad(90,70)}}{wrapped}")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
