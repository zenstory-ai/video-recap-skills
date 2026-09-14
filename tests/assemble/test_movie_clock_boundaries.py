"""A fractional-second AAC interval must survive producing, pairing and packaging."""

from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'skills/video-assemble/scripts'
sys.path.insert(0, str(SCRIPTS))
import assemble  # noqa: E402
import compose_foreground  # noqa: E402
import pair_media  # noqa: E402
import source_score  # noqa: E402
import lib  # noqa: E402
from frozen_audio import probe_audio_packets, verify_adopted_audio  # noqa: E402
from lib import CONFIG  # noqa: E402
from test_explicit_audio_mix import explicit_case, _quiet  # noqa: E402, F401

pytestmark = pytest.mark.skipif(
    not (shutil.which('ffmpeg') and shutil.which('ffprobe')),
    reason='ffmpeg and ffprobe are needed for fractional movie clocks',
)


def run(*args):
    subprocess.run(list(map(str, args)), check=True, capture_output=True)


def identity(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def assert_sample_clock(video):
    audio = probe_audio_packets(video, 0)
    last = audio['packets'][-1]
    end = Fraction(last['pts']) + Fraction(last['duration'])
    header_end = Fraction(audio['start_time']) + Fraction(audio['duration'])
    assert abs(end - header_end) <= Fraction(1, audio['sample_rate']), (
        end, header_end, audio['sample_rate'],
    )
    pair_media.validate_pair_timing(pair_media.probe_picture(video), audio)


def base_media(tmp_path, rate):
    video = tmp_path / 'fractional.mp4'
    run('ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=blue:size=64x48:rate=24:duration=1.333333333333',
        '-f', 'lavfi', '-i', f'sine=sample_rate={rate}:duration=2',
        '-af', f'atrim=end_sample={rate * 4 // 3}',
        '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p',
        '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709',
        '-color_trc', 'bt709', '-x264-params',
        'colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited',
        '-c:a', 'aac', '-movie_timescale', rate, video)
    assert pair_media.probe_picture(video)['frame_count'] == 32
    assert_sample_clock(video)
    return video


@pytest.mark.parametrize('rate', [48000, 44100])
@pytest.mark.parametrize('operation', ['pair', 'adopted_assemble', 'compose'])
def test_frozen_aac_fractional_interval_survives_each_consumer(
    tmp_path, monkeypatch, rate, operation,
):
    base = base_media(tmp_path, rate)
    output_dir = tmp_path / 'output'
    commands = []
    original_run = lib.run_cmd

    def capture_command(command, *args, **kwargs):
        commands.append(list(map(str, command)))
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(lib, 'run_cmd', capture_command)
    if operation == 'pair':
        plan = tmp_path / 'pair.json'
        plan.write_text(json.dumps({
            'artifact': 'media_pair', 'schema_version': 1,
            'picture': identity(base), 'audio': {**identity(base), 'selected_stream': 0},
        }))
        pair_media.run_pair(plan, output_dir)
        output = output_dir / 'paired.mp4'
    elif operation == 'adopted_assemble':
        _quiet(monkeypatch)
        monkeypatch.setitem(CONFIG, 'bgm_path', '')
        output_dir.mkdir()
        output = output_dir / 'output.mp4'
        assemble.assemble_video(base, [], output_dir, output, audio_mode='adopted-packet-copy')
    else:
        clear = tmp_path / 'clear.png'
        run('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
            'color=black@0:size=64x48,format=rgba', '-frames:v', '1', clear)
        sequence = tmp_path / 'foreground'
        sequence.mkdir()
        for index in range(30):
            (sequence / (compose_foreground.PATTERN % index)).symlink_to(clear)
        receipt = tmp_path / 'receipt.json'
        receipt.write_text('{}')
        plan = tmp_path / 'compose.json'
        plan.write_text(json.dumps({
            'artifact': 'foreground_compose_plan', 'schema_version': 1,
            'base': identity(base),
            'video': {'fps': '24/1', 'width': 64, 'height': 48, 'total_frames': 32},
            'foreground': {
                'directory': str(sequence), 'pattern': compose_foreground.PATTERN,
                'start_frame': 0, 'end_frame': 30,
                'ordered_sha256': compose_foreground.ordered_sequence_digest(
                    sequence, compose_foreground.PATTERN, 0, 30,
                ),
            },
            'endcard': {'kind': 'still', **identity(clear), 'start_frame': 30, 'end_frame': 32},
            'producer_receipt': identity(receipt),
        }))
        compose_foreground.run_compose(plan, output_dir)
        output = output_dir / 'foreground.mp4'
    assert_sample_clock(output)
    verify_adopted_audio(base, output, 0, 0)
    if operation == 'pair':
        command = json.loads((output_dir / 'mux.command.json').read_text())
    elif operation == 'compose':
        command = json.loads((output_dir / 'compose.command.json').read_text())
    else:
        command = next(c for c in commands if '-c:a' in c and 'copy' in c)
    assert command.count('-movie_timescale') == 1
    assert command[command.index('-movie_timescale') + 1] == str(rate)
    assert '-t' not in command and '-shortest' not in command


@pytest.mark.parametrize('rate', [48000, 44100])
def test_adopted_assemble_rejects_quantized_new_output_without_publishing(
    tmp_path, monkeypatch, rate,
):
    base = base_media(tmp_path, rate)
    work = tmp_path / 'work'
    work.mkdir()
    _quiet(monkeypatch)
    monkeypatch.setitem(CONFIG, 'bgm_path', '')
    original = lib.run_cmd
    changed = []

    def drop_timescale(command, *args, **kwargs):
        command = list(command)
        if '-movie_timescale' in command:
            index = command.index('-movie_timescale')
            del command[index:index + 2]
            changed.append(True)
        return original(command, *args, **kwargs)

    monkeypatch.setattr(lib, 'run_cmd', drop_timescale)
    output = work / 'bad_output.mp4'
    with pytest.raises(ValueError, match='packet clock'):
        assemble.assemble_video(base, [], work, output, audio_mode='adopted-packet-copy')
    assert changed == [True]
    assert not output.exists()
    assert not (work / 'assembly_manifest.json').exists()
    assert not (work / 'assembly_qc.json').exists()


@pytest.mark.parametrize('drop_timescale', [False, True])
def test_explicit_mix_produces_sample_accurate_fractional_movie_clock(
    explicit_case, monkeypatch, drop_timescale,  # noqa: F811 - imported pytest fixture
):
    picture, work, segments, meta, narration, adoption = explicit_case
    _quiet(monkeypatch)
    run('ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=black:size=64x48:rate=24:duration=1.333333333333',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an', picture)
    chosen = json.loads(adoption.read_text())
    receipt = Path(chosen['prepared_receipt']['path'])
    prepared = json.loads(receipt.read_text())
    for name, old in prepared['outputs'].items():
        path = Path(old['path'])
        trimmed = path.with_name('trimmed_' + name)
        run('ffmpeg', '-v', 'error', '-i', path, '-af', 'atrim=end_sample=64000',
            '-c:a', 'pcm_f32le', trimmed)
        trimmed.replace(path)
        prepared['outputs'][name] = source_score._output_identity(path)
    prepared['format']['total_samples'] = 64000
    receipt.write_text(json.dumps(prepared))
    chosen.update(picture_sha256=identity(picture)['sha256'], prepared_receipt=identity(receipt))
    chosen['format']['total_samples'] = 64000
    adoption.write_text(json.dumps(chosen))
    output = work / 'output.mp4'
    if drop_timescale:
        original = lib.run_cmd

        def lose_container_precision(command, *args, **kwargs):
            command = list(command)
            if '-movie_timescale' in command:
                index = command.index('-movie_timescale')
                del command[index:index + 2]
            return original(command, *args, **kwargs)

        monkeypatch.setattr(lib, 'run_cmd', lose_container_precision)
        with pytest.raises(ValueError, match='packet clock'):
            assemble.assemble_video(
                picture, segments, work, output, narration_adoption_path=narration,
                tts_meta_path=meta, audio_mix_adoption_path=adoption,
            )
        assert not output.exists()
        assert not (work / 'audio_mix_binding.json').exists()
        assert not (work / 'narration_input_binding.json').exists()
        return
    assemble.assemble_video(
        picture, segments, work, output, narration_adoption_path=narration,
        tts_meta_path=meta, audio_mix_adoption_path=adoption,
    )
    assert_sample_clock(output)


def test_old_millisecond_quantized_header_still_fails_original_gate(tmp_path):
    base = base_media(tmp_path, 48000)
    bad = tmp_path / 'coarse_header.mp4'
    run('ffmpeg', '-v', 'error', '-i', base, '-c', 'copy', '-movie_timescale', '1000', bad)
    verify_adopted_audio(base, bad, 0, 0)
    with pytest.raises(ValueError, match='packet clock'):
        pair_media.validate_pair_timing(pair_media.probe_picture(bad), probe_audio_packets(bad, 0))
