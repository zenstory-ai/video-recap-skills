"""Tests for skills/video-recap/scripts/export_jianying.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import export_jianying as jy


def _fake_probe(path: str) -> tuple[int, int, int]:
    p = str(path).lower()
    if p.endswith(".png"):
        return 0, 972, 230
    if "bgm" in p:
        return jy.us(4.0), 0, 0  # shorter than 10s timeline to exercise BGM loop splitting
    if "whoosh" in p:
        return jy.us(1.0), 0, 0
    if p.endswith(".wav"):
        return jy.us(2.0), 0, 0
    return jy.us(20.0), 1920, 1080


def _seq_id():
    n = 0
    def _next():
        nonlocal n
        n += 1
        return f"ID-{n:04d}"
    return _next


def test_build_draft_tracks_keyframes_utf16_and_sfx(sample_timeline):
    # Add an overlapping second SFX to verify TrackAllocator auto-suffixing (sfx, sfx-1)
    sample_timeline["audio"]["sfx"].append({"id": "x2", "source": "sfx1", "at": 6.3, "gain": 0.6})
    content, meta, notes = jy.build_draft(sample_timeline, new_id=_seq_id(), probe=_fake_probe)

    assert notes == []
    assert content["canvas_config"] == {"width": 1080, "height": 1920, "ratio": "original"}
    assert content["duration"] == jy.us(10.0)
    assert meta["tm_duration"] == jy.us(10.0)
    assert len(content["materials"]) == 46  # all JianYing parallel arrays preserved
    assert content["platform"]["device_id"] == ""
    assert content["last_modified_platform"]["mac_address"] == ""

    track_names = [t["name"] for t in content["tracks"]]
    assert "video" in track_names
    assert "narration" in track_names
    assert "bgm" in track_names
    assert "sfx" in track_names and "sfx-1" in track_names
    assert "overlay" in track_names
    assert "subtitle" in track_names

    # Video clip 1 has ducking keyframes; muted video clip 2 has volume 0 and no keyframes
    v_track = next(t for t in content["tracks"] if t["name"] == "video")
    assert len(v_track["segments"]) == 2
    assert v_track["segments"][0]["common_keyframes"]
    assert v_track["segments"][1]["volume"] == 0.0
    assert v_track["segments"][1]["common_keyframes"] == []

    # Looped 4s BGM over 10s splits into 3 pieces (4s + 4s + 2s)
    bgm_track = next(t for t in content["tracks"] if t["name"] == "bgm")
    assert len(bgm_track["segments"]) == 3
    assert sum(s["target_timerange"]["duration"] for s in bgm_track["segments"]) == jy.us(10.0)

    # UTF-16-LE range counts surrogate pair emoji 🎬 as 2 code units
    text_mat = content["materials"]["texts"][0]
    payload = json.loads(text_mat["content"])
    expected_utf16 = len("Opening hook 🎬".encode("utf-16-le")) // 2
    assert payload["styles"][0]["range"] == [0, expected_utf16]

    # Overlay rect {x:0.05, y:0.05, w:0.90, h:0.12} converts to Y-up center coords
    ovl_seg = next(t for t in content["tracks"] if t["name"] == "overlay")["segments"][0]
    assert ovl_seg["clip"]["transform"]["x"] == pytest.approx(0.0, abs=1e-4)
    assert ovl_seg["clip"]["transform"]["y"] == pytest.approx(0.78, abs=1e-4)


def test_export_bundles_media_dedupes_and_avoids_overwrite(sample_timeline, tmp_path: Path):
    out_dir = tmp_path / "drafts"
    draft1, notes1 = jy.export_timeline_to_jianying(
        sample_timeline, out_dir, "my_cut", new_id=_seq_id(), probe=_fake_probe, bundle_media=True
    )
    assert notes1 == []
    d1 = Path(draft1)
    assert (d1 / "draft_content.json").is_file()
    assert (d1 / "draft_info.json").is_file()
    assert (d1 / "draft_meta_info.json").is_file()
    assert (d1 / "Resources" / "local" / "video" / "ep01.mp4").is_file()
    assert (d1 / "Resources" / "local" / "audio" / "whoosh.wav").is_file()
    assert (d1 / "Resources" / "local" / "image" / "plate.png").is_file()

    saved = json.loads((d1 / "draft_content.json").read_text(encoding="utf-8"))
    # Two video clips referencing ep01.mp4 share one bundled placeholder path
    vid_paths = [m["path"] for m in saved["materials"]["videos"] if m.get("type") != "photo"]
    assert len(set(vid_paths)) == 1
    assert vid_paths[0].startswith("##_draftpath_placeholder_")

    # Second export with same name creates my_cut_2 instead of overwriting
    draft2, notes2 = jy.export_timeline_to_jianying(
        sample_timeline, out_dir, "my_cut", new_id=_seq_id(), probe=_fake_probe, bundle_media=False
    )
    assert Path(draft2).name == "my_cut_2"
    assert any("my_cut_2" in n for n in notes2)


def test_rejects_unsafe_draft_name(sample_timeline, tmp_path: Path):
    for bad in ("", "../escape", "a/b", "/abs"):
        with pytest.raises(ValueError):
            jy.export_timeline_to_jianying(sample_timeline, tmp_path, bad, probe=_fake_probe)


def test_export_video_overlay_mov_as_video_material(sample_timeline, tmp_path: Path):
    mov = tmp_path / "remotion_overlay.mov"
    mov.write_bytes(b"\x00\x00\x00\x14ftypqt  ")
    sample_timeline["sources"]["remotion_packaging"] = str(mov)
    sample_timeline["overlays"].append({
        "id": "remotion_packaging", "source": "remotion_packaging",
        "start": 0.0, "end": 10.0, "rect": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
    })
    draft_dir, notes = jy.export_timeline_to_jianying(
        sample_timeline, tmp_path / "out", "rem_cut", new_id=_seq_id(),
        probe=lambda p: (jy.us(10.0), 1080, 1920) if str(p).endswith(".mov") else _fake_probe(p),
        bundle_media=True,
    )
    assert notes == []
    d = Path(draft_dir)
    assert (d / "Resources" / "local" / "video" / "remotion_overlay.mov").is_file()
    content = json.loads((d / "draft_content.json").read_text(encoding="utf-8"))
    mov_mats = [m for m in content["materials"]["videos"] if m["material_name"] == "remotion_overlay.mov"]
    assert len(mov_mats) == 1 and mov_mats[0]["type"] == "video"
    ovl_tracks = [t for t in content["tracks"] if t["name"].startswith("overlay")]
    assert len(ovl_tracks) >= 1


def test_build_draft_skips_zero_duration_out_of_range_bgm(sample_timeline):
    sample_timeline["audio"]["bgm"] = [{"id": "b1", "source": "bgm1", "in": 9.0, "at": 0.0, "out": 10.0, "loop": False, "gain": 0.2}]
    content, _, _ = jy.build_draft(sample_timeline, new_id=_seq_id(), probe=_fake_probe)
    assert not any(t["name"] == "bgm" for t in content["tracks"])
