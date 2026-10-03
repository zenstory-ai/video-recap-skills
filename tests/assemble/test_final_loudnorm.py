"""Final loudness pass: linear two-pass loudnorm really stays linear, and assembly QC records
the mode ffmpeg actually ran instead of the one that was asked for."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-assemble" / "scripts"))

import assemble  # noqa: E402
import audio_mix  # noqa: E402
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


def _linear_holds(measured, targets):
    """ffmpeg af_loudnorm's own test for staying in linear mode."""
    gained_peak = float(measured["input_tp"]) + targets["integrated"] - float(measured["input_i"])
    return gained_peak <= CONFIG["target_true_peak"] and float(measured["input_lra"]) <= targets["lra"]


def test_peak_without_headroom_caps_the_gain_just_enough():
    targets = audio_mix._linear_loudnorm_targets(_PEAKY)

    assert _linear_holds(_PEAKY, targets)
    # -17.20 + (-1.0 - -0.01) = -18.19 LUFS is the loudest linear target; 0.02 LU spare.
    assert -18.22 <= targets["integrated"] < -18.19
    assert targets["gain_capped_db"] == pytest.approx(-14.0 - targets["integrated"])
    assert targets["lra"] == 11.0 and targets["true_peak"] == -1.0


def test_enough_headroom_keeps_the_requested_target():
    measured = {**_PEAKY, "input_i": "-15.37", "input_tp": "-4.18"}
    targets = audio_mix._linear_loudnorm_targets(measured)
    assert targets["integrated"] == -14.0 and targets["gain_capped_db"] == 0.0
    assert _linear_holds(measured, targets)


def test_wide_loudness_range_raises_the_lra_target_linear_mode_never_applies():
    measured = {**_PEAKY, "input_tp": "-6.00", "input_lra": "16.90"}
    targets = audio_mix._linear_loudnorm_targets(measured)
    assert targets["lra"] == 16.9 and _linear_holds(measured, targets)


@pytest.mark.parametrize("override", [
    pytest.param({"input_lra": "25.00"}, id="lra-beyond-loudnorm-range"),
    pytest.param({"input_i": "-inf", "input_tp": "-inf"}, id="silence"),
    pytest.param({"input_thresh": "-70.00"}, id="ffmpeg-unmeasured-sentinel"),
    pytest.param({"input_i": None}, id="malformed"),
])
def test_no_linear_targets_when_linear_mode_is_impossible(override):
    assert audio_mix._linear_loudnorm_targets({**_PEAKY, **override}) is None


def test_second_pass_filter_uses_the_capped_targets():
    filt = audio_mix.final_loudnorm_filter(_PEAKY)
    targets = audio_mix._linear_loudnorm_targets(_PEAKY)
    assert filt.startswith(f"loudnorm=I={targets['integrated']}:TP=-1.0:LRA=11.0:linear=true")
    assert ":measured_I=-17.20:measured_TP=-0.01:" in filt
    assert filt.endswith("print_format=summary,alimiter=limit=0.98:level=false")
    # Without a measurement the single pass keeps the configured targets.
    assert audio_mix.final_loudnorm_filter().startswith("loudnorm=I=-14.0:TP=-1.0:LRA=11.0")


@pytest.mark.parametrize("kind, mode", [("Linear", "two_pass_linear"),
                                        ("Dynamic", "two_pass_dynamic")])
def test_final_pass_summary_decides_the_recorded_mode(kind, mode):
    report = audio_mix.loudnorm_final_pass("noise\n" + _SUMMARY.format(kind=kind), _PEAKY)
    assert report["normalization_type"] == kind.lower()
    assert report["output_integrated"] == -16.5 and report["output_true_peak"] == -1.0
    assert report["target"] == audio_mix._linear_loudnorm_targets(_PEAKY)
    assert audio_mix._loudness_mode(_PEAKY, report["normalization_type"]) == mode


def test_loudness_mode_without_a_summary_is_predicted():
    assert audio_mix._loudness_mode(_PEAKY) == "two_pass_linear"
    assert audio_mix._loudness_mode({**_PEAKY, "input_lra": "25.00"}) == "two_pass_dynamic"
    assert audio_mix._loudness_mode(None) == "equivalent"
    assert audio_mix.loudnorm_final_pass("", None) == {
        "normalization_type": None, "target": None,
        "output_integrated": None, "output_true_peak": None,
    }


# ── assemble records what ffmpeg ran (mocked ffmpeg) ────────────────────────────────


def _assemble_with_stderr(monkeypatch, tmp_path, stderr, measured):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    output = tmp_path / "output.mp4"

    def fake_run_cmd(cmd):
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
    monkeypatch.setattr(audio_mix, "_run_loudnorm_first_pass", lambda *a, **k: measured)
    monkeypatch.setattr(assemble.assembly_contract, "_placed_audio_matches_timeline",
                        lambda _seg: True)
    monkeypatch.setattr("assemble.lib.run_cmd", fake_run_cmd)
    assemble_video(video, [tts_segment(
        start=0.0, end=3.0, narration="响度。", audio_path=str(tmp_path / "narr.wav"),
        audio_duration=1.0,
    )], tmp_path, output)
    return json.loads((tmp_path / "assembly_qc.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind, mode", [("Linear", "two_pass_linear"),
                                        ("Dynamic", "two_pass_dynamic")])
def test_assembly_qc_records_the_mode_ffmpeg_reported(monkeypatch, tmp_path, kind, mode):
    qc = _assemble_with_stderr(monkeypatch, tmp_path, _SUMMARY.format(kind=kind), _PEAKY)
    assert qc["loudness_mode"] == mode
    assert qc["loudnorm_measurement"] == _PEAKY
    assert qc["loudnorm_final_pass"]["normalization_type"] == kind.lower()
    assert qc["loudnorm_final_pass"]["target"]["gain_capped_db"] > 0


def test_assembly_qc_without_first_pass_keeps_equivalent_and_reports_dynamic(
    monkeypatch, tmp_path
):
    qc = _assemble_with_stderr(monkeypatch, tmp_path, _SUMMARY.format(kind="Dynamic"), None)
    assert qc["loudness_mode"] == "equivalent"
    assert qc["loudnorm_final_pass"]["normalization_type"] == "dynamic"
    assert qc["loudnorm_final_pass"]["target"] is None


# ── real ffmpeg ─────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_real_peaky_mix_stays_linear_only_with_capped_gain(tmp_path):
    """A quiet bed with loud bursts: I=-14 would push the true peak past -1 dBTP, so
    ffmpeg ran dynamic while QC said two_pass_linear; the capped target stays linear."""
    wav = tmp_path / "peaky.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "aevalsrc='if(lt(mod(t,4),0.03),0.99*sin(2*PI*1000*t),0.08*sin(2*PI*300*t))'"
         ":s=48000:d=12,aformat=channel_layouts=stereo", str(wav)],
        check=True, capture_output=True,
    )

    def run(filt):
        return subprocess.run(["ffmpeg", "-hide_banner", "-i", str(wav), "-af", filt,
                               "-f", "null", "-"], check=True, capture_output=True,
                              text=True).stderr

    measured = audio_mix._parse_loudnorm_json(run(audio_mix._loudnorm_first_pass_filter()))
    uncapped = (f"loudnorm=I=-14.0:TP=-1.0:LRA=11.0:linear=true"
                f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
                f":measured_LRA={measured['input_lra']}"
                f":measured_thresh={measured['input_thresh']}:print_format=summary")
    assert audio_mix.loudnorm_final_pass(run(uncapped))["normalization_type"] == "dynamic"

    report = audio_mix.loudnorm_final_pass(
        run(audio_mix.final_loudnorm_filter(measured)), measured
    )
    assert report["normalization_type"] == "linear"
    assert report["target"]["gain_capped_db"] > 0
    assert report["output_true_peak"] <= -1.0 + 0.1
    assert audio_mix._loudness_mode(measured, report["normalization_type"]) == "two_pass_linear"
