import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "skills" / "video-assemble" / "scripts"),
)
import importlib
import json
import wave
import pytest
from subprocess import CompletedProcess
import assembly_contract
import audio_mix
import media
import narration_audio
import render_preflight
import source_subtitles
import subtitles.render as subtitle_render
import timeline_emit
import visual_render
from assemble import assemble_video
from tts_fixtures import tts_segment

_FAKE_QC = {
    "verdict": "PASS", "blocking": False, "blocking_codes": [], "loudness_mode": None,
    "loudnorm_measurement": None, "audio_operations": {}, "adopted_audio": None,
}
_DELIVERY = {
    "video_encode_passes": 0, "reencode_reason": [], "audio_sample_rate": 48000,
    "final_compat_notes": ["video_copy", "aac_48000", "faststart"],
}

_FAKE_QC = {
    "verdict": "PASS", "blocking": False, "blocking_codes": [], "loudness_mode": None,
    "loudnorm_measurement": None, "audio_operations": {}, "adopted_audio": None,
}
_DELIVERY = {
    "video_encode_passes": 0, "reencode_reason": [], "audio_sample_rate": 48000,
    "final_compat_notes": ["video_copy", "aac_48000", "faststart"],
}
from assembly_contract import _resolve_final_output
from assembly_settings import assembly_settings_payload
from audio_mix import _build_audio_filter_complex, final_loudnorm_filter
from media import _build_video_clips
from subtitles.core import (
    _split_subtitle_chunks,
    _subtitle_entries,
    _seconds_to_ass_time,
    _seconds_to_srt_time,
)
from subtitles.render import _escape_ass_text, _generate_ass, _generate_srt
from timeline_emit import _emit_timeline
from visual_render import (
    _output_downscale_filter,
    _source_subtitle_mask_filter,
    _subtitle_burn_filter,
)
import lib
from lib import CONFIG, env_float
import assemble


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
def test_env_float_rejects_nonfinite_values(monkeypatch, raw):
    monkeypatch.setenv("NONFINITE_FLOAT", raw)

    with pytest.raises(ValueError, match="NONFINITE_FLOAT.*有限"):
        env_float("NONFINITE_FLOAT", 1.0, minimum=0.0)


def _assembly_manifest_payload(
    input_video,
    tts_segments,
    work_dir,
    output_path,
    tts_meta_path=None,
    final_output=None,
):
    return assembly_contract._assembly_manifest_payload(
        input_video,
        tts_segments,
        work_dir,
        output_path,
        tts_meta_path=tts_meta_path,
        final_output=final_output,
        settings_payload=assembly_settings_payload,
    )


def _adjust_tts_speed(*args, **kwargs):
    return narration_audio._adjust_tts_speed(
        *args,
        command_runner=narration_audio.run_cmd,
        duration_probe=narration_audio.get_video_duration,
        logger=narration_audio.log,
        **kwargs,
    )


def _apply_narration_speed(*args, **kwargs):
    return narration_audio._apply_narration_speed(
        *args,
        command_runner=narration_audio.run_cmd,
        duration_probe=narration_audio.get_video_duration,
        logger=narration_audio.log,
        **kwargs,
    )


def _canvas(width=1280, height=720, fps=30.0):
    return media._canvas_from_stream(
        {
            "width": width,
            "height": height,
            "r_frame_rate": f"{int(fps)}/1",
            "sample_aspect_ratio": "1:1",
            "display_aspect_ratio": f"{width}:{height}",
        }
    )


def _write_silent_wav(path, seconds, sample_rate=44100, frame=b"\0\0"):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frame * int(seconds * sample_rate))
    return path


def _mock_assemble_media(monkeypatch, *, duration=4.0, has_audio=True):
    canvas = _canvas()
    monkeypatch.setattr(assemble.lib, "get_video_duration", lambda _path: duration)
    monkeypatch.setattr(media, "_probe_canvas", lambda _path: canvas)
    monkeypatch.setattr(media, "_has_audio_stream", lambda _path: has_audio)
    monkeypatch.setattr(
        narration_audio, "_apply_narration_speed", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(timeline_emit, "_emit_timeline", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        audio_mix, "_run_loudnorm_first_pass", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        assembly_contract,
        "_build_assembly_qc",
        lambda *_args, **_kwargs: {"blocking": False, "blocking_codes": []},
    )


def _build_timed_narration(*args, **kwargs):
    return narration_audio._build_timed_narration(
        *args,
        adjust_speed=narration_audio._adjust_tts_speed,
        command_runner=narration_audio.run_cmd,
        logger=narration_audio.log,
        **kwargs,
    )


def _volume_expr_from_filter(filter_complex):
    marker = "volume='"
    start = filter_complex.index(marker) + len(marker)
    end = filter_complex.index("':eval=frame", start)
    return filter_complex[start:end]


def _eval_duck_expr(filter_complex, t):
    expr = _volume_expr_from_filter(filter_complex)
    return eval(
        expr,
        {"__builtins__": {}},
        {
            "t": float(t),
            "min": min,
            "max": max,
            "between": lambda value, lo, hi: 1.0 if lo <= value <= hi else 0.0,
        },
    )


def _adjust_result_parts(result):
    assert isinstance(result, tuple), (
        "_adjust_tts_speed should return path, duration, metadata"
    )
    assert len(result) >= 3, (
        "P0 requires machine-readable fit metadata from _adjust_tts_speed"
    )
    return result[0], result[1], result[2]


def _duck_config(monkeypatch, **overrides):
    values = {
        "ducking_mode": "fixed",
        "idle_orig_volume": 0.85,
        "speech_ducking_volume": 0.2,
        "zone_ducking_volume": 0.12,
        "duck_fade_seconds": 0.25,
        **overrides,
    }
    for key, value in values.items():
        monkeypatch.setitem(CONFIG, key, value)


def _write_legacy_anchors(work_dir, sentence_anchors):
    (work_dir / "speech_boundary_anchors.json").write_text(
        json.dumps({"sentence_anchors": sentence_anchors}), encoding="utf-8"
    )
    (work_dir / "asr_result.json").write_text(
        json.dumps([{"start": 0, "end": 30, "text": "持续原声。"}]), encoding="utf-8"
    )


def _write_cut_output_anchors(work_dir, evidence, *, plan=None, stale=False):
    """Write anchors at least as new as the validated plan; ``stale`` backdates them."""
    import os

    plan = {} if plan is None else plan
    plan_path = work_dir / "clip_plan_validated.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    payload = {"schema_version": 2, "timeline": "cut_output", **evidence}
    anchors = work_dir / "speech_boundary_anchors_output.json"
    anchors.write_text(json.dumps(payload), encoding="utf-8")
    if stale:
        older = plan_path.stat().st_mtime_ns - 1_000_000_000
        os.utime(anchors, ns=(older, older))


def test_adjust_tts_speed_derives_outputs_from_audio_name_only(monkeypatch, tmp_path):
    """Parent dirs containing .wav must not redirect adjusted files outside the audio dir."""
    wav_parent = tmp_path / "episode.wav-cache"
    wav_parent.mkdir()
    src = wav_parent / "narr_000.wav"
    src.write_bytes(b"wav")
    commands = []

    def fake_run_cmd(cmd, **kw):
        commands.append(cmd)
        Path(cmd[-1]).write_bytes(b"adjusted")
        return CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(
        narration_audio,
        "get_video_duration",
        lambda path: 2.2 if Path(path) == src else 2.0,
    )
    monkeypatch.setattr(narration_audio, "run_cmd", fake_run_cmd)

    adjusted, actual_dur, meta = _adjust_result_parts(
        _adjust_tts_speed(src, target_duration=2.0)
    )

    assert actual_dur == 2.0
    assert Path(adjusted) == wav_parent / "narr_000_adj.wav"
    assert meta["fit_status"] in {"fit", "tempo_adjusted"}
    assert commands[-1][-1] == str(wav_parent / "narr_000_adj.wav")


def test_build_video_clips_prefers_newer_validated_cut_plan(
    monkeypatch, tmp_path
):
    original = tmp_path / "original.mp4"
    edited = tmp_path / "edited_source.mp4"
    original.write_bytes(b"orig")
    edited.write_bytes(b"edited")

    raw_payload = {"clips": [{"source_start": 99.0, "source_end": 100.0}]}
    (tmp_path / "clip_plan.json").write_text(json.dumps(raw_payload), encoding="utf-8")
    (tmp_path / "clip_plan_validated.json").write_text(
        json.dumps(
            {
                "clips": [
                    {
                        "clip_id": 0,
                        "source_start": 10.0,
                        "source_end": 20.0,
                        "output_start": 0.0,
                        "output_end": 10.0,
                    },
                    {
                        "clip_id": 1,
                        "source_start": 40.0,
                        "source_end": 45.0,
                        "output_start": 10.0,
                        "output_end": 15.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(CONFIG, "source_video", str(original))
    monkeypatch.setitem(CONFIG, "source_video_explicit", True)

    clips = _build_video_clips(edited, tmp_path, duration_s=15.0)

    assert clips == [
        {
            "source_id": None,
            "source_path": str(original),
            "source_start": 10.0,
            "source_end": 20.0,
            "timeline_start": 0.0,
            "timeline_end": 10.0,
        },
        {
            "source_id": None,
            "source_path": str(original),
            "source_start": 40.0,
            "source_end": 45.0,
            "timeline_start": 10.0,
            "timeline_end": 15.0,
        },
    ]


def test_build_video_clips_ignores_ambient_source_video_without_explicit_opt_in(
    monkeypatch, tmp_path
):
    original = tmp_path / "ambient_original.mp4"
    edited = tmp_path / "edited_source.mp4"
    original.write_bytes(b"orig")
    edited.write_bytes(b"edited")
    (tmp_path / "clip_plan.json").write_text(
        json.dumps({"clips": [{"start": 10.0, "end": 20.0}]}), encoding="utf-8"
    )
    monkeypatch.setitem(CONFIG, "source_video", str(original))
    monkeypatch.setitem(CONFIG, "source_video_explicit", False)
    (tmp_path / "assembly_qc.json").write_text(json.dumps(_FAKE_QC), encoding="utf-8")

    clips = _build_video_clips(edited, tmp_path, duration_s=15.0)
    manifest = _assembly_manifest_payload(edited, [], tmp_path, tmp_path / "output.mp4")

    assert clips == [
        {
            "source_path": str(edited),
            "source_start": 0.0,
            "source_end": 15.0,
            "timeline_start": 0.0,
            "timeline_end": 15.0,
        }
    ]
    assert manifest["source_video"] is None
    assert manifest["source_video_identity"] is None


def test_build_video_clips_ignores_stale_validated_cut_plan(monkeypatch, tmp_path):
    import os

    original = tmp_path / "original.mp4"
    edited = tmp_path / "edited_source.mp4"
    original.write_bytes(b"orig")
    edited.write_bytes(b"edited")
    (tmp_path / "clip_plan.json").write_text(
        '{"clips":[{"start":40.0,"end":45.0}]}', encoding="utf-8"
    )
    (tmp_path / "clip_plan_validated.json").write_text(
        '{"clips":[{"clip_id":0,"source_start":0.0,"source_end":10.0,"output_start":0.0,"output_end":10.0}]}',
        encoding="utf-8",
    )
    # The agent rewrote clip_plan.json after validation: the raw plan is newer.
    os.utime(tmp_path / "clip_plan_validated.json", (1_000, 1_000))
    os.utime(tmp_path / "clip_plan.json", (1_001, 1_001))
    monkeypatch.setitem(CONFIG, "source_video", str(original))
    monkeypatch.setitem(CONFIG, "source_video_explicit", True)

    clips = _build_video_clips(edited, tmp_path, duration_s=5.0)

    assert clips == [
        {
            "source_id": None,
            "source_path": str(original),
            "source_start": 40.0,
            "source_end": 45.0,
            "timeline_start": 0.0,
            "timeline_end": 5.0,
        },
    ]


def test_assemble_main_creates_missing_output_dir(monkeypatch, tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    stale_source = tmp_path / "stale_source.mp4"
    stale_source.write_bytes(b"stale")
    monkeypatch.setitem(CONFIG, "source_video", str(stale_source))
    work = tmp_path / "work"
    work.mkdir()
    (work / "tts_meta.json").write_text(json.dumps({"segments": []}), encoding="utf-8")
    (work / "timeline.json").write_text('{"provenance": {}}', encoding="utf-8")
    (work / "narration.wav").write_bytes(b"narration")
    (work / "subtitles.srt").write_text("", encoding="utf-8")
    missing_out = tmp_path / "missing" / "nested"

    def fake_assemble(input_video, tts_segments, work_dir, output_path, **_kwargs):
        Path(output_path).write_bytes(b"mp4")
        (Path(work_dir) / "assembly_qc.json").write_text(
            json.dumps(_FAKE_QC), encoding="utf-8"
        )
        return output_path

    monkeypatch.setattr("assemble.assemble_video", fake_assemble)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "assemble.py",
            str(video),
            "--work-dir",
            str(work),
            "--output-dir",
            str(missing_out),
            "--recap-stem",
            "demo",
        ],
    )

    assemble.main()

    assert (missing_out / "recap_demo.mp4").read_bytes() == b"mp4"
    manifest = json.loads((work / "assembly_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_video"] is None
    assert manifest["source_video_identity"] is None
    assert manifest["final_output"].endswith("recap_demo.mp4")


@pytest.mark.parametrize(
    ("explicit_opacity_env", "expected_opacity"),
    [(None, 1.0), ("0.75", 0.75)],
    ids=("ambient-opacity-forced-opaque", "explicit-opacity-preserved"),
)
def test_assemble_main_applies_explicit_measured_subtitle_band(
    monkeypatch, tmp_path, explicit_opacity_env, expected_opacity
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    work.mkdir()
    (work / "tts_meta.json").write_text('{"segments": []}', encoding="utf-8")
    captured = {}

    def fake_assemble(input_video, tts_segments, work_dir, output_path, **_kwargs):
        captured.update(
            {
                "top": CONFIG["subtitle_y_top"],
                "bot": CONFIG["subtitle_y_bot"],
                "mask": CONFIG["mask_source_subtitles"],
                "policy": CONFIG["source_subtitle_mask_policy"],
                "declared": CONFIG["source_subtitle_mask_policy_declared"],
                "opacity": CONFIG["subtitle_mask_opacity"],
            }
        )
        Path(output_path).write_bytes(b"mp4")
        (Path(work_dir) / "assembly_qc.json").write_text(
            json.dumps(_FAKE_QC), encoding="utf-8"
        )
        return output_path

    for key, value in {
        "subtitle_y_top": -1,
        "subtitle_y_bot": -1,
        "mask_source_subtitles": False,
        "source_subtitle_mask_policy": "off",
        "source_subtitle_mask_policy_declared": False,
        "subtitle_mask_opacity": 0.6,
    }.items():
        monkeypatch.setitem(CONFIG, key, value)
    if explicit_opacity_env is not None:
        monkeypatch.setenv("SUBTITLE_MASK_OPACITY", explicit_opacity_env)
        monkeypatch.setitem(CONFIG, "subtitle_mask_opacity", float(explicit_opacity_env))
    monkeypatch.setattr(render_preflight, "_preflight_burn_subtitles", lambda: None)
    monkeypatch.setattr(assemble, "assemble_video", fake_assemble)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "assemble.py",
            str(video),
            "--work-dir",
            str(work),
            "--subtitle-y-top",
            "610",
            "--subtitle-y-bot",
            "660",
        ],
    )

    assemble.main()

    assert captured == {
        "top": 610,
        "bot": 660,
        "mask": True,
        "policy": "opt_in",
        "declared": True,
        "opacity": expected_opacity,
    }


def test_resolve_final_output_overwrites_stable_alias(tmp_path):
    """The recap output is always the stable alias recap_<stem>.mp4 (overwritten in
    place), so the iterate-on-narration loop refreshes one file instead of spawning
    differently named copies of every render."""
    (tmp_path / "recap_clip.mp4").write_bytes(b"previous-render")

    resolved = _resolve_final_output(tmp_path, "clip")

    assert resolved == tmp_path / "recap_clip.mp4"


def test_source_subtitle_mask_filter_toggles_with_effective_burn_policy(monkeypatch):
    canvas = _canvas()
    monkeypatch.setitem(CONFIG, "burn_subtitles", False)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", False)
    assert _source_subtitle_mask_filter(canvas, Path.cwd(), [], 1.0) is None

    # no-burn means sidecar-subtitle mode: ambient/default source masking must not
    # create a black band without burned recap subtitles.
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", True)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "opt_in")
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_ratio", 0.15)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_timing", "all")
    assert _source_subtitle_mask_filter(canvas, Path.cwd(), [], 1.0) is None

    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    f = _source_subtitle_mask_filter(canvas, Path.cwd(), [], 1.0)
    assert f is not None and f.startswith("drawbox=") and "t=fill" in f

    monkeypatch.setitem(
        CONFIG, "source_subtitle_mask_ratio", 0.0
    )  # ratio 0 still allowed if style band requires it
    assert _source_subtitle_mask_filter(canvas, Path.cwd(), [], 1.0) is not None


def test_source_subtitle_mask_can_restore_opaque_full_timeline_mode(monkeypatch):
    """The enhanced default stays reversible for projects that need the old mask look."""
    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", True)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "opt_in")
    monkeypatch.setitem(CONFIG, "subtitle_mask_opacity", 1.0)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_timing", "all")

    filt = _source_subtitle_mask_filter(
        _canvas(), Path.cwd(),
        [{"actual_place_start": 1.0, "actual_place_end": 2.0}], 3.0,
    )

    assert "color=black@1.00" in filt
    assert "enable=" not in filt


def test_source_subtitle_mask_can_follow_custom_band_and_narration_windows(monkeypatch):
    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", True)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "opt_in")
    monkeypatch.setitem(CONFIG, "subtitle_y_top", 610)
    monkeypatch.setitem(CONFIG, "subtitle_y_bot", 660)
    monkeypatch.setitem(CONFIG, "subtitle_mask_padding", 4)
    monkeypatch.setitem(CONFIG, "subtitle_mask_opacity", 0.6)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_timing", "narration")

    filt = _source_subtitle_mask_filter(
        _canvas(), Path.cwd(), [
            {"actual_place_start": 1.25, "actual_place_end": 2.5},
            {"actual_place_start": 4.0, "actual_place_end": 4.0},
            {"actual_place_start": 5.0, "actual_place_end": 6.0},
        ], 7.0,
    )

    assert filt.count("drawbox=") == 2
    assert "y=606:w=iw:h=58" in filt
    assert filt.count("color=black@0.60") == 2
    assert "between(t,1.250,2.500)" in filt
    assert "between(t,5.000,6.000)" in filt


@pytest.mark.parametrize("configured_opacity", [0.0, 0.6])
def test_source_subtitle_mask_opaquely_covers_byo_gap_subtitles(
    monkeypatch, tmp_path, configured_opacity
):
    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", True)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "opt_in")
    monkeypatch.setitem(CONFIG, "subtitle_y_top", 610)
    monkeypatch.setitem(CONFIG, "subtitle_y_bot", 660)
    monkeypatch.setitem(CONFIG, "subtitle_mask_opacity", configured_opacity)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_timing", "narration")
    (tmp_path / "user_subtitles.json").write_text(
        json.dumps([{"start": 1.0, "end": 4.0, "text": "用户校订原声"}]),
        encoding="utf-8",
    )

    filt = _source_subtitle_mask_filter(
        _canvas(),
        tmp_path,
        [{"actual_place_start": 5.0, "actual_place_end": 8.0, "narration": "解说",
          "spoken_text": "解说"}],
        10.0,
    )

    if configured_opacity > 0:
        assert "color=black@0.60" in filt
        assert "between(t,5.000,8.000)" in filt
    assert "color=black@1.00" in filt
    assert "between(t,1.000,4.000)" in filt


def test_source_subtitle_mask_coalesces_overlaps_to_avoid_double_opacity(monkeypatch):
    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", True)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "opt_in")
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_timing", "narration")

    filt = _source_subtitle_mask_filter(
        _canvas(), Path.cwd(), [
            {"actual_place_start": 1.0, "actual_place_end": 2.0},
            {"actual_place_start": 1.5, "actual_place_end": 3.0},
        ], 4.0,
    )

    assert filt.count("drawbox=") == 1
    assert "between(t,1.000,3.000)" in filt


def test_narration_timed_source_mask_without_narration_draws_nothing(monkeypatch):
    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", True)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "opt_in")
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_timing", "narration")

    assert (
        _source_subtitle_mask_filter(_canvas(), Path.cwd(), [], 1.0)
        is None
    )


def test_generate_ass_places_subtitle_bottom_on_measured_y(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "subtitle_y_top", 610)
    monkeypatch.setitem(CONFIG, "subtitle_y_bot", 650)

    ass = _generate_ass(
        [{"start": 0.0, "end": 1.0, "actual_place_start": 0.0,
          "actual_place_end": 1.0, "narration": "贴合原字幕", "spoken_text": "贴合原字幕"}],
        tmp_path,
        1.0,
        _canvas(),
    ).read_text(encoding="utf-8")

    style_line = next(
        line for line in ass.splitlines() if line.startswith("Style: Default,")
    )
    assert style_line.split(",")[-2] == "70"  # 720 - measured y_bot 650
    assert (
        style_line.split(",")[2] == "31"
    )  # line box + outline + shadow fit above anchored bot


@pytest.mark.parametrize(
    ("config", "canvas", "message"),
    [
        ({"subtitle_y_top": 700, "subtitle_y_bot": 760}, None, "字幕带坐标无效"),
        (
            {"subtitle_y_top": 610, "subtitle_y_bot": 650, "subtitle_alignment": 8},
            None,
            "bottom-aligned",
        ),
        (
            {"subtitle_y_top": 300, "subtitle_y_bot": 340},
            {"width": 720, "height": 1280, "sample_aspect_ratio": "2:1"},
            "SAR 1:1",
        ),
    ],
    ids=("band-outside-canvas", "non-bottom-alignment", "non-square-pixels"),
)
def test_generate_ass_rejects_invalid_measured_band(
    monkeypatch, tmp_path, config, canvas, message
):
    for key, value in config.items():
        monkeypatch.setitem(CONFIG, key, value)

    with pytest.raises(ValueError, match=message):
        _generate_ass(
            [{"start": 0.0, "end": 1.0, "narration": "测量带无效"}],
            tmp_path,
            1.0,
            canvas or _canvas(),
        )


def test_mask_band_stays_one_line_small_when_burning(monkeypatch):
    """Subtitles are split into short ONE-LINE chunks, so the burned-in band must size for a single
    line + margin (small), NOT two lines. A 2-line band ate ~23% of the height and compressed the
    picture; a one-line band keeps it near the raw mask ratio."""
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", True)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "opt_in")
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_ratio", 0.14)
    monkeypatch.setitem(CONFIG, "subtitle_font_size", 42)
    monkeypatch.setitem(CONFIG, "subtitle_margin_v", 30)
    monkeypatch.setitem(CONFIG, "subtitle_play_res_y", 720)
    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_timing", "all")
    import re

    ratio_on = float(
        re.search(
            r"ih-ih\*([0-9.]+)",
            _source_subtitle_mask_filter(_canvas(), Path.cwd(), [], 1.0),
        ).group(1)
    )
    one_line = (30 + 42 * 1.25 + 10) / 720
    assert ratio_on == pytest.approx(max(0.14, one_line), abs=0.005), ratio_on
    assert ratio_on < 0.16, ratio_on  # stays small — never the old ~0.23 two-line band


def test_apply_narration_speed_atempos_each_segment(monkeypatch, tmp_path):
    src = tmp_path / "narr_000.wav"
    src.write_bytes(b"RIFFwav")
    segs = [{"index": 0, "audio_path": str(src), "audio_duration": 5.0}]
    cmds = []

    def fake_run_cmd(cmd, **kw):
        cmds.append(cmd)
        Path(cmd[-1]).write_bytes(b"sped")
        return CompletedProcess(cmd, 0, "", "")

    monkeypatch.setitem(CONFIG, "narration_speed", 1.12)
    monkeypatch.setattr(narration_audio, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(narration_audio, "get_video_duration", lambda p: 4.46)

    _apply_narration_speed(segs, tmp_path)

    assert any("atempo=1.120" in " ".join(c) for c in cmds)
    assert segs[0]["audio_path"].endswith("_spd_0.wav")
    assert segs[0]["audio_duration"] == 4.46


def test_apply_narration_speed_noop_at_1x(monkeypatch, tmp_path):
    src = tmp_path / "narr_000.wav"
    src.write_bytes(b"x")
    segs = [{"index": 0, "audio_path": str(src), "audio_duration": 5.0}]

    def boom(*a, **k):
        raise AssertionError("must not re-encode at speed 1.0")

    monkeypatch.setitem(CONFIG, "narration_speed", 1.0)
    monkeypatch.setattr(narration_audio, "run_cmd", boom)
    _apply_narration_speed(segs, tmp_path)
    assert segs[0]["audio_path"] == str(src)  # unchanged


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00:00,000"),
        (3661.5, "01:01:01,500"),
        (1.65, "00:00:01,650"),
        (0.29, "00:00:00,290"),
        (1.001, "00:00:01,001"),
        (1.2496, "00:00:01,249"),
        (1.2494, "00:00:01,249"),
        (53 / 30, "00:00:01,766"),
        (Fraction(53, 30), "00:00:01,766"),
        (-0.5, "00:00:00,000"),
        (59.9996, "00:00:59,999"),
        (60, "00:01:00,000"),
        (3599.9996, "00:59:59,999"),
        (3600, "01:00:00,000"),
    ],
)
def test_seconds_to_srt_time_preserves_millisecond_floor(seconds, expected):
    assert _seconds_to_srt_time(seconds) == expected


def test_seconds_to_ass_time():
    assert _seconds_to_ass_time(3661.5) == "1:01:01.50"
    assert _seconds_to_ass_time(0) == "0:00:00.00"


def test_generate_srt_uses_actual_placement(tmp_path):
    _generate_srt(
        [
            {
                "start": 0.0,
                "end": 2.0,
                "actual_place_start": 0.5,
                "actual_place_end": 1.7,
                "narration": "真实放置时间。",
                "spoken_text": "真实放置时间。",
            },
            {"start": 3.0, "end": 3.05, "actual_place_start": 3.0,
             "actual_place_end": 3.0, "narration": "过短跳过。", "spoken_text": "过短跳过。"},
        ],
        tmp_path,
        4.0,
    )

    srt = (tmp_path / "subtitles.srt").read_text(encoding="utf-8")
    assert "00:00:00,500 --> 00:00:01,700" in srt
    assert "真实放置时间" in srt
    assert "过短跳过" not in srt


def test_renderers_strip_terminal_display_punctuation_without_mutating_source(
    tmp_path,
):
    narration = [
        {
            "start": 0.0,
            "end": 2.0,
            "actual_place_start": 0.5,
            "actual_place_end": 1.7,
            "narration": "他终于明白真相。",
            "spoken_text": "他终于明白真相。",
        },
        {
            "start": 2.0,
            "end": 4.0,
            "actual_place_start": 2.2,
            "actual_place_end": 3.5,
            "narration": "What now?",
            "spoken_text": "What now?",
        },
        {
            "start": 4.0,
            "end": 6.0,
            "actual_place_start": 4.2,
            "actual_place_end": 5.5,
            "narration": "It ends.",
            "spoken_text": "It ends.",
        },
    ]

    _generate_srt(narration, tmp_path, 6.0)
    _generate_ass(narration, tmp_path, 6.0, _canvas())

    srt = (tmp_path / "subtitles.srt").read_text(encoding="utf-8")
    ass = (tmp_path / "subtitles.ass").read_text(encoding="utf-8")
    assert "他终于明白真相\n" in srt
    assert "他终于明白真相。" not in srt
    assert "What now\n" in srt
    assert "What now?" not in srt
    assert "It ends\n" in srt
    assert "It ends." not in srt
    assert "他终于明白真相" in ass
    assert "他终于明白真相。" not in ass
    assert narration[0]["narration"].endswith(
        "。"
    )  # display-only; TTS source is untouched


def test_generate_ass_escapes_text_and_writes_style(tmp_path):
    _generate_ass(
        [
            {
                "start": 1.0,
                "end": 4.0,
                "actual_place_start": 1.25,
                "actual_place_end": 3.5,
                "narration": "第一行{重点}\\路径\n第二行",
                "spoken_text": "第一行{重点}\\路径\n第二行",
            }
        ],
        tmp_path,
        5.0,
        _canvas(),
    )

    ass = (tmp_path / "subtitles.ass").read_text(encoding="utf-8")
    assert "[V4+ Styles]" in ass
    assert "Style: Default" in ass
    assert "Dialogue: 0,0:00:01.25,0:00:03.50" in ass
    assert r"第一行\{重点\}\\路径\N第二行" in ass
    assert _escape_ass_text("{x}\\y") == r"\{x\}\\y"


def test_build_timed_narration_clamps_delay_to_slot(monkeypatch, tmp_path):
    wav = _write_silent_wav(tmp_path / "narr.wav", 0.8)
    segment = tts_segment(
        index=0,
        start=0.0,
        end=1.0,
        narration="短槽位解说。",
        audio_path=str(wav),
        audio_duration=0.8,
    )
    monkeypatch.setitem(CONFIG, "narration_delay_seconds", 1.5)
    monkeypatch.setitem(CONFIG, "narration_tail_pad_seconds", 0.1)

    _build_timed_narration([segment], tmp_path / "out.wav", 2.0, tmp_path)

    assert segment["actual_place_start"] == pytest.approx(0.1, abs=0.02)
    assert segment["actual_place_end"] == pytest.approx(0.9, abs=0.02)


def test_narration_start_has_no_hidden_default_delay(monkeypatch, tmp_path):
    wav = _write_silent_wav(tmp_path / "narr.wav", 0.5)
    segment = tts_segment(
        index=0,
        start=5.81,
        end=7.0,
        narration="句末切入。",
        audio_path=str(wav),
        audio_duration=0.5,
    )
    monkeypatch.setitem(CONFIG, "narration_delay_seconds", 0.0)
    monkeypatch.setitem(CONFIG, "narration_tail_pad_seconds", 0.1)

    _build_timed_narration([segment], tmp_path / "out.wav", 8.0, tmp_path)

    assert segment["actual_place_start"] == pytest.approx(5.81, abs=0.01)


def test_subtitle_burn_filter_escapes_path():
    path = Path("/tmp/video recap/a:b,c[1].ass")
    filt = _subtitle_burn_filter(path)
    assert filt.startswith("subtitles=")
    assert "video recap" in filt
    assert r"a\:b\,c\[1\].ass" in filt


def test_assemble_video_burns_ass_subtitles(monkeypatch, tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    wav = tmp_path / "narr.wav"
    wav.write_bytes(b"wav")
    output = tmp_path / "output.mp4"
    commands = []

    def fake_run_cmd(cmd):
        commands.append(cmd)
        output.write_bytes(b"mp4")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(
        CONFIG, "mask_source_subtitles", False
    )  # isolate burn behavior from the mask default
    monkeypatch.setitem(CONFIG, "output_crf", 0)  # lossless is falsy but valid
    _mock_assemble_media(monkeypatch)
    monkeypatch.setattr(
        narration_audio,
        "_build_timed_narration",
        lambda segments, out, duration, wd: Path(out).write_bytes(b"narration"),
    )
    monkeypatch.setattr("assemble.lib.run_cmd", fake_run_cmd)

    assemble_video(
        video,
        [
            tts_segment(
                start=0.0,
                end=3.0,
                actual_place_start=0.2,
                actual_place_end=2.5,
                narration="压制字幕。",
                audio_path=str(wav),
                audio_duration=1.0,
            )
        ],
        tmp_path,
        output,
    )

    ffmpeg_cmd = commands[-1]
    assert (tmp_path / "subtitles.srt").exists()
    assert (tmp_path / "subtitles.ass").exists()
    assert "-vf" in ffmpeg_cmd
    assert any(str(arg).startswith("subtitles=") for arg in ffmpeg_cmd)
    assert ffmpeg_cmd[ffmpeg_cmd.index("-c:v") + 1] == "libx264"
    assert ffmpeg_cmd[ffmpeg_cmd.index("-crf") + 1] == "0"


@pytest.mark.parametrize("reads_option_files, script_option", [
    (True, "-/filter:v:0"),         # ffmpeg >= 7 (9 removed -filter_script)
    (False, "-filter_script:v:0"),  # ffmpeg <= 6
])
def test_assemble_video_uses_filter_script_for_long_timed_mask(
    monkeypatch, tmp_path, reads_option_files, script_option
):
    """Dense long-form narration must not place a >32K video graph on Windows' command line."""
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    output = tmp_path / "output.mp4"
    commands = []
    video_filter_scripts = []
    monkeypatch.setattr(lib, "_ffmpeg_reads_option_files", lambda: reads_option_files)

    def fake_run_cmd(cmd):
        commands.append(cmd)
        if script_option in cmd:
            script = Path(cmd[cmd.index(script_option) + 1])
            video_filter_scripts.append((script, script.read_text(encoding="utf-8")))
        output.write_bytes(b"mp4")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", True)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "opt_in")
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy_declared", True)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_timing", "narration")
    monkeypatch.setitem(CONFIG, "subtitle_mask_opacity", 0.6)
    monkeypatch.setitem(CONFIG, "subtitle_y_top", 610)
    monkeypatch.setitem(CONFIG, "subtitle_y_bot", 660)
    _mock_assemble_media(monkeypatch, duration=3800.0)
    monkeypatch.setattr(
        narration_audio, "_apply_narration_speed", lambda segments, work_dir: None
    )
    monkeypatch.setattr(
        narration_audio,
        "_build_timed_narration",
        lambda segments, out, duration, wd: Path(out).write_bytes(b"narration"),
    )
    monkeypatch.setattr("assemble.lib.run_cmd", fake_run_cmd)

    segments = [
        tts_segment(
            index=i,
            start=i * 10.0,
            end=i * 10.0 + 6.0,
            actual_place_start=i * 10.0,
            actual_place_end=i * 10.0 + 6.0,
            narration=f"第{i}段",
            audio_path=str(tmp_path / "narr.wav"),
            audio_duration=1.0,
        )
        for i in range(375)
    ]

    assemble_video(video, segments, tmp_path, output)

    ffmpeg_cmd = commands[-1]
    assert script_option in ffmpeg_cmd
    assert "-vf" not in ffmpeg_cmd
    assert video_filter_scripts[0][1].count("drawbox=") == 375
    assert len(" ".join(map(str, ffmpeg_cmd))) < 32767
    assert not video_filter_scripts[0][0].exists()


@pytest.mark.parametrize("probe_stderr, expected", [
    ("Unrecognized option '/filter_complex'.\nError splitting the argument list: Option not found\n",
     ["-filter_complex_script", "graph.txt"]),
    ("Cannot find an unused video input stream to feed the unlabeled input pad null:default.\n",
     ["-/filter_complex", "graph.txt"]),
])
def test_filter_file_args_follow_what_this_ffmpeg_accepts(monkeypatch, probe_stderr, expected):
    monkeypatch.setattr(lib.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        lib.subprocess, "run",
        lambda cmd, **kwargs: CompletedProcess(cmd, 1, stdout="", stderr=probe_stderr),
    )
    lib._ffmpeg_reads_option_files.cache_clear()
    try:
        assert lib.filter_file_args("filter_complex", "graph.txt") == expected
    finally:
        lib._ffmpeg_reads_option_files.cache_clear()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not available")
@pytest.mark.parametrize("option, graph", [
    ("filter_complex", "[0:v]null[v]"),
    ("filter:v:0", "null"),
])
def test_filter_file_args_run_on_the_installed_ffmpeg(tmp_path, option, graph):
    script = tmp_path / "graph.txt"
    script.write_text(graph, encoding="utf-8")
    maps = ["-map", "[v]"] if option == "filter_complex" else []
    lib._ffmpeg_reads_option_files.cache_clear()
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.1",
         *lib.filter_file_args(option, script), *maps, "-f", "null", "-"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not available")
def test_loudnorm_first_pass_measures_on_the_installed_ffmpeg(monkeypatch, tmp_path):
    """The probe reads its graph from a file; ffmpeg 9 dropped -filter_complex_script, which
    silently degraded every render to the single-pass limiter."""
    video = tmp_path / "input.mp4"
    narration = tmp_path / "narration.wav"
    for args, out in (
        (["-f", "lavfi", "-i", "color=c=black:s=64x64:d=1", "-c:v", "libx264"], video),
        (["-f", "lavfi", "-i", "sine=frequency=440:duration=1"], narration),
    ):
        subprocess.run(["ffmpeg", "-y", "-v", "error", *args, str(out)], check=True)
    monkeypatch.setitem(CONFIG, "final_loudnorm", True)
    lib._ffmpeg_reads_option_files.cache_clear()

    measured = audio_mix._run_loudnorm_first_pass(
        video, narration, [], [], "[1:a]anull[aout]", tmp_path)

    assert measured is not None
    assert not (tmp_path / ".filter_complex_loudnorm_probe.txt").exists()


def test_assemble_video_rejects_empty_tts_segments(tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")

    with pytest.raises(RuntimeError, match="没有有效解说音频"):
        assemble_video(video, [], tmp_path, tmp_path / "output.mp4")


def test_emit_timeline_failure_is_not_swallowed(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("timeline schema failure")

    monkeypatch.setattr(
        timeline_emit,
        "_build_video_clips",
        lambda input_video, work_dir, duration_s: [
            {
                "source_path": str(input_video),
                "source_start": 0.0,
                "source_end": 1.0,
                "timeline_start": 0.0,
                "timeline_end": 1.0,
            }
        ],
    )
    monkeypatch.setattr(timeline_emit, "build_timeline", boom)

    with pytest.raises(RuntimeError, match="timeline schema failure"):
        _emit_timeline(
            tmp_path / "input.mp4",
            [tts_segment(start=0.0, end=1.0, actual_place_start=0.0,
                         actual_place_end=1.0, placed_audio_path="narr.wav",
                         narration="test")], tmp_path,
            1.0, _canvas(), False
        )

    assert not (tmp_path / "timeline.json").exists()


def test_assemble_video_no_burn_keeps_video_copy_and_ignores_source_mask_default(
    monkeypatch, tmp_path
):
    # no-burn sidecar mode keeps the video stream a copy and must not draw a
    # mask-only black band, even if the ambient mask_source_subtitles default is true.
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    output = tmp_path / "output.mp4"
    commands = []

    def fake_run_cmd(cmd):
        commands.append(cmd)
        output.write_bytes(b"mp4")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setitem(CONFIG, "burn_subtitles", False)
    monkeypatch.setitem(CONFIG, "force_video_reencode", False)
    # mask_source_subtitles left at its default (True) on purpose
    _mock_assemble_media(monkeypatch)
    monkeypatch.setattr(
        narration_audio,
        "_build_timed_narration",
        lambda segments, out, duration, wd: Path(out).write_bytes(b"narration"),
    )
    monkeypatch.setattr("assemble.lib.run_cmd", fake_run_cmd)

    assemble_video(
        video,
        [
            tts_segment(
                start=0.0,
                end=3.0,
                actual_place_start=0.0,
                actual_place_end=1.0,
                narration="外挂字幕不遮黑条。",
                audio_path=str(tmp_path / "narr.wav"),
                audio_duration=1.0,
            )
        ],
        tmp_path,
        output,
    )

    ffmpeg_cmd = commands[-1]
    assert (tmp_path / "subtitles.srt").exists()
    assert not (tmp_path / "subtitles.ass").exists()
    assert "-vf" not in ffmpeg_cmd
    assert not any("drawbox=" in str(arg) for arg in ffmpeg_cmd)
    assert ffmpeg_cmd[ffmpeg_cmd.index("-c:v") + 1] == "copy"


def test_build_audio_filter_complex_gap_fill_envelope(monkeypatch):
    # keep the 3s gap unbridged so each beat keeps its own level
    _duck_config(monkeypatch, duck_bridge_seconds=0.5)
    fc = _build_audio_filter_complex(
        [
            {
                "actual_place_start": 0.0,
                "actual_place_end": 2.0,
                "overlaps_speech": True,
            },
            {
                "actual_place_start": 5.0,
                "actual_place_end": 7.0,
                "overlaps_speech": False,
            },
        ]
    )
    assert (
        "volume='max(0,min(1,0.85" in fc
    )  # idle baseline holds the original up in the gaps
    assert "eval=frame" in fc
    assert _eval_duck_expr(fc, 0.00) == pytest.approx(
        0.2
    )  # already ducked at spoken start
    assert _eval_duck_expr(fc, 2.00) == pytest.approx(0.2)  # holds through spoken end
    assert _eval_duck_expr(fc, 5.00) == pytest.approx(0.12)
    assert _eval_duck_expr(fc, 7.00) == pytest.approx(0.12)
    assert _eval_duck_expr(fc, 3.50) == pytest.approx(0.85)  # true gap releases to idle
    assert fc.endswith("[aout]")


def test_build_audio_filter_complex_all_overlap_still_fills_gaps(monkeypatch):
    # Regression: all-overlap beats used to fall back to a constant duck, leaving the
    # original quiet across the whole video (dead air). A single beat ducks only under its
    # own window; the lead-in/out around it still swells back to idle.
    _duck_config(monkeypatch)
    fc = _build_audio_filter_complex(
        [
            {
                "actual_place_start": 0.0,
                "actual_place_end": 2.0,
                "overlaps_speech": True,
            },
        ]
    )
    assert "volume='max(0,min(1,0.85" in fc  # NOT a constant; idle baseline present
    assert _eval_duck_expr(fc, 0.00) == pytest.approx(0.2)
    assert _eval_duck_expr(fc, 2.00) == pytest.approx(0.2)
    assert _eval_duck_expr(fc, 2.25) == pytest.approx(0.85)
    assert "eval=frame" in fc
    assert fc.endswith("[aout]")


def test_build_audio_filter_complex_bridges_short_gaps(monkeypatch):
    # The fix: beats separated by a gap smaller than duck_bridge_seconds stay ducked across
    # the gap (one held span) so the source dialogue does not pop back up between sentences.
    _duck_config(monkeypatch, duck_bridge_seconds=6.0)
    fc = _build_audio_filter_complex(
        [
            {
                "actual_place_start": 0.0,
                "actual_place_end": 2.0,
                "overlaps_speech": True,
            },
            {
                "actual_place_start": 4.0,
                "actual_place_end": 6.0,
                "overlaps_speech": True,
            },  # gap 2.0 < 6.0
        ]
    )
    assert _eval_duck_expr(fc, 0.0) == pytest.approx(0.2)
    assert _eval_duck_expr(fc, 3.0) == pytest.approx(
        0.2
    )  # one held duck across both beats + gap
    assert _eval_duck_expr(fc, 6.0) == pytest.approx(0.2)
    assert fc.count("(-0.650)") == 1  # a single coalesced duck term


def test_build_audio_filter_complex_original_blocks_play_full_volume(monkeypatch):
    # Narration comes in BLOCKS that duck the original; a gap >= duck_bridge_seconds between
    # blocks is an "original block" that releases to FULL volume (idle=1.0) so the picture can
    # breathe, while the short bridge keeps within-block micro-gaps ducked.
    _duck_config(
        monkeypatch, idle_orig_volume=1.0, duck_fade_seconds=0.3, duck_bridge_seconds=1.5
    )
    fc = _build_audio_filter_complex(
        [
            {
                "actual_place_start": 0.0,
                "actual_place_end": 8.0,
                "overlaps_speech": True,
            },  # narration block
            {
                "actual_place_start": 14.0,
                "actual_place_end": 22.0,
                "overlaps_speech": True,
            },  # next block; 6s gap = original block
        ]
    )
    assert "volume='max(0,min(1,1.0" in fc  # full-volume original baseline (was 0.85)
    assert fc.count("(-0.800)") == 2  # 1.0 -> 0.2 under each block, two separate dips
    assert _eval_duck_expr(fc, 0.0) == pytest.approx(0.2)
    assert _eval_duck_expr(fc, 8.0) == pytest.approx(0.2)
    assert _eval_duck_expr(fc, 11.0) == pytest.approx(
        1.0
    )  # the 6s gap stays full between
    assert _eval_duck_expr(fc, 14.0) == pytest.approx(0.2)
    assert _eval_duck_expr(fc, 22.0) == pytest.approx(0.2)


def test_build_audio_filter_complex_bridged_mixed_levels_flatten_to_min(monkeypatch):
    # A bridged span mixing a speech beat (0.2) and a quiet beat (0.12) flattens to the MIN
    # level across the span — matching variable_ducking_keyframes so the 剪映 draft == the mp4.
    _duck_config(monkeypatch, duck_bridge_seconds=6.0)
    fc = _build_audio_filter_complex(
        [
            {
                "actual_place_start": 0.0,
                "actual_place_end": 2.0,
                "overlaps_speech": True,
            },  # 0.2
            {
                "actual_place_start": 4.0,
                "actual_place_end": 6.0,
                "overlaps_speech": False,
            },  # 0.12, gap 2 < 6
        ]
    )
    assert _eval_duck_expr(fc, 0.0) == pytest.approx(0.12)
    assert _eval_duck_expr(fc, 3.0) == pytest.approx(
        0.12
    )  # one coalesced span across both beats
    assert _eval_duck_expr(fc, 6.0) == pytest.approx(0.12)
    assert "(-0.730)" in fc  # held at the MIN level (0.12)
    assert "(-0.650)" not in fc  # NOT the speech level (0.2)


def test_build_audio_filter_complex_bgm_envelope_bridges_short_gaps(monkeypatch):
    # The BGM bed bridges the same way: two beats within the bridge coalesce to one BGM dip.
    _duck_config(
        monkeypatch, bgm_volume=0.18, bgm_ducking_volume=0.10, duck_bridge_seconds=6.0
    )
    fc = _build_audio_filter_complex(
        [
            {
                "actual_place_start": 0.0,
                "actual_place_end": 2.0,
                "overlaps_speech": True,
            },
            {
                "actual_place_start": 4.0,
                "actual_place_end": 6.0,
                "overlaps_speech": True,
            },  # gap 2 < 6
        ],
        has_bgm=True,
    )
    bgm_part = fc.split("[bgm]")[0]  # the bgm chain comes first
    assert "(-0.080)" in bgm_part  # 0.18 -> 0.10 in one coalesced BGM dip
    assert bgm_part.count("(-0.080)") == 1


def test_build_audio_filter_complex_bgm_adds_third_track(monkeypatch):
    _duck_config(monkeypatch, bgm_volume=0.18, bgm_ducking_volume=0.10)
    segs = [
        {"actual_place_start": 0.0, "actual_place_end": 2.0, "overlaps_speech": True}
    ]
    fc = _build_audio_filter_complex(segs, has_bgm=True)
    assert "[2:a]volume='" in fc  # BGM bed from input 2, ducked under narration
    assert "[bgm]" in fc
    assert "amix=inputs=3" in fc
    assert fc.endswith("[aout]")
    # default (no BGM) stays a two-track mix with no third input referenced
    fc2 = _build_audio_filter_complex(segs, has_bgm=False)
    assert "amix=inputs=2" in fc2
    assert "[2:a]" not in fc2


def test_build_audio_filter_complex_explicit_modes(monkeypatch):
    segs = [
        {"actual_place_start": 0.0, "actual_place_end": 2.0, "overlaps_speech": True}
    ]
    monkeypatch.setitem(CONFIG, "ducking_mode", "none")
    none_fc = _build_audio_filter_complex(segs)
    assert "sidechaincompress" not in none_fc
    assert "volume='" not in none_fc  # no envelope in 'none' mode
    monkeypatch.setitem(CONFIG, "ducking_mode", "sidechaincompress")
    assert "sidechaincompress" in _build_audio_filter_complex(segs)


def test_assembly_settings_payload_tracks_burn_style(monkeypatch):
    monkeypatch.setitem(CONFIG, "burn_subtitles", False)
    plain = assembly_settings_payload()
    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    burned = assembly_settings_payload()
    monkeypatch.setitem(CONFIG, "subtitle_font_size", 50)
    bigger = assembly_settings_payload()

    assert plain["burn_subtitles"] is False
    assert burned["burn_subtitles"] is True
    assert burned["subtitle_renderer"] == "ass"
    assert bigger != burned


def test_final_loudnorm_filter_and_settings_payload(monkeypatch):
    monkeypatch.setitem(CONFIG, "final_loudnorm", True)
    monkeypatch.setitem(CONFIG, "target_lufs", -14.0)
    monkeypatch.setitem(CONFIG, "target_true_peak", -1.0)
    monkeypatch.setitem(CONFIG, "target_lra", 11.0)
    filt = final_loudnorm_filter()
    assert "loudnorm=I=-14.0:TP=-1.0:LRA=11.0" in filt
    assert "linear=true" in filt
    assert "alimiter=limit=0.98:level=false" in filt
    assert assembly_settings_payload()["audio_mix"]["final_loudnorm"] == filt
    assert assembly_settings_payload()["audio_mix"]["loudness_mode"] in {
        "two_pass_linear",
        "equivalent",
    }

    monkeypatch.setitem(CONFIG, "target_lufs", -11.9)
    assert "loudnorm=I=-11.9:TP=-1.0:LRA=11.0" in final_loudnorm_filter()

    monkeypatch.setitem(CONFIG, "final_loudnorm", False)
    assert final_loudnorm_filter() == "alimiter=limit=0.98:level=false"
    assert (
        assembly_settings_payload()["audio_mix"]["final_loudnorm"]
        == "alimiter=limit=0.98:level=false"
    )
    assert (
        assembly_settings_payload()["audio_mix"]["loudness_mode"] == "limiter_only"
    )


_BASE_SETTINGS = {
    "burn_subtitles": False,
    "mask_source_subtitles": False,
    "source_subtitle_mask_ratio": 0.14,
    "narration_speed": 1.0,
    "bgm_path": "",
    "bgm_volume": 0.18,
    "duck_fade_seconds": 0.25,
    "output_crf": 18,
    "output_preset": "veryfast",
    "output_max_height": 0,
}


def _settings_with(monkeypatch, **overrides):
    for key, value in {**_BASE_SETTINGS, **overrides}.items():
        monkeypatch.setitem(CONFIG, key, value)
    return assembly_settings_payload()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("mask_source_subtitles", True),
        ("burn_subtitles", True),
        ("narration_speed", 1.2),
        ("bgm_path", "/tmp/bgm.mp3"),
        ("bgm_volume", 0.30),
        ("duck_fade_seconds", 0.50),
        ("output_crf", 24),
        ("output_preset", "slow"),
        ("output_max_height", 720),
    ],
)
def test_assembly_settings_payload_tracks_render_affecting_settings(
    monkeypatch, key, value
):
    base = _settings_with(monkeypatch)

    assert _settings_with(monkeypatch, **{key: value}) != base


def test_assembly_settings_payload_records_legacy_implicit_mask_policy(monkeypatch):
    legacy_implicit = _settings_with(monkeypatch, mask_source_subtitles=True)

    assert (
        legacy_implicit["video_filters"]["source_subtitle_mask_policy"]
        == "legacy_implicit"
    )
    assert (
        legacy_implicit["video_filters"]["source_subtitle_mask_policy_declared"]
        is False
    )
    # the mask ratio only matters once a mask is actually burned
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_ratio", 0.20)
    assert assembly_settings_payload() == legacy_implicit


def test_assemble_video_uses_silent_original_track_when_source_has_no_audio(
    monkeypatch, tmp_path
):
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"video")
    output = tmp_path / "output.mp4"
    commands = []

    def fake_run_cmd(cmd):
        commands.append(cmd)
        output.write_bytes(b"mp4")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setitem(CONFIG, "burn_subtitles", False)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", False)
    monkeypatch.setitem(CONFIG, "final_loudnorm", False)
    _mock_assemble_media(monkeypatch, has_audio=False)
    monkeypatch.setattr(
        narration_audio,
        "_build_timed_narration",
        lambda segments, out, duration, wd: Path(out).write_bytes(b"narration"),
    )
    monkeypatch.setattr("assemble.lib.run_cmd", fake_run_cmd)

    assemble_video(
        video,
        [
            tts_segment(
                start=0.0,
                end=3.0,
                actual_place_start=0.2,
                actual_place_end=2.0,
                narration="无原声音轨也应能混音。",
                audio_path=str(tmp_path / "narr.wav"),
                audio_duration=1.0,
            )
        ],
        tmp_path,
        output,
    )

    ffmpeg_cmd = commands[-1]
    joined = " ".join(str(part) for part in ffmpeg_cmd)
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in joined
    assert "[2:a]volume=" in joined
    assert output.exists()


def test_split_subtitle_chunks_breaks_block_into_short_one_line_pieces():
    block = "这婴儿还在襁褓里，脑子里却装着一个现代人将死的记忆。他叫范闲，注定要搅动这座庙堂。"
    chunks = _split_subtitle_chunks(block, max_chars=20)
    assert len(chunks) >= 2  # a long block is split, not shown whole
    assert all(len(c) <= 20 for c in chunks)  # every piece fits one line
    assert "".join(chunks) == block.replace(" ", "")  # no text lost
    assert all(c == c.strip() and c for c in chunks)  # no empty / whitespace-only chunk
    # a short block stays a single chunk
    assert _split_subtitle_chunks("他叫范闲。", max_chars=20) == ["他叫范闲。"]
    assert _split_subtitle_chunks("   ", max_chars=20) == []


def test_split_subtitle_chunks_balances_hard_wrap_without_orphan_character():
    clause = "十八岁的詹姆斯在选秀夜举起骑士二十三号球衣"
    chunks = _split_subtitle_chunks(clause, max_chars=20)

    assert "".join(chunks) == clause
    assert all(len(chunk) <= 20 for chunk in chunks)
    assert min(map(len, chunks)) > 1
    assert max(map(len, chunks)) - min(map(len, chunks)) <= 1


def test_subtitle_entries_keep_timing_topology_when_stripping_display_punctuation():
    entries = _subtitle_entries(
        [
            {
                "start": 0.0,
                "end": 10.0,
                "actual_place_start": 0.0,
                "actual_place_end": 10.0,
                "narration": "短。长长长长。",
                "spoken_text": "短。长长长长。",
            }
        ]
    )

    assert [e["text"] for e in entries] == ["短", "长长长长"]
    # Raw chunks are "短。" and "长长长长。" (2:5), so display cleanup must not
    # retime the cue as stripped display text (1:4).
    assert entries[0]["end"] == pytest.approx(10.0 * 2 / 7)


def test_subtitle_entries_keep_closing_quote_with_stripped_terminal_punctuation():
    entries = _subtitle_entries(
        [
            {
                "start": 0.0,
                "end": 2.0,
                "actual_place_start": 0.0,
                "actual_place_end": 2.0,
                "narration": "他说：「你好。」",
                "spoken_text": "他说：「你好。」",
            }
        ]
    )

    assert [e["text"] for e in entries] == ["他说：「你好」"]
    assert all(e["text"] != "」" for e in entries)


def test_subtitle_entries_distribute_block_window_across_chunks():
    # one placed block of 12s -> several timed one-line entries spanning [start, end] with no gaps
    entries = _subtitle_entries(
        [
            {
                "start": 0.0,
                "end": 12.0,
                "actual_place_start": 2.0,
                "actual_place_end": 14.0,
                "narration": "这婴儿还在襁褓里，脑子里却装着一个现代人将死的记忆。他叫范闲，注定要搅动这座庙堂。",
                "spoken_text": "这婴儿还在襁褓里，脑子里却装着一个现代人将死的记忆。他叫范闲，注定要搅动这座庙堂。",
            }
        ]
    )
    assert len(entries) >= 2
    assert entries[0]["start"] == pytest.approx(
        2.0
    )  # first chunk starts at the audio start
    assert entries[-1]["end"] == pytest.approx(14.0)  # last chunk ends at the audio end
    for a, b in zip(entries, entries[1:]):
        assert b["start"] == pytest.approx(a["end"])  # contiguous, karaoke-style
        assert a["end"] > a["start"]
    assert all(len(e["text"]) <= 20 for e in entries)  # each line is short
    assert all(
        not e["text"].endswith(("。", "！", "？", "!", "?", "…")) for e in entries
    )


def test_subtitle_entries_never_drops_a_sub_threshold_chunk():
    # A trailing chunk whose proportional slice is < 0.05s must be folded into the previous line,
    # not silently dropped — otherwise its text would vanish from the burned subtitles.
    entries = _subtitle_entries(
        [
            {
                "start": 0.0,
                "end": 0.3,
                "actual_place_start": 0.0,
                "actual_place_end": 0.3,
                "narration": "第一句子比较长一点点。第二句子也比较长。第三。",
                "spoken_text": "第一句子比较长一点点。第二句子也比较长。第三。",
            }
        ]
    )
    joined = "".join(e["text"] for e in entries)
    assert "第三" in joined  # the tiny trailing chunk survives
    assert entries[-1]["end"] == pytest.approx(0.3)  # still closes at the audio end
    assert joined == "第一句子比较长一点点第二句子也比较长第三"


def test_output_compression_knobs_default_and_override(monkeypatch):
    """OUTPUT_CRF / OUTPUT_PRESET / OUTPUT_MAX_HEIGHT drive the re-encode; defaults keep the prior
    visually-lossless behaviour (crf 18, veryfast, no scaling)."""
    import lib as _lib

    try:
        for var in ("OUTPUT_CRF", "OUTPUT_PRESET", "OUTPUT_MAX_HEIGHT"):
            monkeypatch.delenv(var, raising=False)
        importlib.reload(_lib)
        assert _lib.CONFIG["output_crf"] == 18
        assert _lib.CONFIG["output_preset"] == "veryfast"
        assert _lib.CONFIG["output_max_height"] == 0  # 0 → no downscale

        monkeypatch.setenv("OUTPUT_CRF", "24")
        monkeypatch.setenv("OUTPUT_PRESET", "slow")
        monkeypatch.setenv("OUTPUT_MAX_HEIGHT", "720")
        importlib.reload(_lib)
        assert _lib.CONFIG["output_crf"] == 24
        assert _lib.CONFIG["output_preset"] == "slow"
        assert _lib.CONFIG["output_max_height"] == 720

        monkeypatch.setenv(
            "OUTPUT_CRF", "0"
        )  # lossless is a valid CRF, must not be coerced away
        importlib.reload(_lib)
        assert _lib.CONFIG["output_crf"] == 0
    finally:
        for var in ("OUTPUT_CRF", "OUTPUT_PRESET", "OUTPUT_MAX_HEIGHT"):
            monkeypatch.delenv(var, raising=False)
        importlib.reload(_lib)


def test_output_downscale_filter_forces_even_height():
    """An odd OUTPUT_MAX_HEIGHT must still yield an even output height (libx264/yuv420p reject odd),
    and the filter must never regress to the width-only `-2:'min(ih,H)'` form that crashed the mux."""
    import math

    # Regression guard: the exact even-forcing filter string is pinned.
    assert (
        _output_downscale_filter(721)
        == "scale=-2:'2*trunc(min(ih,721)/2)':flags=lanczos"
    )
    assert (
        _output_downscale_filter(720)
        == "scale=-2:'2*trunc(min(ih,720)/2)':flags=lanczos"
    )
    # The embedded expression is even for any (source height, cap) pair, and only ever shrinks.
    for ih in (720, 1080, 2160, 723):
        for max_h in (480, 720, 721, 1080, 1081):
            height = 2 * math.trunc(min(ih, max_h) / 2)  # mirrors the ffmpeg expression
            assert height % 2 == 0
            assert height <= max_h and height <= ih


def test_foreign_source_audio_near_mutes_original_under_narration(monkeypatch):
    """FOREIGN_SOURCE_AUDIO near-mutes the original UNDER narration so a foreign-language
    soundtrack (e.g. Japanese) doesn't bleed under Chinese narration as 怪音. Gaps stay full
    (idle_orig_volume), and an explicit SPEECH_DUCKING_VOLUME still overrides the foreign default."""
    import lib as _lib

    try:
        monkeypatch.setenv("FOREIGN_SOURCE_AUDIO", "1")
        monkeypatch.delenv("SPEECH_DUCKING_VOLUME", raising=False)
        monkeypatch.delenv("ZONE_DUCKING_VOLUME", raising=False)
        importlib.reload(_lib)
        assert _lib.CONFIG["foreign_source_audio"] is True
        assert (
            _lib.CONFIG["speech_ducking_volume"] == 0.05
        )  # under-narration original near-silent
        assert _lib.CONFIG["zone_ducking_volume"] == 0.05
        assert (
            _lib.CONFIG["idle_orig_volume"] == 1.0
        )  # gap/original blocks stay full volume

        monkeypatch.setenv("SPEECH_DUCKING_VOLUME", "0.15")
        importlib.reload(_lib)
        assert (
            _lib.CONFIG["speech_ducking_volume"] == 0.15
        )  # explicit override wins over foreign default
    finally:
        monkeypatch.delenv("FOREIGN_SOURCE_AUDIO", raising=False)
        monkeypatch.delenv("SPEECH_DUCKING_VOLUME", raising=False)
        monkeypatch.delenv("ZONE_DUCKING_VOLUME", raising=False)
        importlib.reload(_lib)  # restore default CONFIG for any later tests


def test_p0_adjust_tts_speed_respects_cumulative_tempo_cap(monkeypatch, tmp_path):
    """Segment atempo must be budgeted against global narration_speed and TTS rate offset."""
    src = tmp_path / "narr_000.wav"
    src.write_bytes(b"wav")
    commands = []

    def fake_run_cmd(cmd, **kw):
        commands.append(cmd)
        Path(cmd[-1]).write_bytes(b"adjusted")
        return CompletedProcess(cmd, 0, "", "")

    monkeypatch.setitem(CONFIG, "narration_speed", 1.2)
    monkeypatch.setitem(CONFIG, "narration_cumulative_tempo_max", 1.35)
    monkeypatch.setattr(
        narration_audio,
        "get_video_duration",
        lambda path: 11.2 if Path(path) == src else 10.0,
    )
    monkeypatch.setattr(narration_audio, "run_cmd", fake_run_cmd)

    out, dur, meta = _adjust_result_parts(
        _adjust_tts_speed(
            src, target_duration=10.0, tts_rate_offset=0.05
        )
    )

    effective_tempo = (
        float(meta["global_narration_speed"])
        * (1.0 + float(meta["tts_rate_offset"]))
        * float(meta["segment_tempo_factor"])
    )
    assert effective_tempo <= 1.35 + 1e-6
    assert meta["fit_status"] in {"fit", "tempo_adjusted", "no_safe_fit"}
    if commands:
        afilter = " ".join(commands[-1])
        assert "atempo=1.120" not in afilter, (
            "raw overrun ratio must not be used after global/rate tempo budget"
        )


def test_p0_adjust_tts_speed_no_safe_fit_does_not_time_cut(monkeypatch, tmp_path):
    """When cumulative cap cannot fit a segment, assemble records blocking metadata, not time-only cuts."""
    src = tmp_path / "narr_000.wav"
    src.write_bytes(b"wav")
    commands = []

    def fake_run_cmd(cmd, **kw):
        commands.append(cmd)
        Path(cmd[-1]).write_bytes(b"unexpected")
        return CompletedProcess(cmd, 0, "", "")

    monkeypatch.setitem(CONFIG, "narration_speed", 1.2)
    monkeypatch.setitem(CONFIG, "narration_cumulative_tempo_max", 1.35)
    monkeypatch.setattr(
        narration_audio,
        "get_video_duration",
        lambda path: 15.0 if Path(path) == src else 10.0,
    )
    monkeypatch.setattr(narration_audio, "run_cmd", fake_run_cmd)

    out, dur, meta = _adjust_result_parts(
        _adjust_tts_speed(
            src, target_duration=10.0, tts_rate_offset=0.05
        )
    )

    assert Path(out) == src
    assert dur == 15.0
    assert meta["fit_status"] == "no_safe_fit"
    assert meta["truncate_reason"] in {"no_safe_boundary", "no_room"}
    assert meta.get("blocking") is True
    assert commands == [], "P0 forbids assemble-side atempo or time-only speech cuts"


def test_p0_build_timed_narration_propagates_no_safe_fit_metadata(
    monkeypatch, tmp_path
):
    """_build_timed_narration must preserve audio/text truth and expose no-safe-fit for QC."""
    wav = _write_silent_wav(tmp_path / "long.wav", 2.0, frame=b"\x00\x10")

    def fake_adjust(path, target_duration, tts_rate_offset=0.0):
        return (
            str(path),
            2.0,
            {
                "fit_status": "no_safe_fit",
                "truncate_reason": "no_safe_boundary",
                "blocking": True,
                "segment_tempo_factor": 1.0,
                "effective_tempo": 1.2,
                "global_narration_speed": 1.15,
            },
        )

    monkeypatch.setitem(CONFIG, "narration_delay_seconds", 0.0)
    monkeypatch.setitem(CONFIG, "narration_tail_pad_seconds", 0.0)
    monkeypatch.setitem(CONFIG, "narration_tighten", False)
    monkeypatch.setattr(narration_audio, "_adjust_tts_speed", fake_adjust)
    seg = tts_segment(
        index=0,
        start=0.0,
        end=1.0,
        narration="原始长文案，不能猜测截断。",
        spoken_text="已合成但仍太长的文案。",
        audio_path=str(wav),
        audio_duration=2.0,
        tts_rate_offset=0.0,
    )

    _build_timed_narration([seg], tmp_path / "narration.wav", 1.5, tmp_path)

    assert seg["fit_status"] == "no_safe_fit"
    assert seg["truncate_reason"] == "no_safe_boundary"
    assert seg["blocking"] is True
    assert seg["spoken_text"] == "已合成但仍太长的文案。"
    assert seg["narration"] == "原始长文案，不能猜测截断。"


def test_emit_timeline_uses_exact_placed_audio_not_longer_prefit_source(
    monkeypatch, tmp_path
):
    original = tmp_path / "long-prefit.wav"
    placed = tmp_path / "complete-fitted.wav"
    original.write_bytes(b"long")
    placed.write_bytes(b"fit")
    monkeypatch.setattr(timeline_emit, "_timeline_subtitle_segments", lambda *args: [])
    monkeypatch.setitem(CONFIG, "ducking_mode", "none")

    timeline = _emit_timeline(
        tmp_path / "input.mp4",
        [
            tts_segment(
                audio_path=str(original),
                placed_audio_path=str(placed),
                actual_place_start=1.0,
                actual_place_end=2.0,
                narration="完整一句。",
            )
        ],
        tmp_path,
        3.0,
        _canvas(),
        False,
    )
    narration_track = next(
        track for track in timeline["tracks"] if track.get("name") == "narration"
    )
    assert narration_track["segments"][0]["source_path"] == str(placed)


def test_emit_timeline_maps_adopted_mix_by_index_across_a_skipped_segment(
    monkeypatch, tmp_path
):
    """Adopted mix segments are 1:1 with the unfiltered narration list, not the placed one."""
    placed = tmp_path / "placed.wav"
    placed.write_bytes(b"fit")
    prepared = tmp_path / "prepared_bed.wav"
    prepared.write_bytes(b"bed")
    monkeypatch.setattr(timeline_emit, "_timeline_subtitle_segments", lambda *args: [])
    monkeypatch.setitem(CONFIG, "ducking_mode", "none")
    segments = [
        tts_segment(index=0, placed_audio_path=str(placed), actual_place_start=0.5,
                    actual_place_end=1.5, narration="第一句。"),
        # unplaced: zero-width window, dropped from the timeline but still mixed 1:1
        tts_segment(index=1, placed_audio_path=str(placed), actual_place_start=2.0,
                    actual_place_end=2.0, narration="被跳过。"),
        tts_segment(index=2, placed_audio_path=str(placed), actual_place_start=2.5,
                    actual_place_end=3.5, narration="第三句。"),
    ]
    explicit_audio_mix = {
        "segments": [
            {"index": 0, "gain": 0.1, "output_start_sample": 0, "output_end_sample": 10},
            {"index": 1, "gain": 0.2, "output_start_sample": 10, "output_end_sample": 20},
            {"index": 2, "gain": 0.3, "output_start_sample": 20, "output_end_sample": 30},
        ],
        "prepared": {"prepared_bed.wav": {"path": str(prepared)}},
        "format": {"total_samples": 30},
        "master_gain_db": 0.0,
    }

    timeline = _emit_timeline(
        tmp_path / "input.mp4", segments, tmp_path, 4.0, _canvas(), False,
        explicit_audio_mix=explicit_audio_mix,
    )

    narration_track = next(
        track for track in timeline["tracks"] if track.get("name") == "narration"
    )
    placed_segments = narration_track["segments"]
    assert [item["gain"] for item in placed_segments] == [0.1, 0.3]
    assert [item["output_start_sample"] for item in placed_segments] == [0, 20]
    assert [item["output_end_sample"] for item in placed_segments] == [10, 30]


def test_build_timed_narration_never_trims_even_subframe_speech_overrun(
    monkeypatch, tmp_path
):
    """A few milliseconds may contain the final phoneme; block instead of trimming."""
    # longer than the 2.0s slot -> triggers fit
    wav = _write_silent_wav(tmp_path / "orig.wav", 2.1, frame=b"\x00\x10")

    def fake_adjust(path, target_duration, tts_rate_offset=0.0):
        # simulate atempo landing ~10ms over the fit target (real ffmpeg rounding drift)
        over = _write_silent_wav(
            tmp_path / "over.wav", target_duration + 0.010, frame=b"\x00\x10"
        )
        return (
            str(over),
            target_duration + 0.010,
            {
                "fit_status": "tempo_adjusted",
                "truncate_reason": "none",
                "blocking": False,
                "segment_tempo_factor": 1.0,
                "effective_tempo": 1.2,
                "global_narration_speed": 1.15,
            },
        )

    monkeypatch.setitem(CONFIG, "narration_delay_seconds", 0.0)
    monkeypatch.setitem(CONFIG, "narration_tail_pad_seconds", 0.0)
    monkeypatch.setitem(CONFIG, "narration_tighten", False)
    monkeypatch.setattr(narration_audio, "_adjust_tts_speed", fake_adjust)
    seg = tts_segment(
        index=0,
        start=0.0,
        end=2.0,
        narration="一段刚好超出零点几帧的解说。",
        spoken_text="一段刚好超出零点几帧的解说。",
        audio_path=str(wav),
        audio_duration=2.1,
        tts_rate_offset=0.0,
    )

    _build_timed_narration([seg], tmp_path / "narration.wav", 2.0, tmp_path)

    assert seg["fit_status"] == "no_safe_fit"
    assert seg.get("blocking") is True
    assert seg["truncate_reason"] == "no_safe_boundary"
    assert seg["placed_audio_duration"] == 0.0
    assert seg["narration"] == "一段刚好超出零点几帧的解说。"


def test_speech_safe_fades_do_not_attenuate_edge_phonemes():
    sample_rate = 1000
    # Constant non-silent tone right to both edges: only the 5ms anti-click ramp is allowed.
    tone = (1000).to_bytes(2, "little", signed=True) * 1000
    assert narration_audio._speech_safe_fade_lengths(tone, 1000, sample_rate, 120) == (
        5,
        5,
    )

    # 80ms trailing silence may own the fade; speech before it remains untouched.
    with_tail = (1000).to_bytes(2, "little", signed=True) * 920 + b"\0\0" * 80
    assert narration_audio._speech_safe_fade_lengths(
        with_tail, 1000, sample_rate, 120
    ) == (5, 80)


def test_source_handoff_restores_only_at_next_sentence_anchor(monkeypatch, tmp_path):
    _write_legacy_anchors(
        tmp_path,
        [
            {"time": 5.81, "pause_start": 5.22, "confidence": "high"},
            {"time": 14.34, "pause_start": 13.74, "confidence": "high"},
            {"time": 22.86, "pause_start": 22.27, "confidence": "high"},
        ],
    )
    monkeypatch.setitem(CONFIG, "duck_fade_seconds", 0.3)
    seg = {
        "index": 0,
        "actual_place_start": 5.81,
        "actual_place_end": 12.0,
        "overlaps_speech": True,
    }

    report = audio_mix._apply_source_sentence_handoffs([seg], tmp_path, 30.0)

    assert report[0]["status"] == "sentence_boundary"
    assert seg["source_duck_end"] == pytest.approx(13.74)
    assert seg["source_restore_at"] == pytest.approx(14.34)
    assert seg.get("source_handoff_blocking") is not True

    expr = audio_mix._duck_envelope([seg], 1.0, 0.2, 0.12, 0.3, bridge=1.5)
    env = {"__builtins__": {}}
    names = {
        "min": min,
        "max": max,
        "between": lambda value, lo, hi: 1.0 if lo <= value <= hi else 0.0,
    }
    assert eval(expr, env, {**names, "t": 13.70}) == pytest.approx(0.2)
    assert 0.2 < eval(expr, env, {**names, "t": 14.04}) < 1.0
    assert eval(expr, env, {**names, "t": 14.34}) == pytest.approx(1.0)

    from timeline import build_timeline

    model = build_timeline(
        {"width": 1280, "height": 720, "fps": 30},
        30.0,
        [
            {
                "source_path": "source.mp4",
                "source_start": 0.0,
                "source_end": 30.0,
                "timeline_start": 0.0,
                "timeline_end": 30.0,
            }
        ],
        [
            {
                "source_path": "narr.wav",
                "timeline_start": 5.81,
                "timeline_end": 12.0,
                "source_duck_end": 13.74,
                "source_restore_at": 14.34,
                "text": "句末交接",
                "overlaps_speech": True,
                "gain": 1.0,
            }
        ],
        ducking={"idle": 1.0, "speech": 0.2, "quiet": 0.12, "fade": 0.3, "bridge": 1.5},
    )
    keyframes = model["tracks"][0]["clips"][0]["audio"]["volume_keyframes"]
    assert {"t": 13.74, "gain": 0.2} in keyframes
    assert {"t": 14.34, "gain": 1.0} in keyframes


def test_source_handoff_holds_to_end_when_no_later_sentence_anchor(
    monkeypatch, tmp_path
):
    _write_legacy_anchors(tmp_path, [{"time": 22.86, "confidence": "high"}])
    seg = {
        "index": 0,
        "actual_place_start": 22.86,
        "actual_place_end": 27.5,
        "overlaps_speech": True,
    }

    report = audio_mix._apply_source_sentence_handoffs([seg], tmp_path, 30.0)

    assert report[0]["status"] == "held_to_timeline_end"
    assert seg["source_duck_end"] == 30.0
    assert seg["source_restore_at"] == 30.0

    monkeypatch.setitem(CONFIG, "duck_fade_seconds", 0.3)
    monkeypatch.setitem(CONFIG, "duck_bridge_seconds", 1.5)
    monkeypatch.setitem(CONFIG, "idle_orig_volume", 1.0)
    monkeypatch.setitem(CONFIG, "speech_ducking_volume", 0.2)
    expr = audio_mix._duck_envelope([seg], 1.0, 0.2, 0.12, 0.3, bridge=1.5)
    # Narration ended at 27.5, but source remains ducked through the timeline end.
    assert eval(
        expr,
        {"__builtins__": {}},
        {
            "t": 29.0,
            "min": min,
            "max": max,
            "between": lambda value, lo, hi: 1.0 if lo <= value <= hi else 0.0,
        },
    ) == pytest.approx(0.2)


def test_source_handoff_blocks_unsafe_entry_and_missing_anchors_with_speech(tmp_path):
    _write_legacy_anchors(tmp_path, [{"time": 10.0, "confidence": "high"}])
    unsafe = {
        "index": 0,
        "actual_place_start": 7.0,
        "actual_place_end": 8.0,
        "overlaps_speech": True,
    }
    audio_mix._apply_source_sentence_handoffs([unsafe], tmp_path, 30.0)
    assert unsafe["source_handoff_blocking"] is True
    assert unsafe["source_entry_status"] == "unsafe_entry"

    (tmp_path / "speech_boundary_anchors.json").unlink()
    missing = {
        "index": 1,
        "actual_place_start": 7.0,
        "actual_place_end": 8.0,
        "overlaps_speech": True,
    }
    audio_mix._apply_source_sentence_handoffs([missing], tmp_path, 30.0)
    assert missing["source_handoff_blocking"] is True
    assert missing["source_entry_status"] == "anchors_unavailable"


_CUT_OUTPUT_HANDOFF_CASES = [
    pytest.param(
        {
            "sentence_anchors": [{"time": 4.0, "pause_start": 3.8, "confidence": "high"}],
            "speech_spans": [{"start": 0.0, "end": 10.0}],
            "quiet_windows": [{"start": 3.8, "end": 4.1}],
        },
        {"start": 2.0, "end": 3.0, "actual_place_start": 2.0, "actual_place_end": 3.0,
         "overlaps_speech": False},
        {"source_handoff_blocking": True, "source_entry_status": "unsafe_entry"},
        {"status": "sentence_boundary"},
        id="output-speech-evidence-beats-authored-flag",
    ),
    pytest.param(
        {
            "sentence_anchors": [{"time": 1.0, "pause_start": 0.95, "confidence": "high"}],
            "speech_spans": [{"start": 0.0, "end": 1.0}],
            "quiet_windows": [{"start": 1.0, "end": 10.0}],
        },
        {"actual_place_start": 0.8, "actual_place_end": 10.0, "overlaps_speech": False},
        {"source_handoff_blocking": True, "source_entry_status": "unsafe_entry"},
        {},
        id="entry-checked-before-later-quiet",
    ),
    pytest.param(
        {
            "sentence_anchors": [{"time": 6.0, "pause_start": 5.8, "confidence": "high"}],
            "speech_spans": [{"start": 3.0, "end": 10.0}],
            "quiet_windows": [{"start": 0.0, "end": 3.0}],
        },
        {"actual_place_start": 1.0, "actual_place_end": 5.0, "overlaps_speech": True},
        {"source_handoff_blocking": None, "source_entry_status": "quiet_source"},
        {"status": "sentence_boundary"},
        id="quiet-entry-stays-safe-when-later-audio-is-speech",
    ),
    pytest.param(
        {
            "sentence_anchors": [{"time": 10.0, "pause_start": 9.8, "confidence": "high"}],
            "speech_spans": [{"start": 8.5, "end": 10.0}],
            "quiet_windows": [{"start": 0.0, "end": 8.5}],
        },
        {"actual_place_start": 1.0, "actual_place_end": 10.0, "overlaps_speech": False},
        {"overlaps_speech": True, "source_entry_status": "quiet_source"},
        {"status": "sentence_boundary"},
        id="later-speech-ducked-after-mostly-quiet-entry",
    ),
    pytest.param(
        {"sentence_anchors": [], "speech_spans": [], "quiet_windows": []},
        {"actual_place_start": 1.0, "actual_place_end": 2.0, "overlaps_speech": False},
        {"overlaps_speech": True, "source_handoff_blocking": True,
         "source_entry_status": "anchors_unavailable"},
        {"status": "anchors_unavailable"},
        id="current-but-empty-evidence-fails-closed",
    ),
    pytest.param(
        {
            "sentence_anchors": [{"time": 10.0, "pause_start": 9.8, "confidence": "high"}],
            "speech_spans": [],
            "quiet_windows": [{"start": 0.0, "end": 4.0}, {"start": 0.0, "end": 4.0}],
        },
        {"actual_place_start": 0.0, "actual_place_end": 10.0, "overlaps_speech": False},
        {"overlaps_speech": True},
        {"status": "sentence_boundary"},
        id="overlapping-quiet-evidence-not-double-counted",
    ),
]


@pytest.mark.parametrize(
    ("evidence", "segment", "expected_segment", "expected_report"),
    _CUT_OUTPUT_HANDOFF_CASES,
)
def test_source_handoff_reads_cut_output_speech_evidence(
    tmp_path, evidence, segment, expected_segment, expected_report
):
    _write_cut_output_anchors(tmp_path, evidence)

    report = audio_mix._apply_source_sentence_handoffs([segment], tmp_path, 10.0)

    for key, value in expected_segment.items():
        if value is None:
            assert segment.get(key) is not True, key
        else:
            assert segment[key] == value, key
    for key, value in expected_report.items():
        assert report[0][key] == value, key


def test_source_handoff_rejects_stale_cut_output_evidence(tmp_path):
    _write_cut_output_anchors(
        tmp_path,
        {
            "sentence_anchors": [{"time": 4.0, "pause_start": 3.8, "confidence": "high"}],
            "speech_spans": [{"start": 0.0, "end": 10.0}],
        },
        plan={"clips": [{"source_start": 0, "source_end": 10}]},
        stale=True,
    )
    segment = {"actual_place_start": 4.0, "actual_place_end": 5.0, "overlaps_speech": False}

    report = audio_mix._apply_source_sentence_handoffs([segment], tmp_path, 10.0)

    assert segment["source_handoff_blocking"] is True
    assert segment["source_entry_status"] == "anchors_unavailable"
    assert report[0]["anchor_artifact"] is None


def test_source_handoff_blocks_entry_after_anchor_pause_has_ended(tmp_path):
    _write_legacy_anchors(
        tmp_path,
        [
            {"time": 10.0, "pause_start": 9.7, "confidence": "high"},
            {"time": 20.0, "pause_start": 19.7, "confidence": "high"},
        ],
    )
    seg = {
        "index": 0,
        "actual_place_start": 10.3,
        "actual_place_end": 12.0,
        "overlaps_speech": True,
    }

    audio_mix._apply_source_sentence_handoffs([seg], tmp_path, 30.0)

    assert seg["source_handoff_blocking"] is True
    assert seg["source_entry_status"] == "unsafe_entry"


def test_p0_subtitles_use_spoken_text_not_authored_narration(tmp_path):
    segs = [
        {
            "start": 0.0,
            "end": 3.0,
            "actual_place_start": 0.25,
            "actual_place_end": 2.0,
            "narration": "作者原文第一句。作者原文第二句不应出现在字幕。",
            "spoken_text": "实际说出的第一句。",
        }
    ]

    entries = _subtitle_entries(segs)
    _generate_srt(segs, tmp_path, 3.0)
    _generate_ass(segs, tmp_path, 3.0, _canvas())
    srt = (tmp_path / "subtitles.srt").read_text(encoding="utf-8")
    ass = (tmp_path / "subtitles.ass").read_text(encoding="utf-8")

    assert "实际说出的第一句" in "".join(e["text"] for e in entries)
    assert "作者原文第二句" not in srt
    assert "作者原文第二句" not in ass
    assert "实际说出的第一句" in srt
    assert "实际说出的第一句" in ass


def test_p0_manifest_references_audio_qc_artifact(tmp_path):
    video = tmp_path / "input.mp4"
    output = tmp_path / "out.mp4"
    video.write_bytes(b"v")
    output.write_bytes(b"o")
    qc = tmp_path / "assembly_qc.json"
    qc.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "verdict": "FAIL",
                "blocking_codes": ["no_safe_fit"],
                "loudness_mode": "two_pass_linear",
                "loudnorm_measurement": {"input_i": "-18.0", "target_offset": "0.1"},
                "audio_operations": {},
                "adopted_audio": None,
            }
        ),
        encoding="utf-8",
    )

    manifest = _assembly_manifest_payload(video, [], tmp_path, output)

    assert manifest["qc_path"].endswith("assembly_qc.json")
    assert manifest["qc_verdict"] == "FAIL"
    assert manifest["qc_blocking_codes"] == ["no_safe_fit"]
    assert manifest["qc_loudness_mode"] == "two_pass_linear"
    assert manifest["qc_loudnorm_measurement"] == {
        "input_i": "-18.0",
        "target_offset": "0.1",
    }
    assert manifest["assembly_settings"]["audio_mix"]["loudness_mode"] in {
        "two_pass_linear",
        "equivalent",
        "limiter_only",
    }


@pytest.mark.parametrize(
    ("segments", "expected_codes", "expected_summary"),
    [
        pytest.param(
            [
                tts_segment(index=0, fit_status="fits", placed_audio_duration=0.0,
                            effective_tempo=1.15),
                tts_segment(index=1, fit_status="skipped", truncate_reason="missing_wav",
                            placed_audio_duration=0.0, effective_tempo=1.15),
            ],
            {"skipped_segments"},
            {"skipped_segments": [1]},
            id="skipped-segments",
        ),
        pytest.param(
            [
                tts_segment(index=0, fit_status="no_safe_fit",
                            truncate_reason="no_safe_boundary", placed_audio_duration=0.0,
                            effective_tempo=1.15),
            ],
            {"no_safe_fit"},
            {"no_safe_fit_segments": [0]},
            id="no-safe-fit",
        ),
        pytest.param(
            [
                tts_segment(index=0, fit_status="tempo_adjusted", placed_audio_duration=0.0,
                            effective_tempo=1.2, truncate_reason="tail_trim_tolerance",
                            source_handoff_blocking=True),
            ],
            {"truncated_speech", "unsafe_source_handoff"},
            {},
            id="tail-trim-and-unsafe-source-handoff",
        ),
        pytest.param(
            [
                tts_segment(index=0, fit_status="tempo_adjusted", placed_audio_duration=1.0,
                            actual_place_start=1.0, actual_place_end=2.0,
                            placed_audio_path="/nonexistent/missing.wav", effective_tempo=1.2),
            ],
            {"timeline_audio_mismatch"},
            {"timeline_audio_mismatch_segments": [0]},
            id="editable-timeline-audio-mismatch",
        ),
    ],
)
@pytest.mark.parametrize(
    "source_facts",
    [{}, {"source_has_audio": True, "loudness_mode": "two_pass_linear"}],
    ids=("unknown-source-audio", "declared-source-audio"),
)
def test_assembly_qc_blocks_unsafe_narration(
    segments, expected_codes, expected_summary, source_facts
):
    qc = assembly_contract._build_assembly_qc(
        segments, 3.0, audio_operations={}, render_delivery=_DELIVERY, **source_facts
    )

    assert qc["verdict"] == "FAIL"
    assert qc["release_gate"]["audio_qc"] == "FAIL"
    assert expected_codes <= set(qc["blocking_codes"])
    for key, value in expected_summary.items():
        assert qc["summary"][key] == value


# --- Subtitle / visual-presentation special plan contracts -------------------

_VISUAL_QC_ALLOWED_TOP_LEVEL = {
    "schema_version",
    "artifact",
    "verdict",
    "blocking",
    "blocking_codes",
    "geometry",
    "subtitles",
    "overlays",
    "mask",
    "summary",
}
_VISUAL_QC_FORBIDDEN_DELIVERY_KEYS = {
    "video_encode_passes",
    "reencode_reason",
    "audio_sample_rate",
    "final_compat_notes",
    "double_encode",
    "delivery_compatibility",
    "codec",
    "audio_codec",
}


def _flatten_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _flatten_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _flatten_keys(child)


def test_visual_qc_builder_excludes_delivery_facts_from_visual_layer(
    monkeypatch, tmp_path
):
    """visual_qc.json is a visual-facts artifact only; delivery/encode facts belong to
    assembly_qc rollup, never to the visual layer."""
    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", False)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "off")
    (tmp_path / "visual_overlays.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "overlays": [
                    {"type": "top_title", "text": "第一章", "start": 0.0, "end": 2.0},
                ],
            }
        ),
        encoding="utf-8",
    )

    filters, overlay_qc = visual_render._visual_overlay_filters(
        tmp_path, _canvas(1080, 1920), 5.0
    )
    qc = visual_render._build_visual_qc(
        [
            {
                "start": 0.0,
                "end": 2.0,
                "actual_place_start": 0.0,
                "actual_place_end": 2.0,
                "narration": "多行\n字幕",
                "spoken_text": "多行\n字幕",
            }
        ],
        tmp_path,
        5.0,
        _canvas(1080, 1920),
        overlay_qc=overlay_qc,
    )

    assert set(qc) <= _VISUAL_QC_ALLOWED_TOP_LEVEL
    assert not (_VISUAL_QC_FORBIDDEN_DELIVERY_KEYS & set(_flatten_keys(qc)))
    assert qc["geometry"]["canvas"]["width"] == 1080
    assert qc["subtitles"]["multi_line"] is True
    assert qc["mask"]["policy"] == "off"
    assert qc["overlays"]["facts"][0]["type"] == "top_title"
    assert filters and all("drawtext=" in f for f in filters)


def test_subtitle_layout_qc_records_multiline_safe_area_and_overflow(monkeypatch):
    monkeypatch.setitem(CONFIG, "subtitle_max_lines", 2)
    style = {
        "font_size": 42,
        "outline": 2,
        "shadow": 1,
        "margin_l": 40,
        "margin_r": 40,
        "margin_v": 48,
        "max_chars": 20,
        "play_res_x": 640,
        "play_res_y": 360,
        "alignment": 2,
    }
    qc = visual_render._subtitle_layout_qc(
        [{"start": 0, "end": 2, "text": "第一行\n第二行\n第三行"}],
        style,
    )

    assert qc["safe_area"]["width"] == 560
    assert qc["multi_line"] is True
    assert qc["overflow"] is True
    assert qc["overflow_entries"][0]["overflow_reasons"] == ["max_lines_exceeded"]


def test_assembly_qc_rolls_up_visual_and_delivery_facts_without_polluting_visual():
    """assembly_qc.json is the release-gate rollup: it consumes visual_qc plus
    delivery facts while preserving visual_qc as a pure visual artifact."""
    visual_qc = {
        "artifact": "visual_qc.json",
        "verdict": "PASS",
        "blocking": False,
        "blocking_codes": [],
        "geometry": {"canvas": {"width": 1080, "height": 1920}, "rotation": 90},
        "subtitles": {
            "overflow": False,
            "entries": 1,
            "multi_line": True,
            "safe_area": {"x": 54},
        },
        "overlays": {
            "present": True,
            "rendered": 1,
            "facts": [{"type": "inline_label_or_callout", "overflow": False}],
            "unsupported": [],
            "overflow": [],
        },
        "mask": {
            "policy": "safe",
            "scope": "bottom_band",
            "trigger": "explicit",
            "reason": "source_subtitles",
        },
        "summary": {"subtitle_entries": 1, "overlay_rendered": 1},
    }
    delivery_qc = {
        "video_encode_passes": 1,
        "reencode_reason": "burn_subtitles",
        "audio_sample_rate": 48000,
        "final_compat_notes": ["faststart", "yuv420p"],
    }

    qc = assembly_contract._build_assembly_qc(
        [
            tts_segment(
                index=0,
                fit_status="fit",
                placed_audio_duration=0.0,
                effective_tempo=1.0,
            )
        ],
        2.0,
        source_has_audio=True,
        loudness_mode="limiter_only",
        visual_qc=visual_qc,
        audio_operations={},
        render_delivery=delivery_qc,
    )

    assert qc["visual_qc"]["geometry"] == visual_qc["geometry"]
    assert qc["visual_qc"]["subtitles"]["overflow"] is False
    assert qc["visual_qc"]["mask"] == visual_qc["mask"]
    assert qc["delivery_qc"]["audio_sample_rate"] == 48000
    assert qc["delivery_qc"]["reencode_reason"] == "burn_subtitles"
    assert qc["release_gate"]["visual_qc"] == "PASS"
    assert not (_VISUAL_QC_FORBIDDEN_DELIVERY_KEYS & set(_flatten_keys(visual_qc)))


def test_mask_policy_must_be_explicit_and_recorded_in_settings(monkeypatch):
    """Changing source-subtitle mask policy changes rendered pixels, so the
    effective policy must be declared and included in the assembly settings payload."""
    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", True)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "safe")
    safe_fp = assembly_settings_payload()
    assert safe_fp["video_filters"]["source_subtitle_mask_policy"] == "safe"

    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "forced")
    forced_fp = assembly_settings_payload()
    assert forced_fp["video_filters"]["source_subtitle_mask_policy"] == "forced"
    assert forced_fp != safe_fp

    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "legacy_implicit")
    qc = source_subtitles._source_subtitle_mask_policy()
    assert qc["policy"] == "legacy_implicit"
    assert qc.get("blocking") is True


def test_visual_overlay_loader_uses_canonical_artifact_and_rejects_platform_expansion(
    tmp_path,
):
    """visual_overlays.json is the sole first-release overlay input, and only
    top_title plus inline_label_or_callout are supported."""
    (tmp_path / "overlays.json").write_text(
        json.dumps([{"type": "card", "text": "wrong file"}]), encoding="utf-8"
    )
    (tmp_path / "visual_overlays.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "overlays": [
                    {"type": "top_title", "text": "第一章", "start": 0.0, "end": 2.0},
                    {
                        "type": "inline_label_or_callout",
                        "text": "关键证据",
                        "start": 1.0,
                        "end": 3.0,
                        "x": 0.2,
                        "y": 0.3,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    overlays, source = visual_render._load_visual_overlays(tmp_path)
    assert source["present"] is True
    assert [item["type"] for item in overlays] == [
        "top_title",
        "inline_label_or_callout",
    ]
    filters, qc = visual_render._visual_overlay_filters(
        tmp_path, {"width": 1280, "height": 720}, 5.0
    )
    assert len(filters) == 2
    assert qc["rendered"] == 2
    assert qc["unsupported"] == []

    (tmp_path / "visual_overlays.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "overlays": [
                    {"type": "top_title", "text": "allowed", "start": 0.0, "end": 1.0},
                    {
                        "type": "chapter_card",
                        "text": "not in first release",
                        "start": 1.0,
                        "end": 2.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    filters, qc = visual_render._visual_overlay_filters(
        tmp_path, {"width": 1280, "height": 720}, 5.0
    )
    assert len(filters) == 1
    assert qc["unsupported"] == [
        {"index": 1, "type": "chapter_card", "reason": "unsupported_overlay_type"}
    ]


def test_assemble_video_render_failure_does_not_leave_pass_assembly_qc(
    monkeypatch, tmp_path
):
    """A stale/pre-render PASS assembly_qc.json must be removed before render, and a
    failed ffmpeg delivery must not leave a PASS final assembly_qc behind."""
    input_video = tmp_path / "input.mp4"
    output = tmp_path / "out.mp4"
    input_video.write_bytes(b"video")
    (tmp_path / "assembly_qc.json").write_text(
        json.dumps({"verdict": "PASS"}), encoding="utf-8"
    )
    segs = [
        tts_segment(
            index=0,
            start=0.0,
            end=1.0,
            actual_place_start=0.0,
            actual_place_end=1.0,
            placed_audio_duration=1.0,
            fit_status="fit",
            effective_tempo=1.0,
            narration="hello",
        )
    ]

    monkeypatch.setitem(CONFIG, "burn_subtitles", False)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", False)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "off")
    monkeypatch.setattr(assemble.lib, "get_video_duration", lambda path: 2.0)
    monkeypatch.setattr(
        media, "_probe_canvas", lambda path: _canvas()
    )
    monkeypatch.setattr(
        narration_audio, "_apply_narration_speed", lambda segments, work_dir: None
    )
    monkeypatch.setattr(
        narration_audio,
        "_build_timed_narration",
        lambda segments, wav, duration, work_dir: Path(wav).write_bytes(b"wav"),
    )
    monkeypatch.setattr(
        subtitle_render,
        "_generate_srt",
        lambda segments, work_dir, duration: Path(work_dir) / "subtitles.srt",
    )
    monkeypatch.setattr(timeline_emit, "_emit_timeline", lambda *args, **kwargs: None)
    monkeypatch.setattr(media, "_has_audio_stream", lambda path: True)
    monkeypatch.setattr(
        audio_mix, "_run_loudnorm_first_pass", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        assemble.lib,
        "run_cmd",
        lambda cmd, **kwargs: CompletedProcess(cmd, 1, "", "ffmpeg failed"),
    )

    with pytest.raises(RuntimeError, match="视频组装失败"):
        assemble_video(input_video, segs, tmp_path, output)

    qc_path = tmp_path / "assembly_qc.json"
    assert (
        not qc_path.exists()
        or json.loads(qc_path.read_text(encoding="utf-8")).get("verdict") != "PASS"
    )
    assert (tmp_path / "visual_qc.json").exists()


def test_malformed_visual_overlays_is_rejected(tmp_path):
    (tmp_path / "visual_overlays.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON 无效"):
        visual_render._load_visual_overlays(tmp_path)


@pytest.mark.parametrize(
    "payload",
    [
        [{"type": "top_title", "text": "legacy top-level list"}],
        {"overlays": []},
        {"schema_version": 2, "overlays": []},
        {"schema_version": "1", "overlays": []},
        {"schema_version": True, "overlays": []},
        {"overlays": {"type": "top_title", "text": "not a list"}},
        "not a schema",
        42,
        {"schema_version": 1},
    ],
)
def test_invalid_visual_overlays_schema_is_rejected(tmp_path, payload):
    (tmp_path / "visual_overlays.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="schema 无效"):
        visual_render._load_visual_overlays(tmp_path)


def test_measured_subtitle_band_is_the_visual_qc_safe_area(tmp_path, monkeypatch):
    """A band too narrow for even the minimum font must block instead of passing canvas QC."""
    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", True)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "opt_in")
    monkeypatch.setitem(CONFIG, "subtitle_y_top", 610)
    monkeypatch.setitem(CONFIG, "subtitle_y_bot", 620)
    monkeypatch.setitem(CONFIG, "subtitle_mask_padding", 0)

    qc = visual_render._build_visual_qc(
        [{"start": 0.0, "end": 1.0, "actual_place_start": 0.0,
          "actual_place_end": 1.0, "narration": "窄字幕带", "spoken_text": "窄字幕带"}],
        tmp_path,
        2.0,
        _canvas(),
    )

    assert qc["subtitles"]["safe_area"]["y"] == 610
    assert qc["subtitles"]["safe_area"]["height"] == 10
    assert qc["subtitles"]["overflow"] is True
    assert "subtitle_overflow" in qc["blocking_codes"]


def test_measured_subtitle_qc_contains_normal_line_above_anchored_bottom(
    tmp_path, monkeypatch
):
    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", True)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "opt_in")
    monkeypatch.setitem(CONFIG, "subtitle_y_top", 610)
    monkeypatch.setitem(CONFIG, "subtitle_y_bot", 650)
    monkeypatch.setitem(CONFIG, "subtitle_mask_padding", 4)

    qc = visual_render._build_visual_qc(
        [{"start": 0.0, "end": 1.0, "actual_place_start": 0.0,
          "actual_place_end": 1.0, "narration": "正常字幕带", "spoken_text": "正常字幕带"}],
        tmp_path,
        2.0,
        _canvas(),
    )

    safe = qc["subtitles"]["safe_area"]
    entry = qc["subtitles"]["entry_facts"][0]
    assert safe == {"x": 40, "y": 606, "width": 1200, "height": 44, "bottom_margin": 70}
    assert entry["band_height"] <= safe["height"]
    assert qc["subtitles"]["overflow"] is False


def test_legacy_mask_env_without_explicit_policy_is_blocking(monkeypatch):
    import lib as assemble_lib

    snapshot = dict(CONFIG)
    try:
        monkeypatch.setenv("MASK_SOURCE_SUBTITLES", "1")
        monkeypatch.delenv("SOURCE_SUBTITLE_MASK_POLICY", raising=False)
        importlib.reload(assemble_lib)

        policy = source_subtitles._source_subtitle_mask_policy()

        assert CONFIG["mask_source_subtitles"] is True
        assert CONFIG["source_subtitle_mask_policy"] == "off"
        assert CONFIG["source_subtitle_mask_policy_declared"] is False
        assert policy["policy"] == "legacy_implicit"
        assert policy["declared"] is False
        assert policy["blocking"] is True
    finally:
        CONFIG.clear()
        CONFIG.update(snapshot)
