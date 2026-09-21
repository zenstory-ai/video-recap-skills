"""Real independent picture/audio pairing, not copying an already finished AV movie."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'skills/video-assemble/scripts'
sys.path.insert(0, str(SCRIPTS))
import pair_media
from frozen_audio import probe_audio_packets
from subtitles.track_binding import current_bindings, prepare_subtitle_track


pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe unavailable: actual pairing not checked",
)


def run(*args):
    return subprocess.run(list(map(str, args)), capture_output=True, text=True, check=True)


@pytest.fixture
def media(tmp_path):
    picture = tmp_path / 'picture.mp4'
    donor = tmp_path / 'donor.m4a'
    run('ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'testsrc2=size=160x120:rate=24:duration=2', '-an', '-c:v', 'libx264',
        '-threads', '2', '-pix_fmt', 'yuv420p', picture)
    run('ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'sine=frequency=300:sample_rate=48000:duration=2', '-f', 'lavfi', '-i',
        'sine=frequency=900:sample_rate=48000:duration=2', '-map', '0:a', '-map', '1:a',
        '-c:a', 'aac', donor)
    document = {'artifact': 'media_pair', 'schema_version': 1,
                'picture': {'path': str(picture)},
                'audio': {'path': str(donor), 'selected_stream': 1}}
    plan = tmp_path / 'pair.json'
    plan.write_text(json.dumps(document))
    return picture, donor, plan, document


def test_real_pair_selected_audio_copy_picture_and_bound_cues(media, tmp_path):
    picture, donor, plan, _ = media
    target = tmp_path / 'new'
    report = pair_media.run_pair(plan, target)
    output = target / 'paired.mp4'
    assert report['status'] == 'PAIR_RENDERED'
    assert report['output']['path'] == str(output) and output.is_file()
    assert report['plan'] == {'path': str(plan.resolve())}
    assert report['direct_listening'] == 'NOT_CHECKED'
    assert report['normal_speed_review'] == 'NOT_CHECKED'
    assert report['release_approved'] is False
    assert report['picture']['frame_count'] == 48
    assert report['audio']['input_stream'] == 1
    assert report['audio']['output_stream'] == 0
    a = probe_audio_packets(donor, 1)
    b = probe_audio_packets(output, 0)
    assert a['decoder'] == b['decoder'] and a['packets'] == b['packets']
    assert a['packets'][0]['pts'] == '-8/375'  # AAC priming must survive
    assert a['payload_bytes'] != probe_audio_packets(donor, 0)['payload_bytes']
    assert pair_media.probe_picture(picture) == pair_media.probe_picture(output)
    command = json.loads((target / 'mux.command.json').read_text())
    for forbidden in ['-shortest', '-t', '-r', '-af', '-filter_complex', '-itsoffset', '-ar']:
        assert forbidden not in command
    assert command[command.index('-c') + 1] == 'copy'
    binding = current_bindings(output, 0)
    assert binding['picture'] == {'path': str(output.resolve())}
    assert binding['audio'] == {'selected_stream': 0, 'sample_rate': 48000,
                                'packet_count': b['packet_count']}
    track = {'schema_version': 1, 'clock': {'kind': 'output',
              'timebase': {'numerator': 1, 'denominator': 24}, 'duration_ticks': 48},
             'overlap_policy': 'forbid', 'bindings': binding,
             'cues': [{'start_tick': 12, 'end_tick': 24, 'text': 'test',
                       'attribution': {'kind': 'source', 'ref': 'test:1'},
                       'timing_evidence': {'kind': 'legacy_estimate', 'calibration': 'none',
                                           'word_alignment': 'none', 'evidence_refs': []}}]}
    (target / 'subtitle_track.json').write_text(json.dumps(track))
    prepared = prepare_subtitle_track(output, target, 2, audio_mode='adopted-packet-copy')
    assert prepared is not None
    track['bindings']['picture']['path'] = str(picture)
    (target / 'subtitle_track.json').write_text(json.dumps(track))
    with pytest.raises(ValueError):
        prepare_subtitle_track(output, target, 2, audio_mode='adopted-packet-copy')
    track['bindings'] = binding
    track['bindings']['audio']['packet_count'] += 1
    (target / 'subtitle_track.json').write_text(json.dumps(track))
    with pytest.raises(ValueError):
        prepare_subtitle_track(output, target, 2, audio_mode='adopted-packet-copy')


def test_plan_only_existing_directory_and_independent_cli(media, tmp_path):
    _, _, plan, _ = media
    copied = tmp_path / 'installed'
    shutil.copytree(SCRIPTS, copied)
    assert not (copied / 'picture_plan.py').exists()
    output = tmp_path / 'planned'
    launcher = ('import sys,runpy; p=sys.argv.pop(1);sys.path.insert(0,p);'
                'sys.argv[0]=p+"/pair_media.py";runpy.run_path(sys.argv[0],run_name="__main__")')
    result = run(sys.executable, '-I', '-c', launcher, copied, plan,
                 '--output-dir', output, '--plan-only')
    assert result.returncode == 0
    assert json.loads((output / 'pair_run.json').read_text())['status'] == 'PLANNED'
    assert not (output / 'paired.mp4').exists()
    assert not (output / 'mux.command.json').exists()
    with pytest.raises(FileExistsError):
        pair_media.run_pair(plan, output)
    rendered = tmp_path / 'isolated-rendered'
    run(sys.executable, '-I', '-c', launcher, copied, plan, '--output-dir', rendered)
    assert json.loads((rendered / 'pair_run.json').read_text())['status'] == 'PAIR_RENDERED'


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(schema_version=True),
    lambda d: d.update(schema_version=2),
    lambda d: d.update(artifact='anything'),
    lambda d: d.update(unknown=0),
    lambda d: d['picture'].update(path='/nonexistent/picture.mp4'),
    lambda d: d['audio'].pop('selected_stream'),
    lambda d: d['audio'].update(selected_stream=True),
    lambda d: d['audio'].update(selected_stream=-1),
    lambda d: d['audio'].update(selected_stream=3),
    lambda d: d['audio'].update(gain=0.5),
    lambda d: d['picture'].update(path=''),
    lambda d: d['picture'].update(path='https://example.test/media'),
])
def test_invalid_pair_never_publishes(media, tmp_path, mutation):
    _, _, plan, document = media
    mutation(document)
    plan.write_text(json.dumps(document))
    with pytest.raises((ValueError, RuntimeError, KeyError, FileNotFoundError)):
        pair_media.run_pair(plan, tmp_path / 'failed')
    assert not (tmp_path / 'failed/paired.mp4').exists()
    assert json.loads((tmp_path / 'failed/pair_run.json').read_text())['status'] == 'FAILED'


@pytest.mark.parametrize('duration', [1, 3])
def test_short_or_long_audio_rejected(media, tmp_path, duration):
    _, donor, plan, doc = media
    run('ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        f'sine=sample_rate=48000:duration={duration}', '-c:a', 'aac', donor)
    doc['audio'].update(selected_stream=0)
    plan.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='interval'):
        pair_media.run_pair(plan, tmp_path / 'bad_duration')


def test_non_aac_rejected(media, tmp_path):
    _, _, plan, doc = media
    wav = tmp_path / 'pcm.wav'
    run('ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=duration=2', wav)
    doc['audio'] = {'path': str(wav), 'selected_stream': 0}
    plan.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='AAC'):
        pair_media.run_pair(plan, tmp_path / 'not_aac')


@pytest.mark.parametrize('mutation', ['mux_fail', 'corrupt_audio', 'corrupt_picture'])
def test_corrupt_or_failed_mux_never_publishes(media, tmp_path, monkeypatch, mutation):
    _picture, _donor, plan, _ = media
    original = pair_media._run_mux
    def changed(command, directory):
        if mutation == 'mux_fail':
            raise RuntimeError('simulated mux failure')
        original(command, directory)
        if mutation.startswith('corrupt_'):
            staged = directory / 'paired.rendering.mp4'
            source = directory / 'before.mp4'
            staged.rename(source)
            args = ['ffmpeg', '-v', 'error', '-y', '-i', source, '-map', '0:v:0', '-map', '0:a:0']
            args += ['-c:v', 'copy', '-c:a', 'aac', '-af', 'volume=0.5'] if mutation == 'corrupt_audio' else [
                '-c:a', 'copy', '-c:v', 'libx264', '-vf', 'hflip', '-threads', '2']
            run(*args, staged)
    monkeypatch.setattr(pair_media, '_run_mux', changed)
    output = tmp_path / 'interrupted'
    with pytest.raises((ValueError, RuntimeError)):
        pair_media.run_pair(plan, output)
    assert not (output / 'paired.mp4').exists()
    assert json.loads((output / 'pair_run.json').read_text())['status'] == 'FAILED'


@pytest.mark.parametrize('mutation', ['missing_time', 'oversized', 'zero', 'gap', 'offset', 'end_drift'])
def test_timing_metadata_cannot_expand_tolerance(media, tmp_path, monkeypatch, mutation):
    _, donor, plan, _ = media
    data = probe_audio_packets(donor, 1)
    if mutation == 'missing_time':
        data['start_time'] = None
    elif mutation == 'oversized':
        data['packets'][2]['duration'] = '5/1'
    elif mutation == 'zero':
        data['packets'][2]['duration'] = '0/1'
    elif mutation == 'gap':
        data['packets'][2]['pts'] = '99/1'
    elif mutation == 'offset':
        data['start_time'] = '1'
    elif mutation == 'end_drift':
        data['start_time'] = '0.03'
        data['duration'] = '2.03'  # Each alone < 1 frame; end drift exceeds it
    monkeypatch.setattr(pair_media, 'probe_audio_packets', lambda *_: copy.deepcopy(data))
    with pytest.raises(ValueError):
        pair_media.run_pair(plan, tmp_path / 'timing')


def test_audio_donor_unrelated_video_ignored(media, tmp_path):
    picture, donor, plan, doc = media
    av = tmp_path / 'unrelated.mp4'
    run('ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=c=red:size=160x120:rate=24:duration=3', '-i', donor,
        '-map', '0:v:0', '-map', '1:a:1', '-c:v', 'libx264', '-threads', '2', '-c:a', 'copy', av)
    doc['audio'] = {'path': str(av), 'selected_stream': 0}
    plan.write_text(json.dumps(doc))
    output = tmp_path / 'donor_video'
    pair_media.run_pair(plan, output)
    assert pair_media.probe_picture(picture) == pair_media.probe_picture(output / 'paired.mp4')


@pytest.mark.parametrize('mutation', ['interior', 'offset', 'missing_start', 'duration', 'codec'])
def test_complete_picture_clock_and_decoder_required(media, tmp_path, monkeypatch, mutation):
    _, _, plan, _ = media
    original = pair_media._probe
    def changed(path, *args):
        data = original(path, *args)
        if '-show_frames' in args and mutation == 'interior':
            data['frames'][10]['pts'] += 1
        if '-show_streams' in args:
            v = data['streams'][0]
            if mutation == 'offset':
                v['start_pts'] = 1
            elif mutation == 'missing_start':
                v.pop('start_pts', None)
            elif mutation == 'duration':
                v['duration_ts'] += 1
            elif mutation == 'codec':
                v['codec_name'] = 'vp9'
        return data
    monkeypatch.setattr(pair_media, '_probe', changed)
    with pytest.raises(ValueError):
        pair_media.run_pair(plan, tmp_path / 'bad_clock')


def test_uniform_packet_shift_cannot_hide_behind_stream_headers(media, tmp_path, monkeypatch):
    from fractions import Fraction
    _, donor, plan, _ = media
    data = probe_audio_packets(donor, 1)
    for packet in data['packets']:
        for field in ['pts', 'dts']:
            packet[field] = str(Fraction(packet[field]) + 100)
    monkeypatch.setattr(pair_media, 'probe_audio_packets', lambda *_: copy.deepcopy(data))
    with pytest.raises(ValueError, match='packet.*interval'):
        pair_media.run_pair(plan, tmp_path / 'uniform_packet_shift')


@pytest.mark.parametrize('change', ['start_time', 'duration'])
def test_output_stream_interval_revalidated_before_publish(media, tmp_path, monkeypatch, change):
    _, _, plan, _ = media
    original = pair_media.verify_adopted_audio
    def changed(*args):
        proof = original(*args)
        proof['output'][change] = '9'
        return proof
    monkeypatch.setattr(pair_media, 'verify_adopted_audio', changed)
    output = tmp_path / 'bad_output_header'
    with pytest.raises(ValueError):
        pair_media.run_pair(plan, output)
    assert not (output / 'paired.mp4').exists()
    assert json.loads((output / 'pair_run.json').read_text())['status'] == 'FAILED'
