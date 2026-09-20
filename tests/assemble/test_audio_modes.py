"""Regression contract for explicit non-narration assembly audio modes."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "video-assemble" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from assemble import assemble_video  # noqa: E402
from assembly_settings import assembly_settings_fingerprint  # noqa: E402
import frozen_audio  # noqa: E402
from frozen_audio import probe_audio_packets, verify_adopted_audio  # noqa: E402
from lib import CONFIG  # noqa: E402
import timeline_emit  # noqa: E402


pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not available",
)


def _run(*args):
    subprocess.run(args, check=True, capture_output=True)


def _make_av(path, *, video_seconds=2.0, audio_seconds=None, codec="aac"):
    audio_seconds = video_seconds if audio_seconds is None else audio_seconds
    _run(
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=s=160x120:r=12:d={video_seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=431:sample_rate=48000:d={audio_seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", codec, str(path),
    )


def _quiet_visuals(monkeypatch):
    monkeypatch.setitem(CONFIG, "burn_subtitles", False)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", False)
    monkeypatch.setitem(CONFIG, "subtitle_original_in_gaps", False)
    monkeypatch.setitem(CONFIG, "output_max_height", 0)
    monkeypatch.setitem(CONFIG, "bgm_path", "")


def _qc(work):
    return json.loads((work / "assembly_qc.json").read_text(encoding="utf-8"))


def test_default_narration_mode_still_rejects_empty_tts(tmp_path):
    with pytest.raises(RuntimeError, match="没有有效解说音频"):
        assemble_video(tmp_path / "missing.mp4", [], tmp_path, tmp_path / "out.mp4")


def test_nondefault_modes_reject_misleading_or_unsupported_arguments(tmp_path, monkeypatch):
    _quiet_visuals(monkeypatch)
    source = tmp_path / "source.mp4"
    work = tmp_path / "work"
    work.mkdir()
    _make_av(source)
    with pytest.raises(RuntimeError, match="source-mix.*TTS"):
        assemble_video(source, [{"index": 0}], work, work / "source.mp4", audio_mode="source-mix")
    with pytest.raises(RuntimeError, match="narration.*audio_stream_index"):
        assemble_video(source, [{"index": 0}], work, work / "narr.mp4",
                       audio_mode="narration", audio_stream_index=1)
    with pytest.raises(RuntimeError, match="非负整数"):
        assemble_video(source, [], work, work / "bool.mp4",
                       audio_mode="adopted-packet-copy", audio_stream_index=True)
    with pytest.raises(RuntimeError, match="非负整数"):
        probe_audio_packets(source, True)


def test_source_mix_declared_missing_bgm_fails_closed(tmp_path, monkeypatch):
    _quiet_visuals(monkeypatch)
    source = tmp_path / "source.mp4"
    work = tmp_path / "work"
    work.mkdir()
    _make_av(source)
    monkeypatch.setitem(CONFIG, "bgm_path", str(tmp_path / "missing.wav"))
    with pytest.raises(RuntimeError, match="source-mix.*BGM.*不存在"):
        assemble_video(source, [], work, work / "output.mp4", audio_mode="source-mix")


def test_adopted_timeline_uses_current_whole_input_and_flat_selected_audio(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    rendered = tmp_path / "rendered.mp4"
    rendered.write_bytes(b"rendered")
    (work / "clip_plan_validated.json").write_text(
        json.dumps({"clips": [{"source_path": "/wrong/original.mp4", "source_start": 8,
                                "source_end": 9, "output_start": 0, "output_end": 1}]}),
        encoding="utf-8",
    )
    monkeypatch.setitem(CONFIG, "ducking_mode", "fixed")
    monkeypatch.setitem(CONFIG, "idle_orig_volume", 0.25)
    monkeypatch.setattr(timeline_emit, "_timeline_subtitle_segments", lambda *_: [])

    timeline = timeline_emit._emit_timeline(
        rendered, [], work, 2.0, {"width": 160, "height": 120, "fps": 12}, False,
        audio_mode="adopted-packet-copy", selected_audio_stream=0,
    )

    video = timeline["tracks"][0]["clips"]
    assert len(video) == 1
    assert video[0]["source_path"] == str(rendered)
    assert video[0]["source_start"] == 0.0 and video[0]["source_end"] == 2.0
    assert video[0]["audio"] == {
        "role": "original", "volume_keyframes": [], "base_gain": 1.0,
        "selected_stream": 0, "mode": "adopted-packet-copy",
    }
    assert timeline["audio_delivery"]["packet_frozen"] is True
    assert timeline["audio_delivery"]["reconstructable"] is True


def test_optional_editor_export_rejects_nonzero_selected_stream(tmp_path, monkeypatch):
    _quiet_visuals(monkeypatch)
    monkeypatch.setitem(CONFIG, "export_jianying", True)
    source = tmp_path / "source.mp4"
    work = tmp_path / "work"
    work.mkdir()
    _make_av(source)
    with pytest.raises(RuntimeError, match="剪映.*音频流"):
        assemble_video(source, [], work, work / "output.mp4",
                       audio_mode="adopted-packet-copy", audio_stream_index=1)


def test_source_mix_succeeds_without_tts_and_reports_actual_operations(tmp_path, monkeypatch):
    _quiet_visuals(monkeypatch)
    monkeypatch.setitem(CONFIG, "final_loudnorm", True)
    source = tmp_path / "source.mp4"
    work = tmp_path / "work"
    work.mkdir()
    _make_av(source)

    output = assemble_video(source, [], work, work / "output.mp4", audio_mode="source-mix")

    assert output.exists()
    qc = _qc(work)
    assert qc["verdict"] == "PASS"
    assert qc["audio_mode"] == "source-mix"
    assert "missing_narration" not in qc["blocking_codes"]
    assert qc["audio_operations"]["narration"] is False
    assert qc["audio_operations"]["loudness_normalization"] is True
    assert qc["audio_operations"]["packet_copy"] is False


def test_source_mix_executes_declared_bgm_and_volume_processing(tmp_path, monkeypatch):
    _quiet_visuals(monkeypatch)
    monkeypatch.setitem(CONFIG, "final_loudnorm", False)
    monkeypatch.setitem(CONFIG, "idle_orig_volume", 0.4)
    source = tmp_path / "source.mp4"
    bgm = tmp_path / "bgm.wav"
    work = tmp_path / "work"
    work.mkdir()
    _make_av(source)
    _run("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=910:d=1", str(bgm))
    monkeypatch.setitem(CONFIG, "bgm_path", str(bgm))

    assemble_video(source, [], work, work / "output.mp4", audio_mode="source-mix")

    qc = _qc(work)
    assert qc["verdict"] == "PASS"
    assert qc["audio_operations"]["source_mix"] is True
    assert qc["audio_operations"]["bgm_mix"] is True
    assert qc["audio_operations"]["loudness_normalization"] is False
    assert qc["audio_operations"]["limiter"] is True


def test_source_mix_cli_does_not_read_ambient_tts_meta(tmp_path):
    source = tmp_path / "source.mp4"
    work = tmp_path / "work"
    delivery = tmp_path / "delivery"
    work.mkdir()
    _make_av(source)
    (work / "tts_meta.json").write_text("not-json", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "assemble.py"), str(source),
            "--work-dir", str(work), "--output-dir", str(delivery),
            "--audio-mode", "source-mix", "--no-burn-subtitles",
        ],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (delivery / "recap_source.mp4").exists()
    manifest = json.loads((work / "assembly_manifest.json").read_text(encoding="utf-8"))
    assert manifest["tts_meta"] is None
    assert manifest["tts_segments"] == 0
    assert manifest["audio_mode"] == "source-mix"


def test_adopted_copy_with_video_reencode_preserves_all_selected_aac_packets(tmp_path, monkeypatch):
    _quiet_visuals(monkeypatch)
    monkeypatch.setitem(CONFIG, "force_video_reencode", True)
    source = tmp_path / "source.mp4"
    work = tmp_path / "work"
    work.mkdir()
    _make_av(source)

    output = assemble_video(
        source, [], work, work / "output.mp4",
        audio_mode="adopted-packet-copy", audio_stream_index=0,
    )

    before = probe_audio_packets(source, 0)
    after = probe_audio_packets(output, 0)
    assert before["packet_count"] == after["packet_count"]
    assert before["payload_sha256"] == after["payload_sha256"]
    assert before["packets"] == after["packets"]
    qc = _qc(work)
    assert qc["verdict"] == "PASS"
    assert qc["audio_mode"] == "adopted-packet-copy"
    assert qc["adopted_audio"]["verified"] is True
    assert qc["adopted_audio"]["input"]["codec"] == "aac"
    assert qc["adopted_audio"]["selected_audio_stream_index"] == 0
    assert qc["audio_operations"] == {
        "narration": False,
        "source_mix": False,
        "bgm_mix": False,
        "ducking": False,
        "loudness_normalization": False,
        "limiter": False,
        "resample": False,
        "tempo": False,
        "packet_copy": True,
    }


@pytest.mark.parametrize("conflict", ["tts", "bgm"])
def test_adopted_copy_rejects_explicit_audio_processing(tmp_path, monkeypatch, conflict):
    _quiet_visuals(monkeypatch)
    source = tmp_path / "source.mp4"
    work = tmp_path / "work"
    work.mkdir()
    _make_av(source)
    segments = [{"index": 0}] if conflict == "tts" else []
    if conflict == "bgm":
        bgm = tmp_path / "bgm.wav"
        _run("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", str(bgm))
        monkeypatch.setitem(CONFIG, "bgm_path", str(bgm))

    with pytest.raises(RuntimeError, match="adopted-packet-copy.*(?:TTS|BGM)"):
        assemble_video(
            source, segments, work, work / "output.mp4",
            audio_mode="adopted-packet-copy", audio_stream_index=0,
        )


def test_adopted_copy_rejects_missing_wrong_stream_and_duration_mismatch(tmp_path, monkeypatch):
    _quiet_visuals(monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    silent = tmp_path / "silent.mp4"
    _run(
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=s=160x120:r=12:d=2", "-c:v", "libx264", str(silent),
    )
    with pytest.raises(RuntimeError, match="音频流"):
        assemble_video(silent, [], work, work / "silent-out.mp4",
                       audio_mode="adopted-packet-copy", audio_stream_index=0)

    source = tmp_path / "source.mp4"
    _make_av(source)
    with pytest.raises(RuntimeError, match="音频流"):
        assemble_video(source, [], work, work / "wrong-out.mp4",
                       audio_mode="adopted-packet-copy", audio_stream_index=3)

    mismatch = tmp_path / "mismatch.mp4"
    _make_av(mismatch, video_seconds=1.0, audio_seconds=2.0)
    with pytest.raises(RuntimeError, match="时长.*不兼容"):
        assemble_video(mismatch, [], work, work / "mismatch-out.mp4",
                       audio_mode="adopted-packet-copy", audio_stream_index=0)

    unsupported = tmp_path / "unsupported.mp4"
    _make_av(unsupported, codec="mp3")
    with pytest.raises(RuntimeError, match="只支持.*AAC.*mp3"):
        assemble_video(unsupported, [], work, work / "unsupported-out.mp4",
                       audio_mode="adopted-packet-copy", audio_stream_index=0)


def test_adopted_audio_verifier_detects_output_tampering(tmp_path):
    source = tmp_path / "source.mp4"
    copied = tmp_path / "copied.mp4"
    tampered = tmp_path / "tampered.mp4"
    _make_av(source)
    _run("ffmpeg", "-y", "-loglevel", "error", "-i", str(source),
         "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", str(copied))
    evidence = verify_adopted_audio(source, copied, 0)
    assert evidence["verified"] is True
    _run("ffmpeg", "-y", "-loglevel", "error", "-i", str(copied),
         "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "64k", str(tampered))

    with pytest.raises(RuntimeError, match="payload"):
        verify_adopted_audio(source, tampered, 0)


def test_adopted_settings_fingerprint_ignores_narration_mix_defaults(tmp_path, monkeypatch):
    _quiet_visuals(monkeypatch)
    first = assembly_settings_fingerprint(tmp_path, audio_mode="adopted-packet-copy", audio_stream_index=0)
    monkeypatch.setitem(CONFIG, "narration_speed", 1.91)
    monkeypatch.setitem(CONFIG, "ducking_orig_volume", 0.01)
    monkeypatch.setitem(CONFIG, "final_loudnorm", not CONFIG["final_loudnorm"])
    second = assembly_settings_fingerprint(tmp_path, audio_mode="adopted-packet-copy", audio_stream_index=0)
    assert first == second
    assert first["audio"] == {"mode": "adopted-packet-copy", "selected_stream_index": 0}
    assert assembly_settings_fingerprint(tmp_path, audio_mode="narration") != first


def _mock_packet_probe_payload(*, extradata=True):
    stream = {
        "index": 1,
        "codec_name": "aac",
        "time_base": "1/48000",
        "start_time": "0.000000",
        "duration": "0.021333",
        "sample_rate": "48000",
        "channels": 2,
        "channel_layout": "stereo",
    }
    if extradata:
        stream["extradata_hash"] = "SHA256:" + "ab" * 32
    return {
        "streams": [stream],
        "packets": [{
            "pts": -1024,
            "dts": -1024,
            "duration": 1024,
            "size": "7",
            "data_hash": "SHA256:" + "cd" * 32,
            "side_data_list": [{
                "side_data_type": "Skip Samples",
                "skip_samples": 1024,
                "discard_padding": 211,
                "skip_reason": 0,
                "discard_reason": 0,
            }],
        }],
    }


def test_packet_probe_captures_decoder_extradata_and_packet_side_data(monkeypatch):
    monkeypatch.setattr(frozen_audio, "_probe", lambda *_args: _mock_packet_probe_payload())

    identity = frozen_audio.probe_audio_packets("unused.mp4", 0)

    assert identity["decoder"] == {
        "codec": "aac",
        "sample_rate": 48000,
        "channels": 2,
        "channel_layout": "stereo",
        "extradata_sha256": "ab" * 32,
    }
    assert identity["packets"][0]["side_data_list"] == [{
        "side_data_type": "Skip Samples",
        "skip_samples": 1024,
        "discard_padding": 211,
        "skip_reason": 0,
        "discard_reason": 0,
    }]


def test_packet_probe_rejects_aac_without_decoder_extradata(monkeypatch):
    monkeypatch.setattr(
        frozen_audio, "_probe", lambda *_args: _mock_packet_probe_payload(extradata=False)
    )

    with pytest.raises(RuntimeError, match="AAC.*extradata"):
        frozen_audio.probe_audio_packets("unused.mp4", 0)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("decoder", "sample_rate", 44100), "decoder"),
        (("decoder", "channels", 1), "decoder"),
        (("decoder", "extradata_sha256", "ef" * 32), "decoder"),
        (("packet_side_data", "discard_padding", 0), "side data"),
    ],
)
def test_verifier_rejects_mutated_decoder_or_packet_side_data(monkeypatch, mutation, message):
    monkeypatch.setattr(frozen_audio, "_probe", lambda *_args: _mock_packet_probe_payload())
    expected = frozen_audio.probe_audio_packets("input.mp4", 0)
    actual = json.loads(json.dumps(expected))
    if mutation[0] == "decoder":
        actual["decoder"][mutation[1]] = mutation[2]
        actual[mutation[1]] = mutation[2]
    else:
        actual["packets"][0]["side_data_list"][0][mutation[1]] = mutation[2]
    monkeypatch.setattr(
        frozen_audio,
        "probe_audio_packets",
        lambda path, _stream: expected if path == "input.mp4" else actual,
    )

    with pytest.raises(RuntimeError, match=message):
        frozen_audio.verify_adopted_audio("input.mp4", "output.mp4", 0)
