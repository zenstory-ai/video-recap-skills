"""Final-mix loudness: two-pass linear loudnorm, behind a true-peak limiter when peaks block the gain.

ffmpeg's `loudnorm=linear=true` applies one constant gain only while
measured_TP + (I - measured_I) <= TP and measured_LRA <= LRA; otherwise it silently runs
dynamic (3-second AGC) normalisation, which pumps. Real mixes often carry a few transients
(gunshots, music hits) at 0 dBTP, so reaching TARGET_LUFS with one gain needs those peaks
limited first. The chain then is: constant pre-gain -> oversampled lookahead limiter (sample
peaks at 4x the rate approximate true peaks) -> second loudnorm pass in linear mode, measured
on the limited signal, that trims the last fraction of a LU without crossing TP.

Every stage aims at a pre-encode true-peak target (`peak_target`, default TP). The AAC
encode raises the delivered true peak over it, so `codec_peak.py` renders the first time
CODEC_PEAK_HEADROOM_DB under TP and lowers the target further when the delivered file
still measures over TP.
"""

import functools
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

from lib import CONFIG, filter_file_args, log, run_cmd

# loudnorm's LRA option range is 1-20 in older ffmpeg (1-50 in current releases).
_LOUDNORM_MAX_LRA = 20.0
# The limiter ceiling sits this far under the true-peak target, leaving the linear loudnorm
# stage room to make up the loudness the limiter shaved off (about 0.3 LU at 6 dB of
# reduction on a real film mix, 1.1 LU at 10 dB).
LIMITER_HEADROOM_DB = 1.0
# The limiter runs at 4x the 48 kHz mix rate so its sample peaks track the true peak.
_LIMITER_OVERSAMPLE_RATE = 192000
_LIMITER_ATTACK_MS = 5
_LIMITER_RELEASE_MS = 100
# AAC at 192 kb/s raised the true peak 0.3-1.4 dB over the loudnorm stage's output on real
# renders (most on heavily limited mixes); the first render aims this far under TP.
CODEC_PEAK_HEADROOM_DB = 0.5


def first_render_peak_target():
    """The pre-encode true-peak target (dBTP) of the first render: TP minus codec headroom."""
    return round(float(CONFIG["target_true_peak"]) - CODEC_PEAK_HEADROOM_DB, 2)


def _peak_target(peak_target):
    return float(CONFIG["target_true_peak"]) if peak_target is None else float(peak_target)


def _limiter_filter():
    return f"alimiter=limit={CONFIG['final_limiter_peak']:.2f}:level=false"


@functools.lru_cache(maxsize=None)
def _alimiter_compensates_latency():
    """Whether this ffmpeg's alimiter has the `latency` option (asked once per process).

    Without it the lookahead delays the audio by the attack time (5 ms).
    """
    if shutil.which("ffmpeg") is None:
        return False
    result = subprocess.run(["ffmpeg", "-hide_banner", "-h", "filter=alimiter"],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=20)
    return "latency" in result.stdout


def _measured_values(measured):
    """(I, TP, LRA, threshold) from a loudnorm measurement, or None when it is unusable."""
    try:
        values = [float(measured[key])
                  for key in ("input_i", "input_tp", "input_lra", "input_thresh")]
    except (KeyError, TypeError, ValueError):
        return None
    measured_i, measured_tp, measured_lra, measured_thresh = values
    # ffmpeg's own "not measured" sentinels (silence measures -inf / -70 / 0).
    if (not all(math.isfinite(v) for v in values) or measured_tp == 99
            or measured_thresh == -70 or measured_lra == 0 or measured_i == 0):
        return None
    return values


def _linear_loudnorm_targets(measured, peak_target=None):
    """Second-pass targets that keep loudnorm linear, or None when no such targets exist.

    The integrated target is lowered until the gained true peak fits under `peak_target`
    (default TP; quieter than TARGET_LUFS instead of compressed), and the LRA target, which
    linear mode never applies, is raised to the measured range.
    """
    values = _measured_values(measured)
    if values is None:
        return None
    measured_i, measured_tp, measured_lra, _thresh = values
    requested = float(CONFIG["target_lufs"])
    true_peak = _peak_target(peak_target)
    # 0.01 LU under the exact limit: ffmpeg compares the floating-point sum.
    peak_limited = math.floor((true_peak - measured_tp + measured_i) * 100) / 100 - 0.01
    integrated = round(min(requested, peak_limited), 2)
    lra = max(float(CONFIG["target_lra"]), measured_lra)
    if integrated < -70 or lra > _LOUDNORM_MAX_LRA:
        return None
    return {
        "integrated": integrated,
        "lra": lra,
        "true_peak": true_peak,
        "gain_capped_db": round(max(0.0, requested - integrated), 2),
    }


def _peak_limiter_plan(measured, peak_target=None):
    """Pre-gain and ceiling for the true-peak limiter, or None when it is not needed.

    The ceiling sits LIMITER_HEADROOM_DB under `peak_target` (default TP). None when one
    linear gain already reaches TARGET_LUFS under the target, when the measurement is
    unusable or too wide for linear mode (a limiter does not narrow LRA), or when
    LOUDNESS_LIMITER_MAX_DB is 0. Above that maximum the pre-gain stops where the limiter
    would remove exactly the maximum; the rest of the gain is capped as before.
    """
    values = _measured_values(measured)
    max_reduction = float(CONFIG["loudness_limiter_max_db"])
    if values is None or max_reduction <= 0:
        return None
    measured_i, measured_tp, measured_lra, _thresh = values
    if max(float(CONFIG["target_lra"]), measured_lra) > _LOUDNORM_MAX_LRA:
        return None
    gain = float(CONFIG["target_lufs"]) - measured_i
    true_peak = _peak_target(peak_target)
    if measured_tp + gain <= true_peak:
        return None
    ceiling = round(true_peak - LIMITER_HEADROOM_DB, 2)
    required = measured_tp + gain - ceiling
    pre_gain = min(gain, ceiling + max_reduction - measured_tp)
    return {
        "pre_gain_db": round(pre_gain, 2),
        "ceiling_dbtp": ceiling,
        "required_reduction_db": round(required, 2),
        "max_reduction_db": max_reduction,
    }


def _peak_limiter_chain(limiter):
    """Constant pre-gain, then the oversampled lookahead limiter, back at the mix rate."""
    limit = 10 ** (limiter["ceiling_dbtp"] / 20)
    latency = ":latency=true" if _alimiter_compensates_latency() else ""
    return (
        f"volume={limiter['pre_gain_db']}dB,aresample={_LIMITER_OVERSAMPLE_RATE},"
        f"alimiter=limit={limit:.6f}:attack={_LIMITER_ATTACK_MS}"
        f":release={_LIMITER_RELEASE_MS}:level=false{latency},aresample=48000"
    )


def _loudnorm_stage_measurement(measured, limiter):
    """What the second loudnorm pass sees: the limited signal when a limiter runs."""
    return limiter["measurement"] if limiter else measured


def _loudness_mode(measured=None, normalization_type=None, limiter=None):
    """`limiter_only`, `equivalent` (single pass), or the two-pass mode ffmpeg really ran.

    `normalization_type` is what the final render's loudnorm reported; without it the mode
    is predicted from whether `_linear_loudnorm_targets` found linear targets.
    """
    if not CONFIG["final_loudnorm"]:
        return "limiter_only"
    if not measured:
        return "equivalent"
    if normalization_type is None:
        stage = _loudnorm_stage_measurement(measured, limiter)
        normalization_type = "linear" if _linear_loudnorm_targets(stage) else "dynamic"
    if normalization_type != "linear":
        return "two_pass_dynamic"
    return "two_pass_linear_peak_limited" if limiter else "two_pass_linear"


def final_loudnorm_filter(measured=None, limiter=None, peak_target=None):
    """Final-mix loudness normalization/limiter filter from CONFIG.

    Ducking branches set only relative balance; this single stage owns the
    absolute output loudness so the recap is not left too quiet. When `measured`
    is supplied from a first loudnorm pass, ffmpeg runs the deterministic second
    pass with the targets from `_linear_loudnorm_targets`, behind the `limiter` chain
    when one was planned (its loudnorm then uses the limited signal's measurement);
    without it we still force the same target and peak limiter as a documented
    equivalent/fallback path. Every path aims at `peak_target` (default TP).
    """
    if not CONFIG["final_loudnorm"]:
        return _limiter_filter()
    stage = _loudnorm_stage_measurement(measured, limiter) if measured else None
    targets = _linear_loudnorm_targets(stage, peak_target) if stage else None
    integrated = targets["integrated"] if targets else CONFIG["target_lufs"]
    lra = targets["lra"] if targets else CONFIG["target_lra"]
    filt = (
        f"loudnorm=I={integrated}"
        f":TP={_peak_target(peak_target)}"
        f":LRA={lra}"
        f":linear=true"
    )
    if stage:
        for src, dst in (
            ("input_i", "measured_I"),
            ("input_tp", "measured_TP"),
            ("input_lra", "measured_LRA"),
            ("input_thresh", "measured_thresh"),
            ("target_offset", "offset"),
        ):
            if src in stage:
                filt += f":{dst}={stage[src]}"
    filt += ":print_format=summary"
    if measured and limiter:
        filt = f"{_peak_limiter_chain(limiter)},{filt}"
    return f"{filt},{_limiter_filter()}"


def _parse_loudnorm_json(text):
    """Extract ffmpeg loudnorm JSON from stderr/stdout."""
    for match in reversed(list(re.finditer(r"\{[\s\S]*?\}", text))):
        try:
            data = json.loads(match.group(0))
        except ValueError:
            continue
        if {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"} <= set(data):
            return data
    return None


def _loudnorm_summary_value(text, label):
    """Last `<label>: <number>` in a loudnorm summary (None when absent, e.g. `-inf`)."""
    matches = re.findall(rf"{label}:\s*([+-]?\d+(?:\.\d+)?)\b", text)
    return float(matches[-1]) if matches else None


def loudnorm_final_pass(stderr, measured=None, limiter=None, peak_target=None):
    """What the final render's loudness stage actually did, from its `print_format=summary`.

    `normalization_type` is `linear` or `dynamic` (None when ffmpeg printed no summary);
    `target` holds the second-pass targets when a first pass measured the mix, and its
    `gain_capped_db` is how far the delivered target sits under TARGET_LUFS. `peak_limiter`
    is the limiter that ran before it (None when none did). `output_true_peak` is the
    stage's pre-encode output; the AAC file's own peak is `codec_peak`'s `delivered`.
    """
    text = stderr or ""
    kinds = re.findall(r"Normalization Type:\s*(Linear|Dynamic)", text)
    stage = _loudnorm_stage_measurement(measured, limiter) if measured else None
    return {
        "normalization_type": kinds[-1].lower() if kinds else None,
        "target": _linear_loudnorm_targets(stage, peak_target) if stage else None,
        "output_integrated": _loudnorm_summary_value(text, "Output Integrated"),
        "output_true_peak": _loudnorm_summary_value(text, "Output True Peak"),
        "peak_limiter": limiter if measured else None,
    }


def _loudnorm_first_pass_filter():
    return (
        f"loudnorm=I={CONFIG['target_lufs']}"
        f":TP={CONFIG['target_true_peak']}"
        f":LRA={CONFIG['target_lra']}"
        f":print_format=json"
    )


def _measure_loudness(input_video, narration_wav, original_audio_input,
                      bgm_input, filter_complex, work_dir, pre_chain=None):
    """Measure the exact mixed audio graph (after `pre_chain` when given) before the render.

    Returns ffmpeg loudnorm JSON, or None when probing fails.
    """
    stage = f"{pre_chain}," if pre_chain else ""
    probe_fc = f"{filter_complex};[aout]{stage}{_loudnorm_first_pass_filter()}[lnprobe]"
    probe_script = Path(work_dir) / ".filter_complex_loudnorm_probe.txt"
    probe_script.write_text(probe_fc, encoding="utf-8")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_video),
        "-i", str(narration_wav),
        *original_audio_input,
        *bgm_input,
        *filter_file_args("filter_complex", probe_script),
        "-map", "[lnprobe]",
        "-f", "null", "-",
    ]
    try:
        result = run_cmd(cmd)
    finally:
        probe_script.unlink(missing_ok=True)
    if result.returncode != 0:
        log(f"  ⚠️ loudnorm 测量失败: {result.stderr}")
        return None
    measured = _parse_loudnorm_json(result.stdout + "\n" + result.stderr)
    if not measured:
        log("  ⚠️ loudnorm 测量未返回 JSON")
    return measured


def plan_final_loudness(input_video, narration_wav, original_audio_input,
                        bgm_input, filter_complex, work_dir, peak_target=None, measured=None):
    """Measure the mix and pick the final loudness stage for `peak_target`: (measured, limiter).

    `measured` is the first-pass loudnorm JSON (None: single-pass fallback); a caller that
    already has it passes it in and only the limited signal is measured. `limiter` is
    the planned true-peak limiter plus `measurement` (the limited signal's loudnorm JSON)
    and `reduction_db` (measured true peak before minus after it), or None when one linear
    gain fits or the limited signal could not be measured (the gain is then capped).
    """
    if not CONFIG["final_loudnorm"]:
        return None, None
    args = (input_video, narration_wav, original_audio_input, bgm_input, filter_complex,
            work_dir)
    measured = measured or _measure_loudness(*args)
    if not measured:
        log("  ⚠️ loudnorm 首遍测量不可用，降级到单遍目标滤镜+limiter")
        return None, None
    limiter = _peak_limiter_plan(measured, peak_target)
    if limiter:
        limited = _measure_loudness(*args, pre_chain=_peak_limiter_chain(limiter))
        if limited and _linear_loudnorm_targets(limited, peak_target):
            reduction = (float(measured["input_tp"]) + limiter["pre_gain_db"]
                         - float(limited["input_tp"]))
            limiter = {**limiter, "reduction_db": round(reduction, 2), "measurement": limited}
            log(f"  成片真峰值限幅：先增益 {limiter['pre_gain_db']} dB，峰值压到 "
                f"{limiter['ceiling_dbtp']} dBTP（削去 {limiter['reduction_db']} dB），"
                "再线性增益到目标响度")
        else:
            log("  ⚠️ 限幅后的混音无法测量或无法线性归一，改为下调目标响度")
            limiter = None
    targets = _linear_loudnorm_targets(_loudnorm_stage_measurement(measured, limiter),
                                       peak_target)
    if targets and targets["gain_capped_db"] > 0:
        log(f"  成片目标响度从 {CONFIG['target_lufs']} 降到 {targets['integrated']} LUFS"
            f"（限幅上限 {CONFIG['loudness_limiter_max_db']} dB），保持线性增益")
    return measured, limiter
