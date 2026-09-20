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
    file_fingerprint,
    get_video_duration,
    is_mimo_token_plan_key,
    normalize_api_url,
    step_cache_key,
)
from understanding_brief import _research_context  # noqa: E402
from vlm import (  # noqa: E402
    _mimo_video_chunks,
    _video_data_url,
    analyze_scenes,
    analyze_video_overview,
)


def test_get_video_duration_returns_zero_for_unparseable_output(monkeypatch):
    def fake_run_cmd(cmd):
        return CompletedProcess(cmd, 0, stdout="N/A\n", stderr="")

    monkeypatch.setattr("lib.run_cmd", fake_run_cmd)
    assert get_video_duration("bad.mp4") == 0.0


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


def test_mimo_video_overview_uses_scene_chunks(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake-video")
    seen_payloads = []

    def fake_api_call(payload):
        seen_payloads.append(payload)
        return {
            "model": "mimo-v2.5",
            "choices": [
                {"message": {"content": "分片概览", "reasoning_content": "推理内容"}}
            ],
            "usage": {"prompt_tokens_details": {"video_tokens": 12}},
        }

    def fake_extract(video_path, chunk, output_path):
        output_path.write_bytes(b"tiny-chunk")
        return output_path

    monkeypatch.setitem(CONFIG, "mimo_video_api_key", "mimo-secret")
    monkeypatch.setitem(CONFIG, "mimo_video_model", "mimo-v2.5")
    monkeypatch.setitem(CONFIG, "mimo_video_overview", True)
    monkeypatch.setitem(CONFIG, "mimo_video_fps", 2)
    monkeypatch.setitem(CONFIG, "mimo_video_chunk_max_seconds", 2)
    monkeypatch.setitem(CONFIG, "mimo_video_chunk_min_seconds", 0.5)
    monkeypatch.setitem(CONFIG, "mimo_media_resolution", "max")
    monkeypatch.setattr("vlm._extract_video_chunk", fake_extract)
    monkeypatch.setattr("vlm.mimo_video_api_call", fake_api_call)

    overview = analyze_video_overview(
        video, tmp_path, [{"scene_id": 7, "start": 0.0, "end": 5.0}]
    )

    assert overview["input"] == "scene_chunks"
    assert overview["chunk_count"] == 3
    assert overview["model"] == "mimo-v2.5"
    assert len(seen_payloads) == 3
    for payload in seen_payloads:
        video_part = payload["messages"][0]["content"][0]
        assert payload["model"] == "mimo-v2.5"
        assert payload["max_tokens"] == 1200
        assert video_part["type"] == "video_url"
        assert video_part["video_url"]["url"].startswith("data:video/mp4;base64,")
        assert video_part["fps"] == 2
        assert video_part["media_resolution"] == "max"
    assert "分片概览" in overview["content"]
    assert overview["settings"]["mimo_video_chunk_max_seconds"] == 2
    assert all(not Path(item["clip_path"]).is_absolute() for item in overview["chunks"])
    assert all(
        item["clip_path"].startswith("mimo_video_chunks/")
        for item in overview["chunks"]
    )
    assert (tmp_path / "mimo_video_overview.json").exists()


def test_mimo_video_chunks_split_on_scene_boundaries(monkeypatch):
    monkeypatch.setitem(CONFIG, "mimo_video_chunk_max_seconds", 2)
    monkeypatch.setitem(CONFIG, "mimo_video_chunk_min_seconds", 0.5)

    chunks = _mimo_video_chunks([{"scene_id": 3, "start": 0.0, "end": 5.0}])

    assert [(c["scene_id"], c["start"], c["end"]) for c in chunks] == [
        (3, 0.0, 2.0),
        (3, 2.0, 4.0),
        (3, 4.0, 5.0),
    ]


def test_mimo_video_overview_embeds_small_local_chunk(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"tiny")
    monkeypatch.setitem(CONFIG, "mimo_video_base64_max_mb", 1)

    data_url = _video_data_url(video)

    assert data_url.startswith("data:video/mp4;base64,")


def test_content_fingerprint_cache_keys_ignore_path_and_mtime(tmp_path):
    first = tmp_path / "a.mp4"
    second = tmp_path / "nested" / "b.mp4"
    second.parent.mkdir()
    first.write_bytes(b"same video bytes" * 100)
    second.write_bytes(first.read_bytes())

    assert file_fingerprint(first) == file_fingerprint(second)
    assert step_cache_key(first, "vlm", {"model": "x"}) == step_cache_key(
        second, "vlm", {"model": "x"}
    )
    assert step_cache_key(first, "vlm", {"model": "x"}) != step_cache_key(
        first, "vlm", {"model": "y"}
    )


def test_content_fingerprint_detects_middle_only_changes(tmp_path):
    first = tmp_path / "a.mp4"
    second = tmp_path / "b.mp4"
    first.write_bytes(b"A" * 70000 + b"middle-one" + b"Z" * 70000)
    second.write_bytes(b"A" * 70000 + b"middle-two" + b"Z" * 70000)

    assert first.stat().st_size == second.stat().st_size
    assert first.read_bytes()[:65536] == second.read_bytes()[:65536]
    assert first.read_bytes()[-65536:] == second.read_bytes()[-65536:]
    assert file_fingerprint(first) != file_fingerprint(second)


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
