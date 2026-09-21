"""Independent real-PCM checks for adopted score and source-bed boundaries."""

from array import array
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'skills/video-assemble/scripts'
sys.path.insert(0, str(SCRIPTS))
import source_score  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (shutil.which('ffmpeg') and shutil.which('ffprobe')),
    reason='actual source-score PCM requires ffmpeg/ffprobe',
)


def run(*args):
    return subprocess.run(list(map(str, args)), capture_output=True, check=True)


def pcm(path):
    raw = run('ffmpeg', '-v', 'error', '-i', path, '-map', '0:a:0',
              '-ar', '48000', '-ac', '2', '-f', 'f32le', '-').stdout
    samples = array('f')
    samples.frombytes(raw)
    if sys.byteorder != 'little':
        samples.byteswap()
    return samples


def score_file(tmp_path, codec='pcm_s24le', left=.0625, right=-.125):
    path = tmp_path / 'adopted_score.wav'
    run('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        f'aevalsrc={left}|{right}:s=48000:d=1', '-c:a', codec, path)
    return path


def score_only(tmp_path, score):
    return {
        'artifact': 'source_score_plan', 'schema_version': 1,
        'output': {'sample_rate': 48000, 'channels': 2, 'total_samples': 48000},
        'source_segments': [],
        'source_silence': [{'output_start_sample': 0, 'output_end_sample': 48000,
                            'role': 'silence'}],
        'score': {'kind': 'frozen', 'path': str(score), 'audio_stream': 0},
    }


def prepare(tmp_path, document, name='result'):
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(document))
    target = tmp_path / name
    return source_score.prepare_source_score(path, target), target


@pytest.mark.parametrize('codec', ['pcm_s16le', 'pcm_s24le', 'pcm_f32le'])
def test_adopted_pcm_score_keeps_stereo_and_whole_samples(tmp_path, codec):
    score = score_file(tmp_path, codec)
    _, out = prepare(tmp_path, score_only(tmp_path, score))
    expected = pcm(score)
    assert len(expected) == 96000
    assert expected == pcm(out / 'score_bed.wav') == pcm(out / 'prepared_bed.wav')
    assert max(abs(value) for value in pcm(out / 'source_bed.wav')) == 0
    assert expected[0] == .0625 and expected[1] == -.125


def test_prepared_float_preserves_headroom_without_normalization(tmp_path):
    score = score_file(tmp_path, 'pcm_f32le', left=1.25, right=-1.125)
    receipt, out = prepare(tmp_path, score_only(tmp_path, score))
    actual = pcm(out / 'prepared_bed.wav')
    assert actual == pcm(score)
    assert max(actual) == 1.25 and min(actual) == -1.125
    assert receipt['direct_listening'] == 'NOT_CHECKED'
    assert receipt['release_approved'] is False


@pytest.mark.parametrize('field,value', [('gain', .5), ('source_offset_sample', 24),
                                        ('fade_out_samples', 96)])
def test_frozen_score_cannot_be_silently_processed(tmp_path, field, value):
    score = score_file(tmp_path)
    plan = score_only(tmp_path, score)
    plan['score'][field] = value
    with pytest.raises(ValueError):
        prepare(tmp_path, plan)
    assert not (tmp_path / 'result/prepared_bed_receipt.json').exists()
    assert not (tmp_path / 'result/prepared_bed.wav').exists()


def test_float_score_nan_is_not_publishable(tmp_path):
    score = score_file(tmp_path, 'pcm_f32le')
    raw = bytearray(score.read_bytes())
    # Locate actual RIFF data payload rather than assuming a 44-byte WAV header.
    offset = 12
    while raw[offset:offset + 4] != b'data':
        size = int.from_bytes(raw[offset + 4:offset + 8], 'little')
        offset += 8 + size + (size % 2)
    raw[offset + 8:offset + 12] = b'\x00\x00\xc0\x7f'
    score.write_bytes(raw)
    with pytest.raises((ValueError, RuntimeError)):
        prepare(tmp_path, score_only(tmp_path, score))
    assert not (tmp_path / 'result/prepared_bed_receipt.json').exists()


def test_source_fade_has_inclusive_sample_endpoints(tmp_path):
    source = tmp_path / 'source.mov'
    run('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'color=black:s=32x32:r=24:d=1', '-f', 'lavfi', '-i',
        'aevalsrc=0.25|-0.5:s=48000:d=1', '-c:v', 'libx264', '-threads', '2',
        '-c:a', 'pcm_s16le', source)
    score = score_file(tmp_path, 'pcm_f32le', 0, 0)
    plan = score_only(tmp_path, score)
    plan['source_silence'] = []
    plan['source_segments'] = [{
        'id': 'source', 'path': str(source), 'audio_stream': 0,
        'source_fps': '24/1', 'source_start_frame': 0, 'source_end_frame': 24,
        'output_start_sample': 0, 'gain': 1, 'role': 'protected_original',
        'fade_in_samples': 96, 'fade_out_samples': 12000, 'fade_shape': 'linear',
    }]
    _, out = prepare(tmp_path, plan)
    actual = pcm(out / 'source_bed.wav')
    assert len(actual) == 96000
    assert actual[:2] == array('f', [0, 0])
    assert actual[-2:] == array('f', [0, 0])
    assert actual[190:194] == array('f', [.25, -.5, .25, -.5])
    for i in [1, 47, 94, 95, 35999, 36000, 40000, 47998, 47999]:
        gain = i / 95 if i < 96 else ((47999 - i) / 11999 if i >= 36000 else 1)
        assert actual[2*i] == pytest.approx(.25 * gain, abs=1e-7)
        assert actual[2*i+1] == pytest.approx(-.5 * gain, abs=1e-7)


def test_raw_half_cosine_changes_only_the_declared_end_windows(tmp_path):
    score = score_file(tmp_path, 'pcm_f32le', .25, -.5)
    plan = score_only(tmp_path, score)
    plan['score'].update(kind='raw', source_offset_sample=0, gain=.5,
                         fade_in_samples=4800, fade_out_samples=12000,
                         fade_shape='half_cosine')
    _, out = prepare(tmp_path, plan)
    actual = pcm(out / 'score_bed.wav')
    assert actual[:2] == array('f', [0, 0])
    assert actual[-2:] == array('f', [0, 0])
    # A cosine must not keep cycling outside its fade window; both channels matter.
    for i in [4799, 4800, 5000, 15000, 23999, 30000, 35999, 36000]:
        assert actual[2*i] == pytest.approx(.125, abs=1e-7)
        assert actual[2*i+1] == pytest.approx(-.25, abs=1e-7)
    import math
    for i in [2400, 42000]:
        position = i / 4799 if i < 4800 else (47999-i) / 11999
        gain = .5-.5*math.cos(math.pi*position)
        assert actual[2*i] == pytest.approx(.125*gain, abs=1e-7)
        assert actual[2*i+1] == pytest.approx(-.25*gain, abs=1e-7)


def test_half_open_reorder_quiet_gain_and_continuous_score_sample_by_sample(tmp_path):
    import wave

    raw = tmp_path / 'source.wav'
    samples = array('h')
    for i in range(88200):
        samples.extend([((i * 31) % 12001) - 6000, ((i * 17) % 8009) - 4000])
    if sys.byteorder != 'little':
        samples.byteswap()
    with wave.open(str(raw), 'wb') as output:
        output.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
        output.writeframes(samples.tobytes())
    source = tmp_path / 'source.mov'
    run('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'color=black:s=32x32:r=24:d=2', '-i', raw, '-map', '0:v:0', '-map', '1:a:0',
        '-c:v', 'libx264', '-threads', '2', '-c:a', 'pcm_s16le', source)
    score = tmp_path / 'score.wav'
    run('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'aevalsrc=n/768000|-n/384000:s=48000:d=3', '-c:a', 'pcm_f32le', score)
    plan = score_only(tmp_path, score)
    plan['output']['total_samples'] = 96000
    plan['source_silence'] = []
    low_gain = 0.012529680840681807
    plan['source_segments'] = [{
        'id': str(i), 'path': str(source), 'audio_stream': 0,
        'source_fps': '24/1', 'source_start_frame': start,
        'source_end_frame': start + 24, 'output_start_sample': i*48000,
        'gain': gain, 'role': role, 'fade_in_samples': 0,
        'fade_out_samples': 0, 'fade_shape': 'linear',
    } for i, (start, gain, role) in enumerate([
        (24, 1, 'protected_original'), (0, low_gain, 'mixed_original_under_narration')])]
    plan['score'].update(kind='raw', source_offset_sample=5000, gain=.25,
                         fade_in_samples=0, fade_out_samples=0, fade_shape='linear')
    _, out = prepare(tmp_path, plan)
    canonical = pcm(source)
    expected = list(canonical[96000:192000]) + [v*low_gain for v in canonical[:96000]]
    actual_source = pcm(out / 'source_bed.wav')
    assert len(actual_source) == len(expected) == 192000
    assert max(abs(a-b) for a, b in zip(actual_source, expected)) < 1e-8
    # Check the two stereo samples on both sides of the exact source-order change.
    assert list(actual_source[95996:96004]) == pytest.approx(expected[95996:96004], abs=1e-8)
    expected_score = [v*.25 for v in pcm(score)[10000:202000]]
    actual_score = pcm(out / 'score_bed.wav')
    assert list(actual_score) == expected_score  # nonperiodic, restart would fail
    mixed = pcm(out / 'prepared_bed.wav')
    assert len(mixed) == len(actual_source)
    assert max(abs(m-a-b) for m, a, b in zip(mixed, actual_source, actual_score)) < 1e-7
