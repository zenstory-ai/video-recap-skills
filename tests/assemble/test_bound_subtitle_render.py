"""Actual output-clock cue consumption, independent of ASR accuracy claims."""
import json
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills' / 'video-assemble' / 'scripts'))
import subtitle_track_binding as binding
import source_subtitles
import subtitle_render


def _media(path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg/ffprobe unavailable: real cue render not checked')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'color=black:s=320x240:r=24:d=6', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=6', '-c:v', 'libx264', '-preset',
                    'ultrafast', '-c:a', 'aac', str(path)], check=True, capture_output=True)


def _track(video):
    return {
        'schema_version': 1, 'overlap_policy': 'forbid',
        'clock': {'kind': 'output', 'timebase': {'numerator': 1, 'denominator': 24}, 'duration_ticks': 144},
        'bindings': binding.current_bindings(video, 0),
        'cues': [{'start_tick': 106, 'end_tick': 140, 'text': 'BOUNDARY',
                  'attribution': {'kind': 'source', 'ref': 'known-synthetic-cue'},
                  'timing_evidence': {'kind': 'legacy_estimate', 'evidence_refs': [],
                                      'calibration': 'none', 'word_alignment': 'none'}}],
    }


def _write_track(work, track):
    (work / 'subtitle_track.json').write_text(json.dumps(track))


def test_bound_entries_bypass_legacy_splitting_and_preserve_declared_frames(tmp_path):
    video = tmp_path / 'input.mp4'
    _media(video)
    _write_track(tmp_path, _track(video))
    prepared = binding.prepare_subtitle_track(video, tmp_path, 6, audio_mode='adopted-packet-copy')
    assert prepared['metadata']['timing_evidence_kinds'] == ['legacy_estimate']
    entries = source_subtitles._combined_subtitle_entries([], tmp_path, 6)
    assert [(x['start'], x['end'], x['text']) for x in entries] == [(106 / 24, 140 / 24, 'BOUNDARY')]
    assert all(not (e['start'] <= 4 < e['end']) for e in entries)
    assert entries[0]['start'] <= 106 / 24 < entries[0]['end']


@pytest.mark.parametrize('change', ['track', 'video'])
def test_prepared_track_cannot_be_reused_after_input_changes(tmp_path, change):
    video = tmp_path / 'input.mp4'
    _media(video)
    track = _track(video)
    _write_track(tmp_path, track)
    binding.prepare_subtitle_track(video, tmp_path, 6, audio_mode='adopted-packet-copy')
    if change == 'track':
        track['cues'][0]['start_tick'] = 83
        _write_track(tmp_path, track)
    else:
        with video.open('ab') as f:
            f.write(b'changed')
    with pytest.raises(ValueError, match='stale'):
        source_subtitles._combined_subtitle_entries([], tmp_path, 6)


def test_explicit_track_without_current_preparation_never_falls_back(tmp_path):
    _write_track(tmp_path, {})
    with pytest.raises(ValueError, match='prepare'):
        source_subtitles._combined_subtitle_entries([], tmp_path, 6)


@pytest.mark.parametrize('mode', ['narration', 'source-mix'])
def test_unbound_new_mix_rejected_before_render(tmp_path, mode):
    _write_track(tmp_path, {})
    with pytest.raises(ValueError, match='adopted-packet-copy'):
        binding.prepare_subtitle_track('not-read.mp4', tmp_path, 6, audio_mode=mode)


def test_absent_track_keeps_legacy_consumer(tmp_path):
    assert binding.prepare_subtitle_track('not-read.mp4', tmp_path, 6, audio_mode='narration') is None
    assert source_subtitles._combined_subtitle_entries([], tmp_path, 6) == []


def test_actual_burn_starts_on_declared_frame_not_coarse_window(tmp_path):
    video = tmp_path / 'input.mp4'
    _media(video)
    _write_track(tmp_path, _track(video))
    binding.prepare_subtitle_track(video, tmp_path, Fraction(144, 24), audio_mode='adopted-packet-copy')
    ass = subtitle_render._generate_ass([], tmp_path, 6, {'width': 320, 'height': 240})
    # Raw RGB checks actual libass burn. Whole plain frame is black until cue106.
    cmd = ['ffmpeg', '-v', 'error', '-i', str(video), '-vf',
           f"subtitles={ass},select='eq(n,96)+eq(n,105)+eq(n,106)+eq(n,140)'",
           '-fps_mode', 'passthrough', '-an', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    size = 320 * 240 * 3
    assert len(raw) == 4 * size
    before, last_before, on, off = [raw[i:i + size] for i in range(0, len(raw), size)]
    assert before == last_before == off
    assert on != before


@pytest.mark.parametrize('rate', [24, 25, 30, 60])
def test_ass_quantization_changes_at_each_actual_frame(rate):
    frames = [Fraction(n, rate) for n in range(2 * rate)]
    for i, pts in enumerate(frames):
        value = binding._project_boundary(pts, frames, Fraction(2))['ass_time']
        hours, minutes, seconds = value.split(':')
        threshold_ms = (int(hours) * 3600 + int(minutes) * 60) * 1000 + int(Fraction(seconds) * 1000)
        assert threshold_ms <= int(pts * 1000)
        if i:
            assert int(frames[i - 1] * 1000) < threshold_ms


def test_dense_frame_clock_fails_when_ass_cannot_represent_boundary():
    with pytest.raises(ValueError, match='ASS 10ms'):
        binding._project_boundary(Fraction(1, 240), [Fraction(n, 240) for n in range(240)], Fraction(1))


def test_between_frame_cue_is_rejected_instead_of_silently_invisible(tmp_path):
    video = tmp_path / 'input.mp4'
    _media(video)
    track = _track(video)
    track['clock']['timebase'] = {'numerator': 1, 'denominator': 48000}
    track['clock']['duration_ticks'] = 288000
    track['cues'][0].update(start_tick=200001, end_tick=200002)
    _write_track(tmp_path, track)
    with pytest.raises(ValueError, match='visible frame'):
        binding.prepare_subtitle_track(video, tmp_path, 6, audio_mode='adopted-packet-copy')


def test_deleting_explicit_track_clears_old_manifest_evidence(tmp_path):
    (tmp_path / binding.VALIDATION).write_text('{"old":true}')
    assert binding.prepare_subtitle_track('not-read.mp4', tmp_path, 6, audio_mode='narration') is None
    assert not (tmp_path / binding.VALIDATION).exists()


def test_old_projector_version_is_not_reusable(tmp_path):
    video = tmp_path / 'input.mp4'
    _media(video)
    _write_track(tmp_path, _track(video))
    binding.prepare_subtitle_track(video, tmp_path, 6, audio_mode='adopted-packet-copy')
    path = tmp_path / binding.VALIDATION
    record = json.loads(path.read_text())
    record['projector_version'] = -1
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='version'):
        source_subtitles._combined_subtitle_entries([], tmp_path, 6)


def test_unaligned_ticks_record_resolved_frame_and_share_consumer_times(tmp_path):
    video = tmp_path / 'input.mp4'
    _media(video)
    track = _track(video)
    track['clock']['timebase'] = {'numerator': 1, 'denominator': 1000}
    track['clock']['duration_ticks'] = 6000
    track['cues'][0].update(start_tick=4401, end_tick=5801)
    _write_track(tmp_path, track)
    loaded = binding.prepare_subtitle_track(video, tmp_path, 6, audio_mode='adopted-packet-copy')
    entry = loaded['entries'][0]
    projection = entry['frame_projection']
    assert projection['start']['requested_time'] == '4401/1000'
    assert projection['start']['frame_index'] == 106
    assert Fraction(projection['start']['pts']) == Fraction(106, 24)
    assert Fraction(projection['start']['delta']) == Fraction(106, 24) - Fraction(4401, 1000)
    assert entry['start'] == 106 / 24
    assert entry['end'] == 140 / 24
    assert projection['requested_ticks'] == [4401, 5801]


def test_actual_assembly_burn_and_output_clock_are_verified(tmp_path, monkeypatch):
    from assemble import assemble_video
    from lib import CONFIG
    video = tmp_path / 'input.mp4'
    work = tmp_path / 'work'
    work.mkdir()
    _media(video)
    _write_track(work, _track(video))
    monkeypatch.setitem(CONFIG, 'bgm_path', '')
    monkeypatch.setitem(CONFIG, 'burn_subtitles', True)
    monkeypatch.setitem(CONFIG, 'mask_source_subtitles', False)
    monkeypatch.setitem(CONFIG, 'output_max_height', 0)
    output = work / 'output.mp4'
    assemble_video(video, [], work, output, audio_mode='adopted-packet-copy')
    report = json.loads((work / binding.VALIDATION).read_text())
    assert report['rendered_picture']['frame_clock_verified'] is True
    assert report['rendered_picture']['frame_count'] == 144
    assert report['binding']['acoustic_alignment'] == 'NOT_CHECKED'
    from assembly_contract import _assembly_manifest_payload
    from assembly_settings import assembly_settings_payload
    manifest = _assembly_manifest_payload(video, [], work, output,
        settings_payload=assembly_settings_payload, audio_mode='adopted-packet-copy')
    assert manifest['subtitle_track']['rendered_picture']['frame_clock_verified'] is True
    # A different output clock is a failure even when source/track are unchanged.
    short = tmp_path / 'short.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(output), '-t', '5', '-c', 'copy', str(short)], check=True)
    with pytest.raises(ValueError, match='frame clock'):
        binding.verify_rendered_picture(work, short)


def test_bound_srt_quantization_does_not_delay_first_frame(tmp_path):
    video = tmp_path / 'input.mp4'
    _media(video)
    _write_track(tmp_path, _track(video))
    binding.prepare_subtitle_track(video, tmp_path, 6, audio_mode='adopted-packet-copy')
    srt = subtitle_render._generate_srt([], tmp_path, 6).read_text()
    assert '00:00:04,416 --> 00:00:05,833' in srt
