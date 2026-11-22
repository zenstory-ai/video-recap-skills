"""Integration tests for probe.py, frames.py, and transcribe.py."""

from __future__ import annotations

import json
from pathlib import Path

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
