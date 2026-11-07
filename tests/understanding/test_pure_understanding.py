import json
import os
import subprocess
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
import asr as asr_module  # noqa: E402
from detect import detect_scenes  # noqa: E402
from extract import extract_frames  # noqa: E402
from lib import (  # noqa: E402
    CONFIG,
    _api_headers,
    _prepare_api_payload,
    _retry_after_seconds,
    default_mimo_api_url,
    env_bool,
    env_float,
    env_int,
    get_video_duration,
    is_mimo_token_plan_key,
    load_background_research,
    normalize_api_url,
)
from timeline_fusion import _build_timeline_fusion  # noqa: E402
from understanding_brief import _research_context  # noqa: E402
from vlm import analyze_scenes  # noqa: E402


def test_get_video_duration_raises_for_unparseable_output(monkeypatch):
    def fake_run_cmd(cmd):
        return CompletedProcess(cmd, 0, stdout="N/A\n", stderr="")

    monkeypatch.setattr("lib.run_cmd", fake_run_cmd)
    with pytest.raises(RuntimeError, match="无法解析"):
        get_video_duration("bad.mp4")


def test_get_video_duration_raises_with_ffprobe_stderr(monkeypatch):
    def fake_run_cmd(cmd):
        return CompletedProcess(cmd, 1, stdout="", stderr="bad.mp4: Invalid data found")

    monkeypatch.setattr("lib.run_cmd", fake_run_cmd)
    with pytest.raises(RuntimeError, match="Invalid data found"):
        get_video_duration("bad.mp4")


def test_retry_after_seconds_accepts_malformed_header():
    assert _retry_after_seconds("not-a-number-or-date", 4) == 4


@pytest.mark.parametrize(
    "raw",
    ["https://example.com/v1", "https://example.com/v1/", "https://example.com/v1/chat/completions"],
)
def test_normalize_api_url_accepts_base_or_full_endpoint(raw):
    assert normalize_api_url(raw) == "https://example.com/v1/chat/completions"


def test_mimo_token_plan_key_uses_an_explicit_cluster(monkeypatch):
    # The credential is classified here and never handed to the URL builder, so no
    # secret flows into a value that doctor.py prints.
    assert is_mimo_token_plan_key("tp-example") is True
    assert is_mimo_token_plan_key("sk-example") is False

    assert default_mimo_api_url(True) == "https://token-plan-cn.xiaomimimo.com/v1"
    assert (
        default_mimo_api_url(True, cluster="sgp")
        == "https://token-plan-sgp.xiaomimimo.com/v1"
    )
    assert default_mimo_api_url(False) == "https://api.xiaomimimo.com/v1"

    monkeypatch.setenv("MIMO_TOKEN_PLAN_CLUSTER", "unknown")
    with pytest.raises(ValueError, match="token-plan cluster.*unknown"):
        default_mimo_api_url(True)


def test_env_int_and_float_helpers_reject_bad_values(monkeypatch):
    monkeypatch.setenv("BAD_INT", "not-an-int")
    monkeypatch.setenv("LOW_INT", "-3")
    monkeypatch.setenv("YES_BOOL", "on")
    monkeypatch.setenv("NO_BOOL", "0")
    monkeypatch.setenv("BAD_FLOAT", "nope")
    monkeypatch.setenv("LOW_FLOAT", "-1.5")
    monkeypatch.setenv("NONFINITE_FLOAT", "nan")

    with pytest.raises(ValueError, match="BAD_INT.*not-an-int"):
        env_int("BAD_INT", 8, minimum=1)
    with pytest.raises(ValueError, match="LOW_INT.*>= 1.*-3"):
        env_int("LOW_INT", 8, minimum=1)
    assert env_bool("YES_BOOL") is True
    assert env_bool("NO_BOOL", default=True) is False
    with pytest.raises(ValueError, match="BAD_FLOAT.*nope"):
        env_float("BAD_FLOAT", 0.5, minimum=0)
    with pytest.raises(ValueError, match="LOW_FLOAT.*>= 0.*-1.5"):
        env_float("LOW_FLOAT", 0.5, minimum=0)
    with pytest.raises(ValueError, match="NONFINITE_FLOAT.*finite.*nan"):
        env_float("NONFINITE_FLOAT", 0.5, minimum=0)


def test_detect_scenes_respects_zero_threshold(monkeypatch, tmp_path):
    commands = []

    def fake_run_cmd(cmd, **kwargs):
        commands.append(cmd)
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("detect.run_cmd", fake_run_cmd)
    monkeypatch.setattr("detect.get_video_duration", lambda video: 12.5)
    scenes = detect_scenes(Path("video.mp4"), tmp_path, threshold=0.0)

    assert "scdet=threshold=0" in commands[0]
    assert scenes == [{"start": 0.0, "end": 12.5}]


def test_extract_frames_rejects_non_positive_fps(tmp_path):
    with pytest.raises(ValueError, match="fps 必须大于 0"):
        extract_frames(Path("video.mp4"), tmp_path, fps=0)


def test_analyze_scenes_rejects_empty_frames(tmp_path):
    with pytest.raises(RuntimeError, match="frames 为空"):
        analyze_scenes([{"start": 0.0, "end": 1.0}], [], tmp_path)


def test_mimo_api_headers_and_payload_mapping(monkeypatch):
    monkeypatch.setitem(CONFIG, "api_key", "secret")

    # MiMo is the only provider: always an api-key header, never Bearer.
    headers = _api_headers()
    assert headers["api-key"] == "secret"
    assert "Authorization" not in headers

    payload = _prepare_api_payload({"model": "mimo-v2.5", "max_tokens": 7})
    assert payload["max_completion_tokens"] == 7
    assert payload["thinking"] == {"type": "disabled"}
    assert "max_tokens" not in payload

    # TTS and ASR models must NOT get a `thinking` field (no text-reasoning budget).
    tts_payload = _prepare_api_payload({"model": "mimo-v2.5-tts", "max_tokens": 7})
    assert tts_payload["max_completion_tokens"] == 7
    assert "thinking" not in tts_payload
    asr_payload = _prepare_api_payload({"model": "mimo-v2.5-asr"})
    assert "thinking" not in asr_payload


def test_mimo_asr_401_names_the_env_var_that_supplied_the_key(tmp_path):
    """A MIMO_ASR_API_KEY rejected with 401 is reported under its own name, not MIMO_API_KEY."""
    script = """
import io, sys, urllib.error, urllib.request
sys.path.insert(0, sys.argv[1])
import lib

def reject(*_a, **_k):
    raise urllib.error.HTTPError("u", 401, "unauthorized", {}, io.BytesIO(b"{}"))

urllib.request.urlopen = reject
try:
    lib.mimo_asr_api_call({"model": "m"}, max_retries=1)
except RuntimeError as exc:
    print(exc)
"""
    scripts_dir = Path(__file__).resolve().parents[2] / "skills" / "video-understanding" / "scripts"
    env = {k: v for k, v in os.environ.items() if not k.startswith("MIMO_")}
    env["MIMO_ASR_API_KEY"] = "asr-only-key"
    env["PYTHONIOENCODING"] = "utf-8"  # the message is Chinese; Windows pipes default to cp1252
    out = subprocess.run(
        [sys.executable, "-c", script, str(scripts_dir)],
        env=env, capture_output=True, text=True, encoding="utf-8", check=True, cwd=tmp_path,
    ).stdout
    assert "401" in out and "MIMO_ASR_API_KEY" in out


def test_research_context_feeds_vlm_from_background_research(tmp_path):
    assert _research_context(tmp_path) == ""  # no research file -> empty context

    (tmp_path / "background_research.json").write_text(
        json.dumps(
            {
                "synopsis": "现代灵魂穿越古代的故事。",
                "episode_context": "第一集，主角范闲登场。",
                "characters": {
                    "范闲": "核心男主角",
                    "张庆": "年轻黑衣男子，戴眼镜，大学生",
                },
                "character_details": {
                    "范闲": {
                        "aliases": ["小范大人"],
                        "role": "主角",
                        "relationships": ["与五竹互相信任"],
                    },
                },
                "plot_arcs": [
                    {
                        "name": "身世线",
                        "description": "查清母亲死亡真相",
                        "status": "进行中",
                    }
                ],
                "cultural_notes": [{"item": "监察院", "explanation": "权力机构"}],
                "noise": "x" * 5000,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    ctx = _research_context(tmp_path)

    assert "现代灵魂穿越古代" in ctx
    assert "范闲（核心男主角）" in ctx
    assert "张庆（年轻黑衣男子，戴眼镜，大学生）" in ctx
    assert "小范大人" in ctx
    assert "与五竹互相信任" in ctx
    assert "身世线" in ctx and "查清母亲死亡真相" in ctx
    assert "监察院" in ctx
    assert "noise" not in ctx
    assert len(ctx) <= 1200  # bounded so it never blows the VLM prompt budget


def test_segment_and_transcribe_uses_configured_window(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "asr_segment_seconds", 30.0)
    monkeypatch.setattr(
        asr_module,
        "run_cmd",
        lambda *a, **k: CompletedProcess(a[0] if a else [], 0, "", ""),
    )
    monkeypatch.setattr(asr_module, "_run_asr", lambda wav: "对白")
    segs = asr_module._segment_and_transcribe(tmp_path / "audio.wav", tmp_path, 100.0)
    assert len(segs) == 4  # 30s windows over 100s: 0-30,30-60,60-90,90-100
    assert segs[0]["start"] == 0 and segs[0]["end"] == 30
    assert segs[-1]["end"] == 100
    assert all(s["text"] == "对白" for s in segs)


def test_load_background_research_missing_is_empty_and_malformed_raises(tmp_path):
    assert load_background_research(tmp_path) == {}
    (tmp_path / "background_research.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="不是合法 JSON"):
        load_background_research(tmp_path)
    (tmp_path / "background_research.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON 对象"):
        load_background_research(tmp_path)


def test_timeline_fusion_aligns_scenes_dialogue_and_quiet_slots():
    fusion = _build_timeline_fusion(
        [
            {
                "scene_id": 0,
                "start": 0.0,
                "end": 10.0,
                "description": "对峙",
                "frame_facts": {"1.0": ["看门"]},
            }
        ],
        [
            {"start": 2.0, "end": 4.0, "text": "你到底是谁"},
            {"start": 8.0, "end": 12.0, "text": "跨场对白"},
        ],
        [
            {"start": 0.0, "end": 1.0, "duration": 1.0, "has_speech": False},
            {"start": 5.0, "end": 7.0, "duration": 2.0, "has_speech": False},
            {"start": 9.0, "end": 9.5, "duration": 0.5, "has_speech": True},
        ],
    )

    item = fusion[0]
    assert item["dialogue_overlap_seconds"] == 4.0
    assert [seg["text"] for seg in item["dialogue_segments"]] == [
        "你到底是谁",
        "跨场对白",
    ]
    assert [(slot["start"], slot["end"]) for slot in item["narration_slots"]] == [
        (0.0, 1.0),
        (5.0, 7.0),
    ]
    assert item["frame_facts"] == {"1.0": ["看门"]}
