import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zlib

import pytest


SCRIPTS = Path(__file__).parents[2] / "skills" / "video-assemble" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import theme_foreground  # noqa: E402
import subtitle_track_binding  # noqa: E402


def _png(path, width, height, pixels):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    rows = b"".join(b"\0" + bytes(pixels[y * width * 4:(y + 1) * width * 4]) for y in range(height))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _node_and_browser():
    """Resolve this machine's Node/Chromium capture runtime or skip."""
    node = shutil.which("node")
    try:
        browser = theme_foreground.resolve_browser_executable() if node else None
    except ValueError:
        browser = None
    if not node or not browser:
        pytest.skip("Installed local Node/Chrome unavailable")
    return node, browser


def _profile(tmp_path, *, bad_window=False):
    underlay = tmp_path / "underlay.png"
    pixels = bytearray([20, 30, 40, 255] * (120 * 100))
    for y in range(30, 80):
        for x in range(20, 100):
            pixels[(y * 120 + x) * 4 + 3] = 0
    if bad_window:
        pixels[(40 * 120 + 40) * 4 + 3] = 1
    _png(underlay, 120, 100, pixels)
    font = next((p for p in [Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
                              Path("/System/Library/Fonts/SFNS.ttf")]
                 if p.is_file()), None)
    if font is None:
        pytest.skip("No local test font")
    base_style = {
        "font_id": "body", "font_weight": 700, "preferred_size": 3,
        "minimum_size": 2, "line_height": {"kind": "ratio", "value": 1.1},
        "letter_spacing": 0, "color": "#ffffff", "skew_x": 0,
        "shadow": None, "stroke": None, "wrap": "nowrap", "max_lines": 1,
        "text_align": "center",
    }
    profile = {
        "artifact": "theme_foreground_profile", "schema_version": 1,
        "canvas": {"width": 120, "height": 100},
        "picture": {"left": 20, "top": 30, "width": 80, "height": 50},
        "underlay": {"path": str(underlay), "sha256": _sha(underlay)},
        "fonts": [{"id": "body", "weight": 700, "path": str(font), "sha256": _sha(font),
                   "platform_families": ["Arial"], "postscript_names": ["ArialMT"]}],
        "roles": {
            "title": {
                "anchor": {"horizontal": "center", "vertical": "bottom", "x": 60, "y": 25, "width": 100},
                "style": {**base_style, "max_lines": 2},
                "fit": {"policy": "cjk_count_v1", "version": 1, "width": 10,
                        "count_subtract": 1, "addend": 0.7},
                "shared_fit": True, "colors": ["#ffffff", "#eeeeee"],
                "gap": {"minimum": 0, "visible": 1, "size_subtract": 0.12},
            },
            "caption": {"anchor": {"horizontal": "center", "vertical": "bottom", "x": 60, "y": 75, "width": 100},
                        "style": base_style, "fit": {"policy": "fixed", "version": 1, "width": 10,
                                                       "count_subtract": 0, "addend": 0.7}},
            "note": {"anchor": {"horizontal": "left", "vertical": "top", "x": 10, "y": 35, "width": 100},
                     "style": {**base_style, "wrap": "pre_wrap_anywhere", "max_lines": 2,
                               "text_align": "left"},
                     "fit": {"policy": "fixed", "version": 1, "width": 10,
                             "count_subtract": 0, "addend": 0.7}},
            "marker": {"anchor": {"horizontal": "right", "vertical": "top", "x": 110, "y": 25, "width": 40},
                       "style": base_style, "fit": {"policy": "fixed", "version": 1, "width": 10,
                                                       "count_subtract": 0, "addend": 0.7}},
        },
    }
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile))
    return profile_path


def _plan(tmp_path, profile_path):
    base = tmp_path / "base.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=120x100:r=24",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-frames:v", "8", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(base)], check=True)
    return {
        "artifact": "theme_foreground_plan", "schema_version": 1,
        "base": {"path": str(base), "sha256": _sha(base)},
        "video": {"width": 120, "height": 100, "fps": "24/1", "total_frames": 8, "body_end_frame": 8},
        "subtitle": {"kind": "none"},
        "profile": {"path": str(profile_path), "sha256": _sha(profile_path)},
        "content": {
            "title": {"primary": "A", "secondary": "B"},
            "notes": [{"id": "n1", "start_frame": 4, "end_frame": 6,
                       "lines": ["Note"], "position": {"left": 1, "top": 2}}],
            "markers": [{"id": "m1", "start_frame": 2, "end_frame": 3,
                         "text": "M", "visible": False}],
        },
    }


def test_cjk_count_v1_exact_formula_and_shared_title_fit():
    fit = {"policy": "cjk_count_v1", "version": 1, "width": 940,
           "count_subtract": 1, "addend": 0.7}
    assert theme_foreground.fit_size("1234567890", fit, 100, 12) == pytest.approx((940 - 10) / 10.7)
    assert theme_foreground.shared_title_size("1234567890", "12345", fit, 74, 12) == pytest.approx(
        min((940 - 10) / 10.7, (940 - 5) / 5.7, 74)
    )
    assert theme_foreground.fit_size("一二三\n四", fit, 1000, 12) == pytest.approx((940 - 3) / 3.7)
    assert theme_foreground.fit_size("x" * 500, fit, 74, 12) == 12


def test_schema_forbids_duplicate_captions_and_arbitrary_code(tmp_path):
    profile = _profile(tmp_path)
    plan = _plan(tmp_path, profile)
    plan["content"]["captions"] = []
    with pytest.raises(ValueError, match="fields"):
        theme_foreground.validate_plan_document(plan)
    del plan["content"]["captions"]
    data = json.loads(profile.read_text())
    data["roles"]["caption"]["style"]["raw_css"] = "body{}"
    profile.write_text(json.dumps(data))
    plan["profile"]["sha256"] = _sha(profile)
    with pytest.raises(ValueError, match="fields"):
        theme_foreground.validate_plan_document(plan)


def test_profile_rejects_nonfinite_numbers_and_invalid_colors(tmp_path):
    profile = _profile(tmp_path)
    plan = _plan(tmp_path, profile)
    data = json.loads(profile.read_text())
    data["roles"]["caption"]["style"]["preferred_size"] = float("inf")
    profile.write_text(json.dumps(data))
    plan["profile"]["sha256"] = _sha(profile)
    with pytest.raises(ValueError, match="finite"):
        theme_foreground.validate_plan_document(plan)
    data["roles"]["caption"]["style"]["preferred_size"] = 3
    data["roles"]["caption"]["style"]["color"] = "rgba(not-a-color)"
    profile.write_text(json.dumps(data))
    plan["profile"]["sha256"] = _sha(profile)
    with pytest.raises(ValueError, match="#RRGGBB"):
        theme_foreground.validate_plan_document(plan)


def test_none_subtitle_still_requires_actual_base_frame_clock(tmp_path):
    profile = _profile(tmp_path)
    plan = _plan(tmp_path, profile)
    plan["video"]["total_frames"] = 9
    plan["video"]["body_end_frame"] = 9
    with pytest.raises(ValueError, match="frame clock"):
        theme_foreground.validate_plan_document(plan)


def test_underlay_requires_zero_alpha_for_every_picture_pixel(tmp_path):
    good = _profile(tmp_path)
    plan = _plan(tmp_path, good)
    normalized = theme_foreground.validate_plan_document(plan)
    assert normalized["profile"]["picture"] == {"left": 20, "top": 30, "width": 80, "height": 50}
    profile_data = json.loads(good.read_text())
    transparent = tmp_path / "transparent.png"
    _png(transparent, 120, 100, bytearray(120 * 100 * 4))
    profile_data["underlay"] = {"path": str(transparent), "sha256": _sha(transparent)}
    good.write_text(json.dumps(profile_data))
    plan["profile"]["sha256"] = _sha(good)
    theme_foreground.validate_plan_document(plan)
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad = _profile(bad_dir, bad_window=True)
    with pytest.raises(ValueError, match="transparent"):
        theme_foreground.validate_plan_document(_plan(bad_dir, bad))


def test_hidden_markers_are_retained_in_state_key(tmp_path):
    profile = _profile(tmp_path)
    normalized = theme_foreground.validate_plan_document(_plan(tmp_path, profile))
    schedule = theme_foreground.build_schedule(normalized)
    assert [(item["start_frame"], item["end_frame"]) for item in schedule] == [
        (0, 2), (2, 3), (3, 4), (4, 6), (6, 8)
    ]
    assert schedule[1]["state"]["markers"][0]["id"] == "m1"
    assert schedule[1]["state"]["markers"][0]["visible"] is False


def test_bound_caption_is_regenerated_from_track_and_sidecar_wording_cannot_override(tmp_path):
    profile = _profile(tmp_path)
    plan = _plan(tmp_path, profile)
    base = plan["base"]["path"]
    track = {
        "schema_version": 1, "overlap_policy": "forbid",
        "clock": {"kind": "output", "timebase": {"numerator": 1, "denominator": 24},
                  "duration_ticks": 8},
        "bindings": subtitle_track_binding.current_bindings(base, 0),
        "cues": [{"start_tick": 2, "end_tick": 4, "text": "轨道真源",
                  "attribution": {"kind": "source", "ref": "synthetic:cue"},
                  "timing_evidence": {"kind": "legacy_estimate", "evidence_refs": [],
                                      "calibration": "none", "word_alignment": "none"}}],
    }
    track_path = tmp_path / "subtitle_track.json"
    track_path.write_text(json.dumps(track))
    subtitle_track_binding.prepare_subtitle_track(base, tmp_path, 8 / 24,
                                                  audio_mode="adopted-packet-copy")
    validation_path = tmp_path / "subtitle_track_validation.json"
    plan["subtitle"] = {"kind": "bound",
                        "track": {"path": str(track_path), "sha256": _sha(track_path)},
                        "validation": {"path": str(validation_path), "sha256": _sha(validation_path)}}
    normalized = theme_foreground.validate_plan_document(plan)
    assert normalized["content"]["captions"][0]["text"] == "轨道真源"
    sidecar = json.loads(validation_path.read_text())
    sidecar["entries"][0]["text"] = "伪造旁路"
    sidecar["binding"]["projection_sha256"] = hashlib.sha256(
        json.dumps(sidecar["entries"], sort_keys=True).encode()
    ).hexdigest()
    validation_path.write_text(json.dumps(sidecar))
    plan["subtitle"]["validation"]["sha256"] = _sha(validation_path)
    with pytest.raises(ValueError, match="canonical track projection"):
        theme_foreground.validate_plan_document(plan)


def test_generic_canvas_browser_render_and_exact_sequence(tmp_path):
    node, chrome = _node_and_browser()
    profile = _profile(tmp_path)
    plan = _plan(tmp_path, profile)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    output = tmp_path / "render"
    receipt = theme_foreground.run_producer(plan_path, output, node_executable=node,
                                             browser_executable=chrome)
    assert receipt["status"] == "FOREGROUND_RENDERED"
    assert [p.name for p in sorted((output / "frames").iterdir())] == [f"frame_{i:06d}.png" for i in range(8)]
    assert receipt["foreground"]["ordered_sha256"] == theme_foreground.ordered_sequence_digest(output / "frames", 8)
    assert receipt["claims"]["normal_speed_review"] == "NOT_CHECKED"
    assert receipt["caption_projection"] == {"kind": "none"}


def test_plan_only_never_issues_success_receipt(tmp_path):
    node, chrome = _node_and_browser()
    profile = _profile(tmp_path)
    plan = _plan(tmp_path, profile)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    output = tmp_path / "planned"
    report = theme_foreground.run_producer(plan_path, output, plan_only=True,
                                            node_executable=node,
                                            browser_executable=chrome)
    assert report["status"] == "PLANNED"
    assert report["producer_receipt"] == "NOT_ISSUED"
    assert not (output / "producer_receipt.json").exists()


def test_browser_rejects_glyph_fallback_outside_explicit_font_face(tmp_path):
    node, chrome = _node_and_browser()
    profile = _profile(tmp_path)
    plan = _plan(tmp_path, profile)
    plan["content"]["title"] = {"primary": "字体回退", "secondary": "不可通过"}
    plan_path = tmp_path / "fallback-plan.json"
    plan_path.write_text(json.dumps(plan))
    output = tmp_path / "fallback"
    with pytest.raises(RuntimeError, match="declared custom face"):
        theme_foreground.run_producer(plan_path, output, node_executable=node,
                                      browser_executable=chrome)
    assert json.loads((output / "theme_foreground_run.json").read_text())["status"] == "FAILED"
    assert not (output / "producer_receipt.json").exists()


def test_browser_counts_wrapped_text_fragments_for_max_lines(tmp_path):
    node, chrome = _node_and_browser()
    profile = _profile(tmp_path)
    plan = _plan(tmp_path, profile)
    plan["content"]["notes"][0]["lines"] = ["W" * 200]
    plan_path = tmp_path / "overflow-plan.json"
    plan_path.write_text(json.dumps(plan))
    output = tmp_path / "overflow"
    with pytest.raises(RuntimeError, match="max_lines"):
        theme_foreground.run_producer(plan_path, output, node_executable=node,
                                      browser_executable=chrome)
    assert not (output / "producer_receipt.json").exists()


def test_browser_resolver_prefers_explicit_then_environment_then_path(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit-chrome"
    explicit.write_text("")
    declared = tmp_path / "declared-chrome"
    declared.write_text("")
    monkeypatch.setenv(theme_foreground.BROWSER_ENV_VAR, str(declared))
    monkeypatch.setattr(theme_foreground.shutil, "which", lambda name: "/from/path/chromium")

    assert theme_foreground.resolve_browser_executable(str(explicit)) == str(explicit)
    assert theme_foreground.resolve_browser_executable() == str(declared)

    monkeypatch.delenv(theme_foreground.BROWSER_ENV_VAR)
    assert theme_foreground.resolve_browser_executable() == "/from/path/chromium"


def test_browser_resolver_reports_every_candidate_when_nothing_is_installed(monkeypatch):
    monkeypatch.delenv(theme_foreground.BROWSER_ENV_VAR, raising=False)
    monkeypatch.setattr(theme_foreground.shutil, "which", lambda name: None)
    monkeypatch.setattr(theme_foreground, "BROWSER_APP_PATHS", ("/nowhere/chrome",))

    with pytest.raises(ValueError, match="google-chrome"):
        theme_foreground.resolve_browser_executable()
