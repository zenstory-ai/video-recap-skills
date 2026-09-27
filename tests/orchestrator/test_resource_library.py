import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import library

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "resource-library"
SCRIPT = ROOT / "skills" / "video-recap" / "scripts" / "library.py"
SUBTITLE = "templates/subtitle_style/clean-white/v1/template.json"
PACKAGING = "templates/packaging/bottom-bar/v1/template.json"
BGM = "resources/bgm/pulse-demo/resource.json"
VOICE = "resources/voice/narrator-demo/resource.json"
SAMPLE = "samples/demo-sample/sample.json"


def _copy_example(tmp_path):
    root = tmp_path / "lib"
    shutil.copytree(EXAMPLE, root)
    return root


def _edit(root, rel, change):
    path = root / rel
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _codes(issues):
    return {(item["path"], item["code"]) for item in issues}


def test_shipped_example_library_is_valid_with_only_the_intended_licence_warning():
    index, report = library.scan_library(EXAMPLE)

    assert report.errors == []
    assert _codes(report.warnings) == {(VOICE, "license_unknown")}
    assert {k: len(v) for k, v in index.items()} == {"resources": 4, "templates": 2, "samples": 1}


@pytest.mark.parametrize(
    "rel, change, expected",
    [
        pytest.param(BGM, lambda d: d.update(id="other"), (BGM, "id"), id="id_differs_from_dir"),
        pytest.param(BGM, lambda d: d.update(kind="sfx"), (BGM, "kind"), id="kind_differs_from_dir"),
        pytest.param(BGM, lambda d: d.update(colour="red"), (BGM, "unknown_keys"), id="unknown_top_level_key"),
        pytest.param(BGM, lambda d: d["license"].update(status="free"), (BGM, "license"), id="bad_license_status"),
        pytest.param(BGM, lambda d: d["files"][0].update(path="missing.wav"), (BGM, "file_missing"), id="missing_file"),
        pytest.param(BGM, lambda d: d["files"][0].update(path="../../../../outside.wav"), (BGM, "file_path"), id="path_escapes_library"),
        pytest.param(BGM, lambda d: d["files"][0].update(path="/etc/hosts"), (BGM, "file_path"), id="absolute_path"),
        pytest.param(VOICE, lambda d: d["voice"].update(provider="acme"), (VOICE, "voice"), id="unknown_voice_provider"),
        pytest.param(SUBTITLE, lambda d: d.pop("adoption"), (SUBTITLE, "adoption"), id="adopted_without_record"),
        pytest.param(SUBTITLE, lambda d: d.update(version=2), (SUBTITLE, "version"), id="version_differs_from_dir"),
        pytest.param(SUBTITLE, lambda d: d["params"]["size_px"].update(provenance="guessed"), (SUBTITLE, "provenance"), id="bad_provenance"),
        pytest.param(SUBTITLE, lambda d: d["params"]["band"]["value"].update(y_bot=1700), (SUBTITLE, "band"), id="band_outside_canvas"),
        pytest.param(SUBTITLE, lambda d: d["params"].update(font={"resource": "pulse-demo"}), (SUBTITLE, "resource_kind"), id="font_ref_to_bgm"),
        pytest.param(SUBTITLE, lambda d: d.update(samples=["nope"]), (SUBTITLE, "sample_missing"), id="missing_sample_ref"),
        pytest.param(SUBTITLE, lambda d: d["params"].update(fontsize={"value": 1, "provenance": "specified"}), (SUBTITLE, "unknown_params"), id="misspelled_subtitle_param"),
        pytest.param(SUBTITLE, lambda d: d["params"].pop("max_chars"), (SUBTITLE, "max_chars"), id="uncalibrated_line_length"),
        pytest.param(SUBTITLE, lambda d: d["params"]["max_chars"].update(value=20), (SUBTITLE, "line_too_wide"), id="line_wider_than_canvas"),
        pytest.param(PACKAGING, lambda d: d["params"]["layers"][0]["rect"].update(width=901), (PACKAGING, "rect_outside_canvas"), id="layer_outside_canvas"),
        pytest.param(PACKAGING, lambda d: d["params"]["layers"][0]["image"].update(resource="gone"), (PACKAGING, "resource_missing"), id="missing_layer_image"),
        pytest.param(SAMPLE, lambda d: d.update(templates=["clean-white@v9"]), (SAMPLE, "template_missing"), id="missing_template_ref"),
        pytest.param(SUBTITLE, lambda d: d["params"]["size_px"].update(value=52.5), (SUBTITLE, "integer"), id="fractional_font_size"),
        pytest.param(SUBTITLE, lambda d: d["params"].update(font={"resource": ["x"]}), (SUBTITLE, "resource_ref"), id="font_ref_not_an_id"),
        pytest.param(SUBTITLE, lambda d: d["params"]["band"]["value"].update(y_top="1280"), (SUBTITLE, "band"), id="band_as_string"),
        pytest.param(SUBTITLE, lambda d: d.update(samples=[{"id": "demo-sample"}]), (SUBTITLE, "samples"), id="samples_not_ids"),
        pytest.param(SUBTITLE, lambda d: d["adoption"].update(resources=["frame-demo"]), (SUBTITLE, "adoption_resources"), id="adoption_snapshot_list"),
        pytest.param(SAMPLE, lambda d: d.update(templates=[{"id": "clean-white"}]), (SAMPLE, "templates"), id="sample_templates_not_refs"),
        pytest.param(PACKAGING, lambda d: d["params"]["layers"][0].pop("image"), (PACKAGING, "layer"), id="layer_without_image"),
        pytest.param(PACKAGING, lambda d: d["params"]["layers"][0]["rect"].update(x=True), (PACKAGING, "rect"), id="rect_with_boolean"),
        pytest.param(VOICE, lambda d: d.update(voice={"provider": "fish-audio"}), (VOICE, "voice"), id="fish_voice_without_id"),
        pytest.param(BGM, lambda d: d.update(files="pulse-demo.wav"), (BGM, "files"), id="files_not_a_list"),
    ],
)
def test_invalid_records_are_reported_as_errors(tmp_path, rel, change, expected):
    root = _copy_example(tmp_path)
    _edit(root, rel, change)

    _, report = library.scan_library(root)

    assert expected in _codes(report.errors)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_symlinked_resource_file_outside_the_library_is_rejected(tmp_path):
    root = _copy_example(tmp_path)
    outside = tmp_path / "outside.wav"
    shutil.copy(root / "resources/bgm/pulse-demo/pulse-demo.wav", outside)
    link = root / "resources/bgm/pulse-demo/linked.wav"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not permitted")
    _edit(root, BGM, lambda d: d["files"][0].update(path="linked.wav"))

    _, report = library.scan_library(root)

    assert (BGM, "file_path") in _codes(report.errors)


def test_resource_rewritten_after_adoption_is_flagged(tmp_path):
    root = _copy_example(tmp_path)
    image = root / "resources/image/frame-demo/frame-demo.png"
    identity = library.file_identity(image)
    _edit(root, PACKAGING, lambda d: d.update(
        status="adopted",
        adoption={"date": "2026-09-27", "by": "user", "statement": "就用这个", "scope": "演示",
                  "resources": {"frame-demo": [{"path": "resources/image/frame-demo/frame-demo.png", **identity}]}},
    ))
    assert (PACKAGING, "changed_since_adoption") not in _codes(library.scan_library(root)[1].warnings)

    image.write_bytes(image.read_bytes() + b"\0")

    assert (PACKAGING, "changed_since_adoption") in _codes(library.scan_library(root)[1].warnings)


def test_cli_check_exit_code_and_json_report(tmp_path):
    def run(root):
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPT), "--library-dir", str(root), "check", "--json"],
            capture_output=True, text=True, encoding="utf-8",
        )

    ok = run(EXAMPLE)
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["ok"] is True

    root = _copy_example(tmp_path)
    _edit(root, BGM, lambda d: d["license"].update(status="free"))
    bad = run(root)
    assert bad.returncode == 1
    assert (BGM, "license") in _codes(json.loads(bad.stdout)["errors"])

def test_scan_never_writes_to_the_library(tmp_path):
    root = _copy_example(tmp_path)
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*")}

    library.scan_library(root)

    assert {p: p.stat().st_mtime_ns for p in root.rglob("*")} == before


def test_reference_audio_voice_requires_a_consent_record(tmp_path):
    root = _copy_example(tmp_path)
    shutil.copy(root / "resources/bgm/pulse-demo/pulse-demo.wav", root / "resources/voice/narrator-demo/ref.wav")
    _edit(root, VOICE, lambda d: d.update(files=[{"role": "reference", "path": "ref.wav"}]))
    assert (VOICE, "consent") in _codes(library.scan_library(root)[1].errors)

    _edit(root, VOICE, lambda d: d.update(consent={"status": "unknown"}))
    _, report = library.scan_library(root)
    assert (VOICE, "consent") not in _codes(report.errors)
    assert (VOICE, "consent_unknown") in _codes(report.warnings)


def test_font_resource_bound_to_subtitles_must_name_its_family(tmp_path):
    root = _copy_example(tmp_path)
    font_dir = root / "resources/font/brand-sans"
    font_dir.mkdir(parents=True)
    (font_dir / "brand.ttf").write_bytes(b"font")
    (font_dir / "resource.json").write_text(json.dumps({
        "schema": "video-recap.resource.v1", "id": "brand-sans", "kind": "font", "title": "品牌黑体",
        "files": [{"role": "regular", "path": "brand.ttf"}], "license": {"status": "licensed"}}),
        encoding="utf-8")
    _edit(root, SUBTITLE, lambda d: d["params"].update(font={"resource": "brand-sans"}))
    assert (SUBTITLE, "font_family") in _codes(library.scan_library(root)[1].errors)

    _edit(root, "resources/font/brand-sans/resource.json", lambda d: d.update(font={"family": "Brand Sans"}))
    assert library.scan_library(root)[1].errors == []
