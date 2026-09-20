import json
import sys
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "video-understanding"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import asr  # noqa: E402
import understanding_brief  # noqa: E402
from agent_brief import build_agent_brief  # noqa: E402
from asr_timing_evidence import (  # noqa: E402
    EVIDENCE_FILENAME,
    asr_evidence_summary_for_brief,
    validate_asr_timing_evidence,
    write_asr_timing_evidence,
)
from understanding_cache import (  # noqa: E402
    _asr_cache_payload,
    _asr_cache_state,
    _write_stage_meta,
)


def _read_evidence(work_dir):
    return json.loads((work_dir / EVIDENCE_FILENAME).read_text(encoding="utf-8"))


def _video(tmp_path, content=b"source-video"):
    path = tmp_path / "source.mp4"
    path.write_bytes(content)
    return path


def _successful_extract(audio_bytes=b"RIFF-audio"):
    def fake_run(cmd, **_kwargs):
        Path(cmd[-1]).write_bytes(audio_bytes)
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    return fake_run


def _cache_state_after_meta(tmp_path, video):
    """Stamp a fresh stage sidecar on asr_result.json and classify the cache."""
    result_path = tmp_path / "asr_result.json"
    meta = _asr_cache_payload(video)
    _write_stage_meta(result_path, meta)
    return _asr_cache_state(result_path, meta, video)


def test_no_key_is_explicit_unavailability_not_silence(monkeypatch, tmp_path):
    video = _video(tmp_path)
    monkeypatch.setitem(asr.CONFIG, "mimo_asr_api_key", "")
    monkeypatch.setattr(asr, "run_cmd", lambda *_a, **_k: pytest.fail("no ffmpeg"))

    assert asr.transcribe_audio(video, tmp_path) == []
    assert json.loads((tmp_path / "asr_result.json").read_text()) == []
    evidence = _read_evidence(tmp_path)
    assert evidence["status"] == "UNAVAILABLE_NO_KEY"
    assert evidence["source_video_fingerprint"]
    assert evidence["asr_result_fingerprint"]
    assert evidence["audio_fingerprint"] is None
    assert evidence["precision"]["word_alignment"] == "NOT_PERFORMED"
    assert evidence["precision"]["dialogue_boundaries"] == "NOT_VERIFIED"
    assert evidence["precision"]["empty_text_meaning"] == "UNKNOWN_NOT_PROVEN_SILENCE"


def test_no_duration_binds_extracted_audio_and_is_not_reusable(monkeypatch, tmp_path):
    """Duration 0 writes an empty, evidence-bound result that never becomes a cache hit."""
    video = _video(tmp_path)
    monkeypatch.setitem(asr.CONFIG, "mimo_asr_api_key", "test-key")
    monkeypatch.setattr(asr, "run_cmd", _successful_extract())
    monkeypatch.setattr(asr, "get_video_duration", lambda _path: 0.0)
    monkeypatch.setattr(
        asr, "_run_asr", lambda _path: pytest.fail("no transcription at zero duration")
    )

    assert asr.transcribe_audio(video, tmp_path) == []
    assert json.loads((tmp_path / "asr_result.json").read_text(encoding="utf-8")) == []
    evidence = _read_evidence(tmp_path)
    assert evidence["status"] == "UNAVAILABLE_NO_DURATION"
    assert evidence["audio_fingerprint"]
    assert validate_asr_timing_evidence(
        tmp_path / EVIDENCE_FILENAME, video, tmp_path / "asr_result.json"
    )
    assert _cache_state_after_meta(tmp_path, video) == "MISS"


def _fail_with_partial(cmd, **_kwargs):
    Path(cmd[-1]).write_bytes(b"partial-new-audio")
    return CompletedProcess([], 1, stdout="", stderr="bad input")


def _raise_with_partial(cmd, **_kwargs):
    Path(cmd[-1]).write_bytes(b"partial")
    raise OSError("ffmpeg launch failed")


@pytest.mark.parametrize(
    "run_cmd", [_fail_with_partial, _raise_with_partial], ids=["nonzero_exit", "exception"]
)
def test_audio_extraction_failure_writes_failure_evidence_and_no_partial_audio(
    monkeypatch, tmp_path, run_cmd
):
    video = _video(tmp_path)
    (tmp_path / "audio.wav").write_bytes(b"old-audio")
    (tmp_path / "audio.wav.meta.json").write_text("{}", encoding="utf-8")
    monkeypatch.setitem(asr.CONFIG, "mimo_asr_api_key", "test-key")
    monkeypatch.setattr(asr, "run_cmd", run_cmd)

    with pytest.raises(RuntimeError, match="音频提取失败"):
        asr.transcribe_audio(video, tmp_path)
    assert not (tmp_path / "asr_result.json").exists()
    evidence = _read_evidence(tmp_path)
    assert evidence["status"] == "FAILED_AUDIO_EXTRACTION"
    assert evidence["asr_result_fingerprint"] is None
    assert not (tmp_path / "audio.wav").exists()
    assert not (tmp_path / "audio.wav.meta.json").exists()


def test_provider_failure_writes_failure_evidence(monkeypatch, tmp_path):
    video = _video(tmp_path)
    monkeypatch.setitem(asr.CONFIG, "mimo_asr_api_key", "test-key")
    monkeypatch.setattr(asr, "run_cmd", _successful_extract())
    monkeypatch.setattr(asr, "get_video_duration", lambda _path: 2.5)
    monkeypatch.setattr(
        asr, "_run_asr", lambda _path: (_ for _ in ()).throw(asr.ASRProviderError("down"))
    )

    with pytest.raises(asr.ASRProviderError):
        asr.transcribe_audio(video, tmp_path)
    assert _read_evidence(tmp_path)["status"] == "FAILED_PROVIDER"
    assert not (tmp_path / "asr_result.json").exists()


def test_glossary_keeps_observed_and_post_glossary_text(monkeypatch, tmp_path):
    video = _video(tmp_path)
    (tmp_path / "background_research.json").write_text(
        json.dumps({"characters": {"叶轻眉": "角色"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setitem(asr.CONFIG, "mimo_asr_api_key", "test-key")
    monkeypatch.setitem(asr.CONFIG, "asr_segment_seconds", 30)
    monkeypatch.setattr(asr, "run_cmd", _successful_extract())
    monkeypatch.setattr(asr, "get_video_duration", lambda _path: 3.0)
    monkeypatch.setattr(asr, "_run_asr", lambda _path: "她叫叶青眉")

    result = asr.transcribe_audio(video, tmp_path)
    assert result == [{"start": 0.0, "end": 3.0, "text": "她叫叶轻眉"}]
    window = _read_evidence(tmp_path)["windows"][0]
    assert window["observed_text"] == "她叫叶青眉"
    assert window["post_glossary_text"] == "她叫叶轻眉"
    assert window["glossary_modified"] is True
    assert window["text_availability"] == "AVAILABLE"


def test_empty_provider_text_is_unknown_not_proven_silence_and_not_cached(
    monkeypatch, tmp_path
):
    video = _video(tmp_path)
    monkeypatch.setitem(asr.CONFIG, "mimo_asr_api_key", "test-key")
    monkeypatch.setattr(asr, "run_cmd", _successful_extract())
    monkeypatch.setattr(asr, "get_video_duration", lambda _path: 1.0)
    monkeypatch.setattr(asr, "_run_asr", lambda _path: "")

    assert asr.transcribe_audio(video, tmp_path)[0]["text"] == ""
    evidence = _read_evidence(tmp_path)
    assert evidence["status"] == "EMPTY_UNKNOWN"
    assert evidence["windows"][0]["text_availability"] == "UNAVAILABLE_UNKNOWN"
    assert "silence" not in evidence["windows"][0]
    # one all-empty run must not become a permanent cache hit
    assert _cache_state_after_meta(tmp_path, video) == "MISS"


def test_missing_sidecar_is_legacy_unverified_without_network(tmp_path):
    video = _video(tmp_path)
    result_path = tmp_path / "asr_result.json"
    result_path.write_text(
        json.dumps([{"start": 0.0, "end": 1.0, "text": "legacy"}]),
        encoding="utf-8",
    )
    meta = _asr_cache_payload(video)
    _write_stage_meta(result_path, meta)

    assert _asr_cache_state(result_path, meta, video) == "LEGACY_UNVERIFIED"

    write_asr_timing_evidence(
        tmp_path,
        video,
        "LEGACY_UNVERIFIED",
        final_segments=json.loads(result_path.read_text()),
    )
    evidence = _read_evidence(tmp_path)
    assert evidence["windows"][0]["observed_text"] is None
    assert evidence["windows"][0]["post_glossary_text"] == "legacy"
    assert _asr_cache_state(result_path, meta, video) == "LEGACY_UNVERIFIED"


def test_explicit_skip_sidecar_is_result_bound(tmp_path):
    video = _video(tmp_path)
    result_path = tmp_path / "asr_result.json"
    result_path.write_text("[]", encoding="utf-8")
    write_asr_timing_evidence(tmp_path, video, "EXPLICITLY_SKIPPED", final_segments=[])
    evidence = _read_evidence(tmp_path)
    assert evidence["status"] == "EXPLICITLY_SKIPPED"
    assert evidence["asr_result_fingerprint"]
    assert validate_asr_timing_evidence(
        tmp_path / EVIDENCE_FILENAME, video, result_path
    )


def test_duration_is_probed_from_extracted_audio(monkeypatch, tmp_path):
    video = _video(tmp_path)
    monkeypatch.setitem(asr.CONFIG, "mimo_asr_api_key", "test-key")
    monkeypatch.setattr(asr, "run_cmd", _successful_extract())
    seen = []

    def duration(path):
        seen.append(Path(path).name)
        return 1.0

    monkeypatch.setattr(asr, "get_video_duration", duration)
    monkeypatch.setattr(asr, "_run_asr", lambda _path: "text")
    asr.transcribe_audio(video, tmp_path)
    assert seen == ["audio.wav"]


HELLO = [{"start": 0.0, "end": 1.0, "text": "hello"}]


def _valid_available_evidence(tmp_path, observed=HELLO, final=HELLO):
    """A coherent AVAILABLE_COARSE work_dir: source video, bound audio.wav, result and sidecar."""
    video = _video(tmp_path)
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF-audio")
    (tmp_path / "audio.wav.meta.json").write_text(
        json.dumps({"source_video_fingerprint": asr.file_fingerprint(video)}),
        encoding="utf-8",
    )
    result_path = tmp_path / "asr_result.json"
    result_path.write_text(json.dumps(final, ensure_ascii=False), encoding="utf-8")
    evidence_path = write_asr_timing_evidence(
        tmp_path,
        video,
        "AVAILABLE_COARSE",
        observed_segments=observed,
        final_segments=final,
        audio_path=audio,
    )
    return video, result_path, evidence_path


@pytest.mark.parametrize("changed", ["source", "audio", "result"])
def test_sidecar_rejects_changed_bound_artifact(tmp_path, changed):
    video, result_path, evidence_path = _valid_available_evidence(tmp_path)

    {"source": video, "audio": tmp_path / "audio.wav", "result": result_path}[
        changed
    ].write_bytes(b"changed-content-with-a-different-size")
    assert not validate_asr_timing_evidence(evidence_path, video, result_path)


def test_glossary_change_invalidates_fresh_corrected_cache(tmp_path):
    research = tmp_path / "background_research.json"
    research.write_text(
        json.dumps({"characters": {"叶轻眉": "角色"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    video, result_path, _evidence_path = _valid_available_evidence(
        tmp_path,
        observed=[{"start": 0.0, "end": 1.0, "text": "叶青眉"}],
        final=[{"start": 0.0, "end": 1.0, "text": "叶轻眉"}],
    )
    meta = _asr_cache_payload(video)
    _write_stage_meta(result_path, meta)
    assert _asr_cache_state(result_path, meta, video) == "FRESH"

    research.write_text(
        json.dumps({"characters": {"叶青眉": "另一个名字"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert _asr_cache_state(result_path, meta, video) == "MISS"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update({"schema_version": True}),
        lambda p: p.update({"extra": "not allowed"}),
        lambda p: p.update({"source_video_fingerprint": True}),
        lambda p: p.update({"asr_result_fingerprint": "tampered"}),
        lambda p: p["glossary"].update({"policy_version": True}),
        lambda p: p["windows"][0].update({"start": True}),
        lambda p: p.update({"status": "EMPTY_UNKNOWN"}),
        lambda p: p.update({"audio_fingerprint": None}),
        lambda p: p["windows"][0].update(
            {"observed_text": "different", "glossary_modified": False}
        ),
        lambda p: p["windows"][0].update(
            {"observed_text": "different", "glossary_modified": True}
        ),
    ],
)
def test_strict_sidecar_schema_and_status_consistency(tmp_path, mutate):
    video, result_path, evidence_path = _valid_available_evidence(tmp_path)
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    mutate(payload)
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    assert not validate_asr_timing_evidence(evidence_path, video, result_path)


def test_brief_surfaces_validated_coarse_evidence_and_no_safe_asr_end_claim(
    monkeypatch, tmp_path
):
    video, _result_path, evidence_path = _valid_available_evidence(tmp_path)
    monkeypatch.setitem(asr.CONFIG, "edit_mode", "cut")
    text = build_agent_brief(
        [{"scene_id": 0, "start": 0.0, "end": 2.0, "description": "scene"}],
        HELLO,
        [],
        2.0,
        tmp_path,
        mimo_overview_video_path=video,
        asr_evidence=asr_evidence_summary_for_brief(tmp_path, video),
    ).read_text(encoding="utf-8")
    assert "ASR timing evidence" in text
    assert "AVAILABLE_COARSE" in text
    assert asr.file_fingerprint(evidence_path) in text
    assert "word alignment: NOT_PERFORMED" in text
    assert "ASR [start–end] times + Quiet windows below as safe cut points" not in text
    assert "direct listening" in text


def test_brief_only_validates_stale_sidecar_and_warns_without_network(
    monkeypatch, tmp_path
):
    video, result_path, evidence_path = _valid_available_evidence(tmp_path)
    result_path.write_text(
        json.dumps([{"start": 0.0, "end": 1.0, "text": "changed"}]),
        encoding="utf-8",
    )
    (tmp_path / "scenes.json").write_text(
        json.dumps(
            [{"scene_id": 0, "start": 0.0, "end": 2.0, "description": "scene"}]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(understanding_brief, "detect_speech_boundary_anchors", lambda *_a: [])
    monkeypatch.setattr(understanding_brief, "_generate_source_storyboard", lambda *_a, **_k: None)
    monkeypatch.setattr(understanding_brief, "_generate_edited_storyboard", lambda *_a, **_k: None)
    monkeypatch.setattr(understanding_brief, "_prepend_storyboard_brief_header", lambda *_a, **_k: None)

    understanding_brief._write_brief_from_existing_artifacts(
        video, tmp_path, SimpleNamespace(style="纪录片"), 2.0
    )
    text = (tmp_path / "agent_narration_brief.md").read_text(encoding="utf-8")
    assert "MISSING_OR_STALE" in text
    assert asr.file_fingerprint(evidence_path) in text
    assert "must not be treated as verified dialogue boundaries" in text
