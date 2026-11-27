"""Integration tests for probe.py, frames.py, and transcribe.py."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import frames
import probe
import transcribe


def test_probe_multi_source_scenes_and_silence(synthetic_media: dict[str, Path], tmp_path: Path) -> None:
    out_json = tmp_path / "probe.json"
    rc = probe.main([str(synthetic_media["v1"]), str(synthetic_media["v2"]), "--scenes", "--silence", "--out", str(out_json)])
    assert rc == 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert len(data["sources"]) == 2
    s1, s2 = data["sources"]
    assert s1["width"] == 320 and s1["height"] == 240 and s1["has_audio"] is True
    assert s2["width"] == 240 and s2["height"] == 320 and s2["has_audio"] is False
    assert isinstance(s1["cuts"], list) and isinstance(s1["silences"], list)


def test_frames_contact_sheet_and_cropped_extract(synthetic_media: dict[str, Path], tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.jpg"
    meta_p = tmp_path / "sheet.json"
    rc = frames.main([
        str(synthetic_media["v1"]), "--grid", "2x2", "--window", "0.1:1.9",
        "--width", "160", "--out", str(sheet), "--out-json", str(meta_p),
    ])
    assert rc == 0
    assert sheet.is_file() and sheet.stat().st_size > 1000
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    assert len(meta["tiles"]) == 4

    crop_img = tmp_path / "face_crop.jpg"
    rc2 = frames.main([
        str(synthetic_media["v1"]), "--at", "0.5", "--crop", "40,20,120,120",
        "--no-labels", "--out", str(crop_img),
    ])
    assert rc2 == 0
    assert crop_img.is_file() and crop_img.stat().st_size > 200


def test_transcribe_srt_and_graceful_unavailable(synthetic_media: dict[str, Path], tmp_path: Path) -> None:
    srt_in = tmp_path / "sample.srt"
    srt_in.write_text("1\n00:00:00,100 --> 00:00:01,200\nFirst line\n\n2\n00:00:01,300 --> 00:00:01,900\nSecond line\n", encoding="utf-8")
    out_json = tmp_path / "transcript.json"
    out_srt = tmp_path / "exported.srt"
    rc = transcribe.main([
        str(synthetic_media["v1"]), "--srt", str(srt_in),
        "--out", str(out_json), "--out-srt", str(out_srt),
    ])
    assert rc == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["status"] == "ok" and payload["provider"] == "srt"
    assert len(payload["segments"]) == 2
    assert out_srt.is_file()

    silent_res = transcribe.transcribe_media(synthetic_media["v2"], provider="auto")
    assert silent_res["status"] == "unavailable"
    assert silent_res["segments"] == []


def test_frames_shot_pieces_inset_split_and_window() -> None:
    pieces = frames.shot_pieces([2.0, 2.4, 12.0], duration=20.0, window=(1.0, 13.0), split_s=3.0)
    assert [(p["in"], p["out"]) for p in pieces] == [(0.08, 1.92), (2.48, 5.627), (5.627, 8.773), (8.773, 11.92), (12.08, 16.0), (16.0, 19.92)]
    assert pieces[1]["shot"] == [2.48, 11.92] and [p["n"] for p in pieces] == list(range(6))  # 0.4s shot at 2.0 dropped
    with pytest.raises(ValueError, match="positive"):
        frames.shot_pieces([2.0], duration=10.0, window=(0.0, 10.0), split_s=0)
    with pytest.raises(ValueError, match="positive"):
        frames.shot_pieces([2.0], duration=10.0, window=(0.0, 10.0), split_s=-1.0)


def test_probe_detect_scenes_pairs_per_frame_with_negative_and_na_pts(monkeypatch) -> None:
    lines = [
        "frame:0    pts:-100   pts_time:-0.04",
        "lavfi.scene_score=0.01",
        "frame:1    pts:N/A    pts_time:N/A",
        "lavfi.scene_score=0.95",
        "frame:2    pts:100    pts_time:0.5",  # no scene_score line on this frame
    ]
    for idx in range(3, 12):
        t = 1.0 + (idx - 3) * 0.25
        sc = 0.50 if idx == 7 else 0.01  # idx=7 -> t=2.0
        lines.extend([f"frame:{idx}    pts:{idx * 100}   pts_time:{t}", f"lavfi.scene_score={sc}"])
    stdout = "\n".join(lines) + "\n"
    monkeypatch.setattr(probe, "has_tool", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        probe, "run_cmd",
        lambda cmd, check=False: subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=""),
    )
    assert probe.detect_scenes("dummy.mp4") == [2.0]


def test_frames_probe_cuts_relative_path_and_basename_matching(tmp_path: Path, monkeypatch) -> None:
    proj = tmp_path / "proj"
    (proj / "raw").mkdir(parents=True)
    (proj / "media").mkdir(parents=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    vid = proj / "raw" / "ep1.mp4"
    vid.write_bytes(b"")
    probe_json = proj / "media" / "probe.json"
    probe_json.write_text(json.dumps({"sources": [{"path": "raw/ep1.mp4", "cuts": [1.5, 4.0]}]}), encoding="utf-8")
    monkeypatch.chdir(other)

    assert frames._probe_cuts(str(probe_json), str(vid)) == [1.5, 4.0]

    # Unique basename fallback works when directory moved
    probe_json.write_text(json.dumps({"sources": [{"path": "old_dir/ep1.mp4", "cuts": [2.5]}]}), encoding="utf-8")
    assert frames._probe_cuts(str(probe_json), str(vid)) == [2.5]

    # Ambiguous basename raises ValueError
    probe_json.write_text(json.dumps({"sources": [
        {"path": "a/ep1.mp4", "cuts": [1.0]}, {"path": "b/ep1.mp4", "cuts": [2.0]},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="no cuts"):
        frames._probe_cuts(str(probe_json), str(vid))

    # Different filename in a 1-source probe.json does not falsely match
    probe_json.write_text(json.dumps({"sources": [{"path": "raw/other.mp4", "cuts": [1.0]}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="no cuts"):
        frames._probe_cuts(str(probe_json), str(vid))


def test_frames_shots_cli_width_labels_and_split_validation(synthetic_media: dict[str, Path], tmp_path: Path, monkeypatch) -> None:
    probe_json = tmp_path / "probe.json"
    probe_json.write_text(json.dumps({"sources": [{"path": str(synthetic_media["v1"]), "cuts": [1.0]}]}), encoding="utf-8")

    # --split <= 0 raises ValueError instead of ZeroDivisionError
    with pytest.raises(ValueError, match="positive"):
        frames.main([
            str(synthetic_media["v1"]), "--shots", str(probe_json),
            "--window", "0:2", "--split", "0", "--out", str(tmp_path / "s.jpg"),
        ])

    # Explicit --width is preserved (not clamped to 240), omitted --width defaults to 240, and --no-labels propagates
    captured: list[dict[str, object]] = []

    def fake_shot_sheet(video_path, cuts, out_image, *, window, width=240, crop=None, split_s=3.0, labels=True, **_kw):
        captured.append({"width": width, "labels": labels, "split_s": split_s})
        return {"video": str(video_path), "sheet": str(out_image), "labels_burned": False, "pieces": []}

    monkeypatch.setattr(frames, "build_shot_sheet", fake_shot_sheet)
    assert frames.main([
        str(synthetic_media["v1"]), "--shots", str(probe_json),
        "--window", "0:2", "--out", str(tmp_path / "s1.jpg"), "--out-json", str(tmp_path / "s1.json"),
    ]) == 0
    assert frames.main([
        str(synthetic_media["v1"]), "--shots", str(probe_json),
        "--window", "0:2", "--width", "360", "--no-labels",
        "--out", str(tmp_path / "s2.jpg"), "--out-json", str(tmp_path / "s2.json"),
    ]) == 0
    assert captured == [
        {"width": 240, "labels": True, "split_s": 3.0},
        {"width": 360, "labels": False, "split_s": 3.0},
    ]

    # Real build_shot_sheet with labels=False produces image and labels_burned=False
    monkeypatch.undo()
    out_img = tmp_path / "real_shots.jpg"
    meta = frames.build_shot_sheet(synthetic_media["v1"], [1.0], out_img, window=(0.0, 2.0), width=300, labels=False)
    assert out_img.is_file() and out_img.stat().st_size > 200
    assert meta["labels_burned"] is False


def test_frames_shot_sheet_tags_far_pieces_marks_and_notes(synthetic_media: dict[str, Path], tmp_path: Path) -> None:
    notes = tmp_path / "notes.json"
    notes.write_text(json.dumps({"segments": [{"start": 0.2, "end": 0.5, "text": "她拿起手机"}, {"t": 1.5, "text": "门外有人"}, {"t": 1.6, "text": " "}]}), encoding="utf-8")
    meta = frames.build_shot_sheet(synthetic_media["v1"], [1.0], tmp_path / "s.jpg", window=(0.0, 0.9), far=(0.0, 2.0),
                                   marks=[0.3], notes=frames.load_notes(notes), labels=False)
    near, far = meta["pieces"]
    assert not near["far"] and near["marks"] == [0] and near["note"] == "她拿起手机"
    assert far["far"] and far["marks"] == [] and far["note"] == "门外有人"
