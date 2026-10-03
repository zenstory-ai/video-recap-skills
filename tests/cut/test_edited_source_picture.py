"""edited_source.mp4 picture and container: one colour space across mixed sources, and no
metadata inherited from the sources."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-cut" / "scripts"))
import cut_contract
import cut_render
import media_geometry
from cut_contract import normalize_clip_plan
from cut_render import build_edited_source_video

_HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
_BT709 = {"colorspace": "bt709", "color_primaries": "bt709", "color_trc": "bt709",
          "color_range": "tv"}
_BT601 = {"color_space": "smpte170m", "color_primaries": "smpte170m",
          "color_transfer": "smpte170m", "color_range": "tv"}


def _with_geometry(plan, source_paths):
    _, _, _, geometry_qc = media_geometry._select_output_geometry(source_paths, plan["clips"])
    plan.setdefault("qc", {})["output_geometry"] = geometry_qc
    return plan


# ── per-clip conversion (the shared label: test_pure_cut.py) ───────────────────────


@pytest.mark.parametrize("source, target, expected", [
    pytest.param({"color_space": "gbr", "pix_fmt": "rgb24"}, _BT709,
                 "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,", id="rgb-gbr"),
    pytest.param({"pix_fmt": "argb"}, _BT709,
                 "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,",
                 id="rgb-without-colour-space"),
    pytest.param({"pix_fmt": "bgr0", "color_space": "unknown"}, _BT709,
                 "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,",
                 id="rgb-unknown-colour-space"),
    pytest.param(_BT601, _BT709,
                 "scale=in_color_matrix=smpte170m:in_range=tv:out_color_matrix=bt709"
                 ":out_range=tv,format=yuv420p,", id="bt601-into-bt709"),
    pytest.param({"pix_fmt": "yuvj420p", "color_range": "pc"}, _BT709,
                 "scale=in_color_matrix=bt709:in_range=pc:out_color_matrix=bt709"
                 ":out_range=tv,format=yuv420p,", id="untagged-full-into-limited"),
    pytest.param({"pix_fmt": "yuv420p"}, _BT709,
                 "scale=in_color_matrix=bt709:in_range=tv:out_color_matrix=bt709"
                 ":out_range=tv,format=yuv420p,", id="untagged-pinned-not-negotiated"),
    pytest.param({"color_space": "bt470bg", "color_range": "tv"},
                 {"colorspace": "bt470bg", "color_range": "tv"},
                 "scale=in_color_matrix=bt470:in_range=tv:out_color_matrix=bt470"
                 ":out_range=tv,format=yuv420p,", id="agreeing-pal-stays"),
    pytest.param({"color_space": "ycgco"}, _BT709, "format=yuv420p,",
                 id="matrix-scale-cannot-convert"),
])
def test_clip_color_filter(source, target, expected):
    assert cut_render._clip_color_filter(source, target) == expected


# ── command construction (mocked ffmpeg) ────────────────────────────────────────────


def test_multi_source_render_converts_every_clip_and_drops_source_metadata(
    monkeypatch, tmp_path
):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mov"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    formats = {str(a): _BT601, str(b): {"pix_fmt": "argb"}}
    commands = []

    def fake_run_cmd(cmd):
        commands.append(cmd)
        if cmd[0] == "ffmpeg":
            Path(cmd[-1]).write_bytes(b"mp4")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(cut_render, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(cut_render, "_probe_video_format", lambda path: formats[str(path)])
    monkeypatch.setattr(cut_render, "_has_audio_stream", lambda _path: True)
    monkeypatch.setattr(cut_render, "get_video_duration", lambda _path: 2.0)
    monkeypatch.setattr(cut_render, "_write_edited_source_meta", lambda *a, **k: None)
    monkeypatch.setattr(cut_render, "_warn_on_frame_count_mismatch", lambda *a, **k: None)
    plan = {
        "clips": [
            {"clip_id": 0, "source_path": str(a), "source_start": 0.0, "source_end": 1.0,
             "duration": 1.0, "frame_count": 25},
            {"clip_id": 1, "source_path": str(b), "source_start": 0.0, "source_end": 1.0,
             "duration": 1.0, "frame_count": 25},
        ],
        "qc": {"output_geometry": {
            "width": 320, "height": 240, "frame_rate": "25",
            "sources": [{"path": str(a), "frame_rate": "25"}, {"path": str(b), "frame_rate": "25"}],
        }},
    }

    build_edited_source_video(a, plan, tmp_path)

    cmd = [c for c in commands if c[0] == "ffmpeg"][0]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert ("scale=in_color_matrix=smpte170m:in_range=tv:out_color_matrix=bt709:out_range=tv,"
            "format=yuv420p,") in graph
    assert "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p," in graph
    assert "[v]setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv" in graph
    assert cmd[cmd.index("-map_metadata") + 1] == "-1"
    assert cmd[cmd.index("-map_chapters") + 1] == "-1"
    assert cmd.index("-map_metadata") < cmd.index(str(tmp_path / "edited_source.mp4"))


def test_picture_rules_version_changes_with_the_colour_rules():
    """A cached edited_source.mp4 rendered under the v1 rules is not reused."""
    payload = cut_contract.edited_source_render_cache_payload()
    assert "yuv420p-color-tags-v2" in json.dumps(payload)


# ── real ffmpeg ─────────────────────────────────────────────────────────────────────


def _make_red(path, picture, codec_args):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"color=c=red:size=160x120:rate=12:duration=2,{picture}",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest",
         *codec_args, "-c:a", "aac", str(path)],
        check=True, capture_output=True,
    )


_SOURCES = {
    # name: (suffix, picture filter, codec args, (Y, Cb) of pure red as stored)
    "bt601": (".mp4", "scale=out_color_matrix=bt601:out_range=tv,format=yuv420p,"
              "setparams=colorspace=smpte170m:color_primaries=smpte170m:color_trc=smpte170m",
              ["-c:v", "libx264", "-preset", "ultrafast", "-colorspace", "smpte170m",
               "-color_primaries", "smpte170m", "-color_trc", "smpte170m"], (81, 90)),
    "untagged": (".mp4", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
                 "setparams=colorspace=unknown:range=unknown",
                 ["-c:v", "libx264", "-preset", "ultrafast"], (63, 102)),
    "full": (".mp4", "scale=out_color_matrix=bt709:out_range=pc,format=yuvj420p",
             ["-c:v", "libx264", "-preset", "ultrafast"], (54, 98)),
    "argb": (".mov", "format=argb", ["-c:v", "qtrle"], None),
}


def _source(tmp_path, name):
    suffix, picture, codec_args, _ = _SOURCES[name]
    path = tmp_path / f"{name}{suffix}"
    if not path.exists():
        _make_red(path, picture, codec_args)
    return path


def _probe(path):
    return json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=pix_fmt,color_space,color_primaries,color_transfer,color_range",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout)["streams"][0]


def _centre_y_cb(path, at):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at}", "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
        check=True, capture_output=True,
    ).stdout
    w, h = 160, 120
    return raw[(h // 2) * w + w // 2], raw[w * h + (h // 4) * (w // 2) + w // 4]


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_real_sources_store_the_pixels_the_matrix_assumes(tmp_path):
    """Guard for the fixtures below: each source really stores the pixels listed."""
    for name in ("bt601", "untagged", "full"):
        path = _source(tmp_path, name)
        y, cb = _centre_y_cb(path, 0.2) if name != "full" else _raw_full(path)
        expected = _SOURCES[name][3]
        assert abs(y - expected[0]) <= 2 and abs(cb - expected[1]) <= 2, (name, y, cb)
    assert "color_space" not in _probe(_source(tmp_path, "untagged"))
    assert "color_space" not in _probe(_source(tmp_path, "argb"))


def _raw_full(path):
    """Stored full-range samples (decoding as yuv420p would convert them to limited)."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "yuvj420p", "-"],
        check=True, capture_output=True,
    ).stdout
    w, h = 160, 120
    return raw[(h // 2) * w + w // 2], raw[w * h + (h // 4) * (w // 2) + w // 4]


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
@pytest.mark.parametrize("first, second, tags, red", [
    pytest.param("bt601", "untagged", ("bt709", "bt709", "bt709", "tv"), (63, 102),
                 id="tagged-bt601-beside-untagged"),
    pytest.param("full", "untagged", ("bt709", "bt709", "bt709", "tv"), (63, 102),
                 id="full-range-beside-limited"),
    pytest.param("argb", "bt601", ("bt709", "bt709", "bt709", "tv"), (63, 102),
                 id="rgb-without-colour-space-beside-bt601"),
    pytest.param("bt601", "bt601", ("smpte170m", "smpte170m", "smpte170m", "tv"), (81, 90),
                 id="agreeing-bt601-kept"),
])
def test_real_mixed_sources_share_one_colour_space(tmp_path, first, second, tags, red):
    """Both clips hold pure red in the labelled colour space. ffmpeg 8's concat otherwise
    converts one source into the other's colour space while the label says BT.709."""
    a, b = _source(tmp_path, first), _source(tmp_path, second)
    if a == b:
        b = tmp_path / f"{second}-copy{b.suffix}"
        shutil.copy(a, b)
    manifest = {"sources": [
        {"source_id": "a", "source_path": str(a), "duration": 2.0},
        {"source_id": "b", "source_path": str(b), "duration": 2.0},
    ]}
    plan = cut_contract.normalize_multi_source_clip_plan([
        {"source_id": "a", "start": 0.0, "end": 1.0},
        {"source_id": "b", "start": 0.0, "end": 1.0},
    ], manifest)
    work = tmp_path / "work"
    work.mkdir()

    out = build_edited_source_video(a, _with_geometry(plan, [str(a), str(b)]), work)

    facts = _probe(out)
    assert (facts["pix_fmt"], facts["color_space"], facts["color_primaries"],
            facts["color_transfer"], facts["color_range"]) == ("yuv420p", *tags)
    for at in (0.3, 1.5):
        y, cb = _centre_y_cb(out, at)
        assert abs(y - red[0]) <= 2 and abs(cb - red[1]) <= 2, (at, y, cb)


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_real_rgb_source_without_colour_space_converts_with_bt709(tmp_path):
    """QuickTime RLE reports pix_fmt argb and no colour space at all."""
    video = _source(tmp_path, "argb")
    work = tmp_path / "work"
    work.mkdir()
    plan = normalize_clip_plan([{"start": 0.0, "end": 1.0}], video_duration=2.0)

    out = build_edited_source_video(video, _with_geometry(plan, [str(video)]), work)

    assert _probe(out)["color_space"] == "bt709"
    y, cb = _centre_y_cb(out, 0.3)
    assert abs(y - 63) <= 2 and abs(cb - 102) <= 2, (y, cb)


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_real_render_drops_source_title_comment_and_chapters(tmp_path):
    video = tmp_path / "scraped.mp4"
    meta = tmp_path / "meta.txt"
    meta.write_text(
        ";FFMETADATA1\ntitle=Scraped Site Title\ncomment=https://example.invalid/watch\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1500\ntitle=Site chapter\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=12:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-i", str(meta),
         "-map", "0:v", "-map", "1:a", "-map_metadata", "2", "-map_chapters", "2",
         "-metadata:s:a:0", "language=eng", "-shortest",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(video)],
        check=True, capture_output=True,
    )
    work = tmp_path / "work"
    work.mkdir()
    plan = normalize_clip_plan([{"start": 0.0, "end": 1.0}, {"start": 1.5, "end": 2.5}],
                               video_duration=3.0)

    out = build_edited_source_video(video, _with_geometry(plan, [str(video)]), work)

    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_chapters", "-show_entries",
         "format_tags:stream_tags", "-of", "json", str(out)],
        check=True, capture_output=True, text=True,
    ).stdout)
    tags = probe["format"].get("tags", {})
    assert "title" not in tags and "comment" not in tags, tags
    assert probe.get("chapters", []) == []
    assert all(s.get("tags", {}).get("language") != "eng" for s in probe["streams"])
