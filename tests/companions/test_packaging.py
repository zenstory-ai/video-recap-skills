"""Tests for skills/video-recap/scripts/packaging.py."""
from __future__ import annotations

import json
from pathlib import Path

import packaging


def test_list_and_load_builtin_themes_and_custom_theme(tmp_path: Path):
    themes = packaging.list_themes()
    names = {t["name"] for t in themes}
    assert {"portrait-stage", "portrait-cinema", "landscape-editorial", "drama-recap"} <= names

    loaded = packaging.load_theme("portrait-stage")
    assert loaded["canvas"]["width"] == 1080
    assert loaded["canvas"]["height"] == 1920
    drama = packaging.load_theme("drama-recap")
    assert drama["canvas"]["width"] == 1080
    assert drama["canvas"]["height"] == 1440
    assert drama["canvas"]["backdrop"] == {"kind": "none"}
    assert drama["canvas"]["viewport"] == {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "fit": "cover"}
    assert drama["subtitle_style"]["safe_y"] == [0.72, 0.82]
    assert "highlight_color" not in drama["subtitle_style"]
    assert drama["remotion"]["header_style"] == "drama_minimal"
    assert drama["remotion"]["show_progress_bar"] is False
    assert drama["remotion"]["scrim"] is True
    assert drama["remotion"]["accent"] == "#F3CE7A"
    assert drama["remotion"]["text"] == "#FFFFFF"

    custom_path = tmp_path / "custom.json"
    custom_path.write_text(json.dumps({"name": "custom-square", "canvas": {"width": 1080, "height": 1080}}), encoding="utf-8")
    assert packaging.load_theme(custom_path)["name"] == "custom-square"


def test_drama_recap_remotion_init_passes_header_style_and_huazi(tmp_path: Path, sample_timeline):
    rem_dir = tmp_path / "drama_rem"
    tl = dict(sample_timeline)
    tl.pop("subtitle_style", None)
    info = packaging.remotion_init(
        tl, rem_dir, theme=packaging.load_theme("drama-recap"),
        title="失笑", subhead="嘴上冷脸 身体先护人",
        cards=[
            {"kind": "huazi", "title": "不是借住 是救场", "start": 1.0, "end": 4.2},
            {"kind": "reaction", "title": "刚甜三秒 立刻破功", "start": 5.0, "end": 7.5},
        ],
    )
    props = json.loads(Path(info["props"]).read_text(encoding="utf-8"))
    assert props["width"] == 1080 and props["height"] == 1440
    assert props["viewport"] == {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "fit": "cover"}
    assert props["safeY"] == [0.72, 0.82]
    assert props["headerStyle"] == "drama_minimal"
    assert props["showProgress"] is False
    assert props["theme"]["remotion"]["scrim"] is True
    assert [c["kind"] for c in props["cards"]] == ["huazi", "reaction"]


def test_apply_theme_generates_png_plates_and_detects_collisions(tmp_path: Path, sample_timeline):
    sample_timeline["overlays"] = []
    theme = packaging.load_theme("portrait-stage")
    updated, report = packaging.apply_theme(
        sample_timeline, theme, plates_dir=tmp_path / "plates",
        kicker="EP 01", title="Big Turn", subhead="Night Shift",
        cards=[{"start": 1.0, "end": 4.0, "name": "Lin", "role": "Lead", "side": "left"}],
        callouts=[{"start": 1.5, "end": 3.5, "text": "Plot Twist", "slot": "callout_upper"}],
    )
    assert updated["canvas"]["backdrop"]["kind"] == "blur"
    assert len(updated["overlays"]) == 3
    for ovl in updated["overlays"]:
        png_path = Path(updated["sources"][ovl["source"]])
        assert png_path.is_file()
        assert png_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert not any(w["code"] == "overlay_collision" for w in report["warnings"])

    # Force a spatial+temporal collision and subtitle safe_y overlap
    _, col_report = packaging.apply_theme(
        sample_timeline, theme, plates_dir=tmp_path / "plates2",
        callouts=[
            {"start": 1.0, "end": 3.0, "text": "A", "rect": {"x": 0.1, "y": 0.72, "w": 0.6, "h": 0.1}},
            {"start": 2.0, "end": 4.0, "text": "B", "rect": {"x": 0.2, "y": 0.72, "w": 0.6, "h": 0.1}},
        ],
    )
    codes = {w["code"] for w in col_report["warnings"]}
    assert "overlay_collision" in codes
    assert "overlay_subtitle_overlap" in codes


def test_place_sfx_avoids_narration_and_keep_windows(tmp_path: Path, sample_timeline):
    # sample_timeline has narration [0.5, 2.5] and clip1 keep_window [4.0, 5.5] -> output [2.0, 3.5]
    sample_timeline["audio"]["sfx"] = []
    sfx_file = tmp_path / "hit.wav"
    sfx_file.write_bytes(b"RIFF_SFX")
    events = [
        {"id": "x_near_edge", "source": str(sfx_file), "at": 0.55, "gain": 0.7},   # shifts cleanly to 0.38
        {"id": "x_mid_speech", "source": str(sfx_file), "at": 1.5, "gain": 0.7},   # deep inside narration -> skipped
        {"id": "x_keep_win", "source": str(sfx_file), "at": 2.8, "gain": 0.7},     # deep inside keep_window [2.0, 3.5] -> skipped
        {"id": "x_override", "source": str(sfx_file), "at": 1.5, "gain": 0.5, "allow_over_dialogue": True},
        {"id": "x_clear", "source": str(sfx_file), "at": 6.5, "gain": 0.8},
    ]
    updated, report = packaging.place_sfx(sample_timeline, events)
    by_id = {p["id"]: p for p in report["placed"]}
    assert by_id["x_near_edge"]["status"] == "shifted" and by_id["x_near_edge"]["at"] < 0.5
    assert by_id["x_override"]["status"] == "placed" and by_id["x_override"]["at"] == 1.5
    assert by_id["x_clear"]["status"] == "placed"
    assert len(report["skipped"]) == 2
    assert len(updated["audio"]["sfx"]) == 3


def test_packaging_cli_roundtrip(tmp_path: Path, sample_timeline):
    tl_path = tmp_path / "timeline.json"
    tl_path.write_text(json.dumps(sample_timeline), encoding="utf-8")
    assert packaging.main(["list-themes"]) == 0
    assert packaging.main(["apply", str(tl_path), "--theme", "landscape-editorial", "--title", "Hello"]) == 0
    # Re-applying replaces plate_header idempotently instead of duplicating
    assert packaging.main(["apply", str(tl_path), "--theme", "landscape-editorial", "--title", "Updated"]) == 0
    reloaded = json.loads(tl_path.read_text(encoding="utf-8"))
    assert reloaded["canvas"]["width"] == 1920
    assert [o["id"] for o in reloaded["overlays"]].count("plate_header") == 1


def test_drawtext_fallback_renders_png_without_pillow_or_magick(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(packaging, "_render_with_pillow", lambda *a, **k: False)
    monkeypatch.setattr(packaging, "_render_with_magick", lambda *a, **k: False)
    out = tmp_path / "dt_plate.png"
    info = packaging.render_plate_png(out, 480, 120, ["KICKER", "Title Line"], {"bg": "#111622E6", "border": "#D9B460EB", "title": "#FFFFFF"})
    assert info["renderer"] in {"ffmpeg_drawtext", "stdlib_fallback"}
    assert out.is_file() and out.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_remotion_init_and_mocked_render(tmp_path: Path, sample_timeline, monkeypatch):
    rem_dir = tmp_path / "remotion_proj"
    theme = packaging.load_theme("portrait-cinema")
    init_info = packaging.remotion_init(
        sample_timeline, rem_dir, theme=theme,
        kicker="ACT I", title="Night Alley", subhead="Director Cut",
        cards=[{"title": "Scene 1", "subtitle": "Alley", "side": "left", "start": 1.0, "end": 4.0}],
        callouts=[{"text": "Twist", "slot": "callout_upper", "start": 5.0, "end": 7.0}],
    )
    assert (rem_dir / "package.json").is_file()
    assert (rem_dir / "src" / "PackagingOverlay.tsx").is_file()
    props = json.loads(Path(init_info["props"]).read_text(encoding="utf-8"))
    assert props["width"] == 1080 and props["height"] == 1920
    assert props["durationInFrames"] == 300
    assert props["safeY"] == [0.74, 0.88]
    assert len(props["clips"]) == 2 and props["cards"][0]["title"] == "Scene 1"

    # Mock node/npx + pre-create node_modules so remotion_render runs hermetically
    (rem_dir / "node_modules").mkdir()
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        out_vid = Path(cmd[4])
        out_vid.parent.mkdir(parents=True, exist_ok=True)
        out_vid.write_bytes(b"FAKE_PRORES_MOV")
        return type("Res", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr(packaging.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"node", "npx", "npm"} else None)
    out_mov = tmp_path / "plates" / "remotion_overlay.mov"
    updated, report = packaging.remotion_render(rem_dir, sample_timeline, out_video=out_mov, codec="prores", runner=fake_run)
    assert report["codec"] == "prores"
    assert "--prores-profile=4444" in calls[0][0]
    assert "--pixel-format=yuva444p10le" in calls[0][0]
    assert updated["sources"]["remotion_packaging"] == str(out_mov.resolve())
    assert [o["id"] for o in updated["overlays"]].count("remotion_packaging") == 1
    assert updated["canvas"]["viewport"]["fit"] == "cover"
    # Second render replaces remotion_packaging idempotently
    updated2, _ = packaging.remotion_render(rem_dir, updated, out_video=out_mov, codec="vp9", runner=fake_run)
    assert "--codec=vp9" in calls[1][0]
    assert [o["id"] for o in updated2["overlays"]].count("remotion_packaging") == 1
