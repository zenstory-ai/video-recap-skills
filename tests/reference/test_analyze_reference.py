import json
import shutil
import subprocess
from pathlib import Path

import pytest

import analyze_reference as ar


def test_public_all_and_no_private_exports():
    assert ar.__all__
    assert all(not name.startswith("_") for name in ar.__all__)
    for name in ar.__all__:
        assert hasattr(ar, name)


def test_parse_scdet_and_detect_cuts_isolated_vs_burst():
    stderr = "\n".join([
        "lavfi.scd.score: 12.5, lavfi.scd.time: 0.02",   # dropped (within EDGE_S)
        "lavfi.scd.score: 15.0, lavfi.scd.time: 2.00",   # hard cut
        "lavfi.scd.score: 5.5, lavfi.scd.time: 5.00",    # isolated soft cut
        "lavfi.scd.score: 5.0, lavfi.scd.time: 8.00",    # burst (suppressed -> review window)
        "lavfi.scd.score: 4.5, lavfi.scd.time: 8.15",    # burst neighbor
        "lavfi.scd.score: 14.0, lavfi.scd.time: 9.98",   # dropped (near end)
    ])
    scores = ar.parse_scdet(stderr, duration=10.0)
    assert [t for t, _ in scores] == [2.0, 5.0, 8.0, 8.15]

    cuts, windows = ar.detect_cuts(scores, fps=25.0)
    assert cuts == [2.0, 5.0]
    assert len(windows) == 1
    assert windows[0][0] <= 8.0 <= 8.15 <= windows[0][1]


def test_shot_stats_and_audio_parsers():
    stats = ar.shot_stats([2.0, 5.0], duration=10.0)
    assert stats["shot_count"] == 3
    assert stats["median_s"] == 3.0
    assert stats["cuts_per_min"] == 12.0
    assert stats["density_curve_10s_cpm"] == [12.0]

    ebu_stderr = (
        "t: 0.4 S: -18.2 LUFS\n"
        "t: 1.4 S: -120.7 LUFS\n"
        "Summary:\n"
        "  Integrated loudness:\n"
        "    I:         -14.2 LUFS\n"
        "  Loudness range:\n"
        "    LRA:         6.5 LU\n"
        "  True peak:\n"
        "    Peak:       -1.3 dBFS\n"
    )
    loud = ar.parse_ebur128(ebu_stderr, duration=2.0)
    assert loud["integrated_lufs"] == -14.2
    assert loud["lra_lu"] == 6.5
    assert loud["true_peak_dbtp"] == -1.3
    assert loud["short_term_1s"] == [-18.2, None]

    sil_stderr = (
        "[silencedetect] silence_start: 1.0\n"
        "[silencedetect] silence_end: 2.5 | silence_duration: 1.5\n"
    )
    assert ar.parse_silences(sil_stderr, duration=4.0) == [[1.0, 2.5]]


@pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="requires ffmpeg and ffprobe",
)
def test_analyze_reference_real_video_end_to_end(tmp_path: Path):
    src = tmp_path / "ref.mp4"
    # 3-second video with a hard red->blue color cut at 1.5s and a 440Hz tone
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=red:s=320x180:r=24:d=1.5",
        "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=1.5",
        "-f", "lavfi", "-i", "sine=f=440:r=48000:d=3.0",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(src),
    ]
    subprocess.run(cmd, check=True)

    out_dir = tmp_path / "out"
    rc = ar.main([str(src), "--out-dir", str(out_dir), "--grid", "2x2"])
    assert rc == 0

    metrics_path = out_dir / "reference_metrics.json"
    sheet_path = out_dir / "contact_sheet.jpg"
    assert metrics_path.is_file()
    assert sheet_path.is_file() and sheet_path.stat().st_size > 0

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics["schema_version"] == 1
    assert metrics["source"]["width"] == 320
    assert metrics["source"]["height"] == 180
    assert metrics["source"]["has_audio"] is True
    assert any(1.2 <= t <= 1.8 for t in metrics["shots"]["cuts"])
    assert metrics["audio"]["loudness"]["integrated_lufs"] is not None
    assert metrics["audio"]["active_audio_share"] > 0.5
    assert metrics["contact_sheet"]["grid"] == "2x2"
    assert len(metrics["contact_sheet"]["tile_times_s"]) == 4
