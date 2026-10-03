"""Delivery format of the final render: colour tags, yuv420p, faststart, and the default
subtitle burn that degrades to a .srt sidecar when ffmpeg lacks libass."""

import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-assemble" / "scripts"))

import assemble  # noqa: E402
import assembly_contract  # noqa: E402
import audio_mix  # noqa: E402
import media  # noqa: E402
import narration_audio  # noqa: E402
import render_preflight  # noqa: E402
import source_subtitles  # noqa: E402
import timeline_emit  # noqa: E402
import visual_render  # noqa: E402
from assemble import assemble_video  # noqa: E402
from lib import CONFIG  # noqa: E402
from tts_fixtures import tts_segment  # noqa: E402

_HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
_BT709 = {"colorspace": "bt709", "color_primaries": "bt709", "color_trc": "bt709",
          "color_range": "tv"}


# ── colour tag policy ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("stream, expected", [
    pytest.param({}, _BT709, id="untagged"),
    pytest.param({"color_space": "unknown", "color_primaries": "unknown",
                  "color_transfer": "unknown", "color_range": "unknown"}, _BT709,
                 id="explicit-unknown"),
    pytest.param({"color_space": "bt709", "color_range": "tv"}, _BT709, id="partial-bt709"),
    pytest.param({"color_space": "bt709", "color_primaries": "bt709",
                  "color_transfer": "bt709", "color_range": "pc"},
                 {**_BT709, "color_range": "pc"}, id="bt709-full-range-kept"),
    pytest.param({"color_space": "smpte170m", "color_primaries": "smpte170m",
                  "color_transfer": "smpte170m", "color_range": "tv"},
                 {"colorspace": "smpte170m", "color_primaries": "smpte170m",
                  "color_trc": "smpte170m", "color_range": "tv"}, id="bt601-preserved"),
    pytest.param({"color_space": "bt2020nc", "color_primaries": "bt2020",
                  "color_transfer": "smpte2084"},
                 {"colorspace": "bt2020nc", "color_primaries": "bt2020",
                  "color_trc": "smpte2084", "color_range": "tv"}, id="hdr-preserved"),
    pytest.param({"color_space": "bt470bg", "color_primaries": "unknown",
                  "color_transfer": "not-a-real-name"},
                 {"colorspace": "bt470bg", "color_range": "tv"}, id="only-known-tags-pass"),
    pytest.param({"color_space": "bt470bg", "color_primaries": "bt470bg",
                  "color_transfer": "bt470bg"},
                 {"colorspace": "bt470bg", "color_primaries": "bt470bg",
                  "color_trc": "bt470bg", "color_range": "tv"}, id="pal-bt470-transfer-kept"),
    pytest.param({"color_space": "gbr", "color_range": "pc"},
                 {**_BT709, "from_rgb": True}, id="rgb-source-becomes-bt709-limited"),
    pytest.param({"color_space": "gbr", "color_primaries": "smpte170m",
                  "color_transfer": "unknown", "color_range": "pc"},
                 {"colorspace": "bt709", "color_primaries": "smpte170m", "color_range": "tv",
                  "from_rgb": True}, id="rgb-source-never-writes-gbr"),
])
def test_output_color_tags(stream, expected):
    assert media._output_color_tags(stream) == expected


def test_color_tag_filter_and_args_spell_every_tag():
    assert media._color_tag_filter(_BT709) == (
        "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
    )
    assert media._color_tag_args(_BT709) == [
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv",
    ]
    partial = {"colorspace": "bt470bg", "color_range": "pc"}
    assert media._color_tag_filter(partial) == "setparams=colorspace=bt470bg:range=pc"
    assert media._color_tag_args(partial) == ["-colorspace", "bt470bg", "-color_range", "pc"]
    pal = {"color_trc": "bt470bg", "color_range": "tv"}
    assert media._color_tag_filter(pal) == "setparams=color_trc=bt470bg:range=tv"
    assert media._color_tag_args(pal) == ["-color_trc", "gamma28", "-color_range", "tv"]
    rgb = {**_BT709, "from_rgb": True}
    assert media._color_tag_filter(rgb) == (
        "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
        "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
    )
    assert media._color_tag_args(rgb) == media._color_tag_args(_BT709)


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
@pytest.mark.parametrize("option, value", sorted(
    (option, value) for option, values in media._KNOWN_COLOR_VALUES.items() for value in values
))
def test_every_known_colour_value_is_accepted_by_ffmpeg(tmp_path, option, value):
    """Each tag the policy may write must pass setparams and the output option, as spelled
    by `_color_tag_filter` / `_color_tag_args`, and land in the file as ffprobe names it."""
    tags = {option: value, "color_range": "tv"}
    output = tmp_path / "tagged.mp4"
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=32x32:d=0.1",
         "-vf", media._color_tag_filter(tags), "-c:v", "libx264", "-pix_fmt", "yuv420p",
         *media._color_tag_args(tags), str(output)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr
    field = {opt: key for key, opt in media._COLOR_FIELDS}[option]
    assert _probe(output).get(field) == value


@pytest.mark.parametrize("stream, safe", [
    ({"codec_name": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 720}, True),
    ({"codec_name": "h264", "pix_fmt": "yuvj420p", "width": 640, "height": 360}, True),
    ({"codec_name": "h264", "pix_fmt": "yuv422p10le", "width": 1280, "height": 720}, False),
    ({"codec_name": "hevc", "pix_fmt": "yuv420p", "width": 1280, "height": 720}, False),
    ({"codec_name": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 721}, False),
    ({}, False),
])
def test_video_copy_safe(stream, safe):
    assert media._video_copy_safe(stream) is safe


# ── render command construction (mocked ffmpeg) ────────────────────────────────────


def _canvas():
    return media._canvas_from_stream({
        "width": 1280, "height": 720, "r_frame_rate": "30/1",
        "sample_aspect_ratio": "1:1", "display_aspect_ratio": "16:9",
    })


def _render_cmd(monkeypatch, tmp_path, *, source_format, burn=False):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    output = tmp_path / "output.mp4"
    commands = []

    def fake_run_cmd(cmd):
        commands.append(cmd)
        output.write_bytes(b"mp4")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setitem(CONFIG, "burn_subtitles", burn)
    monkeypatch.setitem(CONFIG, "force_video_reencode", False)
    monkeypatch.setitem(CONFIG, "output_max_height", 0)
    monkeypatch.setattr(assemble.lib, "get_video_duration", lambda _p: 4.0)
    monkeypatch.setattr(media, "_probe_canvas", lambda _p: _canvas())
    monkeypatch.setattr(media, "_has_audio_stream", lambda _p: True)
    monkeypatch.setattr(media, "_probe_video_format", lambda _p: source_format)
    monkeypatch.setattr(narration_audio, "_apply_narration_speed", lambda *a, **k: None)
    monkeypatch.setattr(narration_audio, "_build_timed_narration",
                        lambda segments, out, duration, wd: Path(out).write_bytes(b"n"))
    monkeypatch.setattr(timeline_emit, "_emit_timeline", lambda *a, **k: None)
    monkeypatch.setattr(audio_mix, "_run_loudnorm_first_pass", lambda *a, **k: None)
    monkeypatch.setattr(assembly_contract, "_build_assembly_qc",
                        lambda *a, **k: {"blocking": False, "blocking_codes": []})
    monkeypatch.setattr("assemble.lib.run_cmd", fake_run_cmd)
    assemble_video(video, [tts_segment(
        start=0.0, end=3.0, actual_place_start=0.0, actual_place_end=1.0,
        narration="交付格式。", audio_path=str(tmp_path / "narr.wav"), audio_duration=1.0,
    )], tmp_path, output)
    return commands[-1]


def _value(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def test_copy_safe_untagged_source_is_copied_and_labelled_bt709(monkeypatch, tmp_path):
    cmd = _render_cmd(monkeypatch, tmp_path, source_format={
        "codec_name": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 720,
    })
    assert _value(cmd, "-c:v") == "copy" and "-vf" not in cmd
    for flag, value in (("-colorspace", "bt709"), ("-color_primaries", "bt709"),
                        ("-color_trc", "bt709"), ("-color_range", "tv")):
        assert _value(cmd, flag) == value
    assert _value(cmd, "-movflags") == "+faststart"


def test_ten_bit_422_source_without_filters_is_reencoded_to_yuv420p(monkeypatch, tmp_path):
    """The no-burn / degraded-burn path used to copy any picture through unchanged."""
    cmd = _render_cmd(monkeypatch, tmp_path, source_format={
        "codec_name": "h264", "pix_fmt": "yuv422p10le", "width": 1280, "height": 720,
        "color_space": "bt709",
    })
    assert _value(cmd, "-c:v") == "libx264"
    assert _value(cmd, "-pix_fmt") == "yuv420p"
    assert _value(cmd, "-vf").endswith(
        "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
    )
    assert _value(cmd, "-movflags") == "+faststart"
    qc_delivery = json.loads((tmp_path / "visual_qc.json").read_text(encoding="utf-8"))
    assert qc_delivery["warnings"] == []


def test_burned_render_stamps_source_tags_last_in_the_filter_chain(monkeypatch, tmp_path):
    cmd = _render_cmd(monkeypatch, tmp_path, burn=True, source_format={
        "codec_name": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 720,
        "color_space": "smpte170m", "color_primaries": "smpte170m",
        "color_transfer": "smpte170m",
    })
    video_filter = _value(cmd, "-vf")
    assert "subtitles=" in video_filter
    assert video_filter.endswith(
        "setparams=colorspace=smpte170m:color_primaries=smpte170m:color_trc=smpte170m:range=tv"
    )
    assert _value(cmd, "-colorspace") == "smpte170m"
    assert _value(cmd, "-pix_fmt") == "yuv420p"


# ── degraded default burn ───────────────────────────────────────────────────────────


def _degraded(monkeypatch):
    monkeypatch.setitem(CONFIG, "burn_subtitles", False)
    monkeypatch.setitem(CONFIG, "burn_subtitles_degraded", "ffmpeg_missing_libass")
    monkeypatch.setitem(CONFIG, "subtitle_original_in_gaps", True)


def test_degraded_burn_keeps_user_gap_cues_in_the_sidecar(monkeypatch, tmp_path):
    _degraded(monkeypatch)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", False)
    (tmp_path / "user_subtitles.srt").write_text(
        "1\n00:00:01,000 --> 00:00:04,000\n原声台词\n\n", encoding="utf-8")
    segs = [{"actual_place_start": 5.0, "actual_place_end": 8.0, "narration": "解说"}]
    entries = source_subtitles._original_gap_subtitle_entries(segs, tmp_path, 10.0)
    assert entries and all(e["text"].startswith("「") for e in entries)

    monkeypatch.setitem(CONFIG, "burn_subtitles_degraded", None)  # explicit --no-burn
    assert source_subtitles._original_gap_subtitle_entries(segs, tmp_path, 10.0) == []


def test_degraded_burn_records_machine_readable_warning(monkeypatch, tmp_path):
    _degraded(monkeypatch)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", True)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_policy", "opt_in")
    segs = [tts_segment(start=0.0, end=3.0, actual_place_start=0.5, actual_place_end=2.0,
                        narration="解说", audio_duration=1.5)]
    qc = visual_render._build_visual_qc(segs, tmp_path, 4.0, _canvas())
    assert qc["subtitles"]["renderer"] == "sidecar_srt"
    assert qc["subtitles"]["burn_degraded_reason"] == "ffmpeg_missing_libass"
    assert qc["mask"]["trigger"] == "burn_subtitles_degraded" and not qc["mask"]["active"]
    [warning] = qc["warnings"]
    assert warning["code"] == "subtitle_burn_degraded"
    assert warning["delivered"] == "sidecar_srt" and warning["mask_dropped"] is True
    assert qc["blocking"] is False


def test_degraded_burn_without_any_subtitles_records_no_warning(monkeypatch, tmp_path):
    """source-mix / adopted copy without user subtitles: nothing was lost to the missing
    libass, so nothing is relayed to the user."""
    _degraded(monkeypatch)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", False)
    qc = visual_render._build_visual_qc([], tmp_path, 4.0, _canvas())
    assert qc["warnings"] == []


def test_empty_subtitles_ship_no_sidecar_and_retire_a_stale_one(monkeypatch, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    final = tmp_path / "recap_x.mp4"
    stale = tmp_path / "recap_x.srt"
    stale.write_text("1\n00:00:00,000 --> 00:00:01,000\n旧字幕\n\n", encoding="utf-8")
    monkeypatch.setitem(CONFIG, "burn_subtitles", False)
    (work / "subtitles.srt").write_text("", encoding="utf-8")
    assert assemble._publish_subtitle_sidecar(work, final) is None
    assert not stale.exists()
    (work / "subtitles.srt").unlink()
    assert assemble._publish_subtitle_sidecar(work, final) is None


def test_sidecar_ships_next_to_an_unburned_recap_and_is_retired_by_a_burned_one(
    monkeypatch, tmp_path
):
    work = tmp_path / "work"
    work.mkdir()
    (work / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n解说\n\n",
                                        encoding="utf-8")
    final = tmp_path / "recap_x.mp4"
    monkeypatch.setitem(CONFIG, "burn_subtitles", False)
    sidecar = assemble._publish_subtitle_sidecar(work, final)
    assert sidecar == tmp_path / "recap_x.srt"
    assert sidecar.read_text(encoding="utf-8") == (work / "subtitles.srt").read_text(encoding="utf-8")

    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    assert assemble._publish_subtitle_sidecar(work, final) is None
    assert not sidecar.exists()


# ── drawtext overlay preflight ──────────────────────────────────────────────────────


def _overlays(work_dir, overlays):
    (work_dir / "visual_overlays.json").write_text(
        json.dumps({"schema_version": 1, "overlays": overlays}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_overlay_preflight_fails_before_render_without_drawtext(monkeypatch, tmp_path):
    _overlays(tmp_path, [{"type": "top_title", "text": "第一章"}])
    monkeypatch.setattr(render_preflight.shutil, "which", lambda _n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(render_preflight, "_ffmpeg_filters", lambda: {"drawbox"})
    with pytest.raises(SystemExit, match="drawtext"):
        render_preflight._preflight_visual_overlays(tmp_path)


@pytest.mark.parametrize("setup", ["no_file", "empty", "drawtext_present", "no_ffmpeg"])
def test_overlay_preflight_passes(monkeypatch, tmp_path, setup):
    if setup == "empty":
        _overlays(tmp_path, [])
    elif setup != "no_file":
        _overlays(tmp_path, [{"type": "top_title", "text": "第一章"}])
    monkeypatch.setattr(render_preflight.shutil, "which",
                        lambda _n: None if setup == "no_ffmpeg" else "/usr/bin/ffmpeg")
    monkeypatch.setattr(render_preflight, "_ffmpeg_filters", lambda: {"drawtext"})
    render_preflight._preflight_visual_overlays(tmp_path)  # must not raise


# ── real ffmpeg: what ffprobe reports on the delivered file ─────────────────────────


def _run(*args):
    subprocess.run(args, check=True, capture_output=True)


def _make_source(path, *, pix_fmt="yuv420p", extra=()):
    _run("ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=s=160x120:r=12:d=2",
         "-f", "lavfi", "-i", "sine=frequency=431:sample_rate=48000:d=2",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", pix_fmt, *extra,
         "-c:a", "aac", str(path))


def _probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=pix_fmt,color_space,color_primaries,color_transfer,color_range",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)["streams"][0]


def _moov_before_mdat(path):
    data = path.read_bytes()
    pos, order = 0, []
    while pos + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
        order.append(kind)
        if size < 8:
            break
        pos += size
    return order.index(b"moov") < order.index(b"mdat")


def _quiet(monkeypatch):
    monkeypatch.setitem(CONFIG, "burn_subtitles", False)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", False)
    monkeypatch.setitem(CONFIG, "subtitle_original_in_gaps", False)
    monkeypatch.setitem(CONFIG, "output_max_height", 0)
    monkeypatch.setitem(CONFIG, "force_video_reencode", False)
    monkeypatch.setitem(CONFIG, "bgm_path", "")
    monkeypatch.setitem(CONFIG, "export_jianying", False)


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
@pytest.mark.parametrize("audio_mode", ["source-mix", "adopted-packet-copy"])
@pytest.mark.parametrize("pix_fmt", ["yuv420p", "yuv422p"])
def test_real_render_delivers_bt709_yuv420p_faststart(monkeypatch, tmp_path, audio_mode, pix_fmt):
    _quiet(monkeypatch)
    source = tmp_path / "source.mp4"
    work = tmp_path / "work"
    work.mkdir()
    _make_source(source, pix_fmt=pix_fmt)
    assert _probe(source).get("color_primaries", "unknown") == "unknown"

    output = assemble_video(source, [], work, work / "output.mp4", audio_mode=audio_mode)

    facts = _probe(output)
    assert facts["pix_fmt"] == "yuv420p"
    assert (facts["color_space"], facts["color_primaries"], facts["color_transfer"],
            facts["color_range"]) == ("bt709", "bt709", "bt709", "tv")
    assert _moov_before_mdat(output)
    qc = json.loads((work / "assembly_qc.json").read_text(encoding="utf-8"))
    assert qc["delivery_qc"]["color_tags"] == _BT709
    assert qc["delivery_qc"]["video_encode_passes"] == (0 if pix_fmt == "yuv420p" else 1)


def _first_yuv_pixel(path):
    """(Y, Cb, Cr) of the top-left pixel of the first frame, decoded as 8-bit yuv420p."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
        check=True, capture_output=True,
    ).stdout
    luma = len(raw) * 2 // 3
    return raw[0], raw[luma], raw[luma + luma // 4]


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
@pytest.mark.parametrize("codec", ["png", "libx264rgb"])
def test_real_render_converts_rgb_source_to_bt709(monkeypatch, tmp_path, codec):
    """An RGB source (ffprobe colour space `gbr`) used to fail at `-colorspace gbr`."""
    _quiet(monkeypatch)
    source = tmp_path / ("source.mov" if codec == "png" else "source.mp4")
    work = tmp_path / "work"
    work.mkdir()
    _run("ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=red:s=160x120:r=12:d=2,format=rgb24",
         "-f", "lavfi", "-i", "sine=frequency=431:sample_rate=48000:d=2",
         "-c:v", codec, "-c:a", "aac", str(source))
    assert _probe(source)["color_space"] == "gbr"

    output = assemble_video(source, [], work, work / "output.mp4", audio_mode="source-mix")

    facts = _probe(output)
    assert (facts["pix_fmt"], facts["color_space"], facts["color_primaries"],
            facts["color_transfer"], facts["color_range"]) == (
        "yuv420p", "bt709", "bt709", "bt709", "tv")
    # Pure red in BT.709 limited range is Y=63 Cb=102 Cr=240 (BT.601 would be 81/90/240).
    y, cb, cr = _first_yuv_pixel(output)
    assert abs(y - 63) <= 2 and abs(cb - 102) <= 2 and abs(cr - 240) <= 2, (y, cb, cr)


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_real_render_preserves_declared_bt601_tags(monkeypatch, tmp_path):
    _quiet(monkeypatch)
    source = tmp_path / "source.mp4"
    work = tmp_path / "work"
    work.mkdir()
    _make_source(source, pix_fmt="yuv422p", extra=(
        "-vf", "setparams=colorspace=smpte170m:color_primaries=smpte170m:color_trc=smpte170m",
        "-colorspace", "smpte170m", "-color_primaries", "smpte170m", "-color_trc", "smpte170m",
    ))

    output = assemble_video(source, [], work, work / "output.mp4", audio_mode="source-mix")

    facts = _probe(output)
    assert facts["pix_fmt"] == "yuv420p"
    assert (facts["color_space"], facts["color_primaries"], facts["color_transfer"]) == (
        "smpte170m", "smpte170m", "smpte170m"
    )
