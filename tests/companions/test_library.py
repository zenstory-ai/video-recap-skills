"""Tests for skills/video-recap/scripts/library.py."""
from __future__ import annotations

import json
import os
from pathlib import Path

import library


def test_catalog_add_list_find_extensible_fields(tmp_path: Path):
    lib_dir = tmp_path / "lib"
    bgm = lib_dir / "assets" / "theme.wav"
    bgm.parent.mkdir(parents=True)
    bgm.write_bytes(b"RIFF_BGM")

    entry = library.add_asset(
        lib_dir, asset_id="bgm_warm", kind="bgm", file_path=bgm,
        tags=["warm", "documentary"], license_status="cc0",
        meta={"bpm": 96, "custom_mood": "reflective"},
    )
    assert entry["path"] == "assets/theme.wav"
    assert entry["bpm"] == 96 and entry["custom_mood"] == "reflective"

    found = library.find_items(lib_dir, "reflective", kind="bgm", tag="warm")
    assert [a["id"] for a in found["assets"]] == ["bgm_warm"]


def test_preset_merge_does_not_overwrite_explicit_timeline_keys(tmp_path: Path, sample_timeline):
    lib_dir = tmp_path / "lib"
    library.init_library(lib_dir)
    font = lib_dir / "assets" / "Custom.ttf"
    font.write_bytes(b"TTF")
    preset = {
        "canvas": {"width": 1920, "height": 1080, "backdrop": {"kind": "color", "color": "#111111"}},
        "subtitle_style": {"font_file": "assets/Custom.ttf", "font_size": 48},
        "mix": {"target_lufs": -16.0, "voice_over_bgm_db": 5.0},
    }
    (lib_dir / "presets" / "docu.json").write_text(json.dumps(preset), encoding="utf-8")

    merged = library.apply_preset(lib_dir, "docu", sample_timeline)
    # Explicit sample_timeline canvas width (1080) & target_lufs (-14.0) stay intact; missing defaults filled
    assert merged["canvas"]["width"] == 1080
    assert merged["canvas"]["backdrop"]["kind"] == "color"
    assert merged["mix"]["target_lufs"] == -14.0
    assert merged["mix"]["voice_over_bgm_db"] == 5.0
    assert merged["subtitle_style"]["font_file"] == str(font.resolve())


def test_audit_timeline_reports_license_consent_unregistered_and_missing(tmp_path: Path, sample_timeline):
    lib_dir = tmp_path / "lib"
    library.init_library(lib_dir)
    # Register BGM as restricted, leave SFX unregistered, add missing font
    library.add_asset(lib_dir, asset_id="bgm_reg", kind="bgm",
                      file_path=sample_timeline["sources"]["bgm1"], license_status="restricted")
    sample_timeline["subtitle_style"]["font_file"] = str(tmp_path / "missing_font.ttf")
    sample_timeline["meta"]["voice"] = {"engine": "fish", "voice_id": "v_cloned", "consent": "unknown"}

    report = library.audit_timeline(sample_timeline, library_dir=lib_dir)
    codes = {item["code"] for item in report["attention"]}
    assert "license_restricted" in codes
    assert "unregistered" in codes
    assert "missing_file" in codes
    assert "consent_unknown" in codes


def test_cache_save_restore_is_mtime_independent_and_redacts_secrets(tmp_path: Path):
    lib_dir = tmp_path / "lib"
    src_vid = tmp_path / "episode_01.mp4"
    src_vid.write_bytes(b"VIDEO_BYTES_123456")

    work1 = tmp_path / "run1"
    work1.mkdir()
    (work1 / "probe.json").write_text(
        json.dumps({
            "duration_s": 12.0, "api_key": "sk-secret1234567890abcdef",
            "fish_api_key": "9a8b7c6d5e4f3210deadbeef", "openai_api_key": "sk-proj-9999999999999999",
            "note": "key tp-abc123456789",
        }),
        encoding="utf-8",
    )
    (work1 / "MEDIA_NOTES.md").write_text(
        "MIMO_API_KEY=tp-topsecret999\nFISH_API_KEY=9a8b7c6d5e4f3210deadbeef\n"
        "GROQ_API_KEY=gsk_supersecret999\nGood scene at 4s.\n",
        encoding="utf-8",
    )

    saved = library.cache_save(lib_dir, src_vid, work1)
    assert "probe.json" in saved["files"]
    cached_probe = json.loads((Path(saved["dir"]) / "probe.json").read_text(encoding="utf-8"))
    assert cached_probe["api_key"] == "[redacted]"
    assert cached_probe["fish_api_key"] == "[redacted]"
    assert cached_probe["openai_api_key"] == "[redacted]"
    assert "[redacted-token]" in cached_probe["note"]
    cached_notes = (Path(saved["dir"]) / "MEDIA_NOTES.md").read_text(encoding="utf-8")
    assert "tp-topsecret999" not in cached_notes
    assert "9a8b7c6d5e4f3210deadbeef" not in cached_notes
    assert "gsk_supersecret999" not in cached_notes

    # Mutate mtime_ns on source video (simulating cross-machine copy): restore still hits!
    os.utime(src_vid, (1700000000, 1700000000))
    work2 = tmp_path / "run2"
    work2.mkdir()
    (work2 / "MEDIA_NOTES.md").write_text("# Newer local notes\n", encoding="utf-8")
    restored = library.cache_restore(lib_dir, src_vid, work2)
    assert restored["restored"] is True
    assert (work2 / "probe.json").is_file()
    assert (work2 / "MEDIA_NOTES.md").read_text(encoding="utf-8") == "# Newer local notes\n"
    assert "MEDIA_NOTES.md" not in restored["files"]
