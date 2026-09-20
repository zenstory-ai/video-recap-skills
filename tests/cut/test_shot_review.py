"""Actual decoded frame boundaries, not EDL segment length, drive shot recall."""
import json
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/video-cut/scripts"))
import shot_review
import cut_contract


def clock(count=240, fps=24):
    return [Fraction(i, fps) for i in range(count)], Fraction(count, fps)


def test_long_edl_span_cannot_hide_two_twelve_and_fourteen_frames():
    pts, end = clock()
    report = shot_review.summarize_candidates(pts, end, [48, 50, 100, 112, 180, 194])
    assert [s["frame_count"] for s in report["short_spans"]] == [2, 12, 14]
    assert report["normal_speed_review"] == "NOT_CHECKED"
    assert report["status"] == "NEEDS_REVIEW"
    assert report["automatic_repairs"] == []
    assert {s["origin"] for s in report["candidates"]} == {"UNKNOWN"}


def test_first_last_spans_and_inclusive_threshold():
    pts, end = clock(96)
    report = shot_review.summarize_candidates(pts, end, [2, 82])
    assert [(s["start_frame"], s["end_frame"]) for s in report["short_spans"]] == [(0, 2), (82, 96)]
    assert report["short_spans"][-1]["frame_count"] == 14


def test_dense_windows_count_cuts_not_shots_and_merge_overlap():
    pts, end = clock()
    report = shot_review.summarize_candidates(pts, end, [24, 30, 40, 50, 60, 200])
    assert len(report["dense_windows"]) == 1
    assert report["dense_windows"][0]["cut_frames"] == [24, 30, 40, 50, 60]


def test_vfr_uses_actual_pts_not_average_fps():
    pts = [Fraction(0), Fraction(1, 100), Fraction(3), Fraction(4)]
    report = shot_review.summarize_candidates(pts, Fraction(5), [1, 2], max_short_seconds=0.6)
    assert [(s["start_frame"], s["end_frame"]) for s in report["short_spans"]] == [(0, 1)]
    assert report["short_spans"][0]["duration_exact"] == "1/100"


def test_short_frame_cap_follows_measured_fps_not_a_hardcoded_24():
    pts, end = clock(300, fps=30)
    report = shot_review.summarize_candidates(pts, end, [26])
    assert report["policy"]["max_short_frames"] == 30
    assert [(s["start_frame"], s["end_frame"], s["frame_count"]) for s in report["short_spans"]] == [
        (0, 26, 26)
    ]
    assert report["short_spans"][0]["duration_exact"] == "13/15"
    explicit = shot_review.summarize_candidates(pts, end, [26], max_short_frames=24)
    assert explicit["policy"]["max_short_frames"] == 24
    assert explicit["short_spans"] == []


def test_no_candidates_is_not_perceptual_pass():
    pts, end = clock()
    report = shot_review.summarize_candidates(pts, end, [])
    assert report["status"] == "NO_CANDIDATES"
    assert report["normal_speed_review"] == "NOT_CHECKED"


@pytest.mark.parametrize("pts,end,cuts", [
    ([Fraction(0), Fraction(0)], Fraction(1), []),
    ([Fraction(0), Fraction(2)], Fraction(1), []),
    ([Fraction(0)], Fraction(1), [1]),
    ([Fraction(0)], Fraction(1), [True]),
])
def test_bad_clock_and_frame_indices_fail(pts, end, cuts):
    with pytest.raises(ValueError):
        shot_review.summarize_candidates(pts, end, cuts)


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan"), True])
def test_invalid_threshold_fails_before_media_tools(tmp_path, threshold):
    with pytest.raises(ValueError, match="threshold"):
        shot_review.scan_video(tmp_path / "absent.mp4", threshold=threshold)


def test_supplied_mapping_requires_matching_render_metadata(tmp_path):
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"current")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"clips": [], "total_duration": 10}))
    with pytest.raises(ValueError, match="binding"):
        shot_review.load_bound_plan(video, plan)


def bound_fixture(tmp_path):
    video, source, plan = [tmp_path / p for p in ("edited.mp4", "source.mp4", "plan.json")]
    video.write_bytes(b"edited")
    source.write_bytes(b"source")
    payload = {"total_duration": 10, "clips": [
        {"clip_id": 0, "source_start": 10, "source_end": 15, "output_start": 0, "output_end": 5},
        {"clip_id": 1, "source_start": 20, "source_end": 25, "output_start": 5, "output_end": 10},
    ]}
    plan.write_text(json.dumps(payload))
    cut_contract._write_edited_source_meta(video, payload, source)
    return video, source, plan, payload


@pytest.mark.parametrize("changed", ["video", "source", "plan", "render_settings"])
def test_changed_bound_inputs_fail_including_same_mtime(tmp_path, changed):
    video, source, plan, _ = bound_fixture(tmp_path)
    assert shot_review.load_bound_plan(video, plan)["total_duration"] == 10
    path = {"video": video, "source": source, "plan": plan,
            "render_settings": Path(str(video) + ".meta.json")}[changed]
    if changed in {"source", "video"}:
        import os
        st = path.stat()
        path.write_bytes(b"Z" * st.st_size)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    else:
        obj = json.loads(path.read_text())
        if changed == "plan":
            obj["clips"][0]["source_start"] = 9
        else:
            obj["render_fingerprint"] = "stale"
        path.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match="binding"):
        shot_review.load_bound_plan(video, plan)


def test_bound_plan_only_locates_join_and_internal_clip_not_native_cut(tmp_path):
    video, _, plan_path, _ = bound_fixture(tmp_path)
    plan = shot_review.load_bound_plan(video, plan_path)
    pts, end = clock()
    report = shot_review.summarize_candidates(pts, end, [48, 50, 119, 120, 121, 180, 194])
    shot_review._associate_plan(report, plan, pts, end)
    assert [c["frame"] for c in report["candidates"] if c["origin"] == "EDIT_JOIN_CANDIDATE"] == [119, 120, 121]
    inside = report["candidates"][0]
    assert inside["inside_bound_clip"] == 0
    assert inside["source_time_estimate"] == "12"
    assert inside["origin"] == "UNKNOWN"


@pytest.mark.parametrize("which", ["media", "plan", "meta", "source"])
def test_report_never_overwrites_declared_inputs(tmp_path, which):
    video, source, plan, _ = bound_fixture(tmp_path)
    output = {"media": video, "plan": plan, "meta": Path(str(video) + ".meta.json"), "source": source}[which]
    before = output.read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        shot_review.write_scan(video, output, plan_path=plan)
    assert output.read_bytes() == before


def test_unmatched_scene_pts_fails_not_rounds_to_nearest_frame(tmp_path, monkeypatch):
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"fake")
    pts, end = clock()
    monkeypatch.setattr(shot_review, "probe_frame_clock", lambda _: (pts, end, Fraction(0)))
    monkeypatch.setattr(shot_review, "detect_scene_pts", lambda *_: [Fraction(1, 100)])
    with pytest.raises(ValueError, match="unique decoded frame"):
        shot_review.scan_video(video)


def test_scene_subprocess_failure_does_not_return_empty_candidates(monkeypatch):
    monkeypatch.setattr(shot_review.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "decoder broke"))
    with pytest.raises(RuntimeError, match="scene decode failed"):
        shot_review.detect_scene_pts("ignored", 0.35)


def test_successful_process_missing_frame_clock_is_not_a_good_scan(monkeypatch):
    monkeypatch.setattr(shot_review.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    with pytest.raises(ValueError, match="timebase"):
        shot_review.detect_scene_pts("ignored", 0.35)


@pytest.mark.parametrize("normalize_only", [False, True])
@pytest.mark.parametrize("threshold", [None, 0.08])
@pytest.mark.parametrize("roi", [None, [2, 4, 32, 32]])
def test_cut_cli_opt_in_reviews_actual_cut_not_plan_only(tmp_path, monkeypatch, normalize_only, threshold, roi):
    import cut_cli
    import media_geometry
    video = tmp_path / "source.mp4"
    video.write_bytes(b"source")
    work = tmp_path / "work"
    work.mkdir()
    (work / "clip_plan.json").write_text('[{"start":0,"end":4}]')
    monkeypatch.setitem(cut_cli.CONFIG, "scene_cut_snap", False)
    monkeypatch.setitem(cut_cli.CONFIG, "snap_clip_line_end", False)
    monkeypatch.setattr(cut_cli, "get_video_duration", lambda _: 4)
    monkeypatch.setattr(media_geometry, "_probe_video_geometry", lambda _: media_geometry._geometry_from_stream(
        {"width": 64, "height": 64, "r_frame_rate": "24/1"}))
    monkeypatch.setattr(cut_cli, "build_edited_source_video", lambda a, b, c, d: d.write_bytes(b"edited"))
    scans = []
    monkeypatch.setattr(shot_review, "write_scan", lambda *a, **k: scans.append((a, k)))
    args = ["cut.py", str(video), "--work-dir", str(work), "--review-shots"]
    if normalize_only:
        args.append("--normalize-only")
    if threshold is not None:
        args.extend(["--shot-scene-threshold", str(threshold)])
    if roi is not None:
        args.extend(["--shot-roi", *map(str, roi)])
    monkeypatch.setattr(sys, "argv", args)
    cut_cli.main()
    if normalize_only:
        assert scans == []
    else:
        options = {"plan_path": work / "clip_plan_validated.json"}
        if threshold is not None:
            options["threshold"] = threshold
        if roi is not None:
            options["roi"] = roi
        assert scans == [((work / "edited_source.mp4", work / "shot_review.json"),
                          options)]


@pytest.mark.parametrize('options', [
    ['--shot-roi', '0', '0', '32', '32'],
    ['--review-shots', '--shot-roi', '-1', '0', '32', '32'],
    ['--review-shots', '--shot-roi', '0', '0', '0', '32'],
])
def test_bad_cut_roi_option_fails_before_loading_source(tmp_path, monkeypatch, options):
    import cut_cli
    monkeypatch.setattr(sys, 'argv', ['cut.py', str(tmp_path / 'absent.mp4'),
                                     '--work-dir', str(tmp_path / 'work'), *options])
    with pytest.raises(SystemExit) as exc:
        cut_cli.main()
    assert exc.value.code == 2
    assert not (tmp_path / 'work').exists()


def test_scan_failure_never_leaves_old_success_report(tmp_path, monkeypatch):
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"fake")
    output = tmp_path / "review.json"
    output.write_text('{"schema_version":1,"artifact":"shot_review","status":"NO_CANDIDATES"}')
    def fail(*args, **kwargs):
        raise RuntimeError("decoder failed")
    monkeypatch.setattr(shot_review, "scan_video", fail)
    with pytest.raises(RuntimeError, match="decoder"):
        shot_review.write_scan(video, output)
    report = json.loads(output.read_text())
    assert report["status"] == "SCAN_FAILED"
    assert report["normal_speed_review"] == "NOT_CHECKED"


def test_corrupt_meta_invalidates_verified_old_report_not_unknown_file(tmp_path):
    video, _, plan, _ = bound_fixture(tmp_path)
    Path(str(video) + ".meta.json").write_text("broken")
    output = tmp_path / "shot_review.json"
    output.write_text('{"schema_version":1,"artifact":"shot_review","status":"NO_CANDIDATES"}')
    with pytest.raises(ValueError):
        shot_review.write_scan(video, output, plan_path=plan)
    assert json.loads(output.read_text())["status"] == "SCAN_FAILED"
    output.write_text("something important")
    with pytest.raises(ValueError, match="overwrite"):
        shot_review.write_scan(video, output, plan_path=plan)
    assert output.read_text() == "something important"


def test_stale_plan_and_meta_protect_union_of_declared_sources(tmp_path):
    video, _, plan, payload = bound_fixture(tmp_path)
    other = tmp_path / "other.mp4"
    other.write_bytes(b"irreplaceable")
    payload["clips"][0]["source_path"] = str(other)
    plan.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="overwrite"):
        shot_review.write_scan(video, other, plan_path=plan)
    assert other.read_bytes() == b"irreplaceable"


@pytest.mark.parametrize("last_duration", [None, 0, 1])
def test_incomplete_frame_probe_cannot_claim_full_coverage(monkeypatch, last_duration):
    data = {"streams": [{"time_base": "1/24", "start_pts": 0, "duration_ts": 240}],
            "frames": [{"pts": 0, "duration": 1}, {"pts": 1}]}
    if last_duration is not None:
        data["frames"][-1]["duration"] = last_duration
    monkeypatch.setattr(shot_review.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(data), ""))
    with pytest.raises(ValueError, match="terminal|coverage"):
        shot_review.probe_frame_clock("ignored")


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg/ffprobe")
def test_real_ffmpeg_detects_short_runs_inside_one_continuous_file(tmp_path):
    video = tmp_path / "long-span.mkv"
    # No EDL joins: encoded image runs themselves contain the short candidates.
    durations = [48, 2, 50, 12, 68, 14, 46]
    colors = [0, 255, 0, 255, 0, 255, 0]
    raw = b"".join(bytes([c]) * 64 * 64 * 3 * n for n, c in zip(durations, colors))
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", "64x64", "-r", "24", "-i", "-", "-c:v", "ffv1", str(video),
    ], input=raw, check=True, capture_output=True)
    before = shot_review.sha256_file(video)
    report = shot_review.scan_video(video, threshold=0.35)
    assert report["media"]["frame_count"] == 240
    assert [c["frame"] for c in report["candidates"]] == [48, 50, 100, 112, 180, 194]
    assert [s["frame_count"] for s in report["short_spans"]] == [2, 12, 14]
    assert report["scan_complete"] is True
    assert shot_review.sha256_file(video) == before
