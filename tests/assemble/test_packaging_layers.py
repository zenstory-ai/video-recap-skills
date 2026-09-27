import json
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-assemble" / "scripts"))

import packaging  # noqa: E402
from assemble import assemble_video  # noqa: E402
from lib import CONFIG  # noqa: E402
from tts_fixtures import tts_segment  # noqa: E402

CANVAS = {"width": 320, "height": 240}
BAR = {"x": 0, "y": 200, "width": 320, "height": 40}


def _png(path, width, height, rgba):
    row = b"\x00" + bytes(rgba) * width
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))
    return path


def _plan(work, layers, canvas=CANVAS):
    (work / packaging.PACKAGING_LAYERS).write_text(
        json.dumps({"canvas": canvas, "layers": layers, "template": {"id": "bar", "version": 1}}),
        encoding="utf-8")


def test_without_a_plan_the_filter_chain_is_unchanged(tmp_path):
    assert packaging.load_packaging_layers(tmp_path, CANVAS) == []
    assert packaging.compose_video_filter(["drawbox=1", "scale=2"], [], mask_first=True) == "drawbox=1,scale=2"
    assert packaging.packaging_settings(tmp_path) == {
        "artifact": "packaging_layers.json", "present": False, "layers": []}


def test_layers_sit_between_the_source_mask_and_the_text_layers(tmp_path):
    layer = {"name": "bar", "path": str(tmp_path / "bar.png"), "rect": BAR}

    graph = packaging.compose_video_filter(["MASK", "TEXT", "EVEN"], [layer], mask_first=True)

    assert graph.startswith("movie=filename=")
    assert graph.endswith("[in]MASK[pm];[pm][pk0]overlay=x=0:y=200[po0];[po0]TEXT,EVEN[out]")


@pytest.mark.parametrize(
    "canvas, rect, match",
    [
        pytest.param({"width": 640, "height": 480}, BAR, "640x480", id="other_canvas"),
        pytest.param(CANVAS, {**BAR, "y": 220}, "超出画布", id="rect_outside_canvas"),
    ],
)
def test_plans_that_do_not_fit_the_render_are_rejected(tmp_path, canvas, rect, match):
    _plan(tmp_path, [{"name": "bar", "path": str(_png(tmp_path / "bar.png", 4, 4, (255, 0, 0, 255))),
                      "rect": rect}], canvas=canvas)

    with pytest.raises(RuntimeError, match=match):
        packaging.load_packaging_layers(tmp_path, CANVAS)


@pytest.mark.parametrize(
    "rect, image_size, scale, position",
    [
        pytest.param({"x": 0, "y": 0, "width": 320, "height": 240}, (640, 480), (1.0, 1.0), (0.0, 0.0), id="full_canvas"),
        pytest.param(BAR, (32, 4), (1.0, 1.0), (0.0, -0.833333), id="bottom_bar_same_aspect"),
        pytest.param(BAR, (4, 4), (1.333333, 0.166667), (0.0, -0.833333), id="square_image_stretched_to_bar"),
        pytest.param({"x": 240, "y": 0, "width": 80, "height": 60}, (8, 6), (0.25, 0.25), (0.75, 0.75), id="top_right_logo"),
    ],
)
def test_timeline_segments_place_layers_where_the_render_does(rect, image_size, scale, position):
    layer = {"name": "l", "path": "/l.png", "rect": rect,
             "image_size": {"width": image_size[0], "height": image_size[1]}}

    [segment] = packaging.timeline_image_segments([layer], CANVAS, 3.0)

    assert (segment["timeline_start"], segment["timeline_end"]) == (0.0, 3.0)
    assert (segment["scale"]["x"], segment["scale"]["y"]) == scale
    assert (segment["position"]["x"], segment["position"]["y"]) == position


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not available")
def test_rendered_recap_carries_the_packaging_layer(tmp_path, monkeypatch):
    monkeypatch.setitem(CONFIG, "burn_subtitles", False)
    work = tmp_path / "work"
    work.mkdir()
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2:r=25",
                    "-f", "lavfi", "-i", "sine=frequency=220:duration=2", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)],
                   check=True, capture_output=True)
    wav = work / "narr_000.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-ar", "44100", "-ac", "1", str(wav)], check=True, capture_output=True)
    (tmp_path / "layer dir").mkdir()
    bar = _png(tmp_path / "layer dir" / "bar.png", 32, 4, (255, 0, 0, 255))
    _plan(work, [{"name": "bar", "path": str(bar), "rect": BAR}])
    out = work / "output.mp4"

    assemble_video(src, [tts_segment(index=0, start=0.2, end=1.5, narration="测试。", audio_path=str(wav),
                                     audio_duration=1.0, pause_after_ms=250, overlaps_speech=False)],
                   work, out)

    frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(out), "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
    def pixel(x, y):
        i = (y * 320 + x) * 3
        return tuple(frame[i:i + 3])
    red, blue = pixel(160, 220), pixel(160, 100)
    assert red[0] > 200 and red[2] < 60, red
    assert blue[2] > 200 and blue[0] < 60, blue
    image_track = next(t for t in json.loads((work / "timeline.json").read_text(encoding="utf-8"))["tracks"]
                       if t["kind"] == "image")
    assert image_track["segments"][0]["source_path"] == str(bar.resolve())
