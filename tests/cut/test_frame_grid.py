"""Frame-grid snapping keeps edited_source.mp4 constant frame rate; blocked edges name safe moves."""
import json
import os
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-cut" / "scripts"))
import cut_cli
import frame_grid
import media_geometry
import sentence_boundaries
from cut_contract import normalize_clip_plan

_HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
_REAL_FFMPEG = pytest.mark.skipif(
    not _HAVE_FFMPEG and not os.environ.get("RECAP_REQUIRE_FFMPEG"),
    reason="ffmpeg/ffprobe required for real render (set RECAP_REQUIRE_FFMPEG=1 to make a "
    "missing binary a hard failure instead of a silent skip)",
)


def _grid(source_rate, output_rate=None, origin=0.0):
    rate = Fraction(source_rate) if source_rate else None
    return {"source_rate": rate, "origin": origin,
            "output_rate": Fraction(output_rate or source_rate)}


def _snap(spans, grid, windows=(), speech=(), duration=100.0, allow_overlap=False, joined=None):
    clips = normalize_clip_plan([{"start": s, "end": e} for s, e in spans], duration)["clips"]
    classify = sentence_boundaries._edge_classifier(list(windows), list(speech), duration, 0.05)
    return frame_grid.snap_edges_to_frames(
        clips, grid, list(windows), classify, duration, allow_overlap=allow_overlap,
        joined_to_previous=joined or [False] * len(clips))


@pytest.mark.parametrize("text,expected", [
    ("25/1", Fraction(25)), ("30000/1001", Fraction(30000, 1001)), ("30.0/1", Fraction(30)),
    ("0/0", None), ("90000/1", None), ("N/A", None), (None, None),
])
def test_parse_frame_rate_rejects_unknown_and_timebase_rates(text, expected):
    assert frame_grid.parse_frame_rate(text) == expected


@pytest.mark.parametrize("stream,expected", [
    ({"r_frame_rate": "50/1", "avg_frame_rate": "25/1"}, "25/1"),  # interlaced: field rate
    ({"r_frame_rate": "30000/1001", "avg_frame_rate": "29876/1000"}, "30000/1001"),  # phone VFR
    ({"r_frame_rate": "24/1"}, "24/1"),
    # VFR phone clip whose nominal rate is far above its real average: not 60 fps CFR.
    ({"r_frame_rate": "60/1", "avg_frame_rate": "29600/1000"}, "30/1"),
])
def test_probed_frame_grid_uses_frames_not_fields(stream, expected):
    geometry = media_geometry._geometry_from_stream(
        {"width": 320, "height": 240, "start_time": "1.5", **stream}, format_start="1.4")
    assert geometry.facts["frame_rate"] == expected
    # The canvas fps bucket follows the same grid, not the field or nominal rate.
    assert geometry[2] == pytest.approx(float(Fraction(expected)), abs=1e-3)
    assert geometry.facts["video_start_offset"] == pytest.approx(0.1)


def test_canvas_frame_rate_uses_exact_ntsc_rates():
    assert frame_grid.canvas_frame_rate(29.97) == Fraction(30000, 1001)
    assert frame_grid.canvas_frame_rate(25.0) == Fraction(25)
    rows = [{"frame_rate": "30000/1001"}]
    assert frame_grid.output_frame_rate(rows, 29.97) == Fraction(30000, 1001)
    # Several sources are resampled to the canvas bucket, whatever their own rates.
    assert frame_grid.output_frame_rate(rows * 2, 25.0) == Fraction(25)


def test_off_grid_edges_move_to_the_nearest_frame_boundary():
    clips, events = _snap([(1.01, 3.03), (4.51, 6.53)], _grid(25))
    assert [(c["source_start"], c["source_end"]) for c in clips] == [(1.0, 3.04), (4.52, 6.52)]
    assert [e["frame_count"] for e in events] == [51, 50]


def test_frame_snap_keeps_edges_inside_their_pause_windows():
    """End on a pause start rounds up into the pause; start on a pause end rounds down."""
    windows = [{"start": 6.9, "end": 7.317}, {"start": 9.213, "end": 9.6}]
    speech = [{"start": 7.317, "end": 9.213}]
    clips, _ = _snap([(7.317, 9.213)], _grid(25), windows, speech)
    # Nearest would be 7.32 (inside speech) and 9.2 (13 ms before the word ends).
    assert (clips[0]["source_start"], clips[0]["source_end"]) == (7.28, 9.24)


def test_continuous_join_stays_lossless_and_lengths_are_whole_output_frames():
    # A 24 fps source on a 30 fps canvas: starts on the source grid, lengths in 1/30 s.
    clips, events = _snap([(1.017, 2.5), (2.5, 3.29)], _grid(24, 30), joined=[False, True])
    assert clips[0]["source_start"] == 1.0
    assert clips[1]["source_start"] == clips[0]["source_end"]
    assert events[1]["start_reason"] == "continuous_source_join"
    for clip, event in zip(clips, events):
        frames = (clip["source_end"] - clip["source_start"]) * 30
        assert frames == pytest.approx(event["frame_count"], abs=0.05)


def test_frame_snap_never_grows_a_clip_into_its_neighbours_source_range():
    # The plan plays the later range first; even a pause past 5.02 may not pull the
    # earlier clip's end into it, so the end shrinks to 5.0 instead of growing to 5.04.
    clips, _ = _snap([(5.02, 8.0), (1.0, 5.015)], _grid(None, 25),
                     windows=[{"start": 5.03, "end": 5.05}])
    assert clips[1]["source_end"] == 5.0
    clips, _ = _snap([(5.02, 8.0), (1.0, 5.015)], _grid(None, 25),
                     windows=[{"start": 5.03, "end": 5.05}], allow_overlap=True)
    assert clips[1]["source_end"] == 5.04


def test_frame_snap_does_not_shrink_a_clip_off_a_required_evidence_edge(monkeypatch, tmp_path):
    keep = dict(_grid(25), keep_ranges=[(2.23, 3.0), (4.0, 4.99)])
    clips, _ = _snap([(2.23, 4.99)], keep)
    # Nearest would be 2.24 / 4.96, which would no longer cover the declared nodes exactly.
    assert (clips[0]["source_start"], clips[0]["source_end"]) == (2.2, 5.0)

    # End to end: the declared moment still counts as kept after frame snapping.
    monkeypatch.setattr(media_geometry, "_probe_video_geometry", lambda _path: (
        media_geometry._geometry_from_stream({"width": 320, "height": 240, "r_frame_rate": "25/1"})))
    monkeypatch.setattr(sentence_boundaries, "_detect_shot_changes", lambda *_a, **_k: [])
    monkeypatch.setattr(cut_cli, "get_video_duration", lambda _path: 10.0)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    node = {"id": "beat", "source": str(video), "start": 2.23, "end": 4.99, "track": "video",
            "content": "关键动作完整保留"}
    (tmp_path / "clip_plan.json").write_text(json.dumps({
        "clips": [{"start": 2.23, "end": 4.99}],
        "required_evidence": {"nodes": [node], "before": []}}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["cut.py", str(video), "--work-dir", str(tmp_path),
                                      "--normalize-only"])
    cut_cli.main()
    plan = json.loads((tmp_path / "clip_plan_validated.json").read_text(encoding="utf-8"))
    assert plan["qc"]["required_evidence"]["selection_status"] != "BLOCK"


def _snap_with_gate(tmp_path, spans, rate, speech, duration=10.0, origin=0.0):
    """snap_source_clips (frame pass + gate only) against an ASR transcript of `speech`."""
    (tmp_path / "asr_clean.json").write_text(json.dumps({"segments": [
        {"start": a, "end": b, "text": "完整的一句台词"} for a, b in speech]}), encoding="utf-8")
    plan = normalize_clip_plan([{"start": a, "end": b} for a, b in spans], duration)
    return sentence_boundaries.snap_source_clips(
        plan, tmp_path / "v.mp4", duration, tmp_path, line_max_extend=0.5, scene_margin=0.5,
        scene_threshold=0.4, start_max_prepend=0.5, start_max_trim=0.35, do_line_snap=False,
        do_scene_snap=False, frame_grid=_grid(rate, origin=origin))


def _edge_status(plan, edge, clip_id=0):
    return next(c for c in plan["qc"]["boundary_status"]["sentence_checks"]
                if c["edge"] == edge and c["clip_id"] == clip_id)


@pytest.mark.parametrize("rate", [24, 25, 30])
def test_frame_snap_never_turns_a_gate_safe_edge_into_a_blocked_one(tmp_path, rate):
    """2.055 clears speech [0, 2] by more than the gate's 50 ms; the nearest frame (2.04 at
    25 fps) does not, so the frame pass must pick the frame on the safe side."""
    out = _snap_with_gate(tmp_path, [(0.0, 2.055)], rate, [(0.0, 2.0), (3.0, 10.0)])
    assert out["clips"][0]["source_end"] > 2.05
    assert _edge_status(out, "end")["status"] == "safe"
    assert "blocking" not in out["qc"]


@pytest.mark.parametrize("rate", [24, 25, 29.97, 30, 50])
def test_nearest_safe_suggestion_survives_frame_snap_and_the_gate(tmp_path, rate):
    rate = frame_grid.canvas_frame_rate(rate)
    speech = [(0.0, 2.0), (3.0, 10.0)]
    blocked = _snap_with_gate(tmp_path, [(0.0, 1.5)], rate, speech)
    after = _edge_status(blocked, "end")["nearest_safe"]["after"]["time"]
    retried = _snap_with_gate(tmp_path, [(0.0, after)], rate, speech)
    assert _edge_status(retried, "end")["status"] == "safe"
    assert "blocking" not in retried["qc"]


def test_a_start_at_the_file_start_stays_a_source_start_when_video_begins_later(tmp_path):
    """A source whose first video frame is 0.1 s in: a start at 0.0 snaps onto that frame and
    is still the source start, even though speech covers the opening."""
    out = _snap_with_gate(tmp_path, [(0.0, 2.5)], 25, [(0.0, 2.0)], origin=0.1)
    assert out["clips"][0]["source_start"] == 0.1
    assert _edge_status(out, "start")["reason"] == "source_start"


def test_output_timeline_follows_cumulative_frames_at_ntsc_rates():
    plan = normalize_clip_plan([{"start": i * 10.0, "end": i * 10.0 + 1.0} for i in range(30)],
                               400.0)
    frame_grid.record_frame_grid(plan, {"frame_rate": "30000/1001", "sources": []})
    rate = Fraction(30000, 1001)
    frames = 0
    for clip in plan["clips"]:
        assert clip["output_start"] == round(float(frames / rate), 3)
        frames += clip["frame_count"]
    assert plan["total_duration"] == plan["qc"]["frame_grid"]["duration"]
    assert plan["clips"][-1]["output_end"] == plan["total_duration"]


def test_unknown_source_rate_still_renders_whole_output_frames():
    clips, events = _snap([(1.013, 2.0)], _grid(None, 25))
    assert clips[0]["source_start"] == 1.013
    assert events[0]["start_reason"] == "source_frame_rate_unknown"
    assert clips[0]["source_end"] == pytest.approx(1.013 + 25 / 25, abs=1e-3)


def _gate(plan, windows, speech, duration=10.0):
    return sentence_boundaries.enforce_clip_sentence_boundaries(
        plan, boundary_windows=windows, speech_spans=speech, video_duration=duration)


def test_blocked_edges_name_the_nearest_safe_edges_on_both_sides():
    plan = normalize_clip_plan([{"start": 3.0, "end": 7.0}], 10.0)
    out = _gate(plan, [{"start": 4.8, "end": 5.0}], [{"start": 0.0, "end": 10.0}])
    by_edge = {b["edge"]: b["nearest_safe"] for b in out["qc"]["blocking"]}
    assert by_edge["start"] == {
        "before": {"time": 0.0, "reason": "source_start", "delta": -3.0},
        "after": {"time": 4.8, "reason": "sentence_or_quiet_boundary", "delta": 1.8},
    }
    assert by_edge["end"] == {
        "before": {"time": 5.0, "reason": "sentence_or_quiet_boundary", "delta": -2.0},
        "after": {"time": 10.0, "reason": "source_end", "delta": 3.0},
    }
    checks = out["qc"]["boundary_status"]["sentence_checks"]
    assert all("nearest_safe" in c for c in checks if c["status"] == "blocking")


def test_nearest_safe_edges_clear_speech_by_the_gate_tolerance_and_stay_close():
    plan = normalize_clip_plan([{"start": 13.0, "end": 30.0}], 40.0)
    out = _gate(plan, [], [{"start": 12.0, "end": 14.0}, {"start": 14.5, "end": 25.0},
                           {"start": 25.0, "end": 40.0}], duration=40.0)
    start = next(c for c in out["qc"]["boundary_status"]["sentence_checks"] if c["edge"] == "start")
    assert start["nearest_safe"]["before"]["time"] == 11.94
    assert start["nearest_safe"]["after"]["time"] == 14.06
    assert start["nearest_safe"]["after"]["reason"] == "outside_detected_speech"
    end = next(c for c in out["qc"]["boundary_status"]["sentence_checks"] if c["edge"] == "end")
    # Nothing safe within the search span: the agent must restructure, not nudge.
    assert end["nearest_safe"] == {"before": None, "after": None}


def test_nearest_safe_edges_never_collapse_the_clip():
    plan = normalize_clip_plan([{"start": 3.0, "end": 4.0}], 20.0)
    out = _gate(plan, [{"start": 1.0, "end": 1.2}, {"start": 4.5, "end": 4.6}],
                [{"start": 0.0, "end": 20.0}], duration=20.0)
    by_edge = {b["edge"]: b["nearest_safe"] for b in out["qc"]["blocking"]}
    # The 1.2 pause would end the clip before it starts; the 4.5 pause would start it after its end.
    assert by_edge["end"]["before"] is None and by_edge["end"]["after"]["time"] == 4.5
    assert by_edge["start"]["after"] is None and by_edge["start"]["before"]["time"] == 1.2


def test_same_source_edges_split_by_another_sources_clip_are_not_a_continuous_join(tmp_path):
    """A per-source sub-plan packs a:0-2 and a:2-4 together; b's clip really separates them."""
    work = tmp_path / "work"
    source_work = work / "sources" / "a"
    source_work.mkdir(parents=True)
    (source_work / "asr_clean.json").write_text(json.dumps(
        {"segments": [{"start": 1.0, "end": 3.0, "text": "一句完整的话"}]}), encoding="utf-8")
    (source_work / "silence_periods.json").write_text(json.dumps([{"start": 8.0, "end": 8.5}]),
                                                      encoding="utf-8")
    rows, cursor = [], 0.0
    for clip_id, (sid, start, end) in enumerate([("a", 0.0, 2.0), ("b", 0.0, 2.0), ("a", 2.0, 4.0)]):
        rows.append({"clip_id": clip_id, "source_id": sid, "source_path": f"/{sid}.mp4",
                     "source_start": start, "source_end": end, "output_start": cursor,
                     "output_end": cursor + end - start, "duration": end - start})
        cursor += end - start
    out = sentence_boundaries.snap_multi_source_clips(
        {"allow_overlap": False, "clips": rows, "total_duration": cursor},
        {"a": {"source_path": "/a.mp4", "duration": 10.0},
         "b": {"source_path": "/b.mp4", "duration": 10.0}},
        work, line_max_extend=0.5, scene_margin=0.5, scene_threshold=0.4,
        start_max_prepend=0.5, start_max_trim=0.35, do_scene_snap=False)
    blocked = {(b["clip_id"], b["edge"]) for b in out["qc"]["blocking"]}
    assert blocked == {(0, "end"), (2, "start")}


def test_cut_main_records_frame_aligned_clips_and_frame_grid(monkeypatch, tmp_path):
    monkeypatch.setattr(media_geometry, "_probe_video_geometry", lambda _path: (
        media_geometry._geometry_from_stream(
            {"width": 320, "height": 240, "r_frame_rate": "25/1", "start_time": "0.000000"})))
    monkeypatch.setattr(sentence_boundaries, "_detect_shot_changes", lambda *_a, **_k: [])
    monkeypatch.setattr(cut_cli, "get_video_duration", lambda _path: 10.0)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    (tmp_path / "clip_plan.json").write_text(json.dumps(
        [{"start": 1.01, "end": 3.03}, {"start": 4.51, "end": 6.53}]), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["cut.py", str(video), "--work-dir", str(tmp_path),
                                      "--normalize-only"])

    cut_cli.main()

    plan = json.loads((tmp_path / "clip_plan_validated.json").read_text(encoding="utf-8"))
    assert [(c["source_start"], c["source_end"], c["frame_count"]) for c in plan["clips"]] == [
        (1.0, 3.04, 51), (4.52, 6.52, 50)]
    assert [(c["output_start"], c["output_end"]) for c in plan["clips"]] == [
        (0.0, 2.04), (2.04, 4.04)]
    grid = plan["qc"]["frame_grid"]
    assert grid["output_frame_rate"] == "25" and grid["frame_count"] == 101
    assert grid["duration"] == plan["total_duration"] == 4.04
    assert len(plan["qc"]["boundary_status"]["frame_snaps"]) == 2


def _make_source(path, size, rate, duration, audio=True, audio_duration=None):
    """Synthetic source; `audio_duration` longer than `duration` leaves the video stream short."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
           "-i", f"testsrc=size={size}:rate={rate}:duration={duration}"]
    if audio:
        cmd += ["-f", "lavfi", "-i",
                f"sine=frequency=440:sample_rate=48000:duration={audio_duration or duration}"]
        cmd += [] if audio_duration else ["-shortest"]
    subprocess.run(cmd + ["-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)


def _video_packet_times(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    return sorted(float(t) for t in out.split())


@_REAL_FFMPEG
@pytest.mark.parametrize("multi_source", [False, True], ids=["single_25fps", "multi_30_24fps"])
def test_off_grid_plan_renders_constant_frame_rate_with_exact_frame_count(
        monkeypatch, tmp_path, multi_source):
    """Real ffmpeg: before frame snapping, edges like 1.01/3.03 dropped an 80 ms frame slot
    per join (VFR output). Every frame interval must now be equal and the count exact."""
    if not _HAVE_FFMPEG:
        pytest.fail("RECAP_REQUIRE_FFMPEG is set but ffmpeg/ffprobe is not installed")
    work = tmp_path / "work"
    work.mkdir()
    main = tmp_path / "a.mp4"
    argv = ["cut.py", str(main), "--work-dir", str(work)]
    if multi_source:
        _make_source(main, "320x240", 30, 8)
        other = tmp_path / "b.mp4"
        _make_source(other, "160x120", 24, 8, audio=False)
        manifest = tmp_path / "sources.json"
        manifest.write_text(json.dumps({"sources": [
            {"source_id": "a", "source_path": str(main)},
            {"source_id": "b", "source_path": str(other)}]}), encoding="utf-8")
        argv += ["--sources-manifest", str(manifest)]
        clips = [{"source_id": "a", "start": 0.51, "end": 2.013},
                 {"source_id": "b", "start": 1.017, "end": 3.29},
                 {"source_id": "a", "start": 3.3, "end": 5.55}]
    else:
        _make_source(main, "320x240", 25, 10)
        clips = [{"start": 1.01, "end": 3.03}, {"start": 4.51, "end": 6.53},
                 {"start": 7.01, "end": 9.0}]
    (work / "clip_plan.json").write_text(json.dumps({"clips": clips}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", argv)

    cut_cli.main()

    plan = json.loads((work / "clip_plan_validated.json").read_text(encoding="utf-8"))
    rate = Fraction(plan["qc"]["frame_grid"]["output_frame_rate"])
    assert rate == (30 if multi_source else 25)
    times = _video_packet_times(work / "edited_source.mp4")
    assert len(times) == round(plan["total_duration"] * rate) == plan["qc"]["frame_grid"]["frame_count"]
    deltas = {round(b - a, 4) for a, b in zip(times, times[1:])}
    assert deltas == {round(float(1 / rate), 4)}


@_REAL_FFMPEG
def test_clip_past_the_end_of_a_short_video_stream_still_renders_every_frame(
        monkeypatch, tmp_path):
    """Real ffmpeg: 9.8 s of video under 10 s of audio. The clip ending at the container end
    used to render four frames short (a 0.2 s hole mid-stream when it played first)."""
    if not _HAVE_FFMPEG:
        pytest.fail("RECAP_REQUIRE_FFMPEG is set but ffmpeg/ffprobe is not installed")
    work = tmp_path / "work"
    work.mkdir()
    source = tmp_path / "a.mp4"
    _make_source(source, "320x240", 25, 9.8, audio_duration=10)
    (work / "clip_plan.json").write_text(json.dumps(
        {"clips": [{"start": 8.0, "end": 10.0}, {"start": 1.0, "end": 3.0}]}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["cut.py", str(source), "--work-dir", str(work)])

    cut_cli.main()

    plan = json.loads((work / "clip_plan_validated.json").read_text(encoding="utf-8"))
    times = _video_packet_times(work / "edited_source.mp4")
    assert len(times) == plan["qc"]["frame_grid"]["frame_count"] == 100
    assert {round(b - a, 4) for a, b in zip(times, times[1:])} == {0.04}
