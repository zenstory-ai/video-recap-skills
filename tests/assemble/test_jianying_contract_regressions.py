import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "video-assemble" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import export_jianying  # noqa: E402
from export_jianying import build_draft, export_timeline_to_jianying  # noqa: E402


def _counter_ids():
    count = [0]

    def new_id():
        count[0] += 1
        return f"ID{count[0]:05d}"

    return new_id


def _fake_probe(_path):
    return 5_000_000, 100, 100


def _video_timeline(source_path="/source.mp4", schema_version=2):
    return {
        "schema_version": schema_version,
        "canvas": {"width": 100, "height": 100, "fps": 30},
        "duration": 2.0,
        "tracks": [
            {
                "kind": "video",
                "name": "video",
                "clips": [
                    {
                        "source_path": str(source_path),
                        "source_start": 0.0,
                        "source_end": 2.0,
                        "timeline_start": 0.0,
                        "timeline_end": 2.0,
                    }
                ],
            }
        ],
    }


def test_same_named_overlapping_video_and_image_keep_distinct_tracks():
    timeline = _video_timeline()
    timeline["tracks"][0]["name"] = "shared"
    timeline["tracks"].append(
        {
            "kind": "image",
            "name": "shared",
            "segments": [
                {
                    "source_path": "/overlay.png",
                    "timeline_start": 0.5,
                    "timeline_end": 1.5,
                }
            ],
        }
    )

    content, _meta, _notes = build_draft(
        timeline, new_id=_counter_ids(), probe=_fake_probe
    )

    tracks = content["tracks"]
    assert [(track["type"], track["name"]) for track in tracks] == [
        ("video", "shared"),
        ("video", "shared"),
    ]
    assert [len(track["segments"]) for track in tracks] == [1, 1]
    assert [track["flag"] for track in tracks] == [0, 2]
    assert "_semantic_kind" not in tracks[0]
    assert "_semantic_kind" not in tracks[1]


def test_public_export_bundles_media_by_default(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")

    draft_dir, _notes = export_timeline_to_jianying(
        _video_timeline(source),
        tmp_path / "out",
        new_id=_counter_ids(),
        probe=_fake_probe,
    )

    root = Path(draft_dir)
    assert (root / "Resources/local/video/source.mp4").read_bytes() == b"video"
    content = json.loads((root / "draft_content.json").read_text(encoding="utf-8"))
    assert content["materials"]["videos"][0]["path"].startswith(
        "##_draftpath_placeholder_"
    )


def test_public_export_can_explicitly_disable_media_bundling(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")

    draft_dir, _notes = export_timeline_to_jianying(
        _video_timeline(source),
        tmp_path / "out",
        new_id=_counter_ids(),
        probe=_fake_probe,
        bundle_media=False,
    )

    root = Path(draft_dir)
    assert not (root / "Resources").exists()
    content = json.loads((root / "draft_content.json").read_text(encoding="utf-8"))
    assert content["materials"]["videos"][0]["path"] == str(source)


@pytest.mark.parametrize(
    ("extra_args", "expected"),
    [([], True), (["--no-bundle-media"], False), (["--bundle-media"], True)],
)
def test_direct_cli_defaults_to_bundle_with_explicit_opt_out(
    monkeypatch, tmp_path, extra_args, expected
):
    timeline_path = tmp_path / "timeline.json"
    timeline_path.write_text(json.dumps(_video_timeline()), encoding="utf-8")
    captured = {}

    def fake_export(_timeline, _out_dir, _name, *, bundle_media):
        captured["bundle_media"] = bundle_media
        return str(tmp_path / "draft"), []

    monkeypatch.setattr(export_jianying, "export_timeline_to_jianying", fake_export)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_jianying.py",
            str(timeline_path),
            "--out-dir",
            str(tmp_path / "out"),
            *extra_args,
        ],
    )

    export_jianying.main()

    assert captured == {"bundle_media": expected}


@pytest.mark.parametrize("schema_version", [1, 3, 999])
def test_timeline_schema_other_than_v2_is_rejected(schema_version):
    with pytest.raises(ValueError, match="unsupported timeline schema_version"):
        build_draft(
            _video_timeline(schema_version=schema_version),
            new_id=_counter_ids(),
            probe=_fake_probe,
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda timeline: timeline.pop("schema_version"), "schema_version"),
        (lambda timeline: timeline.update(schema_version=True), "schema_version"),
        (lambda timeline: timeline.pop("canvas"), "canvas"),
        (lambda timeline: timeline["canvas"].update(width=True), "canvas.width"),
        (lambda timeline: timeline.update(duration="two"), "duration"),
        (lambda timeline: timeline.update(tracks={}), "tracks"),
        (lambda timeline: timeline["tracks"].append("video"), "tracks\\[1\\]"),
        (lambda timeline: timeline["tracks"][0].pop("kind"), "tracks\\[0\\].kind"),
        (
            lambda timeline: timeline["tracks"][0].update(kind="transition"),
            "unsupported track kind",
        ),
        (lambda timeline: timeline["tracks"][0].update(clips={}), "tracks\\[0\\].clips"),
        (
            lambda timeline: timeline["tracks"][0]["clips"][0].pop("source_path"),
            "source_path",
        ),
        (
            lambda timeline: timeline["tracks"][0]["clips"][0].update(
                timeline_end=0.0
            ),
            "timeline_end",
        ),
        (
            lambda timeline: timeline["tracks"][0]["clips"][0].update(scale="ignored"),
            "scale",
        ),
    ],
)
def test_v2_timeline_contract_rejects_malformed_required_structure(mutate, message):
    timeline = _video_timeline()
    mutate(timeline)

    with pytest.raises((TypeError, ValueError), match=message):
        build_draft(timeline, new_id=_counter_ids(), probe=_fake_probe)


def test_missing_relative_media_is_reported_during_portable_export(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    timeline = _video_timeline("definitely-missing.mp4")

    _draft_dir, notes = export_timeline_to_jianying(
        timeline, tmp_path / "out", new_id=_counter_ids(), probe=_fake_probe
    )

    assert any("definitely-missing.mp4" in note for note in notes)


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda timeline: timeline["tracks"][0]["clips"][0].update(speed=2.0), "speed"),
        (lambda timeline: timeline["tracks"][0]["clips"][0].update(transition="fade"), "transition"),
        (lambda timeline: timeline.update(style_presets={}), "style_presets"),
        (
            lambda timeline: timeline["tracks"].append({"kind": "sticker", "segments": []}),
            "unsupported track kind",
        ),
    ],
)
def test_removed_authoring_extensions_are_rejected_not_ignored(mutate, field):
    timeline = _video_timeline()
    mutate(timeline)

    with pytest.raises(ValueError, match=field):
        build_draft(timeline, new_id=_counter_ids(), probe=_fake_probe)


def test_runtime_project_scrubs_upstream_hardware_ids():
    content, _meta, _notes = build_draft(
        _video_timeline(), new_id=_counter_ids(), probe=_fake_probe
    )

    for platform_key in ("last_modified_platform", "platform"):
        platform = content[platform_key]
        assert platform["device_id"] == ""
        assert platform["hard_disk_id"] == ""
        assert platform["mac_address"] == ""
