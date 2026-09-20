"""Regression tests for vlm.py bug fixes (null content, retry header, partial chunk cache)."""
import json
import re
import sys
from pathlib import Path

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

from lib import CONFIG  # noqa: E402
from vlm import (  # noqa: E402
    _is_mimo_chunk_usable,
    _load_mimo_partial,
    _mimo_cached_chunks_fingerprint,
    _mimo_chunk_cache_key,
    _mimo_overview_payload_fingerprint,
    _parse_vlm_depth_response,
    _save_mimo_partial,
    analyze_scenes,
    analyze_video_overview,
    file_fingerprint,
    mimo_video_overview_cache_fresh,
    mimo_video_settings_fingerprint,
)


def test_parse_frame_facts_splits_chinese_and_english_punctuation():
    raw = """【描述】
门口对峙
【帧标签】
1.0s | 男子握紧拳头，女子后退一步; 门缓缓关上、灯光变暗；气氛紧张
【深层分析】
关系破裂"""

    _description, _depth, facts = _parse_vlm_depth_response(raw)

    assert facts["1.0"] == ["男子握紧拳头", "女子后退一步", "门缓缓关上", "灯光变暗", "气氛紧张"]


# ── frame-VLM scene analysis ─────────────────────────────────────────────────


def _scene_setup(monkeypatch, tmp_path, frame_count=1, workers=1):
    """Stage `frame_count` fake frames at fps=1 (frame_0000N -> t=(N-1)s) and the VLM config."""
    frames = []
    for n in range(1, frame_count + 1):
        f = tmp_path / f"frame_{n:05d}.jpg"
        f.write_bytes(b"\xff\xd8\xff\xd9")  # minimal jpeg-ish bytes
        frames.append(f)
    monkeypatch.setitem(CONFIG, "fps", 1.0)
    monkeypatch.setitem(CONFIG, "vlm_model", "mimo-v2.5")
    monkeypatch.setitem(CONFIG, "vlm_workers", workers)
    monkeypatch.setitem(CONFIG, "context_info", "")
    return frames


ONE_SCENE = [{"start": 0.0, "end": 1.0}]
# Three 1s windows, each owning exactly one of the frames at 0/1/2/3s.
THREE_SCENES = [{"start": 0.5, "end": 1.5}, {"start": 1.5, "end": 2.5}, {"start": 2.5, "end": 3.5}]


def _reply(content):
    return {"choices": [{"message": {"content": content}}]}


def test_null_content_falls_back_to_reasoning(monkeypatch, tmp_path):
    """providers returning content=null must coerce to reasoning_content, not crash on .strip()."""
    frames = _scene_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "vlm.api_call",
        lambda payload: {"choices": [{"message": {
            "content": None,
            "reasoning_content": "【描述】男子拿起茶壶",
        }}]},
    )

    analyses = analyze_scenes(ONE_SCENE, frames, tmp_path)

    assert len(analyses) == 1
    assert analyses[0]["description"] == "男子拿起茶壶"


def test_analyze_scenes_sends_the_editorial_evidence_prompt_to_vlm(monkeypatch, tmp_path):
    frames = _scene_setup(monkeypatch, tmp_path)
    captured = {}

    def fake_api_call(payload):
        captured["prompt"] = payload["messages"][0]["content"][-1]["text"]
        return _reply("【描述】人物对峙\n【深层分析】关系发生变化")

    monkeypatch.setattr("vlm.api_call", fake_api_call)

    analyses = analyze_scenes([{"start": 0.0, "end": 2.0}], frames, tmp_path)
    numbered_items = {
        int(number): text
        for number, text in re.findall(r"(?m)^(\d+)\.\s+(.+)$", captured["prompt"])
    }

    assert analyses[0]["description"] == "人物对峙"
    assert {1, 2, 3, 4, 5} <= set(numbered_items)
    assert "变化" in numbered_items[3]
    assert "视角" in numbered_items[4]
    assert "反应" in numbered_items[4]


def _fail_scene_two_until_cleared(state):
    """api_call that 429s the [1.5,2.5] window (frame at 2.0s) while state["fail_mid"] is set."""

    def fake_api_call(payload):
        state["calls"] += 1
        text = payload["messages"][0]["content"][-1]["text"]
        if state["fail_mid"] and "2.0s" in text:
            raise RuntimeError("HTTP 429 — Too many requests")
        return _reply("【描述】测试画面")

    return fake_api_call


def test_vlm_resume_cache_persists_on_failure_and_resumes(monkeypatch, tmp_path):
    """A scene that keeps failing aborts the batch, but succeeded scenes persist to
    vlm_scene_cache.json so a re-run only re-analyzes the failed scene."""
    frames = _scene_setup(monkeypatch, tmp_path, frame_count=4)
    state = {"fail_mid": True, "calls": 0}
    monkeypatch.setattr("vlm.api_call", _fail_scene_two_until_cleared(state))

    with pytest.raises(RuntimeError, match="断点续传"):
        analyze_scenes(THREE_SCENES, frames, tmp_path)
    cache = json.loads((tmp_path / "vlm_scene_cache.json").read_text(encoding="utf-8"))
    assert len(cache) == 2  # the two scenes that succeeded are cached for resume

    state["fail_mid"] = False
    calls_before = state["calls"]
    analyses = analyze_scenes(THREE_SCENES, frames, tmp_path)
    assert len(analyses) == 3 and all(a and a["description"] == "测试画面" for a in analyses)
    assert state["calls"] - calls_before == 1  # only the previously-failed scene re-analyzed
    assert not (tmp_path / "vlm_scene_cache.json").exists()  # resume cache cleaned on success


def test_vlm_resume_cache_invalidates_on_request_setting_flip(monkeypatch, tmp_path):
    """A leftover partial cache must not be reused after an output-affecting request setting
    changes, or the resumed run would silently mix old- and new-setting analyses."""
    frames = _scene_setup(monkeypatch, tmp_path, frame_count=4)
    monkeypatch.setitem(CONFIG, "mimo_disable_thinking", True)
    state = {"fail_mid": True, "calls": 0}
    monkeypatch.setattr("vlm.api_call", _fail_scene_two_until_cleared(state))

    with pytest.raises(RuntimeError, match="断点续传"):
        analyze_scenes(THREE_SCENES, frames, tmp_path)
    assert len(json.loads((tmp_path / "vlm_scene_cache.json").read_text(encoding="utf-8"))) == 2

    state["fail_mid"] = False
    monkeypatch.setitem(CONFIG, "mimo_disable_thinking", False)
    calls_before = state["calls"]
    analyses = analyze_scenes(THREE_SCENES, frames, tmp_path)
    assert len(analyses) == 3 and all(a and a["description"] == "测试画面" for a in analyses)
    assert state["calls"] - calls_before == 3  # all re-analyzed; no stale reuse


def test_vlm_auto_throttle_retry_recovers_transient_429(monkeypatch, tmp_path):
    """A scene that 429s once but succeeds when retried at lower concurrency is recovered
    within ONE run, without aborting."""
    frames = _scene_setup(monkeypatch, tmp_path, frame_count=4, workers=4)
    fired = {"once": False}

    def fake_api_call(payload):
        text = payload["messages"][0]["content"][-1]["text"]
        if "2.0s" in text and not fired["once"]:
            fired["once"] = True
            raise RuntimeError("HTTP 429 — Too many requests")
        return _reply("【描述】测试画面")

    monkeypatch.setattr("vlm.api_call", fake_api_call)
    analyses = analyze_scenes(THREE_SCENES, frames, tmp_path)  # must NOT raise
    assert len(analyses) == 3 and all(a["description"] == "测试画面" for a in analyses)
    assert not (tmp_path / "vlm_scene_cache.json").exists()


def test_null_content_and_null_reasoning_coerce_to_empty_then_retry(monkeypatch, tmp_path):
    """content=null AND reasoning_content=null must coerce to "" so the empty-retry loop runs."""
    frames = _scene_setup(monkeypatch, tmp_path)
    calls = {"n": 0}

    def fake_api_call(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"choices": [{"message": {"content": None, "reasoning_content": None}}]}
        return _reply("【描述】第二次成功")

    monkeypatch.setattr("vlm.api_call", fake_api_call)

    analyses = analyze_scenes(ONE_SCENE, frames, tmp_path)

    assert calls["n"] >= 2
    assert analyses[0]["description"] == "第二次成功"


def test_retry_text_keeps_frame_timestamp_header(monkeypatch, tmp_path):
    """The empty-response retry must re-send the frame-timestamp header so 【帧标签】 anchors survive."""
    frames = _scene_setup(monkeypatch, tmp_path)
    retry_texts = []

    def fake_api_call(payload):
        retry_texts.append(payload["messages"][0]["content"][-1]["text"])
        return _reply("")  # always empty -> exhaust the 3 attempts to observe retry payloads

    monkeypatch.setattr("vlm.api_call", fake_api_call)

    with pytest.raises(RuntimeError, match="VLM 分析失败"):
        analyze_scenes(ONE_SCENE, frames, tmp_path)

    assert len(retry_texts) == 3
    # frame_00001.jpg @ fps=1.0 -> 0.0s ; header must appear on every attempt incl. retries
    for text in retry_texts:
        assert "帧时间点" in text
        assert "0.0s" in text
    assert "请务必按格式输出，不要留空。" in retry_texts[1]
    assert "请务必按格式输出，不要留空。" in retry_texts[2]


@pytest.mark.parametrize(
    "api_call",
    [
        lambda payload: (_ for _ in ()).throw(RuntimeError("quota")),
        lambda payload: _reply(""),
    ],
    ids=["api_raises", "always_empty"],
)
def test_scene_failure_does_not_write_placeholder_cache(monkeypatch, tmp_path, api_call):
    """Transient failures and all-empty responses fail the stage instead of caching placeholders."""
    frames = _scene_setup(monkeypatch, tmp_path)
    monkeypatch.setattr("vlm.api_call", api_call)

    with pytest.raises(RuntimeError, match="VLM 分析失败"):
        analyze_scenes(ONE_SCENE, frames, tmp_path)

    assert not (tmp_path / "vlm_analysis.json").exists()


# ── MiMo video overview: incremental chunk cache ─────────────────────────────


def _chunk_result(chunk_path, chunk, content=None):
    return {
        "chunk_id": chunk["chunk_id"],
        "scene_id": chunk["scene_id"],
        "start": chunk["start"],
        "end": chunk["end"],
        "model": "mimo-v2.5",
        "content": f"分片{chunk['chunk_id']}" if content is None else content,
        "reasoning_content": "",
        "usage": {},
        "clip_path": f"mimo_video_chunks/{chunk_path.name}",
    }


def _overview_setup(monkeypatch, tmp_path, chunk_api=_chunk_result):
    """Enable the overview with 2s chunks and stub ffmpeg + the per-chunk API; returns the video."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake-video")
    monkeypatch.setitem(CONFIG, "mimo_video_overview", True)
    monkeypatch.setitem(CONFIG, "mimo_video_api_key", "secret")
    monkeypatch.setitem(CONFIG, "mimo_video_model", "mimo-v2.5")
    monkeypatch.setitem(CONFIG, "vlm_model", "mimo-v2.5")
    monkeypatch.setitem(CONFIG, "mimo_video_chunk_max_seconds", 2)
    monkeypatch.setitem(CONFIG, "mimo_video_chunk_min_seconds", 0.5)

    def fake_extract(video_path, chunk, output_path):
        output_path.write_bytes(b"tiny-chunk")
        return output_path

    monkeypatch.setattr("vlm._extract_video_chunk", fake_extract)
    monkeypatch.setattr("vlm._analyze_mimo_video_chunk", chunk_api)
    return video


ONE_CHUNK = {"chunk_id": 0, "scene_id": 7, "start": 0.0, "end": 2.0}
ONE_CHUNK_SCENES = [{"scene_id": 7, "start": 0.0, "end": 2.0}]


def _saved_partial(tmp_path, video):
    partial_path = tmp_path / "mimo_video_overview.partial.json"
    done = {_mimo_chunk_cache_key(ONE_CHUNK): {"chunk_id": 0, "scene_id": 7, "content": "paid"}}
    _save_mimo_partial(partial_path, done, video, ONE_CHUNK_SCENES)
    return partial_path, done


def test_partial_cache_roundtrips_for_same_video_scenes_and_settings(monkeypatch, tmp_path):
    video = _overview_setup(monkeypatch, tmp_path)
    partial_path, done = _saved_partial(tmp_path, video)
    assert _load_mimo_partial(partial_path, video, ONE_CHUNK_SCENES) == done


def _settings_change(monkeypatch, partial_path, video):
    monkeypatch.setitem(CONFIG, "mimo_video_chunk_max_seconds", 99)
    return video, ONE_CHUNK_SCENES


def _payload_mutation(monkeypatch, partial_path, video):
    payload = json.loads(partial_path.read_text(encoding="utf-8"))
    payload["chunks"][_mimo_chunk_cache_key(ONE_CHUNK)]["content"] = "tampered but non-empty"
    partial_path.write_text(json.dumps(payload), encoding="utf-8")
    return video, ONE_CHUNK_SCENES


def _source_video_mismatch(monkeypatch, partial_path, video):
    other = video.with_name("new.mp4")
    other.write_bytes(b"new-video")
    return other, ONE_CHUNK_SCENES


def _scene_plan_mismatch(monkeypatch, partial_path, video):
    return video, [{"scene_id": 7, "start": 0.0, "end": 4.0}]


@pytest.mark.parametrize(
    "drift",
    [_settings_change, _payload_mutation, _source_video_mismatch, _scene_plan_mismatch],
    ids=lambda fn: fn.__name__.strip("_"),
)
def test_partial_cache_rejects_drifted_provenance(monkeypatch, tmp_path, drift):
    """Paid chunks are reused only for the same settings, bytes, source video and scene plan."""
    video = _overview_setup(monkeypatch, tmp_path)
    partial_path, _done = _saved_partial(tmp_path, video)

    load_video, load_scenes = drift(monkeypatch, partial_path, video)

    assert _load_mimo_partial(partial_path, load_video, load_scenes) == {}


def test_failed_chunk_preserves_completed_chunks_and_resume_skips(monkeypatch, tmp_path):
    """A mid-loop chunk failure keeps completed chunks; resume only redoes the missing one."""
    analyzed = []

    def fail_on_last(chunk_path, chunk):
        analyzed.append(chunk["chunk_id"])
        if chunk["chunk_id"] == 2:
            raise RuntimeError("MiMo 分片失败")
        return _chunk_result(chunk_path, chunk)

    video = _overview_setup(monkeypatch, tmp_path, chunk_api=fail_on_last)
    monkeypatch.setitem(CONFIG, "mimo_video_fps", 2)

    # 5s span @ max 2s -> 3 chunks. First run: chunk 2 fails.
    scenes = [{"scene_id": 7, "start": 0.0, "end": 5.0}]
    with pytest.raises(RuntimeError):
        analyze_video_overview(video, tmp_path, scenes)

    partial_path = tmp_path / "mimo_video_overview.partial.json"
    assert partial_path.exists()
    assert not (tmp_path / "mimo_video_overview.json").exists()
    assert len(json.loads(partial_path.read_text(encoding="utf-8"))["chunks"]) == 2
    assert analyzed == [0, 1, 2]

    # Resume: chunks 0 and 1 are cached, only chunk 2 is re-billed.
    analyzed.clear()

    def all_ok(chunk_path, chunk):
        analyzed.append(chunk["chunk_id"])
        return _chunk_result(chunk_path, chunk)

    monkeypatch.setattr("vlm._analyze_mimo_video_chunk", all_ok)
    overview = analyze_video_overview(video, tmp_path, scenes)

    assert analyzed == [2]
    assert overview["input"] == "scene_chunks"
    assert overview["chunk_count"] == 3
    assert overview["settings"] == mimo_video_settings_fingerprint()
    assert [c["chunk_id"] for c in overview["chunks"]] == [0, 1, 2]
    assert (tmp_path / "mimo_video_overview.json").exists()
    assert not partial_path.exists()


# ── MiMo video overview: final artifact cache ────────────────────────────────


FINAL_SCENES = [{"scene_id": 1, "start": 0.0, "end": 3.0}]


def _fresh_final_overview(monkeypatch, tmp_path):
    video = _overview_setup(monkeypatch, tmp_path)
    analyze_video_overview(video, tmp_path, FINAL_SCENES)
    overview_path = tmp_path / "mimo_video_overview.json"
    assert mimo_video_overview_cache_fresh(overview_path, video, FINAL_SCENES)
    return video, overview_path


def test_final_overview_cache_rejects_payload_mutation(monkeypatch, tmp_path):
    """The final MiMo overview skip path must reject byte/content edits to cached chunks."""
    video, overview_path = _fresh_final_overview(monkeypatch, tmp_path)

    payload = json.loads(overview_path.read_text(encoding="utf-8"))
    payload["chunks"][0]["content"] = "tampered but non-empty"
    overview_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert not mimo_video_overview_cache_fresh(overview_path, video, FINAL_SCENES)

    payload["chunks_fingerprint"] = _mimo_cached_chunks_fingerprint(payload["chunks"])
    overview_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert not mimo_video_overview_cache_fresh(overview_path, video, FINAL_SCENES)

    payload["overview_fingerprint"] = _mimo_overview_payload_fingerprint(payload)
    overview_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert mimo_video_overview_cache_fresh(overview_path, video, FINAL_SCENES)


def test_final_overview_cache_invalidates_on_settings_source_or_chunks(monkeypatch, tmp_path):
    video, overview_path = _fresh_final_overview(monkeypatch, tmp_path)
    original_prompt = CONFIG.get("mimo_video_prompt")
    original_url = CONFIG.get("mimo_video_api_url")

    monkeypatch.setitem(CONFIG, "mimo_video_prompt", "changed prompt")
    assert not mimo_video_overview_cache_fresh(overview_path, video, FINAL_SCENES)

    monkeypatch.setitem(CONFIG, "mimo_video_prompt", original_prompt)
    monkeypatch.setitem(CONFIG, "mimo_video_api_url", "https://changed.example/v1/chat/completions")
    assert not mimo_video_overview_cache_fresh(overview_path, video, FINAL_SCENES)

    monkeypatch.setitem(CONFIG, "mimo_video_api_url", original_url)
    other_video = tmp_path / "other.mp4"
    other_video.write_bytes(b"other-video")
    assert not mimo_video_overview_cache_fresh(overview_path, other_video, FINAL_SCENES)

    changed_scenes = [{"scene_id": 1, "start": 0.0, "end": 5.0}]
    assert not mimo_video_overview_cache_fresh(overview_path, video, changed_scenes)


def test_final_overview_cache_rejects_unusable_cached_chunk(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake-video")
    monkeypatch.setitem(CONFIG, "mimo_video_model", "mimo-v2.5")
    monkeypatch.setitem(CONFIG, "vlm_model", "mimo-v2.5")
    monkeypatch.setitem(CONFIG, "mimo_video_chunk_max_seconds", 2)
    monkeypatch.setitem(CONFIG, "mimo_video_chunk_min_seconds", 0.5)
    chunks = [
        {"chunk_id": 0, "scene_id": 1, "start": 0.0, "end": 2.0, "content": "有效"},
        {"chunk_id": 1, "scene_id": 1, "start": 2.0, "end": 3.0, "content": ""},
    ]
    overview_path = tmp_path / "mimo_video_overview.json"
    overview_path.write_text(json.dumps({
        "input": "scene_chunks",
        "content": "有效\n(MiMo 未返回内容)",
        "chunks": chunks,
        "chunks_fingerprint": _mimo_cached_chunks_fingerprint(chunks),
        "overview_fingerprint": "stale",
        "source_video_fingerprint": file_fingerprint(video),
        "settings": mimo_video_settings_fingerprint(),
    }), encoding="utf-8")

    assert not mimo_video_overview_cache_fresh(overview_path, video, FINAL_SCENES)


# ── MiMo video overview: moderation / empty chunk degradation ────────────────


def test_all_rejected_chunks_skip_overview(monkeypatch, tmp_path):
    """When MiMo rejects every chunk (content moderation) the overview returns None and
    writes neither the canonical file nor a partial, instead of polluting the brief."""
    rejected = "The request was rejected because it was considered high risk"
    video = _overview_setup(
        monkeypatch, tmp_path, chunk_api=lambda p, c: _chunk_result(p, c, content=rejected)
    )

    overview = analyze_video_overview(video, tmp_path, FINAL_SCENES)
    assert overview is None
    assert not (tmp_path / "mimo_video_overview.json").exists()
    assert not (tmp_path / "mimo_video_overview.partial.json").exists()


def test_is_mimo_chunk_usable():
    assert _is_mimo_chunk_usable("范闲在竹林中打斗，剑光凌厉") is True
    assert _is_mimo_chunk_usable("") is False
    assert _is_mimo_chunk_usable("The request was rejected because it was considered high risk") is False
    assert _is_mimo_chunk_usable("内容审核未通过") is False


def test_mixed_unusable_chunks_degrade_to_usable_overview(monkeypatch, tmp_path):
    """A single empty/refused chunk degrades the overview to the usable chunks (partial=true)
    instead of aborting; the affected scene falls back to its frame description downstream."""
    video = _overview_setup(
        monkeypatch,
        tmp_path,
        chunk_api=lambda p, c: _chunk_result(p, c, content="" if c["chunk_id"] == 1 else None),
    )

    overview = analyze_video_overview(video, tmp_path, [{"scene_id": 1, "start": 0.0, "end": 5.0}])

    assert overview is not None
    assert overview["partial"] is True
    assert overview["unusable_chunk_count"] >= 1
    final = json.loads((tmp_path / "mimo_video_overview.json").read_text(encoding="utf-8"))
    assert len(final["chunks"]) >= 1
    assert all(item["content"] for item in final["chunks"])
    # the incremental partial cache is cleaned up once the final overview is written
    assert not (tmp_path / "mimo_video_overview.partial.json").exists()
