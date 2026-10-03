"""Delivered true peak: measure the encoded file and correct an AAC overshoot over TP.

The loudness stage keeps the PCM mix under its true-peak target, but the lossy AAC encode
adds overshoot (0.3-1.4 dB at 192 kb/s on real renders, most on heavily limited mixes), so
the delivered file can sit over TP while the stage reported -1.1 dBTP. The first render
aims `loudness.CODEC_PEAK_HEADROOM_DB` under TP; `deliver_under_true_peak` then decodes
the delivered audio, and while its true peak is still over TP lowers the pre-encode target
by the measured overshoot plus CODEC_PEAK_MARGIN_DB and re-encodes only the audio (the
video stream is copied from the render), at most CODEC_PEAK_MAX_CORRECTIONS times.
"""

import os
from pathlib import Path

import assemble_constants as constants
import lib
from lib import CONFIG, filter_file_args, log
from loudness import (
    _loudnorm_first_pass_filter, _loudnorm_summary_value, _parse_loudnorm_json,
)

CODEC_PEAK_MARGIN_DB = 0.1
CODEC_PEAK_MAX_CORRECTIONS = 2


def delivered_loudness(path):
    """Integrated loudness and true peak of the file's first audio stream, or None.

    The true peak is the higher of loudnorm's and ebur128's readings of the decoded audio:
    the two oversampling detectors disagree by up to about 1 dB on dense transients.
    """
    result = lib.run_cmd([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
        "-af", f"ebur128=peak=true:framelog=quiet,{_loudnorm_first_pass_filter()}",
        "-f", "null", "-",
    ])
    text = f"{result.stdout or ''}\n{result.stderr or ''}"
    measured = _parse_loudnorm_json(text) if result.returncode == 0 else None
    ebur128_peak = _loudnorm_summary_value(text.split("Summary:")[-1], "Peak")
    try:
        integrated, true_peak = float(measured["input_i"]), float(measured["input_tp"])
    except (KeyError, TypeError, ValueError):
        log(f"  ⚠️ 成片真峰值测量失败: {path}")
        return None
    if ebur128_peak is not None:
        true_peak = max(true_peak, ebur128_peak)
    return {"integrated": integrated, "true_peak": true_peak}


def deliver_under_true_peak(output_path, peak_target, reencode_audio):
    """Re-encode the delivered audio until its true peak is under TP; returns (record, state).

    `reencode_audio(peak_target)` renders the audio again for the lower pre-encode target
    into `output_path` and returns its state; `state` is the last one (None when no
    correction ran). `record` holds the delivered `integrated` / `true_peak` (None when
    unmeasured), the final `peak_target_dbtp` and the number of `corrections`.
    """
    true_peak = float(CONFIG["target_true_peak"])
    delivered = delivered_loudness(output_path)
    state = None
    corrections = 0
    while (delivered is not None and delivered["true_peak"] > true_peak
           and corrections < CODEC_PEAK_MAX_CORRECTIONS):
        overshoot = delivered["true_peak"] - true_peak
        peak_target = round(peak_target - overshoot - CODEC_PEAK_MARGIN_DB, 2)
        log(f"  成片 AAC 真峰值 {delivered['true_peak']} dBTP 超过 {true_peak}，"
            f"编码前目标降到 {peak_target} dBTP 后重编码音频")
        state = reencode_audio(peak_target)
        corrections += 1
        delivered = delivered_loudness(output_path)
    return {
        "integrated": delivered["integrated"] if delivered else None,
        "true_peak": delivered["true_peak"] if delivered else None,
        "peak_target_dbtp": peak_target,
        "corrections": corrections,
    }, state


def reencode_audio_track(output_path, inputs, filter_complex, audio_map, video_duration,
                         work_dir):
    """Replace `output_path`'s audio with `audio_map` rendered from `inputs`, copying its video.

    `inputs` are the render's own input arguments, so `filter_complex` keeps its indices;
    the rendered file is appended as the last input. Returns ffmpeg's stderr.
    """
    output_path = Path(output_path)
    video_index = inputs.count("-i")
    candidate = output_path.with_name(f".{output_path.stem}.audio-reencode{output_path.suffix}")
    script = None
    if len(filter_complex.encode("utf-8")) > constants.FILTER_SCRIPT_THRESHOLD_BYTES:
        script = Path(work_dir) / ".filter_complex_reencode.txt"
        script.write_text(filter_complex, encoding="utf-8")
        filter_args = filter_file_args("filter_complex", script)
    else:
        filter_args = ["-filter_complex", filter_complex]
    cmd = [
        "ffmpeg", "-y", *inputs, "-i", str(output_path), *filter_args,
        "-map", f"{video_index}:v:0", "-map", audio_map, "-c:v", "copy",
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
        "-t", str(video_duration), str(candidate),
    ]
    try:
        result = lib.run_cmd(cmd)
        if result.returncode != 0:
            raise RuntimeError(f"成片音频重编码失败: {result.stderr}")
        os.replace(candidate, output_path)
    finally:
        candidate.unlink(missing_ok=True)
        if script is not None:
            script.unlink(missing_ok=True)
    return result.stderr
