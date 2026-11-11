"""Regression contract for explicit non-narration assembly audio modes."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from assemble import assemble_video  # noqa: E402
from lib import CONFIG  # noqa: E402
import timeline_emit  # noqa: E402


pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not available",
)


def _run(*args):
    subprocess.run(args, check=True, capture_output=True)


def _make_av(path):
    _run(
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=s=160x120:r=12:d=2.0",
        "-f", "lavfi", "-i", "sine=frequency=431:sample_rate=48000:d=2.0",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(path),
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


def test_source_mix_declared_missing_bgm_fails_closed(tmp_path, monkeypatch):
    _quiet_visuals(monkeypatch)
    source = tmp_path / "source.mp4"
    work = tmp_path / "work"
    work.mkdir()
    _make_av(source)
    monkeypatch.setitem(CONFIG, "bgm_path", str(tmp_path / "missing.wav"))
    with pytest.raises(RuntimeError, match="source-mix.*BGM.*不存在"):
        assemble_video(source, [], work, work / "output.mp4", audio_mode="source-mix")


def test_source_mix_timeline_uses_current_whole_input_and_flat_selected_audio(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    rendered = tmp_path / "rendered.mp4"
    rendered.write_bytes(b"rendered")
    (work / "clip_plan_validated.json").write_text(
        json.dumps({"clips": [{"source_path": "/wrong/original.mp4", "source_start": 8,
                                "source_end": 9, "output_start": 0, "output_end": 1}]}),
        encoding="utf-8",
    )
    monkeypatch.setitem(CONFIG, "idle_orig_volume", 0.25)
    monkeypatch.setattr(timeline_emit, "_timeline_subtitle_segments", lambda *_: [])

    timeline = timeline_emit._emit_timeline(
        rendered, [], work, 2.0, {"width": 160, "height": 120, "fps": 12}, False,
        audio_mode="source-mix",
    )

    video = timeline["tracks"][0]["clips"]
    assert len(video) == 1
    assert video[0]["source_path"] == str(rendered)
    assert video[0]["source_start"] == 0.0 and video[0]["source_end"] == 2.0
    assert video[0]["audio"] == {
        "role": "original", "volume_keyframes": [], "base_gain": 0.25,
    }
    assert "audio_delivery" not in timeline


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
    assert "packet_copy" not in qc["audio_operations"]


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


def test_source_mix_rejects_missing_audio_stream_before_render(tmp_path, monkeypatch):
    _quiet_visuals(monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    silent = tmp_path / "silent.mp4"
    _run(
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=s=160x120:r=12:d=2", "-c:v", "libx264", str(silent),
    )
    with pytest.raises(RuntimeError, match="找不到音频流 a:0"):
        assemble_video(silent, [], work, work / "silent-out.mp4", audio_mode="source-mix")
    assert not (work / "silent-out.mp4").exists()


def test_isolated_copied_skill_cli_renders_source_mix(tmp_path):
    source = tmp_path / "source.mp4"
    _make_av(source)
    copied = tmp_path / "copied-skill"
    shutil.copytree(SCRIPTS.parent, copied, ignore=shutil.ignore_patterns("__pycache__"))
    work = tmp_path / "cli-work"
    work.mkdir()
    delivery = tmp_path / "delivery"
    # -I drops caller/repo imports; only the copied scripts directory is importable.
    launcher = (
        "import runpy,sys;sys.path.insert(0,sys.argv[1]);sys.argv=sys.argv[2:];"
        "runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    command = [
        sys.executable, "-I", "-c", launcher, copied / "scripts",
        copied / "scripts/assemble.py", source, "--work-dir", work,
        "--audio-mode", "source-mix", "--no-burn-subtitles",
        "--output-dir", delivery, "--recap-stem", "copied",
    ]
    result = subprocess.run(tuple(map(str, command)), env=dict(os.environ), capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (delivery / "recap_copied.mp4").is_file()
    manifest = json.loads((work / "assembly_manifest.json").read_text(encoding="utf-8"))
    assert manifest["audio_mode"] == "source-mix"


def test_removed_stream_flag_fails_at_the_parser(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "assemble.py"), "v.mp4", "--work-dir", str(tmp_path),
         "--audio-mode", "source-mix", "--audio-stream-index", "1"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 2
    assert "--audio-stream-index" in result.stderr


def test_handoff_speech_evidence_ignores_an_old_clean_transcript_left_behind(tmp_path):
    import audio_mix

    (tmp_path / "asr_result.json").write_text(
        json.dumps([{"start": 0.0, "end": 2.0, "text": "原始转写"}]), encoding="utf-8")
    (tmp_path / "asr_clean.json").write_text(
        json.dumps({"segments": [{"start": 0.0, "end": 2.0, "text": "清洗旧稿"}]}), encoding="utf-8")
    assert [row["text"] for row in audio_mix._asr_segments(tmp_path)] == ["原始转写"]


def test_source_mix_rerun_in_its_own_work_dir_is_not_rejected(tmp_path):
    source = tmp_path / "source.mp4"
    _make_av(source)
    work = tmp_path / "work"
    work.mkdir()
    args = [
        sys.executable, str(SCRIPTS / "assemble.py"), str(source),
        "--work-dir", str(work), "--audio-mode", "source-mix", "--no-burn-subtitles",
        "--output-dir", str(tmp_path / "delivery"),
    ]
    assert subprocess.run(args, capture_output=True, text=True).returncode == 0
    second = subprocess.run(args, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr


def test_source_mix_with_tts_meta_is_refused_before_any_render(tmp_path, monkeypatch):
    import assemble

    monkeypatch.setattr(assemble.lib, "get_video_duration", lambda _p: pytest.fail("reached the render"))
    with pytest.raises(RuntimeError, match="tts_meta"):
        assemble_video(tmp_path / "in.mp4", [], tmp_path, tmp_path / "out.mp4",
                       audio_mode="source-mix", tts_meta_path=tmp_path / "tts_meta.json")
