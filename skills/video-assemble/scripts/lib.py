"""Self-contained config + utilities for this skill (no cross-skill imports)."""
import math
import os
import subprocess


# ── 配置 ──────────────────────────────────────────────────────────────
_EXISTING_CONFIG_REF = globals().get("CONFIG")


def env_int(name, default, *, minimum=None):
    """Read an integer env var; an unset/empty value yields `default`, a malformed one is an error."""
    raw = os.environ.get(name, "")
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name}={raw!r} 不是整数") from exc
    return value if minimum is None else max(minimum, value)


def env_bool(name, default=False):
    """Read common boolean env var forms."""
    raw = os.environ.get(name, "")
    if raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_float(name, default, *, minimum=None):
    """Read a float env var; an unset/empty value yields `default`, a malformed one is an error."""
    raw = os.environ.get(name, "")
    if raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name}={raw!r} 不是数字") from exc
    if not math.isfinite(value):
        raise ValueError(f"环境变量 {name}={raw!r} 必须是有限数")
    return value if minimum is None else max(minimum, value)


# Cross-language source: when the original audio is in a language the narration is NOT in
# (e.g. a Japanese drama recapped in Chinese), the original speech bleeding under the narration
# is just noise the viewer can't parse — it reads as 怪音. In that mode the original is ducked to
# near-silent UNDER narration; it still plays full-volume in the original-audio gap blocks, where
# a single language is fine. Explicit SPEECH_DUCKING_VOLUME / ZONE_DUCKING_VOLUME still override.
_foreign_source_audio = env_bool("FOREIGN_SOURCE_AUDIO", False)
_foreign_under_narration_volume = 0.05  # original volume under narration when source audio is foreign

CONFIG = {
    "fade_ms": env_int("FADE_MS", 120, minimum=0),  # 每段 TTS 淡入淡出(ms)；过大会让紧凑的句子一顿一顿，120ms 防爆音又不发闷
    "ducking_mode": "fixed",  # fixed | sidechaincompress | none
    "ducking_threshold": 0.15,
    "ducking_ratio": 3,
    "ducking_attack": 10,
    "ducking_release": 300,
    "ducking_level_sc": 2.0,
    "ducking_makeup": 1.2,
    "ducking_narr_weight": 1.5,
    "ducking_orig_volume": env_float("DUCKING_ORIG_VOLUME", 0.3, minimum=0.0),  # 解说时原声基准音量
    # Derived report of the FOREIGN_SOURCE_AUDIO knob this skill implements: it selects the
    # ducking volumes below. Declared so callers can see which policy is in effect.
    "foreign_source_audio": _foreign_source_audio,
    "zone_ducking_volume": env_float("ZONE_DUCKING_VOLUME",
        _foreign_under_narration_volume if _foreign_source_audio else 0.12, minimum=0.0),  # 解说时原声压低到的音量
    "idle_orig_volume": env_float("IDLE_ORIG_VOLUME", 1.0, minimum=0.0),  # 解说块之间的"原声块"音量：默认满音量(1.0)，让精彩原声整段放出来，不被压低（用户要求解说成块、原声也成块）
    "duck_fade_seconds": env_float("DUCK_FADE_SECONDS", 0.3, minimum=0.0),  # 解说块/原声块切换的淡入淡出(秒)，略放宽到 0.3 让满音量↔压低的过渡更顺
    "duck_bridge_seconds": env_float("DUCK_BRIDGE_SECONDS", 1.5, minimum=0.0),  # 仅把间隔小于此值的相邻解说窗口并成一段压低；超过则视为作者特意留的"原声块"，原声放回满音量。默认 1.5s：解说块内部连续压低，块与块之间的留白放出满音量原声。该值只控制短间隔合并，不设定旁白/原声配额。调大→更连续铺底、原声块更少；调小→更碎
    "bgm_path": os.environ.get("BGM_PATH", "").strip(),  # 背景音乐文件(可选)，留空则不加 BGM
    "source_video": os.environ.get("SOURCE_VIDEO", "").strip(),  # 剪辑模式下的原始视频(可选)，用于时间线/剪映导出引用原片片段
    "source_video_explicit": False,  # 仅 assemble.py --source-video 显式传入时为 True；环境变量 SOURCE_VIDEO 不算显式
    "export_jianying": env_bool("EXPORT_JIANYING", False),  # 渲染后可选导出剪映草稿(默认关；与核心解耦)
    "jianying_draft_dir": os.environ.get("JIANYING_DRAFT_DIR", "").strip(),  # 剪映草稿输出父目录(留空=work_dir)
    "jianying_bundle_media": env_bool("JIANYING_BUNDLE_MEDIA", True),  # 默认开：macOS 剪映沙箱读不到外部路径，须把素材拷进草稿目录
    "bgm_volume": env_float("BGM_VOLUME", 0.18, minimum=0.0),  # BGM 铺底音量
    "bgm_ducking_volume": env_float("BGM_DUCKING_VOLUME", 0.10, minimum=0.0),  # 旁白时 BGM 压低到的音量
    "narration_speed": env_float("NARRATION_SPEED", 1.15, minimum=0.5),  # 解说整体提速(atempo)，默认回到可懂区间；长片可设 1.0
    "narration_cumulative_tempo_max": env_float("NARRATION_CUMULATIVE_TEMPO_MAX", 1.35, minimum=1.0),  # TTS rate × 全局 atempo × 段内 atempo 的累计上限
    "narration_cumulative_tempo_hard_max": env_float("NARRATION_CUMULATIVE_TEMPO_HARD_MAX", 1.40, minimum=1.0),  # QC/阻断硬上限
    "tts_segment_tempo_max": env_float("TTS_SEGMENT_TEMPO_MAX", 1.20, minimum=1.0),  # 兼容旧段内 atempo 上限；实际会被累计预算收紧
    "mask_source_subtitles": env_bool("MASK_SOURCE_SUBTITLES", False),  # 遮挡原片烧录字幕；必须配合显式 SOURCE_SUBTITLE_MASK_POLICY
    "source_subtitle_mask_policy_declared": bool(os.environ.get("SOURCE_SUBTITLE_MASK_POLICY", "").strip()),
    "source_subtitle_mask_policy": (
        os.environ.get("SOURCE_SUBTITLE_MASK_POLICY", "").strip().lower()
        or "off"
    ),  # off | opt_in | safe | forced；MASK_SOURCE_SUBTITLES alone is legacy implicit and QC-blocking
    "source_subtitle_mask_ratio": env_float("SOURCE_SUBTITLE_MASK_RATIO", 0.14, minimum=0.0),  # 底部遮挡比例
    "source_subtitle_mask_timing": os.environ.get("SOURCE_SUBTITLE_MASK_TIMING", "narration").strip().lower(),  # all | narration；增强版默认仅解说时遮罩
    "subtitle_mask_opacity": min(1.0, env_float("SUBTITLE_MASK_OPACITY", 0.6, minimum=0.0)),  # 0=透明，1=全黑；增强版默认半透明
    "subtitle_mask_padding": env_int("SUBTITLE_MASK_PADDING", 4, minimum=0),
    "subtitle_y_top": env_int("SUBTITLE_Y_TOP", -1, minimum=-1),  # 自动旋转后的显示画布坐标；top/bot 同时有效时贴合原字幕带
    "subtitle_y_bot": env_int("SUBTITLE_Y_BOT", -1, minimum=-1),
    "narration_delay_seconds": env_float("NARRATION_DELAY_SECONDS", 0.0, minimum=0.0),  # 默认严格采用 Agent 写入的 start；旧项目可显式恢复延迟
    "narration_tighten": env_bool("NARRATION_TIGHTEN", True),  # 段落内把句子紧贴上一句实际收尾播放，句间间隔稳定≤tight_pause，杜绝"一句解说一段空白"的卡顿
    "narration_run_gap_seconds": env_float("NARRATION_RUN_GAP_SECONDS", 1.6, minimum=0.0),  # 作者留白超过此值=新段落（让精彩原声透出）；小于则视为同一连续段落
    "narration_tight_pause_seconds": env_float("NARRATION_TIGHT_PAUSE_SECONDS", 0.35, minimum=0.0),  # 段落内句间固定间隔(秒)
    "narration_max_pull_seconds": env_float("NARRATION_MAX_PULL_SECONDS", 1.2, minimum=0.0),  # 收紧时一句最多比作者标注提前的秒数（漂移上限，越小越贴画面）
    "narration_tail_pad_seconds": 0.1,  # 解说尾部最少留白；短 slot 会自动压低 delay 避免截断
    "quiet_overlap_min_ratio": 0.8,  # 解说段至少多少比例落在安静窗口内才标记为非对白重叠
    "speech_ducking_volume": env_float("SPEECH_DUCKING_VOLUME",
        _foreign_under_narration_volume if _foreign_source_audio else 0.2, minimum=0.0),    # 解说与对白重叠时原声音量
    "burn_subtitles": env_bool("BURN_SUBTITLES", True),  # 烧录解说字幕（默认开；遮挡原字幕后需自带字幕，否则字幕区空白）
    "subtitle_original_in_gaps": env_bool("SUBTITLE_ORIGINAL_IN_GAPS", True),  # 原声留白处补烧原声台词字幕（来自 ASR）
    "force_video_reencode": env_bool("FORCE_VIDEO_REENCODE", False),  # 组装时重编码视频，修复部分容器时间戳问题
    # 成片压制（仅在重编码时生效：烧字幕/遮罩/缩放/FORCE_VIDEO_REENCODE 任一触发重编码）。
    "output_crf": env_int("OUTPUT_CRF", 18, minimum=0),          # x264 CRF；越大文件越小、画质越低（18≈视觉无损，23~26 体积更小）
    "output_preset": os.environ.get("OUTPUT_PRESET", "veryfast"),  # x264 preset；slow/slower 同 CRF 下体积更小但更慢
    "output_max_height": env_int("OUTPUT_MAX_HEIGHT", 0, minimum=0),  # >0 时把成片高度上限缩到该值(保持宽高比、偶数宽)；0=不缩放
    # 成片末端整体响度归一（默认混音偏轻，归一后更接近常见短视频响度；样片约 -11.9，默认取更安全的 -14）
    "final_loudnorm": env_bool("FINAL_LOUDNORM", True),  # 组装末端做一次整体响度归一
    "target_lufs": env_float("TARGET_LUFS", -14.0),       # 目标综合响度 (LUFS)
    "target_true_peak": env_float("TARGET_TRUE_PEAK", -1.0),  # 目标真峰值 (dBTP)
    "target_lra": env_float("TARGET_LRA", 11.0),          # 目标响度范围 (LU)
    "final_limiter_peak": env_float("FINAL_LIMITER_PEAK", 0.98, minimum=0.1),  # loudnorm 后峰值保护 limiter
    "subtitle_font_name": os.environ.get("SUBTITLE_FONT_NAME", "Arial"),
    "subtitle_font_size": env_int("SUBTITLE_FONT_SIZE", 42, minimum=8),
    "subtitle_primary_color": os.environ.get("SUBTITLE_PRIMARY_COLOR", "&H00FFFFFF"),
    "subtitle_outline_color": os.environ.get("SUBTITLE_OUTLINE_COLOR", "&H00000000"),
    "subtitle_outline": env_float("SUBTITLE_OUTLINE", 2.0, minimum=0.0),
    "subtitle_shadow": env_float("SUBTITLE_SHADOW", 1.0, minimum=0.0),
    "subtitle_margin_v": env_int("SUBTITLE_MARGIN_V", 48, minimum=0),
    "subtitle_margin_l": env_int("SUBTITLE_MARGIN_L", 40, minimum=0),
    "subtitle_margin_r": env_int("SUBTITLE_MARGIN_R", 40, minimum=0),
    "subtitle_alignment": env_int("SUBTITLE_ALIGNMENT", 2, minimum=1),
    "subtitle_max_chars": env_int("SUBTITLE_MAX_CHARS", 20, minimum=6),
    "subtitle_max_lines": env_int("SUBTITLE_MAX_LINES", 2, minimum=1),
    "subtitle_play_res_x": env_int("SUBTITLE_PLAY_RES_X", 1280, minimum=1),
    "subtitle_play_res_y": env_int("SUBTITLE_PLAY_RES_Y", 720, minimum=1),
}
if isinstance(_EXISTING_CONFIG_REF, dict):
    _EXISTING_CONFIG_REF.clear()
    _EXISTING_CONFIG_REF.update(CONFIG)
    CONFIG = _EXISTING_CONFIG_REF

def narration_tempo_budget(tts_rate_offset=0.0):
    """Return the canonical tempo budget shared by voiceover and assemble.

    `effective_tempo` is the user-perceived cumulative compression:
    TTS rate × global narration atempo × per-segment atempo.  The segment atempo
    cap is therefore tightened by the configured global speed and TTS rate
    offset; callers must fail/shorten instead of time-trimming speech when the
    needed ratio exceeds `segment_tempo_max`.
    """
    global_speed = CONFIG["narration_speed"]
    rate_factor = max(0.01, 1.0 + float(tts_rate_offset))
    cumulative_max = CONFIG["narration_cumulative_tempo_max"]
    hard_max = max(cumulative_max, CONFIG["narration_cumulative_tempo_hard_max"])
    segment_tempo_max = max(1.0, min(
        CONFIG["tts_segment_tempo_max"], cumulative_max / (global_speed * rate_factor)
    ))
    return {
        "global_narration_speed": global_speed,
        "tts_rate_factor": rate_factor,
        "cumulative_tempo_max": cumulative_max,
        "cumulative_tempo_hard_max": hard_max,
        "segment_tempo_max": segment_tempo_max,
        "max_raw_duration_factor": global_speed * segment_tempo_max,
    }

def log(msg):
    print(f"[video-recap] {msg}", flush=True)

def run_cmd(cmd, **kwargs):
    """运行命令，返回 CompletedProcess"""
    display = " ".join(
        text if len(text) <= 240 else text[:237] + "..."
        for text in map(str, cmd)
    )
    log(f"运行: {display}")
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)

def get_video_duration(video_path):
    """获取视频时长（秒）"""
    cmd = ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
           "-of", "csv=p=0", str(video_path)]
    result = run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe 无法读取时长 {video_path}: {result.stderr}")
    return float(result.stdout.strip())
