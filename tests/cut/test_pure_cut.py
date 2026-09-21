"""Pure cut behaviours: plan normalization, boundary snapping, render commands and cache reuse."""
import json
import os
import shutil
import sys
import types
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-cut" / "scripts"))
import cut
import cut_contract
import cut_render
import media_geometry
import sentence_boundaries
from lib import env_float
from cut import (
    build_edited_source_video,
    normalize_clip_plan,
    parse_duration_seconds,
    snap_clip_ends_to_lines,
    snap_clips_off_shot_changes,
)

_HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
def test_env_float_rejects_nonfinite_values(monkeypatch, raw):
    monkeypatch.setenv("NONFINITE_FLOAT", raw)
    with pytest.raises(ValueError, match="NONFINITE_FLOAT.*finite"):
        env_float("NONFINITE_FLOAT", 1.0, min_val=0.0)


def _geometry(width, height, fps):
    """A probed geometry as ffprobe reports a square-pixel, unrotated stream."""
    return media_geometry._geometry_from_stream(
        {"width": width, "height": height, "r_frame_rate": f"{fps}/1"}
    )


def _mock_media_probes(monkeypatch):
    """main() probes the (fake) source video for its canvas and shot changes."""
    monkeypatch.setattr(media_geometry, "_probe_video_geometry", lambda _path: _geometry(1280, 720, 30.0))
    monkeypatch.setattr(sentence_boundaries, "_detect_shot_changes", lambda *_args, **_kwargs: [])


def _make_plan(clips_spec, allow_overlap=False, video_duration=100.0):
    """Build a validated plan from a list of (source_start, source_end) tuples."""
    raw = [{"start": s, "end": e} for s, e in clips_spec]
    return normalize_clip_plan(raw, video_duration=video_duration, allow_overlap=allow_overlap)


def _fake_detector(changes):
    """Stand in for _detect_shot_changes: return the seeded cuts that fall in the asked window."""
    def detect(video, win_start, win_end, threshold, lead=0.25):
        return sorted(c for c in changes if win_start <= c <= win_end)
    return detect


def _with_geometry(plan, source_paths):
    """cut_cli selects the output canvas before rendering; direct render calls must do the same."""
    _, _, _, geometry_qc = media_geometry._select_output_geometry(source_paths, plan["clips"])
    plan.setdefault("qc", {})["output_geometry"] = geometry_qc
    return plan


def _capture_render(monkeypatch, tmp_path, raw_clips, video_duration, *, config=None, probe=None):
    """Render a single-source plan with ffmpeg/ffprobe faked; return (output, plan, commands)."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    plan = normalize_clip_plan(raw_clips, video_duration=video_duration)
    commands = []

    def fake_run_cmd(cmd):
        commands.append(cmd)
        if cmd[0] == "ffprobe":
            return probe(cmd) if probe else CompletedProcess(cmd, 0, stdout="0\n", stderr="")
        Path(cmd[-1]).write_bytes(b"mp4")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    if probe is None:
        monkeypatch.setattr(media_geometry, "_probe_video_geometry", lambda _path: _geometry(1280, 720, 30.0))
    monkeypatch.setattr("cut_render.run_cmd", fake_run_cmd)
    monkeypatch.setattr("media_geometry.run_cmd", fake_run_cmd)
    monkeypatch.setattr("cut_render.get_video_duration", lambda path: 2.0)
    if config:
        monkeypatch.setattr("cut_render.CONFIG", {**cut_render.CONFIG, **config})
    output = build_edited_source_video(video, _with_geometry(plan, [str(video)]), work_dir)
    return output, plan, commands


def _ffmpeg_command(commands):
    return " ".join([cmd for cmd in commands if cmd[0] == "ffmpeg"][0])


def test_cut_main_normalize_only_writes_validated_plan_without_render(monkeypatch, tmp_path):
    """--normalize-only writes clip_plan_validated.json and skips the render/map."""
    _mock_media_probes(monkeypatch)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    (tmp_path / "clip_plan.json").write_text('{"clips":[{"start":10.0,"end":20.0}]}', encoding="utf-8")
    monkeypatch.setattr("cut_cli.get_video_duration", lambda p: 30.0)
    rendered = []
    monkeypatch.setattr("cut_cli.build_edited_source_video", lambda *a, **k: rendered.append(1))
    monkeypatch.setattr(sys, "argv", ["cut.py", str(video), "--work-dir", str(tmp_path), "--normalize-only"])

    cut.main()

    validated = json.loads((tmp_path / "clip_plan_validated.json").read_text(encoding="utf-8"))
    assert validated["clips"]
    delivery_qc = validated["qc"]["delivery_qc"]
    assert delivery_qc["video_encode_passes"] == 1
    assert delivery_qc["audio_sample_rate"]["target"] == 48000
    assert delivery_qc["rendered"] is False
    assert delivery_qc["planned"] is True
    assert not (tmp_path / "cut_delivery_qc.json").exists()
    assert rendered == []
    assert not (tmp_path / "edited_source.mp4").exists()
    assert not (tmp_path / "visual_qc.json").exists()


@pytest.mark.parametrize("multi_source", [False, True], ids=["single", "multi"])
def test_cut_main_keeps_sentence_gate_when_line_snapping_is_disabled(monkeypatch, tmp_path, multi_source):
    _mock_media_probes(monkeypatch)
    import cut_cli

    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    clip = {"start": 3.0, "end": 7.0}
    argv = ["cut.py", str(video), "--work-dir", str(tmp_path), "--normalize-only"]
    asr = json.dumps({"segments": [{"start": 0.0, "end": 10.0, "text": "完整原声句子。"}]})

    if multi_source:
        clip["source_id"] = "a"
        source_work = tmp_path / "sources" / "a"
        source_work.mkdir(parents=True)
        (source_work / "asr_clean.json").write_text(asr, encoding="utf-8")
        manifest = tmp_path / "sources.json"
        manifest.write_text(json.dumps({"sources": [{
            "source_id": "a", "source_path": str(video), "source_work_dir": "sources/a", "duration": 10.0,
        }]}), encoding="utf-8")
        argv.extend(["--sources-manifest", str(manifest)])
    else:
        (tmp_path / "asr_clean.json").write_text(asr, encoding="utf-8")
        monkeypatch.setattr(cut_cli, "get_video_duration", lambda _path: 10.0)

    (tmp_path / "clip_plan.json").write_text(json.dumps({"clips": [clip]}), encoding="utf-8")
    monkeypatch.setitem(cut_cli.CONFIG, "snap_clip_line_end", False)
    monkeypatch.setitem(cut_cli.CONFIG, "scene_cut_snap", False)
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit, match="clip_plan QC blocking"):
        cut.main()

    validated = json.loads((tmp_path / "clip_plan_validated.json").read_text(encoding="utf-8"))
    assert {row["edge"] for row in validated["qc"]["blocking"]
            if row.get("code") == "unsafe_clip_sentence_boundary"} == {"start", "end"}


@pytest.mark.parametrize("raw,expected", [
    ("600", 600), ("10m", 600), ("00:10:00", 600), ("1:02", 62), (90, 90), ("", None),
    # Natural compound durations must not crash the cut pipeline (regression: "2m30s").
    ("2m30s", 150), ("1h5m", 3900), ("1h5m30s", 3930), ("500ms", 0.5), ("90s", 90),
])
def test_parse_duration_seconds_accepts_common_and_compound_forms(raw, expected):
    assert parse_duration_seconds(raw) == expected


@pytest.mark.parametrize("raw", ["nope", "-1", "2m30x", "m30"])
def test_parse_duration_seconds_rejects_malformed_forms(raw):
    with pytest.raises(ValueError):
        parse_duration_seconds(raw)


def test_normalize_clip_plan_clamps_and_maps_output_timeline():
    plan = normalize_clip_plan(
        {"target_duration": "10s", "clips": [
            {"start": 1.0, "end": 4.0, "reason": "开端"},
            {"start": 8.0, "end": 12.0, "reason": "反转"},
            {"start": 5.0, "end": 5.1, "reason": "too short"},
            {"start": "bad", "end": 7.0},
        ]},
        video_duration=10.0, clip_padding=0.5,
    )
    assert plan["target_duration"] == 10.0
    assert plan["total_duration"] == 6.5
    assert len(plan["clips"]) == 2
    assert plan["clips"][0]["source_start"] == 0.5
    assert plan["clips"][0]["source_end"] == 4.5
    assert plan["clips"][0]["output_start"] == 0.0
    assert plan["clips"][1]["source_start"] == 7.5
    assert plan["clips"][1]["source_end"] == 10.0
    assert plan["clips"][1]["output_start"] == 4.0


def test_clip_padding_does_not_make_back_to_back_clips_look_duplicated():
    """Overlap is judged on authored ranges, not on the padded ones; real overlap still fails."""
    plan = normalize_clip_plan(
        [{"start": 0.0, "end": 10.0, "reason": "a"}, {"start": 10.0, "end": 20.0, "reason": "b"}],
        video_duration=60.0, clip_padding=0.5,
    )
    assert [(c["source_start"], c["source_end"]) for c in plan["clips"]] == [(0.0, 10.5), (9.5, 20.5)]
    with pytest.raises(ValueError, match="overlaps an earlier source range"):
        normalize_clip_plan([{"start": 0.0, "end": 10.0}, {"start": 5.0, "end": 15.0}],
                            video_duration=60.0, clip_padding=0.5)


def test_multi_source_clip_padding_only_collides_on_authored_ranges():
    manifest = {"sources": [{"source_id": "a", "source_path": "a.mp4", "duration": 60.0}]}
    plan = cut.normalize_multi_source_clip_plan(
        [{"source_id": "a", "start": 0.0, "end": 10.0}, {"source_id": "a", "start": 10.0, "end": 20.0}],
        manifest, clip_padding=0.5,
    )
    assert len(plan["clips"]) == 2
    with pytest.raises(ValueError, match="source_id a"):
        cut.normalize_multi_source_clip_plan(
            [{"source_id": "a", "start": 0.0, "end": 10.0}, {"source_id": "a", "start": 5.0, "end": 15.0}],
            manifest, clip_padding=0.5,
        )


def _load_lib_with_env(monkeypatch, **env):
    """Evaluate video-cut's lib.py under an environment as a separately named module.

    Not importlib.reload(lib): a reload rebinds lib.CONFIG while cut_cli keeps the original,
    so every later test would patch a different object than the code reads.
    """
    import importlib.util

    for name, value in env.items():
        monkeypatch.setenv(name, value)
    path = Path(__file__).resolve().parents[2] / "skills" / "video-cut" / "scripts" / "lib.py"
    spec = importlib.util.spec_from_file_location("_cut_lib_env_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clip_padding_env_is_actually_read_into_config(monkeypatch):
    """env -> CONFIG: monkeypatching CONFIG alone would pass even if lib never declared the key."""
    import lib

    live_config = lib.CONFIG
    probed = _load_lib_with_env(monkeypatch, CLIP_PADDING="2.5")
    assert probed.CONFIG["clip_padding"] == 2.5
    assert lib.CONFIG is live_config, "probing must not rebind the live CONFIG"


def test_clip_padding_env_reaches_the_only_skill_that_implements_it(monkeypatch, tmp_path):
    """CONFIG -> normalizer: video-cut once read the CLI flag alone and ignored CONFIG."""
    _mock_media_probes(monkeypatch)
    import cut_cli

    work = tmp_path / "w"
    work.mkdir()
    video = tmp_path / "src.mp4"
    video.write_bytes(b"video")
    (work / "clip_plan.json").write_text(json.dumps([{"start": 10.0, "end": 20.0, "reason": "x"}]), encoding="utf-8")
    monkeypatch.setitem(cut_cli.CONFIG, "clip_padding", 2.0)
    monkeypatch.setattr("cut_cli.get_video_duration", lambda path: 100.0)
    monkeypatch.setattr("cut_cli.build_edited_source_video", lambda *a, **k: Path(a[-1]))
    monkeypatch.setattr(sys, "argv", ["cut.py", str(video), "--work-dir", str(work)])

    cut.main()

    clip = json.loads((work / "clip_plan_validated.json").read_text(encoding="utf-8"))["clips"][0]
    assert (clip["source_start"], clip["source_end"]) == (8.0, 22.0)


def test_build_edited_source_video_uses_ffmpeg_concat(monkeypatch, tmp_path):
    output, _, commands = _capture_render(
        monkeypatch, tmp_path, [{"start": 0, "end": 1}, {"start": 2, "end": 3}], 4)
    assert output.exists()
    ffmpeg_cmd = [cmd for cmd in commands if cmd[0] == "ffmpeg"][0]
    joined = " ".join(ffmpeg_cmd)
    assert "trim=start=0.000:end=1.000" in joined
    assert "concat=n=2" in joined
    assert ffmpeg_cmd[ffmpeg_cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert ffmpeg_cmd[ffmpeg_cmd.index("-ar") + 1] == "48000"
    assert ffmpeg_cmd[ffmpeg_cmd.index("-movflags") + 1] == "+faststart"


def _seed_cached_cut(tmp_path):
    """A work dir whose edited_source.mp4 is bound (meta) to the current source and plan."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    work.mkdir()
    raw_plan = [{"start": 10.0, "end": 20.0}]
    (work / "clip_plan.json").write_text(json.dumps(raw_plan), encoding="utf-8")
    validated = cut.normalize_clip_plan(raw_plan, video_duration=100.0)
    edited = work / "edited_source.mp4"
    edited.write_bytes(b"cached-edited")
    cut_contract._write_edited_source_meta(edited, validated, video)
    return video, work, edited


@pytest.mark.parametrize("break_binding", ["none", "missing_meta", "plan", "source", "empty_output"])
def test_cut_main_reuses_edited_source_only_while_cache_binding_holds(monkeypatch, tmp_path, break_binding):
    _mock_media_probes(monkeypatch)
    video, work, edited = _seed_cached_cut(tmp_path)
    argv = ["cut.py", str(video), "--work-dir", str(work)]
    if break_binding == "missing_meta":
        Path(f"{edited}.meta.json").unlink()
    elif break_binding == "plan":
        argv += ["--clip-padding", "5"]
    elif break_binding == "source":
        video = tmp_path / "video_new.mp4"
        video.write_bytes(b"new source bytes")
        argv[1] = str(video)
    elif break_binding == "empty_output":
        edited.write_bytes(b"")
    calls = []

    def fake_build(video_path, validated_plan, work_dir, output_path=None):
        calls.append(Path(video_path).name)
        Path(output_path).write_bytes(b"rebuilt-edited")
        cut_contract._write_edited_source_meta(output_path, validated_plan, video_path)
        return Path(output_path)

    monkeypatch.setattr("cut_cli.get_video_duration", lambda path: 100.0)
    monkeypatch.setattr("cut_cli.build_edited_source_video", fake_build)
    monkeypatch.setattr(sys, "argv", argv)

    cut.main()

    if break_binding == "none":
        assert calls == []
        assert edited.read_bytes() == b"cached-edited"
    else:
        assert calls == [video.name]
        assert edited.read_bytes() == b"rebuilt-edited"
    if break_binding == "plan":
        validated = json.loads((work / "clip_plan_validated.json").read_text(encoding="utf-8"))
        assert validated["clips"][0]["source_start"] == 5.0


def test_edited_source_cache_settings_include_render_affecting_config(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    edited = tmp_path / "edited_source.mp4"
    edited.write_bytes(b"edited")
    plan = cut.normalize_clip_plan([{"start": 0.0, "end": 1.0}], video_duration=2.0)

    monkeypatch.setattr("cut_contract.CONFIG", {**cut_contract.CONFIG, "clip_join_audio_fade_ms": 30.0})
    cut_contract._write_edited_source_meta(edited, plan, video)
    meta = json.loads((tmp_path / "edited_source.mp4.meta.json").read_text(encoding="utf-8"))
    assert meta["schema_version"] == 3
    assert meta["render_cache"] == {"clip_join_audio_fade_ms": 30.0}
    assert meta["sources"] == {str(video): {"size": 5, "mtime_ns": video.stat().st_mtime_ns}}
    assert cut.should_reuse_edited_source(edited, plan, video) is True

    monkeypatch.setattr("cut_contract.CONFIG", {**cut_contract.CONFIG, "clip_join_audio_fade_ms": 80.0})
    assert cut.edited_source_render_cache_payload() != meta["render_cache"]
    assert cut.should_reuse_edited_source(edited, plan, video) is False


@pytest.mark.parametrize("metadata", ["not json"])
def test_edited_source_cache_corrupt_own_metadata_raises(tmp_path, metadata):
    """edited_source.mp4.meta.json is this skill's own artifact: unparseable is a bug to
    surface. A missing sidecar, or one from an older schema, stays a plain miss."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    edited = tmp_path / "edited_source.mp4"
    edited.write_bytes(b"edited")
    plan = cut.normalize_clip_plan([{"start": 0.0, "end": 1.0}], video_duration=2.0)
    assert cut.should_reuse_edited_source(edited, plan, video) is False
    Path(f"{edited}.meta.json").write_text(metadata, encoding="utf-8")
    with pytest.raises((ValueError, LookupError, TypeError)):
        cut.should_reuse_edited_source(edited, plan, video)


def test_build_edited_source_video_requires_selected_output_geometry(tmp_path):
    plan = cut.normalize_clip_plan([{"start": 0.0, "end": 1.0}], video_duration=2.0)
    plan["qc"] = {}
    with pytest.raises(KeyError, match="output_geometry"):
        cut.build_edited_source_video(tmp_path / "video.mp4", plan, tmp_path)


# ---------------------------------------------------------------------------
# snap_clip_ends_to_lines / snap_clip_starts_to_lines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("clip_end,silence,max_extend,expected_end", [
    pytest.param(10.0, [{"start": 12.0, "end": 13.0}], 5.0, 12.0, id="extends_to_next_quiet"),
    pytest.param(10.0, [{"start": 9.5, "end": 11.0}], 5.0, 10.0, id="already_in_quiet_window"),
    pytest.param(10.0, [{"start": 20.0, "end": 21.0}], 2.0, 10.0, id="next_quiet_beyond_max_extend"),
    pytest.param(98.0, [{"start": 99.0, "end": 100.0}], 5.0, 99.0, id="capped_at_video_duration"),
])
def test_snap_clip_end_toward_next_quiet_window(clip_end, silence, max_extend, expected_end):
    plan = _make_plan([(0.0, clip_end)])
    snapped = snap_clip_ends_to_lines(plan, silence, video_duration=100.0, max_extend=max_extend)
    assert snapped["clips"][0]["source_end"] == expected_end


def test_snap_no_overlap_when_allow_overlap_false():
    """Extension is capped at the next clip's source_start instead of entering its range."""
    silence = [{"start": 25.0, "end": 26.0}]
    plan = _make_plan([(0.0, 10.0), (20.0, 30.0)], allow_overlap=False)
    snapped = snap_clip_ends_to_lines(plan, silence, video_duration=100.0, max_extend=20.0)
    assert snapped["clips"][0]["source_end"] == 20.0
    assert snapped["clips"][1]["source_start"] == 20.0
    assert snapped["clips"][1]["source_end"] == 30.0


def test_snap_empty_silence_returns_plan_unchanged():
    plan = _make_plan([(0.0, 10.0), (20.0, 30.0)])
    assert snap_clip_ends_to_lines(plan, [], video_duration=100.0, max_extend=2.0) is plan


def test_snap_recomputes_output_timeline_for_all_clips():
    """After snapping clip 0, the following clip's output_start shifts correctly."""
    silence = [{"start": 12.0, "end": 13.0}]
    plan = _make_plan([(0.0, 10.0), (20.0, 30.0)])
    snapped = snap_clip_ends_to_lines(plan, silence, video_duration=100.0, max_extend=5.0)
    c0, c1 = snapped["clips"]
    assert (c0["source_end"], c0["duration"], c0["output_start"], c0["output_end"]) == (12.0, 12.0, 0.0, 12.0)
    assert (c1["source_start"], c1["source_end"], c1["duration"]) == (20.0, 30.0, 10.0)
    assert (c1["output_start"], c1["output_end"]) == (12.0, 22.0)
    assert snapped["total_duration"] == 22.0


def test_sentence_anchor_pause_window_snaps_both_clip_edges(tmp_path):
    """Reliable sentence anchors are first-class cut boundaries even when long-silence is empty."""
    (tmp_path / "speech_boundary_anchors.json").write_text(json.dumps({"sentence_anchors": [
        {"time": 8.4, "pause_start": 8.2, "confidence": "high"},
        {"time": 14.3, "pause_start": 14.0, "confidence": "medium"},
        {"time": 18.0, "pause_start": 17.8, "confidence": "low"},
    ]}), encoding="utf-8")
    boundaries = sentence_boundaries._load_sentence_boundary_windows(tmp_path)
    assert boundaries == [
        {"start": 8.2, "end": 8.4, "kind": "sentence_anchor", "confidence": "high"},
        {"start": 14.0, "end": 14.3, "kind": "sentence_anchor", "confidence": "medium"},
    ]
    plan = _make_plan([(10.0, 13.5)], video_duration=30.0)
    plan = cut.snap_clip_starts_to_lines(plan, boundaries, 30.0, max_prepend=1.8)
    plan = cut.snap_clip_ends_to_lines(plan, boundaries, 30.0, max_extend=2.0)
    assert plan["clips"][0]["source_start"] == 8.4
    assert plan["clips"][0]["source_end"] == 14.0


def test_sentence_boundary_gate_blocks_unsnapped_speech_edges():
    plan = _make_plan([(3.0, 7.0)], video_duration=10.0)
    out = cut.enforce_clip_sentence_boundaries(
        plan, boundary_windows=[{"start": 4.8, "end": 5.0}],
        speech_spans=[{"start": 0.0, "end": 10.0}], video_duration=10.0)
    assert {b["edge"] for b in out["qc"]["blocking"]
            if b["code"] == "unsafe_clip_sentence_boundary"} == {"start", "end"}


def test_sentence_boundary_gate_allows_source_ends_and_contiguous_same_source_join():
    plan = _make_plan([(0.0, 5.0), (5.0, 10.0)], video_duration=10.0)
    out = cut.enforce_clip_sentence_boundaries(
        plan, boundary_windows=[], speech_spans=[{"start": 0.0, "end": 10.0}], video_duration=10.0)
    assert not out["qc"].get("blocking")
    checks = out["qc"]["boundary_status"]["sentence_checks"]
    assert [c["reason"] for c in checks] == [
        "source_start", "continuous_source_join", "continuous_source_join", "source_end"]


@pytest.mark.parametrize("clip,silence,expected_start,event", [
    pytest.param((10.0, 14.0), [{"start": 7.5, "end": 8.4}, {"start": 15.0, "end": 16.0}],
                 8.4, {"action": "prepended"}, id="prepends_to_prior_quiet_end"),
    pytest.param((10.2, 14.0), [{"start": 10.0, "end": 10.5}],
                 10.2, {"reason": "already_quiet"}, id="keeps_when_already_quiet"),
    pytest.param((10.0, 14.0), [{"start": 12.0, "end": 12.5}],
                 10.0, {"start_unsnapped_reason": "no_prior_quiet"}, id="no_prior_quiet_keeps"),
    pytest.param((10.0, 14.0), [{"start": 10.25, "end": 10.8}],
                 10.25, {"action": "trimmed"}, id="tiny_forward_trim_to_next_quiet"),
])
def test_snap_clip_starts_single_clip(clip, silence, expected_start, event):
    plan = _make_plan([clip])
    out = cut.snap_clip_starts_to_lines(plan, silence, video_duration=30.0, max_prepend=1.8, max_trim=0.35)
    c = out["clips"][0]
    assert c["source_start"] == expected_start
    assert c["duration"] == pytest.approx(14.0 - expected_start)
    snap = out["qc"]["boundary_status"]["start_snaps"][0]
    assert {key: snap.get(key) for key in event} == event


def test_snap_clip_starts_warns_when_prepend_would_overlap():
    plan = _make_plan([(8.0, 10.0), (10.0, 14.0)])
    silence = [{"start": 7.5, "end": 8.4}]
    out = cut.snap_clip_starts_to_lines(plan, silence, video_duration=30.0, max_prepend=1.8, max_trim=0.35)
    assert out["clips"][1]["source_start"] == 10.0
    assert out["qc"]["boundary_status"]["start_snaps"][1]["start_unsnapped_reason"] == "overlap_or_collapse"
    assert any(w["code"] == "clip_start_unsnapped" for w in out["qc"]["warnings"])


# ---------------------------------------------------------------------------
# snap_clips_off_shot_changes (avoid 闪烁 at edit points)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("clip,changes,expected", [
    pytest.param((10.0, 30.0), [10.3], (10.3, 30.0), id="start_moves_forward_past_early_change"),
    pytest.param((10.0, 30.0), [29.7], (10.0, 29.7), id="end_pulls_back_before_late_change"),
    pytest.param((10.0, 30.0), [20.0], (10.0, 30.0), id="mid_clip_change_leaves_boundaries"),
    pytest.param((10.0, 10.6), [10.4], (10.0, 10.6), id="skips_when_snap_would_collapse_clip"),
])
def test_scene_cut_snap_single_clip_boundaries(monkeypatch, clip, changes, expected):
    monkeypatch.setattr(sentence_boundaries, "_detect_shot_changes", _fake_detector(changes))
    plan = _make_plan([clip])
    c = snap_clips_off_shot_changes(plan, "v.mp4", margin=0.5, threshold=0.4)["clips"][0]
    assert (c["source_start"], c["source_end"]) == expected
    assert c["duration"] == round(expected[1] - expected[0], 3)
    assert (c["output_start"], c["output_end"]) == (0.0, c["duration"])


def test_scene_cut_snap_recomputes_output_across_multiple_clips(monkeypatch):
    """Output timeline is repacked cursor-based after the source ranges shrink."""
    monkeypatch.setattr(sentence_boundaries, "_detect_shot_changes", _fake_detector([10.3, 49.6]))
    plan = _make_plan([(10.0, 20.0), (40.0, 50.0)])
    snapped = snap_clips_off_shot_changes(plan, "v.mp4", margin=0.5, threshold=0.4)
    a, b = snapped["clips"]
    assert (a["source_start"], a["output_start"], a["output_end"]) == (10.3, 0.0, 9.7)
    assert (b["source_end"], b["output_start"]) == (49.6, 9.7)
    assert snapped["total_duration"] == round(9.7 + 9.6, 3)


def test_detect_shot_changes_offsets_by_seek_and_filters_window(monkeypatch):
    """pts_time is rebased to the seek target; cuts outside [win_start, win_end] are dropped."""
    stderr = "frame showinfo pts_time:1.762\n frame pts_time:5.866 \n frame pts_time:0.05\n"
    monkeypatch.setattr(sentence_boundaries, "subprocess",
                        types.SimpleNamespace(run=lambda *a, **k: CompletedProcess(a, 0, "", stderr)))
    out = sentence_boundaries._detect_shot_changes("v.mkv", 55.0, 68.0, 0.4)  # seek=54.75
    assert out == [56.512, 60.616]  # 54.8 (54.75+0.05) is < win_start -> filtered


# ---------------------------------------------------------------------------
# multi-source plans
# ---------------------------------------------------------------------------


def test_normalize_multi_source_clip_plan_maps_sources_and_validates_per_source_overlap(tmp_path):
    manifest = {"sources": [
        {"source_id": "a", "source_path": str(tmp_path / "a.mp4"), "duration": 10.0},
        {"source_id": "b", "source_path": str(tmp_path / "b.mp4"), "duration": 5.0},
    ]}
    plan = cut.normalize_multi_source_clip_plan([
        {"source_id": "a", "start": 1.0, "end": 4.0, "reason": "A"},
        {"source_id": "b", "start": 4.0, "end": 8.0, "reason": "B"},
        {"source_id": "b", "start": 0.0, "end": 1.0, "reason": "B2"},
    ], manifest, clip_padding=0.5)

    assert plan["total_duration"] == 7.0
    assert [c["source_id"] for c in plan["clips"]] == ["a", "b", "b"]
    assert plan["clips"][0]["source_path"].endswith("a.mp4")
    assert plan["clips"][0]["source_start"] == 0.5
    assert plan["clips"][1]["source_end"] == 5.0  # clamped to source b duration
    assert plan["clips"][2]["output_start"] == 5.5

    with pytest.raises(ValueError, match="source_id a"):
        cut.normalize_multi_source_clip_plan(
            [{"source_id": "a", "start": 1.0, "end": 4.0}, {"source_id": "a", "start": 3.5, "end": 5.0}], manifest)
    with pytest.raises(ValueError, match="missing source_id"):
        cut.normalize_multi_source_clip_plan([{"start": 1.0, "end": 2.0}], manifest)
    with pytest.raises(ValueError, match="unknown source_id"):
        cut.normalize_multi_source_clip_plan([{"source_id": "missing", "start": 1.0, "end": 2.0}], manifest)


def test_build_edited_source_video_multi_source_uses_multiple_inputs_and_cache_meta(monkeypatch, tmp_path):
    monkeypatch.setattr(media_geometry, "_probe_video_geometry", lambda _path: _geometry(1280, 720, 30.0))
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    a.write_bytes(b"aaa")
    b.write_bytes(b"bbb")
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    plan = cut.normalize_multi_source_clip_plan(
        [{"source_id": "a", "start": 0, "end": 1}, {"source_id": "b", "start": 2, "end": 3},
         {"source_id": "a", "start": 4, "end": 5}],
        {"sources": [{"source_id": "a", "source_path": str(a), "duration": 10},
                     {"source_id": "b", "source_path": str(b), "duration": 10}]},
    )
    commands = []

    def fake_run_cmd(cmd):
        commands.append(cmd)
        if cmd[0] == "ffprobe":
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        Path(cmd[-1]).write_bytes(b"edited")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("cut_render.run_cmd", fake_run_cmd)
    monkeypatch.setattr("media_geometry.run_cmd", fake_run_cmd)
    monkeypatch.setattr("cut_render.get_video_duration", lambda path: 3.0)

    out = cut.build_edited_source_video("ignored.mp4", _with_geometry(plan, [str(a), str(b)]), work_dir)

    ffmpeg_cmd = [cmd for cmd in commands if cmd[0] == "ffmpeg"][0]
    # Only the two media inputs: audio is synthesized per-clip inside filter_complex.
    assert ffmpeg_cmd.count("-i") == 2
    joined = " ".join(ffmpeg_cmd)
    assert f"-i {a}" in joined and f"-i {b}" in joined
    assert "[0:v]trim=start=0.000:end=1.000" in joined
    assert "[1:v]trim=start=2.000:end=3.000" in joined
    assert "[0:v]trim=start=4.000:end=5.000" in joined
    # Heterogeneous sources are normalized to one canvas before concat; each clip gets audio.
    assert "scale=1280:720" in joined and "setsar=1" in joined and "format=yuv420p" in joined
    assert "anullsrc=r=48000:cl=stereo" in joined
    assert "concat=n=3:v=1:a=1" in joined
    assert cut.should_reuse_edited_source(out, plan, "ignored.mp4") is True


@pytest.mark.skipif(
    not _HAVE_FFMPEG and not os.environ.get("RECAP_REQUIRE_FFMPEG"),
    reason="ffmpeg/ffprobe required for real render (set RECAP_REQUIRE_FFMPEG=1 to make a missing binary a hard failure instead of a silent skip)",
)
def test_build_edited_source_video_multi_resolution_mixed_audio_real_render(tmp_path):
    """Real ffmpeg: heterogeneous sources (different resolution/fps, one silent) must concat
    into ONE playable output with an audio track. This path was previously all-mocked, hiding
    the missing scale/setsar/fps normalization (concat would abort) and the all-or-nothing
    audio drop. On a runner where ffmpeg MUST exist, set RECAP_REQUIRE_FFMPEG=1 so a missing
    binary fails loudly instead of silently skipping this coverage."""
    import subprocess

    if not _HAVE_FFMPEG:
        pytest.fail("RECAP_REQUIRE_FFMPEG is set but ffmpeg/ffprobe is not installed")

    a = tmp_path / "a_1080_audio.mp4"  # 1920x1080@30, WITH audio
    b = tmp_path / "b_480_silent.mp4"  # 854x480@24, NO audio
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest", "-pix_fmt", "yuv420p", str(a)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=854x480:rate=24:duration=2",
         "-pix_fmt", "yuv420p", str(b)],
        check=True, capture_output=True,
    )
    work = tmp_path / "work"
    work.mkdir()
    plan = cut.normalize_multi_source_clip_plan(
        [{"source_id": "a", "start": 0.0, "end": 1.0}, {"source_id": "b", "start": 0.0, "end": 1.0},
         {"source_id": "a", "start": 1.0, "end": 2.0}],
        {"sources": [{"source_id": "a", "source_path": str(a), "duration": 2.0},
                     {"source_id": "b", "source_path": str(b), "duration": 2.0}]},
    )

    out = cut.build_edited_source_video(str(a), _with_geometry(plan, [str(a), str(b)]), work)
    assert out.exists() and out.stat().st_size > 0

    def _has_stream(kind):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", kind, "-show_entries", "stream=index",
             "-of", "csv=p=0", str(out)],
            capture_output=True, text=True,
        )
        return bool(r.stdout.strip())

    assert _has_stream("v:0"), "no video stream in concatenated output"
    assert _has_stream("a:0"), "audio track dropped even though one source had audio"
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True,
    ).stdout.strip())
    assert dur > 2.0, f"expected ~3s of concatenated clips, got {dur}s"


def _multi_plan(clips):
    """Validated-plan shape from (source_id, source_start, source_end) tuples, packed in order."""
    rows, cursor = [], 0.0
    for clip_id, (source_id, start, end) in enumerate(clips):
        duration = round(end - start, 3)
        rows.append({"clip_id": clip_id, "source_id": source_id, "source_path": f"/{source_id}.mp4",
                     "source_start": start, "source_end": end, "output_start": cursor,
                     "output_end": round(cursor + duration, 3), "duration": duration})
        cursor = round(cursor + duration, 3)
    return {"allow_overlap": False, "clips": rows, "total_duration": cursor}


def _sources(**durations):
    return {sid: {"source_path": f"/{sid}.mp4", "duration": d} for sid, d in durations.items()}


def _write_source_json(work, source_id, name, payload):
    source_work = work / "sources" / source_id
    source_work.mkdir(parents=True, exist_ok=True)
    (source_work / name).write_text(json.dumps(payload), encoding="utf-8")


def _snap_multi(plan, sources, work, **overrides):
    kwargs = dict(line_max_extend=2.0, scene_margin=0.5, scene_threshold=0.4,
                  start_max_prepend=1.8, start_max_trim=0.35)
    kwargs.update(overrides)
    return cut.snap_multi_source_clips(plan, sources, work, **kwargs)


def test_snap_multi_source_clips_line_snaps_each_clip_against_its_own_source(tmp_path):
    """Each clip's end snaps to ITS OWN source's next pause, then the output timeline is repacked."""
    work = tmp_path / "work"
    _write_source_json(work, "a", "silence_periods.json", [{"start": 4.2, "end": 5.0}])
    _write_source_json(work, "b", "silence_periods.json", [{"start": 9.0, "end": 9.5}])
    out = _snap_multi(_multi_plan([("a", 1.0, 4.0), ("b", 2.0, 8.5)]), _sources(a=10.0, b=12.0),
                      work, do_scene_snap=False)
    assert out["clips"][0]["source_end"] == 4.2  # source a's pause
    assert out["clips"][1]["source_end"] == 9.0  # source b's pause, NOT a's 4.2
    assert (out["clips"][0]["output_start"], out["clips"][0]["output_end"]) == (0.0, 3.2)
    assert (out["clips"][1]["output_start"], out["clips"][1]["duration"]) == (3.2, 7.0)
    assert out["total_duration"] == 10.2


def test_snap_multi_source_clips_routes_shot_detection_to_each_clips_source(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(sentence_boundaries, "_detect_shot_changes",
                        lambda video, *a, **k: (seen.append(str(video)), [])[1])
    _snap_multi(_multi_plan([("a", 0.0, 3.0), ("b", 0.0, 3.0)]), _sources(a=10.0, b=10.0),
                tmp_path, do_line_snap=False)
    assert "/a.mp4" in seen and "/b.mp4" in seen


def test_multi_source_sentence_snap_runs_after_shot_snap(monkeypatch, tmp_path):
    """Visual cleanup may move an edge, but the final pass must restore sentence-safe audio edges."""
    _write_source_json(tmp_path, "a", "silence_periods.json", [])
    _write_source_json(tmp_path, "a", "speech_boundary_anchors.json", {"sentence_anchors": [
        {"time": 9.5, "pause_start": 9.3, "confidence": "high"},
        {"time": 20.4, "pause_start": 20.2, "confidence": "high"},
    ]})
    _write_source_json(tmp_path, "a", "asr_result.json", [{"start": 0.0, "end": 30.0, "text": "持续讲话。"}])
    monkeypatch.setattr(sentence_boundaries, "_detect_shot_changes", _fake_detector([10.2, 19.8]))
    out = _snap_multi(_multi_plan([("a", 10.0, 20.0)]), _sources(a=30.0), tmp_path)
    assert out["clips"][0]["source_start"] == 9.5
    assert out["clips"][0]["source_end"] == 20.2
    assert not out["qc"].get("blocking")


def test_snap_multi_source_clips_noops_without_silence_or_flags(tmp_path):
    """No per-source silence data and scene-snap off -> boundaries unchanged (advisory degrade)."""
    out = _snap_multi(_multi_plan([("a", 1.0, 4.0)]), _sources(a=10.0), tmp_path, do_scene_snap=False)
    assert out["clips"][0]["source_end"] == 4.0
    assert out["total_duration"] == 3.0


def test_snap_multi_source_clips_start_snaps_each_source_silence(tmp_path):
    work = tmp_path / "work"
    _write_source_json(work, "a", "silence_periods.json", [{"start": 1.0, "end": 1.4}])
    _write_source_json(work, "b", "silence_periods.json", [{"start": 5.0, "end": 5.3}])
    out = _snap_multi(_multi_plan([("a", 2.0, 4.0), ("b", 6.0, 8.0)]), _sources(a=10.0, b=10.0),
                      work, line_max_extend=0.0, scene_margin=0.0, do_scene_snap=False)
    assert out["clips"][0]["source_start"] == 1.4
    assert out["clips"][1]["source_start"] == 5.3
    assert out["clips"][1]["output_start"] == 2.6


# ---------------------------------------------------------------------------
# output geometry
# ---------------------------------------------------------------------------


def test_select_output_geometry_uses_all_used_sources_not_first(monkeypatch):
    probes = {"/low.mp4": _geometry(854, 480, 24.0), "/hd.mp4": _geometry(1920, 1080, 30.0)}
    monkeypatch.setattr(media_geometry, "_probe_video_geometry", lambda p: probes[str(p)])
    clips = [{"source_id": "low", "source_path": "/low.mp4", "duration": 1.0},
             {"source_id": "hd", "source_path": "/hd.mp4", "duration": 5.0}]
    w, h, fps, qc = media_geometry._select_output_geometry(["/low.mp4", "/hd.mp4"], clips)
    assert (w, h, fps) == (1920, 1080, 30.0)
    assert qc["source_id"] == "hd"
    assert qc["reason"] == "weighted_orientation_area_fps"


def test_select_output_geometry_orientation_duration_and_fps_ties(monkeypatch):
    probes = {"/portrait.mp4": _geometry(1080, 1920, 24.0), "/landscape.mp4": _geometry(1280, 720, 60.0),
              "/landscape2.mp4": _geometry(1920, 1080, 30.0)}
    monkeypatch.setattr(media_geometry, "_probe_video_geometry", lambda p: probes[str(p)])
    clips = [{"source_id": "p", "source_path": "/portrait.mp4", "duration": 2.0},
             {"source_id": "l1", "source_path": "/landscape.mp4", "duration": 1.0},
             {"source_id": "l2", "source_path": "/landscape2.mp4", "duration": 2.0}]
    w, h, fps, qc = media_geometry._select_output_geometry(
        ["/portrait.mp4", "/landscape.mp4", "/landscape2.mp4"], clips)
    assert (w, h) == (1920, 1080)  # landscape wins by total used duration (3s > 2s)
    assert fps == 30.0  # the 30 fps bucket carries 2s of used duration
    assert qc["orientation"] == "landscape"


def test_probe_video_geometry_is_iterable_and_rotation_sar_aware(monkeypatch):
    payload = {"streams": [{"width": 1920, "height": 1080, "r_frame_rate": "30000/1001",
                            "sample_aspect_ratio": "2:1", "display_aspect_ratio": "16:9",
                            "tags": {"rotate": "90"}}]}
    monkeypatch.setattr(media_geometry, "run_cmd",
                        lambda cmd: CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr=""))
    geometry = media_geometry._probe_video_geometry("/rotated.mp4")
    width, height, fps = geometry
    assert (width, height) == (1080, 3840)
    assert fps == 29.97
    assert geometry.facts["coded_width"] == 1920
    assert geometry.facts["coded_height"] == 1080
    assert geometry.facts["rotation"] == 90
    assert geometry.facts["sample_aspect_ratio"] == "2:1"
    assert geometry.facts["rotation_swaps_axes"] is True
    assert geometry.facts["display_aspect_ratio"] == "16:9"
    assert geometry.facts["display_aspect_source"] == "sample_aspect_ratio"


@pytest.mark.parametrize("stream,expected", [
    pytest.param({"width": 1920, "height": 1080, "r_frame_rate": "30000/1001", "sample_aspect_ratio": "1:1",
                  "display_aspect_ratio": "9:16", "tags": {"rotate": "90"}}, (1080, 1920, 29.97),
                 id="rotated_portrait_ignores_dar"),
    # 720x576 PAL with SAR 16:15 displays as 768x576 and should stay even.
    pytest.param({"width": 720, "height": 576, "r_frame_rate": "25/1", "sample_aspect_ratio": "16:15",
                  "display_aspect_ratio": "4:3"}, (768, 576, 25.0), id="pal_non_square_pixels"),
])
def test_cut_probe_video_geometry_uses_display_geometry(monkeypatch, stream, expected):
    """Cut geometry uses display geometry (rotation, SAR), not raw encoded width/height."""
    monkeypatch.setattr(media_geometry, "run_cmd", lambda cmd: CompletedProcess(
        cmd, 0, stdout=json.dumps({"streams": [stream]}), stderr=""))
    assert media_geometry._probe_video_geometry("probe.mp4") == expected


def test_select_output_geometry_qc_exposes_rotation_sar_dar_facts(monkeypatch):
    probes = {
        "/rotated.mp4": cut.VideoGeometry(1080, 1920, 30.0, {
            "coded_width": 1920, "coded_height": 1080, "display_width": 1080, "display_height": 1920,
            "rotation": 90, "rotation_swaps_axes": True, "sample_aspect_ratio": "1:1",
            "sample_aspect_ratio_float": 1.0, "display_aspect_ratio": "16:9"}),
        "/landscape.mp4": _geometry(1280, 720, 30.0),
    }
    monkeypatch.setattr(media_geometry, "_probe_video_geometry", lambda p: probes[str(p)])
    clips = [{"source_id": "r", "source_path": "/rotated.mp4", "duration": 5.0},
             {"source_id": "l", "source_path": "/landscape.mp4", "duration": 1.0}]
    w, h, fps, qc = media_geometry._select_output_geometry(["/rotated.mp4", "/landscape.mp4"], clips)
    assert (w, h, fps) == (1080, 1920, 30.0)
    assert qc["orientation"] == "portrait"
    assert qc["rotation"] == 90
    assert qc["coded_width"] == 1920
    assert qc["sources"][0]["rotation"] == 0
    assert qc["sources"][1]["rotation"] == 90
    assert qc["sources"][1]["display_aspect_ratio"] == "16:9"


# ---------------------------------------------------------------------------
# render command details
# ---------------------------------------------------------------------------


def test_build_edited_source_video_writes_delivery_qc_and_meta_without_visual_qc(monkeypatch, tmp_path):
    def probe(cmd):
        joined = " ".join(cmd)
        if "-of json" in joined:
            return CompletedProcess(cmd, 0, stdout=json.dumps({"streams": [{
                "width": 1280, "height": 720, "r_frame_rate": "30/1", "sample_aspect_ratio": "1:1"}]}), stderr="")
        if "stream=sample_rate" in joined:
            return CompletedProcess(cmd, 0, stdout="48000\n", stderr="")
        return CompletedProcess(cmd, 0, stdout="0\n", stderr="")

    output, plan, _ = _capture_render(
        monkeypatch, tmp_path, [{"start": 0, "end": 1}, {"start": 2, "end": 3}], 4, probe=probe)
    work_dir = tmp_path / "work"
    delivery = json.loads((work_dir / "cut_delivery_qc.json").read_text(encoding="utf-8"))
    assert output.exists()
    assert delivery == plan["qc"]["delivery_qc"]
    assert delivery["video_encode_passes"] == 1
    assert delivery["audio_sample_rate"] == {"target": 48000, "probed": 48000}
    assert "trim_concat_filter_requires_reencode" in delivery["reencode_reason"]
    assert delivery["stream_copy_risk"]["status"] == "avoided"
    assert delivery["output_geometry"]["width"] == 1280
    assert not (work_dir / "visual_qc.json").exists()


def test_audio_join_fade_is_in_filter_graph(monkeypatch, tmp_path):
    _, _, commands = _capture_render(
        monkeypatch, tmp_path, [{"start": 0, "end": 1}, {"start": 2, "end": 3}], 4,
        config={"clip_join_audio_fade_ms": 30.0})
    joined = _ffmpeg_command(commands)
    assert "afade=t=in:st=0:d=0.030" in joined
    assert "afade=t=out:st=0.970:d=0.030" in joined


def test_contiguous_same_source_join_does_not_fade_inside_sentence(monkeypatch, tmp_path):
    """A no-gap source continuation is one sentence stream; do not attenuate its join."""
    _, _, commands = _capture_render(
        monkeypatch, tmp_path, [{"start": 0, "end": 1}, {"start": 1, "end": 2}], 2,
        config={"clip_join_audio_fade_ms": 30.0})
    joined = _ffmpeg_command(commands)
    first_audio = joined.split("[a0]", 1)[0]
    second_audio = joined.split("[a0]", 1)[1].split("[a1]", 1)[0]
    assert "afade=t=out" not in first_audio
    assert "afade=t=in" not in second_audio
    assert "[0:a]atrim=start=1.000:end=2.000,asetpts=PTS-STARTPTS,afade=t=in" not in joined


@pytest.mark.parametrize("kwargs,allowed_by", [
    pytest.param({}, None, id="drift_blocks"),
    pytest.param({"allow_duration_drift": True}, "--allow-duration-drift", id="cli_flag_allows"),
    pytest.param({"allow_duration_drift": True, "duration_drift_allowed_by": "operator-override"},
                 "operator-override", id="custom_allowed_by"),
])
def test_update_cut_qc_duration_status_and_allow_drift(kwargs, allowed_by):
    plan = {"clips": [{"duration": 4.0}], "total_duration": 4.0, "target_duration": 10.0}
    cut.update_cut_qc(plan, **kwargs)
    assert plan["qc"]["target_duration_status"] == "under"
    if allowed_by is None:
        assert plan["qc"]["blocking"][0]["code"] == "target_duration_drift"
    else:
        assert "blocking" not in plan["qc"]
        assert plan["qc"]["target_duration"]["duration_drift_allowed_by"] == allowed_by


def test_edited_source_cache_from_content_hash_schema_is_a_miss(tmp_path):
    """A schema_version 2 sidecar (clip_plan_fingerprint/source_fingerprints), or an empty
    one, simply re-renders."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    edited = tmp_path / "edited_source.mp4"
    edited.write_bytes(b"edited")
    plan = cut.normalize_clip_plan([{"start": 0.0, "end": 1.0}], video_duration=2.0)
    Path(f"{edited}.meta.json").write_text(json.dumps({
        "schema_version": 2, "clip_plan_fingerprint": "a" * 32, "render_fingerprint": "b" * 32,
        "render_cache": {}, "source_fingerprints": {str(video): "c" * 64},
        "edited_source_fingerprint": "d" * 64, "total_duration": 1.0, "clip_count": 1,
    }), encoding="utf-8")
    assert cut.should_reuse_edited_source(edited, plan, video) is False
    for empty in ("{}", "[]"):
        Path(f"{edited}.meta.json").write_text(empty, encoding="utf-8")
        assert cut.should_reuse_edited_source(edited, plan, video) is False
