"""One ffmpeg pass over a finished video: hard-cut times (scdet) and loudness (ebur128).

scdet reports a per-frame score; a fixed score threshold is wrong in both directions on real
recaps (threshold 10 missed about a third of the cuts in a dark scene; threshold 6 counted a
fast-moving single shot as nine cuts). So every frame scoring >= SCORE_FLOOR is kept and a cut is
a frame that scores >= hard, or >= soft while scoring at least ISOLATION_RATIO times every other
frame within ISOLATION_WINDOW_S (the adjacent frames excluded: they carry the cut's own
after-image). Suppressed candidates are grouped into `shots.review_windows` for the agent to look
at; the agent's corrections live in the breakdown (labels.cut_fixes), never in this file.

The score is min(mafd, |mafd - previous mafd|), so a cut out of fast motion scores near zero (a
dark hard cut after a fight scored 0.9). Every cut is still a one-sided peak of mafd itself: at
least every other frame within the window and ISOLATION_RATIO times every frame on one side.
Such peaks (`mafd_peaks`) join the review windows; they never become cuts on their own.

The result is cached in `reference_measurements.json` by the video's {size, mtime_ns}; the raw
scores are cached too, so changing the score settings re-derives the cuts without decoding again.
Shot statistics are computed from these cuts directly; an understanding run's scene list merges
short shots and would inflate the median shot length.
"""
import bisect
import math
import re
from pathlib import Path

from lib import ffprobe, file_identity, log, read_json, run_cmd, write_json

MEASUREMENTS_FILE = "reference_measurements.json"
MEASUREMENTS_SCHEMA = "video-reference.measurements.v3"
DETECTOR = "scdet-isolated-v1"
DEFAULT_HARD_SCORE = 10.0     # always a cut
DEFAULT_SOFT_SCORE = 4.0      # a cut only when isolated
ISOLATION_RATIO = 2.0
ISOLATION_WINDOW_S = 0.3
SCORE_FLOOR = 2.0             # below soft / ratio a frame can neither be a cut nor suppress one;
                              # also the least mafd a review peak needs
REVIEW_PAD_S = 0.2
REVIEW_JOIN_S = 0.5
CURVE_WINDOW_S = 10.0
EDGE_S = 0.05                # cuts this close to either end are not cuts between two shots
SILENT_LUFS = -70.0          # ebur128 reports -120.7 for digital silence / warm-up blocks

_SCDET = re.compile(
    r"lavfi\.scd\.score\s*[:=]\s*(-?\d+(?:\.\d+)?)\s*,?\s*lavfi\.scd\.time\s*[:=]\s*(-?\d+(?:\.\d+)?)")
_MAFD = re.compile(r"\[Parsed_metadata[^\]]*\]\s*(?:frame:.*?pts_time:\s*(\S+)|lavfi\.scd\.mafd=(\S+))")
_EBU_FRAME = re.compile(r"\bt:\s*(\d+(?:\.\d+)?)\s.*?\bS:\s*(-?\d+(?:\.\d+)?|-?inf|nan)")
_SUMMARY_I = re.compile(r"\bI:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_SUMMARY_LRA = re.compile(r"\bLRA:\s*(-?\d+(?:\.\d+)?)\s*LU\b")
_SUMMARY_PEAK = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?\d+(?:\.\d+)?|-inf)")


def parse_scdet(stderr, duration=None):
    """Sorted [time, score] pairs from scdet log lines (`score: 4.2, time: 2.6` or `score=4.2 time=2.6`).

    Frames within EDGE_S of either end are dropped: they are not cuts between two shots.
    """
    scores = {}
    for match in _SCDET.finditer(stderr or ""):
        score, t = float(match.group(1)), round(float(match.group(2)), 3)
        if t <= EDGE_S or (duration is not None and t >= duration - EDGE_S):
            continue
        scores[t] = max(score, scores.get(t, score))
    return [[t, round(scores[t], 3)] for t in sorted(scores)]


def _sides(series, times, i, adjacent):
    """Highest value before and after series[i] within ISOLATION_WINDOW_S, adjacent frames excluded."""
    t = series[i][0]
    lo = bisect.bisect_left(times, t - ISOLATION_WINDOW_S - 1e-9)
    hi = bisect.bisect_right(times, t + ISOLATION_WINDOW_S + 1e-9)
    return (max((v for u, v in series[lo:i] if t - u > adjacent), default=0.0),
            max((v for u, v in series[i + 1:hi] if u - t > adjacent), default=0.0))


def mafd_peaks(stderr, fps, duration=None):
    """[time, mafd] of frames whose mafd (metadata print) is a one-sided peak >= SCORE_FLOOR."""
    series, t = [], None
    for time_text, value in _MAFD.findall(stderr or ""):
        if time_text:
            t = _number(time_text)
        elif t is not None:
            series.append((round(t, 3), _number(value) or 0.0))
            t = None
    adjacent = 1.5 / fps if fps else 0.06
    times = [u for u, _ in series]
    peaks = []
    for i, (t, value) in enumerate(series):
        if value < SCORE_FLOOR or t <= EDGE_S or (duration is not None and t >= duration - EDGE_S):
            continue
        before, after = _sides(series, times, i, adjacent)
        if value >= max(before, after) and value >= ISOLATION_RATIO * min(before, after):
            peaks.append([t, round(value, 3)])
    return peaks


def detect_cuts(scores, fps, *, hard=DEFAULT_HARD_SCORE, soft=DEFAULT_SOFT_SCORE, peaks=()):
    """(cuts, review_windows) from [time, score] pairs and mafd peaks; see the module docstring."""
    frame_s = 1.0 / fps if fps else 0.04
    adjacent = 1.5 * frame_s
    times = [t for t, _ in scores]
    cuts, suppressed = [], [t for t, _ in peaks]
    for i, (t, score) in enumerate(scores):
        if score < soft:
            continue
        if score < hard and score < ISOLATION_RATIO * max(_sides(scores, times, i, adjacent)):
            suppressed.append(t)
            continue
        if cuts and t - cuts[-1][0] <= adjacent:     # one cut spread over two frames
            if score > cuts[-1][1]:
                cuts[-1] = (t, score)
            continue
        cuts.append((t, score))
    cut_times = [t for t, _ in cuts]
    windows = []
    for t in sorted(set(suppressed)):
        if any(abs(t - c) <= adjacent for c in cut_times):
            continue
        if windows and t - windows[-1][1] <= REVIEW_JOIN_S:
            windows[-1][1] = t
        else:
            windows.append([t, t])
    return cut_times, [[round(max(0.0, a - REVIEW_PAD_S), 2), round(b + REVIEW_PAD_S, 2)] for a, b in windows]


def _number(text):
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def parse_ebur128(stderr, duration):
    """Integrated loudness, LRA, true peak (Summary block) and a 1 s short-term series."""
    stderr = stderr or ""
    head, marker, summary = stderr.rpartition("Summary:")
    if not marker:
        return None
    seconds = max(1, math.ceil(duration))
    series = [None] * seconds
    for match in _EBU_FRAME.finditer(head):
        bucket = int(float(match.group(1)))
        value = _number(match.group(2))
        if 0 <= bucket < seconds:
            series[bucket] = None if value is None or value < SILENT_LUFS else round(value, 1)

    def pick(pattern):
        match = pattern.search(summary)
        return None if match is None else _number(match.group(1))

    return {
        "integrated_lufs": pick(_SUMMARY_I),
        "lra_lu": pick(_SUMMARY_LRA),
        "true_peak_dbtp": pick(_SUMMARY_PEAK),
        "short_term_1s": series,
    }


def _percentile(sorted_values, q):
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def shot_stats(cuts, duration):
    """Shot-length distribution and cut density for cuts inside [0, duration]."""
    bounds = [0.0, *cuts, duration]
    lengths = sorted(b - a for a, b in zip(bounds, bounds[1:]) if b > a)
    count = len(lengths)
    minutes = duration / 60.0
    windows = max(1, math.ceil(duration / CURVE_WINDOW_S))
    curve = [0] * windows
    for cut in cuts:
        curve[min(windows - 1, int(cut // CURVE_WINDOW_S))] += 1
    # Scale each window by its own length: the last one is usually partial (6.66 s on a 126.66 s
    # video). Floor at 1 s so a sliver of a window cannot turn one cut into hundreds per minute.
    per_min = [60.0 / max(1.0, min(CURVE_WINDOW_S, duration - i * CURVE_WINDOW_S)) for i in range(windows)]
    return {
        "cuts": list(cuts),
        "count": count,
        "mean_s": round(sum(lengths) / count, 3) if count else None,
        "median_s": round(_percentile(lengths, 0.5), 3) if count else None,
        "p10_s": round(_percentile(lengths, 0.1), 3) if count else None,
        "p90_s": round(_percentile(lengths, 0.9), 3) if count else None,
        "share_under_1s": round(sum(1 for x in lengths if x < 1.0) / count, 3) if count else None,
        "share_over_8s": round(sum(1 for x in lengths if x > 8.0) / count, 3) if count else None,
        "cuts_per_min": round(len(cuts) / minutes, 2) if minutes > 0 else None,
        "curve": {"window_s": CURVE_WINDOW_S, "values": [round(c * k, 1) for c, k in zip(curve, per_min)]},
    }


def _ffmpeg_command(video, scaled, has_audio):
    video_filter = f"scdet=threshold={SCORE_FLOOR:g},metadata=mode=print:key=lavfi.scd.mafd"
    if scaled:
        video_filter = "scale=320:-2," + video_filter
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-map", "0:v:0", "-vf", video_filter]
    if has_audio:
        cmd += ["-map", "0:a:0", "-af", "ebur128=peak=true:framelog=info"]
    return cmd + ["-f", "null", "-"]


def _reusable(cached, identity, scaled):
    """The cached decode (scores + loudness) is valid for this file and scaling."""
    if not isinstance(cached, dict) or cached.get("schema") != MEASUREMENTS_SCHEMA:
        return False
    source = cached.get("source") or {}
    settings = cached.get("settings") or {}
    same_file = {k: source.get(k) for k in identity} == identity
    return same_file and settings.get("scaled") == scaled and settings.get("score_floor") == SCORE_FLOOR


def measure(video, work_dir, *, hard=DEFAULT_HARD_SCORE, soft=DEFAULT_SOFT_SCORE, scaled=True):
    """Measure `video` into work_dir/reference_measurements.json; reuse the decode when the file is unchanged."""
    if not SCORE_FLOOR * ISOLATION_RATIO <= soft <= hard:
        raise ValueError(f"需要 {SCORE_FLOOR * ISOLATION_RATIO:g} ≤ soft ≤ hard，收到 soft={soft} hard={hard}")
    video = Path(video)
    out_path = Path(work_dir) / MEASUREMENTS_FILE
    identity = file_identity(video)
    settings = {"detector": DETECTOR, "hard_score": float(hard), "soft_score": float(soft),
                "isolation_ratio": ISOLATION_RATIO, "isolation_window_s": ISOLATION_WINDOW_S,
                "score_floor": SCORE_FLOOR, "scaled": bool(scaled)}
    cached = read_json(out_path)
    if _reusable(cached, identity, bool(scaled)):
        if cached.get("settings") == settings:
            log(f"复用 {MEASUREMENTS_FILE}（成片与设置未变）")
            return cached
        log("成片未变，只按新的分数设置重算切点（不重新解码）")
        source, scores, loudness = cached["source"], cached["scdet_scores"], cached.get("loudness")
        peaks = cached.get("mafd_peaks") or []
    else:
        source = {**identity, **ffprobe(video)}
        result = run_cmd(_ffmpeg_command(video, scaled, source["audio_streams"] > 0))
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 测量失败: {result.stderr.strip()[-600:]}")
        scores = parse_scdet(result.stderr, source["duration_s"])
        peaks = mafd_peaks(result.stderr, source.get("fps"), source["duration_s"])
        loudness = parse_ebur128(result.stderr, source["duration_s"]) if source["audio_streams"] else None
    cuts, review_windows = detect_cuts(scores, source.get("fps"), hard=hard, soft=soft, peaks=peaks)
    payload = {
        "schema": MEASUREMENTS_SCHEMA,
        "source": source,
        "settings": settings,
        "shots": {**shot_stats(cuts, source["duration_s"]), "review_windows": review_windows},
        "loudness": loudness,
        "scdet_scores": scores,
        "mafd_peaks": peaks,
    }
    write_json(out_path, payload)
    log(f"写入 {out_path}：{len(cuts)} 个切点，{len(review_windows)} 个待复核窗口，时长 {source['duration_s']:.1f}s")
    return payload
