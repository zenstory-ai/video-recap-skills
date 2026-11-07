"""Self-contained config + utilities for this skill (no cross-skill imports).
Merged from the shared core; reads the same env vars as the rest of the bundle."""
import math
import os


# ── 配置 ──────────────────────────────────────────────────────────────

def env_float(name, default, *, minimum=None):
    """Read a float env var, rejecting malformed or below-minimum values."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a float; got {raw!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite; got {raw!r}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}; got {value}")
    return value


CONFIG = {
    # TTS 语速（字符/秒）。实测 mimo-tts 冰糖音色中位 ~3.9 字/秒，可用 SPEECH_RATE 覆盖
    # 生成解说时使用 speech_rate * safety_margin 作为约束
    "speech_rate": env_float("SPEECH_RATE", 3.9, minimum=0.5),  # 旧值 3.5 系统性偏低 ~10-17%
    "speech_safety_margin": 0.85,  # 保守系数：TTS 实际语速有 ±20% 波动
    # Block-coverage lint thresholds — promoted from inline .get() literals to real CONFIG keys (tunable; defaults unchanged)
    "narration_coverage_target": 0.7,   # rough first-draft/diagnostic fallback; content-led audio decisions may differ (not a quota)
    "narration_coverage_max": 0.85,     # above this coverage → no_original_blocks (narration is wall-to-wall)
    "narration_coverage_min": 0.5,      # below this coverage → under_narrated
    "original_block_min_seconds": 2.5,  # a deliberate original-audio gap must be at least this long
    "narration_block_min_chars": 16,    # below this avg block size → fragmented_beats
    "breath_ms": 250,  # 段间呼吸空间(ms)；block recap 块内连贯、块间留原声呼吸
    "narration_speed": env_float("NARRATION_SPEED", 1.15, minimum=0.5),  # 解说整体提速(atempo)，默认回到可懂区间；长片可设 1.0
    "narration_tail_pad_seconds": 0.1,  # 解说尾部最少留白；短 slot 会自动压低 delay 避免截断
    "quiet_overlap_min_ratio": 0.8,  # 解说段至少多少比例落在安静窗口内才标记为非对白重叠
    "visual_beat_max_seconds": 18.0,  # 单段解说超过该时长且跨多个帧锚点时给 lint 提醒
    "visual_beat_max_facts": 3,  # 单段解说最多建议覆盖的 frame_facts 锚点数量
}

def log(msg):
    print(f"[video-recap] {msg}", flush=True)

def file_identity(path):
    """{size, mtime_ns} of an input file: the identity brief consumers compare against
    the provenance a producer stored, instead of re-hashing bytes."""
    st = os.stat(os.fspath(path))
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
