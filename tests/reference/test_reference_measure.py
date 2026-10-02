import math
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

import lib
import reference_measure
from reference_measure import measure, parse_ebur128, parse_scdet, shot_stats

SCDET_COLON = """\
[Parsed_scdet_1 @ 0xb34c0f300] lavfi.scd.score: 15.625, lavfi.scd.time: 2
[Parsed_scdet_1 @ 0xb34c0f300] lavfi.scd.score: 15.625, lavfi.scd.time: 2.6
"""
SCDET_EQUALS = """\
[scdet @ 0x7fe3] lavfi.scd.score=15.625 lavfi.scd.time=2.6
[scdet @ 0x7fe3] lavfi.scd.score=12.1 lavfi.scd.time=2
[scdet @ 0x7fe3] lavfi.scd.score=40.0 lavfi.scd.time=0
"""
EBUR128 = """\
[Parsed_ebur128_0 @ 0xc9f] t: 0.0999792  TARGET:-23 LUFS    M:-120.7 S:-120.7     I: -70.0 LUFS       LRA:   0.0 LU  FTPK: -37.3 dBFS  TPK: -37.3 dBFS
[Parsed_ebur128_0 @ 0xc9f] t: 2.999979   TARGET:-23 LUFS    M: -41.8 S: -41.8     I: -41.8 LUFS       LRA:  20.0 LU  FTPK: -38.0 dBFS  TPK: -37.3 dBFS
[Parsed_ebur128_0 @ 0xc9f] t: 3.099979   TARGET:-23 LUFS    M: -33.1 S: -39.1     I: -40.9 LUFS       LRA:  21.1 LU  FTPK: -20.9 dBFS  TPK: -20.9 dBFS
[Parsed_ebur128_0 @ 0xc9f] t: 3.999979   TARGET:-23 LUFS    M: -27.8 S: -30.2     I: -30.0 LUFS       LRA:  12.0 LU  FTPK: -23.3 dBFS  TPK: -20.9 dBFS
[Parsed_ebur128_0 @ 0xc9f] Summary:

  Integrated loudness:
    I:         -28.0 LUFS
    Threshold: -40.6 LUFS

  Loudness range:
    LRA:         9.5 LU
    Threshold: -50.6 LUFS
    LRA low:   -37.6 LUFS
    LRA high:  -28.1 LUFS

  True peak:
    Peak:      -20.9 dBFS
"""


@pytest.mark.parametrize("stderr", [SCDET_COLON, SCDET_EQUALS])
def test_parse_scdet_reads_both_log_formats_and_drops_edge_cuts(stderr):
    assert parse_scdet(stderr, duration=6.0) == [2.0, 2.6]


def test_parse_ebur128_reads_summary_and_downsamples_short_term_to_seconds():
    loudness = parse_ebur128(EBUR128, duration=5.0)

    assert loudness["integrated_lufs"] == -28.0
    assert loudness["lra_lu"] == 9.5
    assert loudness["true_peak_dbtp"] == -20.9
    # bucket 0 is warm-up (-120.7 -> null); each second keeps its last short-term value
    assert loudness["short_term_1s"] == [None, None, -41.8, -30.2, None]


def test_shot_stats_counts_lengths_and_density():
    stats = shot_stats([2.0, 2.6], 6.0)

    assert stats["count"] == 3
    assert stats["median_s"] == 2.0
    assert stats["share_under_1s"] == pytest.approx(1 / 3, abs=1e-3)
    assert stats["cuts_per_min"] == 20.0
    assert stats["curve"] == {"window_s": 10.0, "values": [20.0]}   # 2 cuts in a 6 s window


def test_curve_scales_the_partial_last_window_by_its_own_length():
    stats = shot_stats([3.0, 12.0, 21.0], 25.0)

    # full 10 s windows count x6; the last 5 s window counts x12
    assert stats["curve"]["values"] == [6.0, 6.0, 12.0]


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")
def test_real_ffmpeg_pass_finds_cuts_shot_stats_and_loudness(tmp_path):
    video = tmp_path / "three_shots.mkv"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=red:s=320x240:r=25:d=2",
        "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=25:d=0.6",
        "-f", "lavfi", "-i", "color=c=green:s=320x240:r=25:d=3.4",
        "-f", "lavfi", "-i", "sine=f=440:d=6:sample_rate=48000",
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v];[3:a]volume='if(lt(t,3),0.1,0.5)':eval=frame[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "ffv1", "-c:a", "pcm_s16le", str(video),
    ], check=True)

    payload = measure(video, tmp_path / "work")

    cuts = payload["shots"]["cuts"]
    assert len(cuts) == 2
    assert cuts[0] == pytest.approx(2.0, abs=0.05) and cuts[1] == pytest.approx(2.6, abs=0.05)
    assert payload["shots"]["count"] == 3
    assert payload["shots"]["share_under_1s"] == pytest.approx(1 / 3, abs=1e-3)
    # The gated integrated loudness is the loud half: a 440 Hz sine at amplitude 1/8 * 0.5.
    amplitude = 0.125 * 0.5
    analytic_lufs = -0.691 + 10 * math.log10(amplitude ** 2 / 2)
    assert payload["loudness"]["integrated_lufs"] == pytest.approx(analytic_lufs, abs=1.0)
    assert payload["source"]["canvas"] == {"width": 320, "height": 240}


def _fake_runner(calls):
    def run(cmd, **_kwargs):
        calls.append(cmd[0])
        if cmd[0] == "ffprobe":
            stdout = ('{"format":{"duration":"6.0"},"streams":[{"codec_type":"video","width":320,'
                      '"height":240,"avg_frame_rate":"25/1"},{"codec_type":"audio"}]}')
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr=SCDET_COLON + EBUR128)
    return run


def test_measure_reuses_cache_by_identity_and_reruns_when_the_file_changes(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(lib, "run_cmd", _fake_runner(calls))
    monkeypatch.setattr(reference_measure, "run_cmd", _fake_runner(calls))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 10)

    measure(video, tmp_path)
    assert len(calls) == 2
    measure(video, tmp_path)
    assert len(calls) == 2, "same size+mtime and settings must not re-run ffmpeg"

    measure(video, tmp_path, scaled=False)
    assert len(calls) == 4, "a settings change re-measures"
    stat = video.stat()
    os.utime(video, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    measure(video, tmp_path, scaled=False)
    assert len(calls) == 6, "an mtime change re-measures"
    video.write_bytes(b"x" * 11)
    measure(video, tmp_path, scaled=False)
    assert len(calls) == 8, "a size change re-measures"
