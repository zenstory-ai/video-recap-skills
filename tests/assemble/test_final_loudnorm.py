"""Final loudness stage: a true-peak limiter makes room for one linear gain to TARGET_LUFS,
loudnorm really stays linear, and assembly QC records what ran instead of what was asked."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-assemble" / "scripts"))

import assemble  # noqa: E402
import loudness  # noqa: E402
import media  # noqa: E402
import narration_audio  # noqa: E402
import timeline_emit  # noqa: E402
from assemble import assemble_video  # noqa: E402
from lib import CONFIG  # noqa: E402
from tts_fixtures import tts_segment  # noqa: E402

_HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

# A real first-pass measurement (anchor_verify run): +3.2 LU of gain would put the true
# peak at +3.2 dBTP, so `linear=true` with I=-14 silently ran dynamic.
_PEAKY = {"input_i": "-17.20", "input_tp": "-0.01", "input_lra": "6.80",
          "input_thresh": "-27.48", "output_i": "-14.29", "output_tp": "-1.00",
          "output_lra": "4.80", "output_thresh": "-24.50",
          "normalization_type": "dynamic", "target_offset": "1.17"}
_SUMMARY = """[Parsed_loudnorm_0 @ 0x7f9a57f04200]
Input Integrated:    -23.1 LUFS
Input True Peak:      -3.1 dBTP
Input LRA:             4.5 LU
Input Threshold:     -33.1 LUFS

Output Integrated:   -16.5 LUFS
Output True Peak:     -1.0 dBTP
Output LRA:            1.6 LU
Output Threshold:    -26.5 LUFS

Normalization Type:   {kind}
Target Offset:        +2.5 LU
"""


@pytest.fixture(autouse=True)
def _targets(monkeypatch):
    monkeypatch.setitem(CONFIG, "final_loudnorm", True)
    monkeypatch.setitem(CONFIG, "target_lufs", -14.0)
    monkeypatch.setitem(CONFIG, "target_true_peak", -1.0)
    monkeypatch.setitem(CONFIG, "target_lra", 11.0)
    monkeypatch.setitem(CONFIG, "loudness_limiter_max_db", 6.0)


def _linear_holds(measured, targets):
    """ffmpeg af_loudnorm's own test for staying in linear mode."""
    gained_peak = float(measured["input_tp"]) + targets["integrated"] - float(measured["input_i"])
    return gained_peak <= CONFIG["target_true_peak"] and float(measured["input_lra"]) <= targets["lra"]


def test_peak_without_headroom_caps_the_gain_just_enough():
    targets = loudness._linear_loudnorm_targets(_PEAKY)

    assert _linear_holds(_PEAKY, targets)
    # -17.20 + (-1.0 - -0.01) = -18.19 LUFS is the loudest linear target; 0.02 LU spare.
    assert -18.22 <= targets["integrated"] < -18.19
    assert targets["gain_capped_db"] == pytest.approx(-14.0 - targets["integrated"])
    assert targets["lra"] == 11.0 and targets["true_peak"] == -1.0


def test_enough_headroom_keeps_the_requested_target():
    measured = {**_PEAKY, "input_i": "-15.37", "input_tp": "-4.18"}
    targets = loudness._linear_loudnorm_targets(measured)
    assert targets["integrated"] == -14.0 and targets["gain_capped_db"] == 0.0
    assert _linear_holds(measured, targets)


def test_wide_loudness_range_raises_the_lra_target_linear_mode_never_applies():
    measured = {**_PEAKY, "input_tp": "-6.00", "input_lra": "16.90"}
    targets = loudness._linear_loudnorm_targets(measured)
    assert targets["lra"] == 16.9 and _linear_holds(measured, targets)


@pytest.mark.parametrize("override", [
    pytest.param({"input_lra": "25.00"}, id="lra-beyond-loudnorm-range"),
    pytest.param({"input_i": "-inf", "input_tp": "-inf"}, id="silence"),
    pytest.param({"input_thresh": "-70.00"}, id="ffmpeg-unmeasured-sentinel"),
    pytest.param({"input_i": None}, id="malformed"),
])
def test_no_linear_targets_when_linear_mode_is_impossible(override):
    assert loudness._linear_loudnorm_targets({**_PEAKY, **override}) is None


def test_second_pass_filter_without_limiter_uses_the_capped_targets():
    filt = loudness.final_loudnorm_filter(_PEAKY)
    targets = loudness._linear_loudnorm_targets(_PEAKY)
    assert filt.startswith(f"loudnorm=I={targets['integrated']}:TP=-1.0:LRA=11.0:linear=true")
    assert ":measured_I=-17.20:measured_TP=-0.01:" in filt
    assert filt.endswith("print_format=summary,alimiter=limit=0.98:level=false")
    # Without a measurement the single pass keeps the configured targets.
    assert loudness.final_loudnorm_filter().startswith("loudnorm=I=-14.0:TP=-1.0:LRA=11.0")


# ── true-peak limiter ahead of the linear gain ──────────────────────────────────────

# What a limited pass measures after `_peak_limiter_chain`: peaks at the -2 dBTP ceiling.
_LIMITED = {**_PEAKY, "input_i": "-14.30", "input_tp": "-2.00", "input_lra": "6.70",
            "input_thresh": "-24.60", "target_offset": "0.10"}


def test_limiter_takes_the_peaks_the_full_gain_would_push_over_true_peak():
    plan = loudness._peak_limiter_plan(_PEAKY)
    # +3.2 dB reaches -14 LUFS; the -0.01 dBTP peak then sits 5.19 dB over the ceiling.
    assert plan == {"pre_gain_db": 3.2, "ceiling_dbtp": -2.0,
                    "required_reduction_db": 5.19, "max_reduction_db": 6.0}


def test_limiter_stops_at_the_maximum_reduction_and_the_rest_is_capped():
    measured = {**_PEAKY, "input_i": "-21.19", "input_tp": "0.80"}
    plan = loudness._peak_limiter_plan(measured)
    assert plan["required_reduction_db"] == 9.99
    # Pre-gain stops where the limiter removes exactly 6 dB: 0.80 + 3.2 = -2.0 + 6.
    assert plan["pre_gain_db"] == 3.2


@pytest.mark.parametrize("measured, max_db", [
    pytest.param({**_PEAKY, "input_tp": "-6.00"}, 6.0, id="linear-gain-fits"),
    pytest.param(_PEAKY, 0.0, id="limiter-disabled"),
    pytest.param({**_PEAKY, "input_lra": "25.00"}, 6.0, id="lra-beyond-linear-mode"),
    pytest.param({**_PEAKY, "input_i": "-inf"}, 6.0, id="unmeasured"),
])
def test_no_limiter_when_it_cannot_help(monkeypatch, measured, max_db):
    monkeypatch.setitem(CONFIG, "loudness_limiter_max_db", max_db)
    assert loudness._peak_limiter_plan(measured) is None


@pytest.mark.parametrize("latency, suffix", [(True, ":latency=true"), (False, "")])
def test_limiter_chain_is_gain_then_oversampled_lookahead_limiter(monkeypatch, latency, suffix):
    monkeypatch.setattr(loudness, "_alimiter_compensates_latency", lambda: latency)
    chain = loudness._peak_limiter_chain(loudness._peak_limiter_plan(_PEAKY))
    assert chain == (
        "volume=3.2dB,aresample=192000,alimiter=limit=0.794328:attack=5:release=100"
        f":level=false{suffix},aresample=48000"
    )


def test_limited_second_pass_measures_and_gains_the_limited_signal():
    limiter = {**loudness._peak_limiter_plan(_PEAKY), "reduction_db": 5.19,
               "measurement": _LIMITED}
    filt = loudness.final_loudnorm_filter(_PEAKY, limiter)
    chain, _, rest = filt.partition(",loudnorm=")
    assert chain == loudness._peak_limiter_chain(limiter)
    # -14.30 LUFS at -2.00 dBTP: the last 0.3 LU of gain fits under -1 dBTP, so the
    # requested target is reached and the stage stays linear.
    assert rest.startswith("I=-14.0:TP=-1.0:LRA=11.0:linear=true:measured_I=-14.30"
                           ":measured_TP=-2.00:")
    assert filt.endswith("print_format=summary,alimiter=limit=0.98:level=false")
    assert loudness._loudness_mode(_PEAKY, limiter=limiter) == "two_pass_linear_peak_limited"
    assert loudness._loudness_mode(_PEAKY, "dynamic", limiter) == "two_pass_dynamic"
    report = loudness.loudnorm_final_pass(_SUMMARY.format(kind="Linear"), _PEAKY, limiter)
    assert report["peak_limiter"] is limiter
    assert report["target"]["integrated"] == -14.0 and report["target"]["gain_capped_db"] == 0


def _plan_with(monkeypatch, *measurements):
    calls = []
    results = iter(measurements)

    def fake_measure(*args, pre_chain=None):
        calls.append(pre_chain)
        return next(results)

    monkeypatch.setattr(loudness, "_measure_loudness", fake_measure)
    return loudness.plan_final_loudness("v", "n", [], [], "[0:a]anull[aout]", "."), calls


def test_plan_measures_the_limited_signal_and_records_the_real_reduction(monkeypatch):
    (measured, limiter), calls = _plan_with(monkeypatch, _PEAKY, _LIMITED)
    assert measured == _PEAKY
    assert calls == [None, loudness._peak_limiter_chain(limiter)]
    assert limiter["measurement"] == _LIMITED
    # -0.01 dBTP + 3.2 dB in, -2.00 dBTP measured out.
    assert limiter["reduction_db"] == 5.19


def test_plan_without_limiter_when_one_gain_fits(monkeypatch):
    fits = {**_PEAKY, "input_tp": "-6.00"}
    (measured, limiter), calls = _plan_with(monkeypatch, fits)
    assert (measured, limiter, calls) == (fits, None, [None])


@pytest.mark.parametrize("limited", [None, {**_LIMITED, "input_lra": "25.00"}],
                         ids=["unmeasured", "not-linear"])
def test_plan_falls_back_to_the_capped_gain_when_the_limited_pass_fails(monkeypatch, limited):
    (measured, limiter), calls = _plan_with(monkeypatch, _PEAKY, limited)
    assert measured == _PEAKY and limiter is None and len(calls) == 2


@pytest.mark.parametrize("kind, mode", [("Linear", "two_pass_linear"),
                                        ("Dynamic", "two_pass_dynamic")])
def test_final_pass_summary_decides_the_recorded_mode(kind, mode):
    report = loudness.loudnorm_final_pass("noise\n" + _SUMMARY.format(kind=kind), _PEAKY)
    assert report["normalization_type"] == kind.lower()
    assert report["output_integrated"] == -16.5 and report["output_true_peak"] == -1.0
    assert report["target"] == loudness._linear_loudnorm_targets(_PEAKY)
    assert loudness._loudness_mode(_PEAKY, report["normalization_type"]) == mode


def test_loudness_mode_without_a_summary_is_predicted():
    assert loudness._loudness_mode(_PEAKY) == "two_pass_linear"
    assert loudness._loudness_mode({**_PEAKY, "input_lra": "25.00"}) == "two_pass_dynamic"
    assert loudness._loudness_mode(None) == "equivalent"
    assert loudness.loudnorm_final_pass("", None) == {
        "normalization_type": None, "target": None,
        "output_integrated": None, "output_true_peak": None, "peak_limiter": None,
    }


# ── assemble records what ffmpeg ran (mocked ffmpeg) ────────────────────────────────


def _assemble_with_stderr(monkeypatch, tmp_path, stderr, measured, limiter=None):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    output = tmp_path / "output.mp4"
    commands = []

    def fake_run_cmd(cmd):
        commands.append(cmd)
        output.write_bytes(b"mp4")
        return CompletedProcess(cmd, 0, stdout="", stderr=stderr)

    monkeypatch.setitem(CONFIG, "burn_subtitles", False)
    monkeypatch.setitem(CONFIG, "bgm_path", "")
    monkeypatch.setitem(CONFIG, "export_jianying", False)
    monkeypatch.setattr(assemble.lib, "get_video_duration", lambda _p: 4.0)
    monkeypatch.setattr(media, "_probe_canvas", lambda _p: media._canvas_from_stream({
        "width": 1280, "height": 720, "r_frame_rate": "30/1",
        "sample_aspect_ratio": "1:1", "display_aspect_ratio": "16:9",
    }))
    monkeypatch.setattr(media, "_has_audio_stream", lambda _p: True)
    monkeypatch.setattr(media, "_probe_video_format", lambda _p: {
        "codec_name": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 720})
    monkeypatch.setattr(narration_audio, "_apply_narration_speed", lambda *a, **k: None)

    def fake_timed(segments, out, duration, wd):
        for seg in segments:
            seg.update(fit_status="fits", placed_audio_duration=1.0,
                       actual_place_start=0.0, actual_place_end=1.0)
        Path(out).write_bytes(b"n")

    monkeypatch.setattr(narration_audio, "_build_timed_narration", fake_timed)
    monkeypatch.setattr(timeline_emit, "_emit_timeline", lambda *a, **k: None)
    monkeypatch.setattr(loudness, "plan_final_loudness", lambda *a, **k: (measured, limiter))
    monkeypatch.setattr(assemble.assembly_contract, "_placed_audio_matches_timeline",
                        lambda _seg: True)
    monkeypatch.setattr("assemble.lib.run_cmd", fake_run_cmd)
    assemble_video(video, [tts_segment(
        start=0.0, end=3.0, narration="响度。", audio_path=str(tmp_path / "narr.wav"),
        audio_duration=1.0,
    )], tmp_path, output)
    qc = json.loads((tmp_path / "assembly_qc.json").read_text(encoding="utf-8"))
    return qc, commands


@pytest.mark.parametrize("kind, mode", [("Linear", "two_pass_linear"),
                                        ("Dynamic", "two_pass_dynamic")])
def test_assembly_qc_records_the_mode_ffmpeg_reported(monkeypatch, tmp_path, kind, mode):
    qc, _ = _assemble_with_stderr(monkeypatch, tmp_path, _SUMMARY.format(kind=kind), _PEAKY)
    assert qc["loudness_mode"] == mode
    assert qc["loudnorm_measurement"] == _PEAKY
    assert qc["loudnorm_final_pass"]["normalization_type"] == kind.lower()
    assert qc["loudnorm_final_pass"]["target"]["gain_capped_db"] > 0
    assert qc["loudnorm_final_pass"]["peak_limiter"] is None


def test_assembly_qc_records_the_limiter_and_renders_through_it(monkeypatch, tmp_path):
    limiter = {**loudness._peak_limiter_plan(_PEAKY), "reduction_db": 5.19,
               "measurement": _LIMITED}
    qc, commands = _assemble_with_stderr(
        monkeypatch, tmp_path, _SUMMARY.format(kind="Linear"), _PEAKY, limiter
    )
    assert qc["loudness_mode"] == "two_pass_linear_peak_limited"
    final_pass = qc["loudnorm_final_pass"]
    assert final_pass["peak_limiter"] == limiter
    assert final_pass["target"]["integrated"] == -14.0
    graph = next(arg for cmd in commands for arg in cmd if "[aoutln]" in str(arg)
                 and "loudnorm" in str(arg))
    assert f"[aout]{loudness.final_loudnorm_filter(_PEAKY, limiter)}[aoutln]" in graph


def test_assembly_qc_without_first_pass_keeps_equivalent_and_reports_dynamic(
    monkeypatch, tmp_path
):
    qc, _ = _assemble_with_stderr(
        monkeypatch, tmp_path, _SUMMARY.format(kind="Dynamic"), None
    )
    assert qc["loudness_mode"] == "equivalent"
    assert qc["loudnorm_final_pass"]["normalization_type"] == "dynamic"
    assert qc["loudnorm_final_pass"]["target"] is None


# ── real ffmpeg ─────────────────────────────────────────────────────────────────────


def _peaky_signal(tmp_path, bed):
    """12 s of a steady 300 Hz bed with a 30 ms near-full-scale 1 kHz burst every 4 s."""
    wav = tmp_path / f"peaky_{bed}.flac"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         f"aevalsrc='if(lt(mod(t,4),0.03),0.99*sin(2*PI*1000*t),{bed}*sin(2*PI*300*t))'"
         ":s=48000:d=12,aformat=channel_layouts=stereo", str(wav)],
        check=True, capture_output=True,
    )
    return wav


def _render(wav, filt, out):
    """Run `filt` over `wav` into `out`; return ffmpeg's stderr (the loudnorm summary)."""
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-y", "-i", str(wav), "-af", filt, "-ar", "48000",
         "-c:a", "pcm_f32le", str(out)],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stderr


def _ebur128(path):
    """Integrated loudness and true peak of `path`, measured independently of loudnorm."""
    text = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true:framelog=quiet",
         "-f", "null", "-"],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stderr.split("Summary:")[-1]
    return (loudness._loudnorm_summary_value(text, "I"),
            loudness._loudnorm_summary_value(text, "Peak"))


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_real_peaky_mix_reaches_the_target_linearly_behind_the_limiter(tmp_path):
    """A bed with near-full-scale bursts: one gain to -14 LUFS would put the true peak about
    +4 dBTP, so loudnorm either ran dynamic or the gain was capped ~5 LU short. Limited
    first, the linear stage reaches the target with the true peak under -1 dBTP."""
    wav = _peaky_signal(tmp_path, 0.15)
    measured, limiter = loudness.plan_final_loudness(
        wav, wav, [], [], "[0:a]anull[aout]", tmp_path
    )
    uncapped = (f"loudnorm=I=-14.0:TP=-1.0:LRA=11.0:linear=true"
                f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
                f":measured_LRA={measured['input_lra']}"
                f":measured_thresh={measured['input_thresh']}:print_format=summary")
    assert loudness.loudnorm_final_pass(
        _render(wav, uncapped, tmp_path / "dynamic.wav"))["normalization_type"] == "dynamic"
    assert limiter is not None and 0 < limiter["required_reduction_db"] <= 6.0

    out = tmp_path / "limited.wav"
    report = loudness.loudnorm_final_pass(
        _render(wav, loudness.final_loudnorm_filter(measured, limiter), out), measured, limiter
    )
    assert report["normalization_type"] == "linear"
    assert loudness._loudness_mode(measured, "linear", limiter) == "two_pass_linear_peak_limited"
    assert report["target"]["gain_capped_db"] == 0
    integrated, true_peak = _ebur128(out)
    assert abs(integrated - -14.0) <= 1.0
    assert true_peak <= -1.0
    assert limiter["reduction_db"] == pytest.approx(limiter["required_reduction_db"], abs=0.5)


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_real_mix_beyond_the_limiter_maximum_caps_only_the_remainder(tmp_path):
    """A quieter bed needs ~8 dB of limiting; the limiter takes its 6 dB maximum, the rest
    of the gain is capped, and the stage still stays linear under -1 dBTP while landing
    several LU louder than capping the whole gain (the old behaviour)."""
    wav = _peaky_signal(tmp_path, 0.08)
    measured, limiter = loudness.plan_final_loudness(
        wav, wav, [], [], "[0:a]anull[aout]", tmp_path
    )
    assert limiter["required_reduction_db"] > 6.0
    assert limiter["reduction_db"] == pytest.approx(6.0, abs=0.5)
    out = tmp_path / "capped.wav"
    report = loudness.loudnorm_final_pass(
        _render(wav, loudness.final_loudnorm_filter(measured, limiter), out), measured, limiter
    )
    assert report["normalization_type"] == "linear"
    capped = report["target"]["gain_capped_db"]
    gain_only_cap = loudness._linear_loudnorm_targets(measured)["gain_capped_db"]
    assert 0 < capped < gain_only_cap - 3.0
    integrated, true_peak = _ebur128(out)
    assert abs(integrated - report["target"]["integrated"]) <= 1.0
    assert true_peak <= -1.0
