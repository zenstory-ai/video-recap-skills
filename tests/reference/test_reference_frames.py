import json
import shutil
import subprocess

import pytest

import reference
from reference_frames import CELL_WIDTH, CELLS, longest_rows, review_rows, span_rows


requires_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not available"
)


def test_span_rows_put_twelve_cells_per_row_with_their_times():
    rows = span_rows(0.0, 30.0, 2.0)

    assert [len(r["times"]) for r in rows] == [12, 4]
    assert rows[0]["times"][:3] == [0.0, 2.0, 4.0]
    assert rows[1]["times"][-1] == 30.0


def test_span_rows_reject_an_empty_span():
    with pytest.raises(ValueError):
        span_rows(5.0, 5.0, 1.0)


def test_review_rows_show_every_frame_of_each_window(measurements):
    measurements["shots"]["review_windows"] = [[0.0, 0.6], [20.0, 20.2]]

    rows = review_rows(measurements, 25.0)

    assert [len(r["times"]) for r in rows] == [12, 4, 6]
    assert rows[0]["times"][1] == 0.04
    assert rows[2]["label"] == "待复核 20.0–20.2s"


def test_longest_rows_sample_inside_the_longest_shots_in_time_order(measurements):
    # shots: 0-5, 5-10, 10-12, 12-20.2, 20.2-30, 30-31, 31-40.4, 40.4-50, 50-60
    rows = longest_rows(measurements, 2)

    assert [r["label"].split("（")[0] for r in rows] == ["镜头 20.20–30.00s", "镜头 50.00–60.00s"]
    assert all(len(r["times"]) == CELLS for r in rows)
    assert rows[0]["times"][0] == 20.3 and rows[0]["times"][-1] == 29.9


def _two_shots(tmp_path, red_s=2):
    """Red for `red_s` seconds, then blue for 2 s."""
    video = tmp_path / "two_shots.mkv"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=red:s=320x180:r=25:d={red_s}",
        "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25:d=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-c:v", "ffv1", str(video),
    ], check=True)
    return video


def _cell_rgb(page, row, col):
    """RGB at the centre of one 160x90 cell of a sheet page."""
    x, y = col * CELL_WIDTH + CELL_WIDTH // 2, row * 90 + 45
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(page), "-vf",
                          f"format=rgb24,crop=2:2:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return tuple(raw[:3])


@requires_ffmpeg
def test_longest_sheet_cells_show_the_frame_at_their_printed_time_not_the_next_shot(tmp_path):
    # A 12 s shot sampled at ~1.07 s steps: nearest-frame sampling filled the last cell (11.9 s)
    # with a frame from after the cut.
    video = _two_shots(tmp_path, red_s=12)
    (tmp_path / "reference_measurements.json").write_text(json.dumps({
        "source": {"duration_s": 14.0, "fps": 25.0}, "shots": {"cuts": [12.0], "review_windows": []}}),
        encoding="utf-8")

    assert reference.main(["frames", str(video), "--work-dir", str(tmp_path), "--longest", "2"]) == 0

    page = tmp_path / "reference_frames" / "longest_1.jpg"
    red, blue = _cell_rgb(page, 0, CELLS - 1), _cell_rgb(page, 1, 0)
    assert red[0] > 200 and red[2] < 60, f"the last cell of the red shot (11.9 s) shows {red}"
    assert blue[2] > 200 and blue[0] < 60, f"the first cell of the blue shot (12.1 s) shows {blue}"


@requires_ffmpeg
def test_frames_cli_renders_a_contact_sheet_page_and_prints_the_legend(tmp_path, capsys):
    video = _two_shots(tmp_path)

    assert reference.main(["frames", str(video), "--work-dir", str(tmp_path), "--span", "1.6,2.2",
                           "--step", "0.04"]) == 0

    page = tmp_path / "reference_frames" / "span_1.jpg"
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "json",
                            str(page)], capture_output=True, text=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["width"] == CELLS * CELL_WIDTH
    assert stream["height"] == 2 * 90, "16 cells make two rows of 160x90 thumbnails"
    assert _cell_rgb(page, 1, 3)[2] > 200 and max(_cell_rgb(page, 1, 4)) < 40, "row 2 holds 4 frames, then blanks"
    out = capsys.readouterr().out
    assert str(page) in out and "第 2 行" in out and "2.2" in out


def test_frames_review_needs_measurements_first(tmp_path):
    assert reference.main(["frames", str(tmp_path / "v.mp4"), "--work-dir", str(tmp_path), "--review"]) == 2


@pytest.mark.parametrize("argv", [["--longest", "0"], ["--span", "3,1"], ["--span", "x"]])
def test_frames_rejects_bad_ranges_with_exit_2(tmp_path, argv):
    assert reference.main(["frames", str(tmp_path / "v.mp4"), "--work-dir", str(tmp_path), *argv]) == 2
