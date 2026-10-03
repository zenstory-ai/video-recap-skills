"""Golden contracts for the JianYing protocol exposed by duo-video.

The expected objects are the shipped templates in ``references/jianying/``, pinned to
duo-video commit ``ef4eb46`` by a content hash, so an edit to a template fails here even
when the builders change in step with it. The tests replace only authored/runtime values
(IDs, paths, dimensions and timing), then compare the remaining protocol object exactly
through the public ``build_draft`` boundary so timeline producers do not need to know
about exporter internals.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "video-assemble" / "scripts"
TEMPLATES = SCRIPTS.parent / "references" / "jianying"
sys.path.insert(0, str(SCRIPTS))

from export_jianying import build_draft  # noqa: E402
from jianying.builders import base_segment  # noqa: E402


# sha256 of each template's canonical JSON (sorted keys, compact separators), so the pin
# survives checkout line-ending conversion but not a change to any key or value.
PINNED_TEMPLATE_SHA256 = {
    "empty_jy_project_info.json": "aa3602e4dd239e6e32d211a2d0b67928e50c7a9a0510557ef6b961854267d6b0",
    "empty_jy_material_video.json": "1267ec3b28e63638220f1425cb59f6466579af2b6ec9d40a3ca83abbcb998129",
    "empty_yj_material_audio.json": "a5ec17f3753bdae92ca5a91b78eaf635bb263ec70d9ea22c65ee75172c218555",
    "empty_yj_material_text.json": "da077745b47ff52a37d2a7620ba45933597d5bb61f38c5bf0ecacbb1a453742f",
    "empty_jy_segment.json": "df97b5990c959d82c31295c3eeef9bc4428a889f37d911420a4478624aba387e",
    "empty_jy_text_styles.json": "929fc3300a1f52c8e64ee49e839a7facd4ca53c92dbace378e33a53140e7e432",
}


def _template(name):
    return json.loads((TEMPLATES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(PINNED_TEMPLATE_SHA256))
def test_shipped_template_still_matches_pinned_duo_commit(name):
    canonical = json.dumps(
        _template(name), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == (
        PINNED_TEMPLATE_SHA256[name]
    ), f"{name} no longer matches duo-video ef4eb46; re-pin it from upstream deliberately"


def _counter_ids():
    count = [0]

    def new_id():
        count[0] += 1
        return f"ID{count[0]:05d}"

    return new_id


def _probe(_path):
    return 6_000_000, 1280, 720


def _timeline(*tracks, duration=6.0):
    return {
        "schema_version": 2,
        "canvas": {"width": 1920, "height": 1080, "fps": 30},
        "duration": duration,
        "tracks": list(tracks),
    }


def _build(*tracks, duration=6.0, probe=_probe):
    return build_draft(
        _timeline(*tracks, duration=duration),
        new_id=_counter_ids(),
        probe=probe,
    )[0]


def _video_track(clip):
    return {"kind": "video", "name": "video", "clips": [clip]}


def _video_clip(path="/video.mp4", **overrides):
    clip = {
        "source_path": str(path),
        "source_start": 0.0,
        "source_end": 2.0,
        "timeline_start": 0.0,
        "timeline_end": 2.0,
    }
    clip.update(overrides)
    return clip


def _only_segment(content, track_type=None):
    tracks = content["tracks"]
    if track_type is not None:
        tracks = [track for track in tracks if track["type"] == track_type]
    segments = [segment for track in tracks for segment in track["segments"]]
    assert len(segments) == 1
    return segments[0]


def _only_material(content, materials_key):
    materials = content["materials"][materials_key]
    assert len(materials) == 1, f"expected one {materials_key} material, got {materials!r}"
    return materials[0]


def test_empty_project_root_config_and_materials_match_duo_template():
    content = _build(duration=3.5)
    expected = _template("empty_jy_project_info.json")
    expected["canvas_config"] = {"width": 1920, "height": 1080, "ratio": "original"}
    expected["duration"] = 3_500_000
    expected["fps"] = 30.0
    expected["id"] = content["id"]
    for platform_key in ("last_modified_platform", "platform"):
        for identity_key in ("device_id", "hard_disk_id", "mac_address"):
            expected[platform_key][identity_key] = ""

    assert content == expected


def test_default_video_material_matches_duo_template(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    content = _build(_video_track(_video_clip(source)))
    actual = _only_material(content, "videos")
    expected = _template("empty_jy_material_video.json")
    expected.update(
        id=actual["id"],
        path=str(source),
        material_name="source.mp4",
        width=1280,
        height=720,
        duration=6_000_000,
    )

    assert actual == expected


def test_default_audio_material_matches_duo_template(tmp_path):
    source = tmp_path / "voice.wav"
    source.write_bytes(b"audio")
    track = {
        "kind": "audio",
        "name": "audio",
        "role": "audio",
        "segments": [
            {
                "source_path": str(source),
                "timeline_start": 0.0,
                "timeline_end": 2.0,
            }
        ],
    }
    content = _build(track)
    actual = _only_material(content, "audios")
    expected = _template("empty_yj_material_audio.json")
    expected.update(id=actual["id"], path=str(source), duration=6_000_000)

    assert actual == expected


def test_default_text_material_matches_duo_template():
    track = {
        "kind": "text",
        "name": "text",
        "segments": [{"text": "hello", "timeline_start": 0.0, "timeline_end": 2.0}],
    }
    content = _build(track)
    actual = _only_material(content, "texts")
    expected = _template("empty_yj_material_text.json")
    expected.update(
        id=actual["id"],
        content=actual["content"],
        type="text",
        alignment=1,
        font_size=8.0,
        text_color="#FFFFFF",
        line_spacing=0.02,
        letter_spacing=0.0,
        check_flag=15,
    )

    assert actual == expected


def test_base_segment_matches_duo_template():
    actual = base_segment("MATERIAL", 1_000_000, 2_000_000, 1.0, [], _counter_ids())
    expected = _template("empty_jy_segment.json")
    expected.update(
        id=actual["id"],
        material_id="MATERIAL",
        render_index=2,
        track_render_index=2,
        target_timerange={"start": 1_000_000, "duration": 2_000_000},
    )

    assert actual == expected


def test_scale_and_position_map_to_duo_clip_shape():
    clip = _video_clip(scale={"x": 1.25, "y": 0.75}, position={"x": 0.2, "y": -0.3})
    content = _build(_video_track(clip))

    assert _only_segment(content, "video")["clip"] == {
        "alpha": 1.0,
        "flip": {"horizontal": False, "vertical": False},
        "rotation": 0.0,
        "scale": {"x": 1.25, "y": 0.75},
        "transform": {"x": 0.2, "y": -0.3},
    }


def test_rich_text_template_matches_pinned_duo_shape():
    content = _build({
        "kind": "text",
        "name": "text",
        "segments": [{"text": "test", "timeline_start": 0.0, "timeline_end": 1.0}],
    })
    actual = json.loads(_only_material(content, "texts")["content"])
    expected = _template("empty_jy_text_styles.json")
    expected["text"] = "test"
    expected["styles"][0]["range"] = [0, 4]
    expected["styles"][0]["size"] = 8.0
    expected["styles"][0].update({
        "bold": False,
        "italic": False,
        "underline": False,
        "strokes": [],
        "use_letter_color": True,
    })

    assert actual == expected
