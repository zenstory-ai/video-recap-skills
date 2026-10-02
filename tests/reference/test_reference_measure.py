import json
import math
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

import lib
import reference
import reference_measure
from reference_measure import detect_cuts, measure, parse_ebur128, parse_scdet, shot_stats

requires_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not available"
)


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
    assert [t for t, _ in parse_scdet(stderr, duration=6.0)] == [2.0, 2.6]
    assert parse_scdet(stderr, duration=6.0)[1] == [2.6, 15.625]


FPS = 25.0


def test_detect_cuts_keeps_isolated_soft_peaks_that_a_fixed_threshold_misses():
    # Dark-scene hard cuts score 5-7: below the old threshold 10, but each is alone in its window.
    scores = [[93.04, 6.6], [93.08, 2.1], [97.36, 5.7], [101.28, 6.8], [101.32, 2.2]]

    cuts, review = detect_cuts(scores, FPS)

    assert cuts == [93.04, 97.36, 101.28]
    assert review == []


def test_detect_cuts_suppresses_a_moving_shot_and_hands_it_to_review():
    # One fast-moving shot: 6-8 point spikes every 0.2 s look like cuts to a fixed threshold.
    burst = [[0.16, 6.6], [0.36, 7.0], [0.44, 8.4], [0.64, 8.1], [0.84, 7.6], [0.92, 7.0], [1.12, 6.8]]
    scores = [*burst, [1.64, 6.7], [1.68, 3.5]]

    cuts, review = detect_cuts(scores, FPS)

    assert cuts == [1.64], "the real cut after the burst is isolated; its own after-frame does not count"
    assert review == [[0.0, 1.32]]


def test_detect_cuts_always_keeps_hard_scores_and_merges_a_cut_spread_over_two_frames():
    scores = [[12.0, 30.0], [12.04, 14.0], [12.2, 25.0], [20.0, 9.9], [20.2, 9.0]]

    cuts, review = detect_cuts(scores, FPS)

    assert cuts == [12.0, 12.2]
    assert review == [[19.8, 20.4]], "two equal soft spikes 0.2 s apart cannot be told apart; the agent looks"


def test_measure_rejects_soft_above_hard():
    with pytest.raises(ValueError):
        measure("missing.mp4", ".", hard=4.0, soft=6.0)


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


@requires_ffmpeg
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

    assert payload["settings"]["detector"] == "scdet-isolated-v1"
    assert payload["shots"]["review_windows"] == []
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

    payload = measure(video, tmp_path, soft=16.0, hard=16.0)
    assert len(calls) == 2, "new score settings re-derive cuts from the cached scores without decoding"
    assert payload["settings"]["soft_score"] == 16.0 and payload["shots"]["cuts"] == []
    assert payload["loudness"]["integrated_lufs"] == -28.0, "loudness comes from the cached decode"
    measure(video, tmp_path, scaled=False)
    assert len(calls) == 4, "a scaling change re-decodes"
    stat = video.stat()
    os.utime(video, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    measure(video, tmp_path, scaled=False)
    assert len(calls) == 6, "an mtime change re-measures"
    video.write_bytes(b"x" * 11)
    measure(video, tmp_path, scaled=False)
    assert len(calls) == 8, "a size change re-measures"


def test_measure_cli_passes_score_settings_and_reports_review_windows(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(lib, "run_cmd", _fake_runner(calls))
    monkeypatch.setattr(reference_measure, "run_cmd", _fake_runner(calls))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")

    assert reference.main(["measure", str(video), "--work-dir", str(tmp_path), "--soft-score", "5",
                           "--hard-score", "12"]) == 0

    settings = json.loads((tmp_path / "reference_measurements.json").read_text(encoding="utf-8"))["settings"]
    assert (settings["soft_score"], settings["hard_score"]) == (5.0, 12.0)
    assert "待复核窗口 0 个" in capsys.readouterr().out
    assert reference.main(["measure", str(video), "--work-dir", str(tmp_path), "--soft-score", "20"]) == 2
