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
        / "video-recap"
        / "scripts"
    ),
)

from lib import CONFIG  # noqa: E402
from vlm import _parse_vlm_depth_response, analyze_scenes  # noqa: E402


def test_parse_frame_facts_splits_chinese_and_english_punctuation():
    raw = """【描述】
门口对峙
【帧标签】
1.0s | 男子握紧拳头，女子后退一步; 门缓缓关上、灯光变暗；气氛紧张
【深层分析】
关系破裂"""

    _description, _depth, facts = _parse_vlm_depth_response(raw)

    assert facts["1.0"] == ["男子握紧拳头", "女子后退一步", "门缓缓关上", "灯光变暗", "气氛紧张"]


def test_parse_frame_facts_drops_lines_whose_timestamp_is_not_a_number():
    raw = """【描述】
门口对峙
【帧标签】
. | 男子转身
1.2.3s | 女子回头
2s | 门关上
【深层分析】
关系破裂"""

    _description, _depth, facts = _parse_vlm_depth_response(raw)

    assert facts == {"2": ["门关上"]}


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


def test_moderation_refusal_is_recorded_as_unreadable_not_as_a_description(monkeypatch, tmp_path):
    frames = _scene_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "vlm.api_call",
        lambda payload: _reply("The request was rejected because it was considered high risk"),
    )

    analyses = analyze_scenes(ONE_SCENE, frames, tmp_path)

    assert analyses[0]["analysis_status"] == "moderation_refused"
    assert "high risk" not in json.dumps(analyses, ensure_ascii=False)
    assert analyses[0]["description"] == _parse_vlm_depth_response("")[0]


def test_asr_moderation_refusal_is_stored_as_no_text(monkeypatch, tmp_path):
    import asr

    wav = tmp_path / "seg.wav"
    wav.write_bytes(b"RIFF0000WAVE")
    monkeypatch.setattr(
        asr, "mimo_asr_api_call",
        lambda payload: _reply("The request was rejected because it was considered high risk"),
    )
    assert asr._run_asr(wav) == ""

    # Real dialogue that merely mentions rules or risk is kept.
    monkeypatch.setattr(asr, "mimo_asr_api_call", lambda payload: _reply("这事违规，风险太高了"))
    assert asr._run_asr(wav) == "这事违规，风险太高了"

    # English dialogue that happens to contain one marker is a transcript, not a refusal.
    line = "My request was rejected by the board, so we start over tomorrow."
    monkeypatch.setattr(asr, "mimo_asr_api_call", lambda payload: _reply(line))
    assert asr._run_asr(wav) == line


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
    assert len(cache["scenes"]) == 2  # the two scenes that succeeded are cached for resume

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
    cache = json.loads((tmp_path / "vlm_scene_cache.json").read_text(encoding="utf-8"))
    assert len(cache["scenes"]) == 2

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


def test_refusal_markers_are_defined_once_in_lib():
    """The provider's refusal wording lives in lib.py only."""
    import ast

    import lib

    scripts = Path(lib.__file__).parent
    for path in scripts.rglob("*.py"):
        if path.name == "lib.py" or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                low = node.value.lower()
                assert not any(m in low for m in lib.MODERATION_REFUSAL_MARKERS), (
                    f"{path.name}:{node.lineno} carries its own MiMo refusal marker; "
                    "import it from lib"
                )
