import json
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "skills"
        / "video-understanding"
        / "scripts"
    ),
)
import asr  # noqa: E402
import extract  # noqa: E402
import understanding_runner as understand  # noqa: E402


def test_segment_cut_failure_yields_empty_text_not_stale_transcription(
    monkeypatch, tmp_path
):
    """切分失败的段应返回空文本，而不是对磁盘陈旧音频转录。"""
    segments_dir = tmp_path / "audio_segments"
    segments_dir.mkdir()
    audio_wav = tmp_path / "audio.wav"
    audio_wav.write_bytes(b"")

    def fake_run_cmd(cmd, **kwargs):
        # 第一段切分成功，第二段切分失败
        seg_target = cmd[-1] if isinstance(cmd, list) else ""
        if "seg_000.wav" in str(seg_target):
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        return CompletedProcess(cmd, 1, stdout="", stderr="cut failed")

    monkeypatch.setattr("asr.run_cmd", fake_run_cmd)
    monkeypatch.setattr("asr._run_asr", lambda wav_path: "STALE-GARBAGE")

    results = asr._segment_and_transcribe(
        audio_wav, segments_dir, total_duration=60.0, segment_length=30
    )

    assert len(results) == 2
    assert results[0]["text"] == "STALE-GARBAGE"  # 成功段照常转录
    assert results[1]["text"] == ""


def test_run_asr_builds_mimo_payload_and_parses_content(monkeypatch, tmp_path):
    """_run_asr base64-encodes the wav into a MiMo input_audio message and reads content."""
    wav = tmp_path / "seg.wav"
    wav.write_bytes(b"RIFFfake-wav-bytes")
    monkeypatch.setitem(asr.CONFIG, "mimo_asr_api_key", "tp-test")
    monkeypatch.setitem(asr.CONFIG, "mimo_asr_model", "mimo-v2.5-asr")
    monkeypatch.setitem(asr.CONFIG, "mimo_asr_language", "auto")

    seen = {}

    def fake_call(payload):
        seen["payload"] = payload
        return {"choices": [{"message": {"content": "  你好，世界。 "}}]}

    monkeypatch.setattr("asr.mimo_asr_api_call", fake_call)
    text = asr._run_asr(wav)

    assert text == "你好，世界。"
    payload = seen["payload"]
    assert payload["model"] == "mimo-v2.5-asr"
    assert payload["asr_options"] == {"language": "auto"}
    audio = payload["messages"][0]["content"][0]
    assert audio["type"] == "input_audio"
    assert audio["input_audio"]["data"].startswith("data:audio/wav;base64,")


def test_run_asr_api_failure_raises_instead_of_silent_empty(monkeypatch, tmp_path):
    """Transient ASR API failures must not become cached empty transcript text."""
    wav = tmp_path / "seg.wav"
    wav.write_bytes(b"RIFFfake-wav-bytes")
    monkeypatch.setitem(asr.CONFIG, "mimo_asr_api_key", "tp-test")

    def fail(payload):
        raise RuntimeError("quota")

    monkeypatch.setattr("asr.mimo_asr_api_call", fail)
    with pytest.raises(RuntimeError, match="MiMo ASR 调用失败"):
        asr._run_asr(wav)


def test_run_asr_skips_oversize_segment(monkeypatch, tmp_path):
    """A segment whose base64 exceeds the MiMo cap is skipped, not sent (10MB API limit)."""
    wav = tmp_path / "big.wav"
    wav.write_bytes(b"x" * (2 * 1024 * 1024))  # ~2.7MB base64 > 1MB cap
    monkeypatch.setitem(asr.CONFIG, "mimo_asr_api_key", "tp-test")
    monkeypatch.setitem(asr.CONFIG, "mimo_asr_base64_max_mb", 1.0)

    def boom(payload):
        raise AssertionError("oversize segment must not hit the API")

    monkeypatch.setattr("asr.mimo_asr_api_call", boom)
    assert asr._run_asr(wav) == ""


def test_extract_frames_returns_only_current_run_frames(monkeypatch, tmp_path):
    """复用 work_dir 时，上一次更高编号的陈旧帧不应泄漏进结果。"""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    # 上一次高 fps 运行残留的陈旧帧
    for n in range(1, 6):
        (frames_dir / f"frame_{n:05d}.jpg").write_bytes(b"stale")

    monkeypatch.setitem(extract.CONFIG, "fps", 1)

    def fake_run_cmd(cmd, **kwargs):
        # 本次只产出 2 帧
        (frames_dir / "frame_00001.jpg").write_bytes(b"new")
        (frames_dir / "frame_00002.jpg").write_bytes(b"new")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("extract.run_cmd", fake_run_cmd)

    frames = extract.extract_frames(tmp_path / "video.mp4", tmp_path, fps=1)

    assert len(frames) == 2
    assert [f.name for f in frames] == ["frame_00001.jpg", "frame_00002.jpg"]


# ── understanding_runner.main() harness ─────────────────────────────────────


def _write_scenes(work_dir, scenes):
    (Path(work_dir) / "scenes.json").write_text(json.dumps(scenes), encoding="utf-8")
    return scenes


def _fake_detect(video_path, work_dir, threshold=None):
    return _write_scenes(work_dir, [{"scene_id": 0, "start": 0.0, "end": 10.0}])


def _fresh_analysis(scenes, frames, work_dir, **kwargs):
    return [{"scene_id": 0, "start": 0.0, "end": 10.0, "description": "fresh"}]


def _patch_runner(monkeypatch, tmp_path, *, overview=False, mimo_key="", real_brief=False):
    """Stub every external stage of understanding_runner.main(); tests override what they probe.

    Returns the single fixture frame. Patches applied here are the defaults; a test that
    calls monkeypatch.setattr afterwards wins.
    """
    frame = tmp_path / "frames" / "frame_00001.jpg"
    frame.parent.mkdir(exist_ok=True)
    if not frame.exists():
        frame.write_bytes(b"frame")

    monkeypatch.setitem(understand.CONFIG, "fps", 1.0)
    monkeypatch.setitem(understand.CONFIG, "api_key", "tp-test")
    monkeypatch.setitem(understand.CONFIG, "mimo_video_overview", overview)
    monkeypatch.setitem(understand.CONFIG, "mimo_video_api_key", mimo_key)
    monkeypatch.setattr("understanding_runner.get_video_duration", lambda path: 10.0)
    monkeypatch.setattr(
        "understanding_runner.api_call",
        lambda payload: {"choices": [{"message": {"content": "ok"}}]},
    )
    monkeypatch.setattr(
        "understanding_runner.extract_frames", lambda video_path, work_dir: [frame]
    )
    monkeypatch.setattr("understanding_runner.detect_scenes", _fake_detect)
    monkeypatch.setattr("understanding_runner.transcribe_audio", lambda *a, **k: [])
    monkeypatch.setattr(
        "understanding_runner.detect_silence_periods", lambda *a, **k: []
    )
    monkeypatch.setattr("understanding_runner.analyze_scenes", _fresh_analysis)
    if not real_brief:
        monkeypatch.setattr(
            "understanding_runner.build_agent_brief",
            lambda *a, **k: tmp_path / "agent_narration_brief.md",
        )
    return frame


def _run_main(monkeypatch, video, work_dir, *argv_extra):
    monkeypatch.setattr(
        sys,
        "argv",
        ["understanding_runner.py", str(video), "--work-dir", str(work_dir), *argv_extra],
    )
    understand.main()


def _video(tmp_path, name="video.mp4", content=b"video"):
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_understand_reextracts_frames_when_source_video_changes(monkeypatch, tmp_path):
    """understanding_runner.py must not skip stale frames just because work_dir/frames exists."""
    old_video = _video(tmp_path, "old.mp4", b"old-video-bytes")
    new_video = _video(tmp_path, "new.mp4", b"new-video-bytes")
    stale_frame = tmp_path / "frames" / "frame_00001.jpg"
    stale_frame.parent.mkdir()
    stale_frame.write_bytes(b"stale-frame")
    understand._write_frames_manifest(tmp_path, old_video, 1.0, [stale_frame])

    calls = []

    def fake_extract(video_path, work_dir):
        calls.append(Path(video_path).name)
        stale_frame.write_bytes(b"fresh-frame")
        return [stale_frame]

    _patch_runner(monkeypatch, tmp_path)
    monkeypatch.setattr("understanding_runner.extract_frames", fake_extract)

    _run_main(monkeypatch, new_video, tmp_path, "--skip-asr")

    assert calls == ["new.mp4"]
    assert stale_frame.read_bytes() == b"fresh-frame"
    assert understand._frames_cache_valid(new_video, tmp_path, 1.0)


def test_understand_removes_stale_mimo_overview_before_failed_recompute(
    monkeypatch, tmp_path
):
    """A failed overview refresh must not leave a stale final overview for the brief."""
    video = _video(tmp_path)
    stale_overview = tmp_path / "mimo_video_overview.json"
    stale_overview.write_text(
        json.dumps({"input": "scene_chunks", "content": "STALE"}), encoding="utf-8"
    )

    def fail_overview(*args, **kwargs):
        raise RuntimeError("overview refresh failed")

    _patch_runner(monkeypatch, tmp_path, overview=True, mimo_key="tp-test")
    monkeypatch.setattr("understanding_runner.analyze_video_overview", fail_overview)

    _run_main(monkeypatch, video, tmp_path, "--skip-asr")

    assert not stale_overview.exists()
    status = json.loads(
        (tmp_path / "mimo_video_overview.status.json").read_text(encoding="utf-8")
    )
    assert status["stage"] == "mimo_video_overview"
    assert status["enabled"] is True
    assert status["status"] == "failed"
    assert "overview refresh failed" in status["message"]
    assert status["artifact"] is None


def test_understand_writes_overview_status_when_key_missing(monkeypatch, tmp_path):
    """Enabled-but-no-key overview skip must be visible to downstream brief/review."""
    video = _video(tmp_path)
    _patch_runner(monkeypatch, tmp_path, overview=True, mimo_key="")

    _run_main(monkeypatch, video, tmp_path, "--skip-asr", "--no-consolidate")

    status = json.loads(
        (tmp_path / "mimo_video_overview.status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "skipped_no_key"
    assert status["artifact"] is None


def test_understand_writes_failed_consolidation_status(monkeypatch, tmp_path):
    video = _video(tmp_path)
    _patch_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "consolidate.consolidate",
        lambda work_dir, do_asr=False, do_index=True: {},
        raising=False,
    )

    _run_main(monkeypatch, video, tmp_path, "--skip-asr")

    status = json.loads(
        (tmp_path / "consolidation.status.json").read_text(encoding="utf-8")
    )
    assert status["stage"] == "consolidation"
    assert status["enabled"] is True
    assert status["do_asr"] is False
    assert status["do_index"] is True
    assert status["status"] == "failed"
    assert "understanding_index.json" in status["message"]
    assert status["artifacts"] == []


def test_understand_omits_stale_mimo_overview_when_overview_disabled(
    monkeypatch, tmp_path
):
    """Direct understand runs must not let an old overview leak into a new brief when disabled."""
    video = _video(tmp_path)
    stale_overview = tmp_path / "mimo_video_overview.json"
    stale_overview.write_text(
        json.dumps({"input": "scene_chunks", "content": "STALE OVERVIEW"}),
        encoding="utf-8",
    )
    _patch_runner(monkeypatch, tmp_path, real_brief=True)

    _run_main(monkeypatch, video, tmp_path, "--skip-asr")

    assert not stale_overview.exists()
    assert "STALE OVERVIEW" not in (tmp_path / "agent_narration_brief.md").read_text(
        encoding="utf-8"
    )


# ── stage cache freshness (each run below skips consolidate: its api_call is the real
#    lib.api_call, not the mocked understand.api_call) ───────────────────────────


def _run_cached(monkeypatch, video, work_dir, *argv_extra):
    _run_main(monkeypatch, video, work_dir, "--no-consolidate", *argv_extra)


def test_understand_recomputes_stage_when_artifact_bytes_change(monkeypatch, tmp_path):
    """A cached artifact rewritten behind its sidecar (identity mismatch) must be recomputed."""
    video = _video(tmp_path)
    calls = []

    def fake_detect(video_path, work_dir, threshold=None):
        calls.append(len(calls) + 1)
        return _write_scenes(work_dir, [{"start": 0.0, "end": 10.0, "run": calls[-1]}])

    _patch_runner(monkeypatch, tmp_path)
    monkeypatch.setattr("understanding_runner.detect_scenes", fake_detect)
    monkeypatch.setattr("understanding_runner.analyze_scenes", lambda *a, **k: [])

    _run_cached(monkeypatch, video, tmp_path)
    _write_scenes(tmp_path, [{"start": 0.0, "end": 10.0, "run": "externally-mutated"}])
    _run_cached(monkeypatch, video, tmp_path)

    assert calls == [1, 2]
    assert (
        json.loads((tmp_path / "scenes.json").read_text(encoding="utf-8"))[0]["run"]
        == 2
    )


def test_understand_recomputes_scenes_when_scene_settings_change(monkeypatch, tmp_path):
    """Scene cache freshness must include threshold/junk settings, not just video mtime."""
    video = _video(tmp_path)
    calls = []

    def fake_detect(video_path, work_dir, threshold=None):
        calls.append(threshold)
        return _write_scenes(
            work_dir, [{"start": 0.0, "end": 10.0, "threshold": threshold}]
        )

    _patch_runner(monkeypatch, tmp_path)
    monkeypatch.setattr("understanding_runner.detect_scenes", fake_detect)
    monkeypatch.setattr("understanding_runner.analyze_scenes", lambda *a, **k: [])

    _run_cached(monkeypatch, video, tmp_path, "--scene-threshold", "0.1")
    _run_cached(monkeypatch, video, tmp_path, "--scene-threshold", "0.4")

    assert calls == [0.1, 0.4]
    assert (
        json.loads((tmp_path / "scenes.json").read_text(encoding="utf-8"))[0][
            "threshold"
        ]
        == 0.4
    )


def test_understand_recomputes_asr_when_asr_settings_change(monkeypatch, tmp_path):
    """ASR cache freshness must include ASR settings and source provenance."""
    video = _video(tmp_path)
    calls = []

    def fake_asr(video_path, work_dir):
        calls.append(understand.CONFIG["asr_segment_seconds"])
        result = [{"start": 0.0, "end": 1.0, "text": f"seg-{calls[-1]}"}]
        (Path(work_dir) / "asr_result.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        return result

    _patch_runner(monkeypatch, tmp_path)
    monkeypatch.setattr("understanding_runner.transcribe_audio", fake_asr)
    monkeypatch.setattr("understanding_runner.analyze_scenes", lambda *a, **k: [])

    monkeypatch.setitem(understand.CONFIG, "asr_segment_seconds", 30.0)
    _run_cached(monkeypatch, video, tmp_path)
    monkeypatch.setitem(understand.CONFIG, "asr_segment_seconds", 12.0)
    _run_cached(monkeypatch, video, tmp_path)

    assert calls == [30.0, 12.0]
    assert (
        json.loads((tmp_path / "asr_result.json").read_text(encoding="utf-8"))[0][
            "text"
        ]
        == "seg-12.0"
    )


def test_understand_recomputes_silence_when_asr_content_changes(monkeypatch, tmp_path):
    """Silence cache freshness must include the ASR artifact identity/provenance."""
    video = _video(tmp_path)
    calls = []
    asr_payload = {"value": [{"start": 0.0, "end": 1.0, "text": "first"}]}

    def fake_asr(video_path, work_dir):
        (Path(work_dir) / "asr_result.json").write_text(
            json.dumps(asr_payload["value"]), encoding="utf-8"
        )
        return asr_payload["value"]

    def fake_silence(video_path, work_dir, asr_result):
        calls.append([seg["text"] for seg in asr_result])
        result = [
            {"start": 0.0, "end": 1.0, "duration": 1.0, "has_speech": bool(calls[-1])}
        ]
        (Path(work_dir) / "silence_periods.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        return result

    _patch_runner(monkeypatch, tmp_path)
    monkeypatch.setattr("understanding_runner.transcribe_audio", fake_asr)
    monkeypatch.setattr("understanding_runner.detect_silence_periods", fake_silence)
    monkeypatch.setattr("understanding_runner.analyze_scenes", lambda *a, **k: [])

    _run_cached(monkeypatch, video, tmp_path)
    asr_payload["value"] = [{"start": 0.0, "end": 1.0, "text": "second"}]
    (tmp_path / "asr_result.json").unlink()
    (tmp_path / "asr_result.json.meta.json").unlink()
    _run_cached(monkeypatch, video, tmp_path)

    assert calls == [["first"], ["second"]]


@pytest.mark.parametrize(
    "config_key, first, second",
    [
        # --context is the CLI path into CONFIG["context_info"]; the others are plain config.
        ("context_info", ("argv", ["--context", "角色A"]), ("argv", ["--context", "角色B"])),
        (
            "api_url",
            ("config", "https://one.example/v1/chat/completions"),
            ("config", "https://two.example/v1/chat/completions"),
        ),
        ("mimo_disable_thinking", ("config", True), ("config", False)),
    ],
    ids=["context", "api_url", "thinking"],
)
def test_understand_recomputes_vlm_when_request_provenance_changes(
    monkeypatch, tmp_path, config_key, first, second
):
    """VLM cache freshness must include prompt/context/endpoint/thinking provenance."""
    video = _video(tmp_path)
    calls = []

    def fake_vlm(scenes, frames, work_dir, **kwargs):
        calls.append(understand.CONFIG.get(config_key))
        result = [{"scene_id": 0, "start": 0.0, "end": 10.0, "description": str(calls[-1])}]
        (Path(work_dir) / "vlm_analysis.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        return result

    _patch_runner(monkeypatch, tmp_path)
    monkeypatch.setattr("understanding_runner.analyze_scenes", fake_vlm)

    expected = []
    for kind, value in (first, second):
        if kind == "argv":
            monkeypatch.setitem(understand.CONFIG, config_key, "")
            _run_cached(monkeypatch, video, tmp_path, *value)
            expected.append(value[-1])
        else:
            monkeypatch.setitem(understand.CONFIG, config_key, value)
            _run_cached(monkeypatch, video, tmp_path)
            expected.append(value)

    assert calls == expected
    assert (
        json.loads((tmp_path / "vlm_analysis.json").read_text(encoding="utf-8"))[0][
            "description"
        ]
        == str(calls[-1])
    )


def test_understand_asr_exception_does_not_cache_empty_transcript(
    monkeypatch, tmp_path
):
    """Unexpected ASR failures must fail fast and leave no reusable empty asr_result.json."""
    video = _video(tmp_path)
    _patch_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "understanding_runner.transcribe_audio",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota")),
    )
    monkeypatch.setattr("understanding_runner.analyze_scenes", lambda *a, **k: [])

    with pytest.raises(RuntimeError, match="ASR 失败"):
        _run_cached(monkeypatch, video, tmp_path)

    assert not (tmp_path / "asr_result.json").exists()
    assert not (tmp_path / "asr_result.json.meta.json").exists()
