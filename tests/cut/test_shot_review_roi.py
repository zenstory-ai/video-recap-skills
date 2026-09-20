"""Detect picture cuts without letterbox dilution or packaging-only changes."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills/video-cut/scripts'))
import shot_review

ROI = [39, 101, 32, 32]  # Odd offsets must not be silently chroma-rounded.
CUTS = [48, 50, 100, 112, 180, 194]


@pytest.fixture
def make_video(tmp_path):
    if not (shutil.which('ffmpeg') and shutil.which('ffprobe')):
        pytest.skip('ffmpeg/ffprobe required for ROI scene tests')

    def make(name, *, graphics_only=False):
        path = tmp_path / f'{name}.mkv'
        runs = [48, 2, 50, 12, 68, 14, 46]
        colors = [0, 255, 0, 255, 0, 255, 0]
        raw = bytearray()
        x, y, width, height = ROI
        for count, color in zip(runs, colors):
            frame = bytearray([color if graphics_only else 0]) * (128 * 256 * 3)
            patch = bytes([90 if graphics_only else color]) * width * 3
            for row in range(y, y + height):
                offset = (row * 128 + x) * 3
                frame[offset:offset + width * 3] = patch
            raw.extend(frame * count)
        subprocess.run([
            'ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
            '-s', '128x256', '-r', '24', '-i', '-', '-c:v', 'ffv1', '-threads', '1', str(path),
        ], input=raw, check=True, capture_output=True)
        return path
    return make


def test_roi_recovers_known_short_shots_hidden_by_large_black_canvas(make_video):
    video = make_video('small_picture')
    original = shot_review.sha256_file(video)
    full = shot_review.scan_video(video, threshold=0.35)
    assert full['candidates'] == []
    report = shot_review.scan_video(video, threshold=0.35, roi=ROI)
    assert [c['frame'] for c in report['candidates']] == CUTS
    assert [s['frame_count'] for s in report['short_spans']] == [2, 12, 14]
    assert report['scene_roi'] == ROI
    assert report['media']['frame_clock_sha256'] == full['media']['frame_clock_sha256']
    assert report['media']['frame_count'] == 240
    assert report['normal_speed_review'] == 'NOT_CHECKED'
    assert shot_review.sha256_file(video) == original


def test_roi_excludes_packaging_changes_without_claiming_full_picture_pass(make_video):
    video = make_video('graphics', graphics_only=True)
    full = shot_review.scan_video(video, threshold=0.35)
    assert [c['frame'] for c in full['candidates']] == CUTS
    report = shot_review.scan_video(video, threshold=0.35, roi=ROI)
    assert report['candidates'] == []
    assert report['status'] == 'NO_CANDIDATES'
    assert report['normal_speed_review'] == 'NOT_CHECKED'
    assert report['scene_roi'] == ROI


@pytest.mark.parametrize('roi', [[], [0, 0, 32], [0, 0, 32, 32, 1],
                                 [-1, 0, 32, 32], [0, -1, 32, 32],
                                 [0, 0, 0, 32], [0, 0, 32, -1],
                                 [True, 0, 32, 32], [0, 0, 32.0, 32],
                                 '0,0,32,32'])
def test_bad_roi_fails_before_media_is_read(tmp_path, roi):
    with pytest.raises(ValueError):
        shot_review.scan_video(tmp_path / 'absent.mp4', roi=roi)


@pytest.mark.parametrize('roi', [[100, 101, 32, 32], [39, 230, 32, 32], [0, 0, 129, 32]])
def test_crop_must_not_silently_clamp_out_of_bounds(make_video, tmp_path, roi):
    video = make_video('bounds')
    output = tmp_path / 'scan.json'
    with pytest.raises(ValueError):
        shot_review.write_scan(video, output, roi=roi)
    failed = json.loads(output.read_text())
    assert failed['status'] == 'SCAN_FAILED'
    assert failed['scene_roi'] == roi
    assert failed['scene_threshold'] == 0.35


@pytest.mark.parametrize('roi', [None, (1, 3, 5, 7)])
def test_failed_decode_keeps_requested_scan_scope_and_never_leaves_old_success(tmp_path, monkeypatch, roi):
    output = tmp_path / 'failed.json'
    output.write_text('{"schema_version":1,"artifact":"shot_review","status":"NO_CANDIDATES"}')
    region = list(roi) if roi is not None else None

    def fail_decode(*args, **kwargs):
        scanning = json.loads(output.read_text())
        assert scanning['status'] == 'SCANNING'
        assert scanning['scene_roi'] == region
        assert scanning['scene_threshold'] == 0.12
        raise RuntimeError('scene decode failed')

    monkeypatch.setattr(shot_review, 'scan_video', fail_decode)
    with pytest.raises(RuntimeError, match='scene decode failed'):
        shot_review.write_scan(tmp_path / 'video.mp4', output, threshold=0.12, roi=roi)
    failed = json.loads(output.read_text())
    assert failed['status'] == 'SCAN_FAILED'
    assert failed['normal_speed_review'] == 'NOT_CHECKED'
    assert failed['scene_roi'] == region
    assert failed['scene_threshold'] == 0.12


def test_public_cli_records_region_and_preserves_real_frame_indices(make_video, tmp_path):
    video = make_video('cli')
    output = tmp_path / 'cli.json'
    result = subprocess.run([sys.executable, str(Path(shot_review.__file__)), str(video),
                             '--output', str(output), '--roi', *map(str, ROI)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert report['scene_roi'] == ROI
    assert [c['frame'] for c in report['candidates']] == CUTS


def test_rotated_video_uses_auto_oriented_native_pixel_coordinates(make_video, tmp_path):
    source = make_video('rotate_source')
    encoded, rotated = tmp_path / 'encoded.mp4', tmp_path / 'rotated.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-c:v', 'libx264',
                    '-qp', '0', '-pix_fmt', 'yuv444p', '-threads', '1', str(encoded)],
                   check=True, capture_output=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-display_rotation:v:0', '90',
                    '-i', str(encoded), '-c', 'copy', str(rotated)],
                   check=True, capture_output=True)
    # Coded 128x256, FFmpeg rotates counterclockwise to 256x128.
    oriented_roi = [101, 128 - (39 + 32), 32, 32]
    report = shot_review.scan_video(rotated, roi=oriented_roi)
    assert [c['frame'] for c in report['candidates']] == CUTS
    assert report['scene_roi'] == oriented_roi
    with pytest.raises(ValueError):
        shot_review.scan_video(rotated, roi=[0, 129, 32, 32])
