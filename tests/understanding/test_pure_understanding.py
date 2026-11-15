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
        / "video-recap"
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

    payload = _prepare_api_payload({"model": "mimo-v2.6-flash", "max_tokens": 7})
    assert payload["max_completion_tokens"] == 7
    assert payload["thinking"] == {"type": "disabled"}
    assert "max_tokens" not in payload

    # TTS (including voiceclone/voicedesign) and ASR models must NOT get a `thinking` field.
    tts_payload = _prepare_api_payload({"model": "mimo-v2.5-tts", "max_tokens": 7})
    assert tts_payload["max_completion_tokens"] == 7
    assert "thinking" not in tts_payload
    clone_payload = _prepare_api_payload({"model": "mimo-v2.5-tts-voiceclone", "max_tokens": 7})
    assert "thinking" not in clone_payload
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
    scripts_dir = Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts"
    env = {k: v for k, v in os.environ.items() if not k.startswith("MIMO_")}
    env["MIMO_ASR_API_KEY"] = "asr-only-key"
    env["PYTHONIOENCODING"] = "utf-8"  # the message is Chinese; Windows pipes default to cp1252
    out = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script, str(scripts_dir)],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=True, cwd=tmp_path,
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


def test_negative_silence_start_and_english_period_sentence_anchors(monkeypatch, tmp_path):
    from detect import detect_silence_periods

    video = tmp_path / "src.mp4"
    video.write_bytes(b"video")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"\x00" * 32000 * 6)
    monkeypatch.setattr("detect._audio_cache_matches", lambda *_a: True)
    monkeypatch.setattr("detect.get_video_duration", lambda *_a: 6.0)

    stderr_main = (
        "[silencedetect @ 0x1] silence_start: -0.0013\n"
        "[silencedetect @ 0x1] silence_end: 1.80 | silence_duration: 1.8013\n"
        "[silencedetect @ 0x1] silence_start: 4.10\n"
        "[silencedetect @ 0x1] silence_end: 5.90 | silence_duration: 1.80\n"
    )
    stderr_pauses = (
        "[silencedetect @ 0x2] silence_start: -0.0013\n"
        "[silencedetect @ 0x2] silence_end: 0.30 | silence_duration: 0.3013\n"
        "[silencedetect @ 0x2] silence_start: 2.85\n"
        "[silencedetect @ 0x2] silence_end: 3.15 | silence_duration: 0.30\n"
        "[silencedetect @ 0x2] silence_start: 5.60\n"
        "[silencedetect @ 0x2] silence_end: 5.95 | silence_duration: 0.35\n"
    )

    def fake_run(cmd, **_kwargs):
        af = cmd[cmd.index("-af") + 1] if "-af" in cmd else ""
        err = stderr_pauses if f"d={CONFIG['source_boundary_min_pause']}" in af else stderr_main
        return CompletedProcess(cmd, 0, stdout="", stderr=err)

    monkeypatch.setattr("detect.run_cmd", fake_run)
    asr_rows = [{"start": 0.4, "end": 5.8, "text": "Wait 1.5 seconds. Stop right here."}]
    periods = detect_silence_periods(video, tmp_path, asr_result=asr_rows)
    assert [(p["start"], p["end"]) for p in periods] == [(0.0, 1.8), (4.1, 5.9)]

    anchors = json.loads((tmp_path / "speech_boundary_anchors.json").read_text(encoding="utf-8"))
    tails = [a["text_tail"] for a in anchors["sentence_anchors"]]
    assert len(tails) == 2
    assert tails[0].endswith("1.5 seconds.")
    assert tails[1].endswith("right here.")


def test_asr_segment_and_transcribe_keeps_subsecond_tail(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "asr_segment_seconds", 15.0)
    monkeypatch.setattr(
        asr_module,
        "run_cmd",
        lambda *a, **k: CompletedProcess(a[0] if a else [], 0, "", ""),
    )
    monkeypatch.setattr(asr_module, "_run_asr", lambda wav: "尾句")
    segs = asr_module._segment_and_transcribe(tmp_path / "audio.wav", tmp_path, 15.4)
    assert [(s["start"], s["end"]) for s in segs] == [(0, 15), (15, 15.4)]


def test_speech_boundary_anchors_skip_ellipsis_and_abbreviations_and_catch_timeout(
    monkeypatch, tmp_path
):
    import subprocess as sp
    import detect

    (tmp_path / "audio.wav").write_bytes(b"RIFF")
    asr_rows = [{"start": 0.0, "end": 8.0, "text": "Wait... Dr. House arrived in the U.S. Now stop."}]
    stderr = (
        "[silencedetect @ 0x1] silence_start: 7.40\n"
        "[silencedetect @ 0x1] silence_end: 7.85 | silence_duration: 0.45\n"
    )
    monkeypatch.setattr(detect, "get_video_duration", lambda _p: 8.0)
    monkeypatch.setattr(
        detect, "run_cmd", lambda cmd, **kw: CompletedProcess(cmd, 0, "", stderr)
    )
    report = detect.detect_speech_boundary_anchors(tmp_path, asr_rows)
    tails = [a["text_tail"] for a in report["sentence_anchors"]]
    assert len(tails) == 1
    assert tails[0].endswith("Now stop.")

    def _raise_timeout(cmd, **kw):
        raise sp.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout", 120))

    monkeypatch.setattr(detect, "run_cmd", _raise_timeout)
    timed_out = detect.detect_speech_boundary_anchors(tmp_path, asr_rows)
    assert timed_out["status"] == "failed"
    assert timed_out["reason"] == "silencedetect_timeout"


def test_plan_asr_windows_splits_at_acoustic_valley_and_trims_dead_silence(tmp_path):
    import array
    import wave

    wav_path = tmp_path / "audio.wav"
    sr = 16000
    dur = 25.0
    samples = array.array("h", [0] * int(sr * dur))
    # Active loud speech/BGM across 1.5s..23.0s (~ -10 dBFS = 10000), EXCEPT a quiet BGM valley
    # around 12.40s..12.80s (amplitude 1200 ~ -28.7 dBFS), while 15.0s is loud mid-word!
    for i in range(int(1.5 * sr), int(23.0 * sr)):
        t = i / sr
        amp = 1200 if 12.40 <= t <= 12.80 else 10000
        samples[i] = amp if (i % 2 == 0) else -amp
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())

    windows = asr_module._plan_asr_windows(wav_path, dur, 15)
    assert len(windows) == 2
    # Leading [0..1.5s] and trailing [23.0..25.0s] dead silences are trimmed with ~0.15s pad,
    # and the split lands in the 12.40..12.80s valley rather than blind 15.0s!
    assert 1.2 <= windows[0][0] <= 1.5
    assert 12.40 <= windows[0][1] <= 12.85
    assert windows[1][0] == windows[0][1]
    assert 23.0 <= windows[1][1] <= 23.3


def test_brief_surfaces_hardcoded_subtitles_and_tail_credits_callouts(tmp_path):
    from briefing.builder import build_agent_brief

    scenes = [
        {"scene_id": 0, "start": 0.0, "end": 20.0, "description": "少年挥剑，底部配有台词字幕"},
        {"scene_id": 1, "start": 20.0, "end": 40.0, "description": "两人对视，下方显示字幕"},
        {"scene_id": 2, "start": 40.0, "end": 65.0, "description": "废墟坍塌，底部有字幕提示"},
        {"scene_id": 3, "start": 70.0, "end": 80.0, "description": "画面右侧滚动显示演职员表"},
        {"scene_id": 4, "start": 80.0, "end": 90.0, "description": "片尾字幕滚动，列出制作人员"},
        {"scene_id": 5, "start": 90.0, "end": 100.0, "description": "下集预告画面闪回"},
    ]
    brief = build_agent_brief(scenes, [], [], 100.0, tmp_path).read_text(encoding="utf-8")
    assert "检测到源片多处画面带有内嵌硬字幕" in brief
    assert "检测到片尾演职员表 / 预告区间参考" in brief
    assert "70.0s–100.0s" in brief



