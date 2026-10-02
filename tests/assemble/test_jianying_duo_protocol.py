"""Golden contracts for the JianYing protocol exposed by duo-video.

The JSON fixtures are JSON-equivalent copies from duo-video commit ``ef4eb46``. The
tests replace only authored/runtime values (IDs, paths, dimensions and timing),
then compare the remaining protocol object exactly through the public
``build_draft`` boundary so timeline producers do not need to know about
exporter internals.
"""

import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "video-assemble" / "scripts"
FIXTURES = Path(__file__).parent / "fixtures" / "jianying"
sys.path.insert(0, str(SCRIPTS))

from export_jianying import build_draft  # noqa: E402
from jianying.builders import base_segment  # noqa: E402


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


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
    expected = _fixture("duo_empty_project_info.json")
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
    expected = _fixture("duo_empty_video.json")
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
    expected = _fixture("duo_empty_audio.json")
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
    expected = _fixture("duo_empty_text.json")
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
    expected = _fixture("duo_empty_segment.json")
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
    expected = _fixture("duo_empty_text_styles.json")
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
