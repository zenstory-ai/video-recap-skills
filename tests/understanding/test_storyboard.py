"""PR-STORY storyboard tests: mock run_cmd, stage EMPTY fixture frames + a fixture
frames-manifest, NO real video. Pins advisory behaviour (Principle 1): a sheet is still
produced when no font is available, and any failure degrades to None without blocking.
"""

import json
import shutil
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

import storyboard  # noqa: E402
import understanding_runner as understand  # noqa: E402
from lib import CONFIG  # noqa: E402


def _stage_frames(work_dir, numbers, fps=2.0):
    """Create empty fixture frame files frame_{n:05d}.jpg + a frames_manifest.json."""
    frames_dir = Path(work_dir) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for n in numbers:
        p = frames_dir / f"frame_{n:05d}.jpg"
        p.write_bytes(b"")
        frames.append(p)
    manifest = {
        "schema_version": 1,
        "fps": float(fps),
        "frame_count": len(frames),
        "frames": [p.name for p in frames],
    }
    (frames_dir / "frames_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return frames


def _mock_run_cmd_makes_output(monkeypatch):
    """Mock run_cmd so any ffmpeg invocation 'succeeds' by writing its last (output) arg.

    Returns the captured commands for shape assertions.
    """
    calls = []

    def fake_run_cmd(cmd, **kwargs):
        calls.append(list(cmd))
        out = Path(str(cmd[-1]))
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"jpeg")
        except OSError:
            pass
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("storyboard.run_cmd", fake_run_cmd)
    monkeypatch.setattr("storyboard._ffmpeg_available", lambda: True)
    monkeypatch.setattr("storyboard.get_video_duration", lambda v: 30.0)
    return calls


def _no_font(monkeypatch):
    monkeypatch.setattr("storyboard._probe_font", lambda: None)


def _with_font(monkeypatch, path="/fake/font.ttf"):
    monkeypatch.setattr("storyboard._probe_font", lambda: path)


ONE_SCENE = [{"start": 0, "end": 2}]


# ── tile selection: cap + scene anchors ──────────────────────────────────────


def test_scene_anchors_midpoint_and_long_scene_thirds(monkeypatch):
    monkeypatch.setitem(CONFIG, "storyboard_long_scene_seconds", 6.0)
    # short scene → 1 anchor (midpoint); long scene (>=6s) → 3 anchors (thirds + mid)
    scenes = [{"start": 0.0, "end": 2.0}, {"start": 10.0, "end": 40.0}]
    anchors = storyboard._scene_anchor_timestamps(scenes, max_tiles=30)
    assert (0, 1.0) in anchors  # short scene midpoint
    long_ts = [ts for sid, ts in anchors if sid == 1]
    assert len(long_ts) == 3  # +1/3, mid, +2/3
    assert long_ts == sorted(long_ts)
    assert len(anchors) == 4


def test_tile_selection_respects_cap(monkeypatch):
    monkeypatch.setitem(CONFIG, "storyboard_long_scene_seconds", 6.0)
    scenes = [
        {"start": float(i) * 10, "end": float(i) * 10 + 9} for i in range(20)
    ]  # 20 long scenes → 60 anchors
    anchors = storyboard._scene_anchor_timestamps(scenes, max_tiles=30)
    assert len(anchors) == 30


# ── nearest-existing-frame + clamp (incl. last-frame/gap) ────────────────────


def test_nearest_existing_frame_and_clamp(tmp_path):
    _stage_frames(tmp_path, [0, 2, 4, 10], fps=2.0)  # numbers sparse: gap 4→10
    paths, numbers = storyboard._frame_index(tmp_path)
    assert numbers == [0, 2, 4, 10]
    nearest = lambda t: storyboard._nearest_existing_frame(t, 2.0, paths, numbers).name  # noqa: E731
    assert nearest(1.0) == "frame_00002.jpg"  # exact target
    assert nearest(3.0) == "frame_00004.jpg"  # gap between 4 and 10; nearest is 4
    assert nearest(-5.0) == "frame_00000.jpg"  # below range clamps to first
    assert nearest(999.0) == "frame_00010.jpg"  # above range clamps to LAST extracted frame


# ── source storyboard end-to-end (mocked ffmpeg) ─────────────────────────────


def test_build_source_storyboard_writes_json_and_tiles(monkeypatch, tmp_path):
    _stage_frames(tmp_path, [0, 2, 4, 6, 8, 10], fps=2.0)
    calls = _mock_run_cmd_makes_output(monkeypatch)
    _with_font(monkeypatch)
    scenes = [{"start": 0.0, "end": 2.0}, {"start": 2.0, "end": 5.0}]
    result = storyboard.build_source_storyboard(tmp_path, "video.mp4", scenes, fps=2.0)
    assert result is not None
    assert result["timeline"] == "source"
    assert result["labels_burned"] is True
    assert result["tiles"], "expected tiles"
    # JSON sidecar lists ALL page paths
    sb_json = json.loads(
        (tmp_path / "storyboard" / "source_storyboard.json").read_text()
    )
    assert sb_json["page_images"]
    assert all(Path(p).exists() for p in sb_json["page_images"])
    # tile command shape: a tile=<cols>x<rows> filter must appear
    tile_cmds = [c for c in calls if any("tile=" in str(t) for t in c)]
    assert tile_cmds, "expected a tile= ffmpeg command"
    assert any("tile=" in str(t) and "x" in str(t) for c in tile_cmds for t in c)


def test_source_storyboard_pages_when_over_one_page(monkeypatch, tmp_path):
    _stage_frames(tmp_path, list(range(0, 80, 2)), fps=2.0)
    monkeypatch.setitem(CONFIG, "storyboard_columns", 3)
    monkeypatch.setitem(
        CONFIG, "storyboard_rows_per_page", 2
    )  # 6 tiles/page → force paging
    monkeypatch.setitem(CONFIG, "storyboard_long_scene_seconds", 6.0)
    _mock_run_cmd_makes_output(monkeypatch)
    _no_font(monkeypatch)
    scenes = [{"start": float(i) * 5, "end": float(i) * 5 + 4} for i in range(20)]
    result = storyboard.build_source_storyboard(tmp_path, "video.mp4", scenes, fps=2.0)
    assert result is not None
    assert len(result["page_images"]) >= 2  # paged
    # paged names use _001/_002 suffixes
    names = [Path(p).name for p in result["page_images"]]
    assert any(n.endswith("_001.jpg") for n in names)
    assert any(n.endswith("_002.jpg") for n in names)


# ── edited storyboard: dual time + forward map correctness ───────────────────


def _validated_plan():
    # forward map: output = output_start + (src - source_start)
    return {
        "clips": [
            {
                "clip_id": 0,
                "source_start": 10.0,
                "source_end": 14.0,
                "output_start": 0.0,
                "output_end": 4.0,
                "duration": 4.0,
            },
            {
                "clip_id": 1,
                "source_start": 30.0,
                "source_end": 30.6,
                "output_start": 4.0,
                "output_end": 4.6,
                "duration": 0.6,
            },
        ],
        "total_duration": 4.6,
    }


def _build_edited(monkeypatch, tmp_path):
    _stage_frames(tmp_path, list(range(0, 80, 2)), fps=2.0)
    _mock_run_cmd_makes_output(monkeypatch)
    _with_font(monkeypatch)
    plan = _validated_plan()
    result = storyboard.build_edited_storyboard(tmp_path, "video.mp4", plan, fps=2.0)
    assert result is not None
    return plan, result


def test_edited_tiles_carry_output_and_source_time_via_forward_map(monkeypatch, tmp_path):
    plan, result = _build_edited(monkeypatch, tmp_path)
    assert result["timeline"] == "output"
    clips = {c["clip_id"]: c for c in plan["clips"]}
    for t in result["tiles"]:
        # every tile has both labels, and output time follows the cut.py forward map
        assert "output_timestamp" in t and "source_timestamp" in t
        assert "out " in t["label"] and "src " in t["label"]
        clip = clips[t["source_clip_id"]]
        expected = clip["output_start"] + (t["source_timestamp"] - clip["source_start"])
        expected = max(clip["output_start"], min(expected, clip["output_end"]))
        assert t["output_timestamp"] == pytest.approx(round(expected, 3), abs=0.01)


def test_edited_short_clip_frame_identity_dedupe(monkeypatch, tmp_path):
    # clip1 is 0.6s @ fps2 → source 30/30.3/30.1 all round to the SAME frame number (60)
    _plan, result = _build_edited(monkeypatch, tmp_path)
    clip1_tiles = [t for t in result["tiles"] if t["source_clip_id"] == 1]
    # ≤1s clip → 1-2 tiles, NOT 3 identical
    assert 1 <= len(clip1_tiles) <= 2
    frame_files = [t["frame_file"] for t in clip1_tiles]
    assert len(frame_files) == len(set(frame_files))  # no duplicate frames


# ── cache reuse vs rebuild on fps change (the staleness regression) ───────────


def _cached_source_storyboard(monkeypatch, tmp_path):
    """Stage a work_dir for understand._generate_source_storyboard with a build counter."""
    _stage_frames(tmp_path, [0, 2, 4, 6], fps=2.0)
    monkeypatch.setitem(CONFIG, "storyboard", True)
    monkeypatch.setitem(CONFIG, "fps", 2.0)
    _mock_run_cmd_makes_output(monkeypatch)
    _with_font(monkeypatch)
    scenes = [{"start": 0.0, "end": 4.0}]
    scenes_json = tmp_path / "scenes.json"
    scenes_json.write_text(json.dumps(scenes), encoding="utf-8")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake-video-bytes")  # real file so the cache meta can fingerprint it

    builds = {"n": 0}
    real_build = storyboard.build_source_storyboard

    def counting_build(*a, **k):
        builds["n"] += 1
        return real_build(*a, **k)

    monkeypatch.setattr(
        "understanding_storyboard.build_source_storyboard", counting_build
    )

    def generate():
        return understand._generate_source_storyboard(tmp_path, video, scenes, scenes_json)

    return generate, builds


def test_cache_reuses_identical_inputs_and_rebuilds_on_fps_or_frame_change(
    monkeypatch, tmp_path
):
    """fps sits in the cache key on its own (belt) and the frames manifest is fingerprinted
    (suspenders): each change alone must rebuild, identical inputs must not."""
    generate, builds = _cached_source_storyboard(monkeypatch, tmp_path)

    generate()
    assert builds["n"] == 1
    generate()
    assert builds["n"] == 1, "expected cache reuse on identical inputs"

    # flip ONLY CONFIG fps; frames manifest held constant
    monkeypatch.setitem(CONFIG, "fps", 3.0)
    generate()
    assert builds["n"] == 2, "fps in the cache key must invalidate even when frames are unchanged"

    # re-stage frames at the (now current) fps; only the manifest fingerprint changes
    _stage_frames(tmp_path, [0, 3, 6, 9, 12, 15], fps=3.0)
    generate()
    assert builds["n"] == 3, "frames-manifest change must invalidate the storyboard cache"


def test_cache_hit_corrupt_sidecar_rebuilds_without_traceback(monkeypatch, tmp_path):
    """A fingerprint-matching but CORRUPT cached sidecar must not raise out of the cache-hit
    read (advisory invariant); it degrades to a rebuild."""
    generate, _builds = _cached_source_storyboard(monkeypatch, tmp_path)

    assert generate() is not None
    json_path = tmp_path / "storyboard" / "source_storyboard.json"
    # cache META stays valid; corrupt ONLY the artifact bytes so the cache-hit read hits bad JSON
    json_path.write_text("{ this is not valid json ", encoding="utf-8")

    assert generate() is not None  # no traceback; rebuilt
    assert json.loads(json_path.read_text(encoding="utf-8"))["timeline"] == "source"


# ── graceful None on no-frames / run_cmd failure / font-probe raise ───────────


def _no_frames(monkeypatch, tmp_path):
    pass


def _run_cmd_fails(monkeypatch, tmp_path):
    _stage_frames(tmp_path, [0, 2, 4], fps=2.0)
    _with_font(monkeypatch)
    monkeypatch.setattr("storyboard._ffmpeg_available", lambda: True)
    monkeypatch.setattr(
        "storyboard.run_cmd",
        lambda cmd, **kwargs: CompletedProcess(cmd, 1, stdout="", stderr="ffmpeg boom"),
    )


def _font_probe_raises(monkeypatch, tmp_path):
    _stage_frames(tmp_path, [0, 2, 4], fps=2.0)
    _mock_run_cmd_makes_output(monkeypatch)

    def boom():
        raise RuntimeError("font subsystem exploded")

    monkeypatch.setattr("storyboard._probe_font", boom)


@pytest.mark.parametrize(
    "degrade",
    [_no_frames, _run_cmd_fails, _font_probe_raises],
    ids=lambda fn: fn.__name__.strip("_"),
)
def test_source_storyboard_degrades_to_none_without_traceback(monkeypatch, tmp_path, degrade):
    degrade(monkeypatch, tmp_path)
    assert (
        storyboard.build_source_storyboard(tmp_path, "video.mp4", ONE_SCENE, fps=2.0)
        is None
    )


def test_brief_still_builds_when_storyboard_fails(monkeypatch, tmp_path):
    # storyboard returns None → header is skipped, brief content untouched
    brief = tmp_path / "agent_narration_brief.md"
    brief.write_text("# Brief body\n", encoding="utf-8")
    understand._prepend_storyboard_brief_header(brief, None, None, cut_mode=False)
    assert brief.read_text(encoding="utf-8") == "# Brief body\n"  # unchanged


# ── font-absent path: sheet IS produced, labels_burned:false, sidecar carries time ──


def test_font_absent_sheet_still_produced_unlabelled(monkeypatch, tmp_path):
    _stage_frames(tmp_path, [0, 2, 4, 6], fps=2.0)
    calls = _mock_run_cmd_makes_output(monkeypatch)
    _no_font(monkeypatch)  # simulate font-probe failure
    scenes = [{"start": 0.0, "end": 3.0}]
    result = storyboard.build_source_storyboard(tmp_path, "video.mp4", scenes, fps=2.0)
    assert result is not None  # sheet still produced
    assert result["labels_burned"] is False
    # NO drawtext command was issued (we never labelled)
    assert not any("drawtext=" in str(t) for c in calls for t in c)
    # JSON sidecar STILL carries the timestamps
    assert all("timestamp" in t and "label" in t for t in result["tiles"])
    sb_json = json.loads(
        (tmp_path / "storyboard" / "source_storyboard.json").read_text()
    )
    assert sb_json["labels_burned"] is False
    assert sb_json["tiles"][0]["timestamp"] is not None


# ── edited storyboard gating + brief header ──────────────────────────────────


def test_edited_storyboard_skipped_without_validated_plan(tmp_path):
    _stage_frames(tmp_path, [0, 2, 4], fps=2.0)
    # no clip_plan_validated.json → pass1 → None (gated on file presence, not edit_mode)
    assert understand._generate_edited_storyboard(tmp_path, "video.mp4") is None


def test_brief_header_branches_on_labels_burned(tmp_path):
    brief = tmp_path / "agent_narration_brief.md"
    brief.write_text("# body\n", encoding="utf-8")
    source = {
        "page_images": ["storyboard/source_storyboard.jpg"],
        "labels_burned": False,
    }
    understand._prepend_storyboard_brief_header(brief, source, None, cut_mode=False)
    text = brief.read_text(encoding="utf-8")
    assert "Storyboard" in text
    assert "先看 storyboard 再写" in text
    assert "inspect clip-map" in text  # labels not burned → point to clip-map
    assert text.rstrip().endswith("# body")  # original body preserved at the end


def test_brief_header_cut_mode_lists_both_timelines(tmp_path):
    brief = tmp_path / "agent_narration_brief.md"
    brief.write_text("# body\n", encoding="utf-8")
    source = {
        "page_images": ["storyboard/source_storyboard.jpg"],
        "labels_burned": True,
    }
    edited = {
        "page_images": ["storyboard/edited_storyboard.jpg"],
        "labels_burned": True,
    }
    understand._prepend_storyboard_brief_header(brief, source, edited, cut_mode=True)
    text = brief.read_text(encoding="utf-8")
    assert "源时间线" in text and "output" in text
    assert "inspect clip-map" not in text  # labels burned → no fallback note


# ── optional real-ffmpeg tile smoke test (skipped when ffmpeg absent) ─────────


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_ffmpeg_tile_smoke(tmp_path):
    # Make 4 real tiny jpgs via ffmpeg, then tile them through the real path.
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir(parents=True)
    nums = [0, 2, 4, 6]
    for n in nums:
        out = frames_dir / f"frame_{n:05d}.jpg"
        rc = shutil.os.system(
            f"ffmpeg -y -f lavfi -i color=c=blue:s=64x64:d=1 -frames:v 1 '{out}' >/dev/null 2>&1"
        )
        if rc != 0 or not out.exists():
            pytest.skip("ffmpeg could not synthesize test frames")
    (frames_dir / "frames_manifest.json").write_text("{}", encoding="utf-8")
    scenes = [{"start": 0.0, "end": 3.0}]
    result = storyboard.build_source_storyboard(tmp_path, "video.mp4", scenes, fps=2.0)
    assert result is not None
    assert all(Path(p).exists() for p in result["page_images"])
