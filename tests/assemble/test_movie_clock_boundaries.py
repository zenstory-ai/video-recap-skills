"""A fractional-second AAC interval must survive producing and packaging."""

import array
from fractions import Fraction
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import wave

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'skills/video-assemble/scripts'
sys.path.insert(0, str(SCRIPTS))
import assemble  # noqa: E402
import source_score  # noqa: E402
import lib  # noqa: E402
from adoption.av_clock import probe_picture, validate_pair_timing  # noqa: E402
from adoption.frozen_audio import probe_audio_packets, verify_adopted_audio  # noqa: E402
from lib import CONFIG  # noqa: E402
from tts_fixtures import tts_segment  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (shutil.which('ffmpeg') and shutil.which('ffprobe')),
    reason='ffmpeg and ffprobe are needed for fractional movie clocks',
)


STRICT_TEMPO = {
    'global_atempo': 1.0, 'bounded_segment_fit': False,
    'segment_tempo_max': 1.0, 'cumulative_tempo_max': 1.0,
    'cumulative_tempo_hard_max': 1.0,
}


def run(*args):
    subprocess.run(list(map(str, args)), check=True, capture_output=True)


def _wav(path, frequency, seconds=0.4):
    rate = 48_000
    data = array.array('h', (
        int(8000 * math.sin(2 * math.pi * frequency * index / rate))
        for index in range(round(rate * seconds))
    ))
    if sys.byteorder != 'little':
        data.byteswap()
    with wave.open(str(path), 'wb') as output:
        output.setparams((1, 2, rate, len(data), 'NONE', 'not compressed'))
        output.writeframes(data.tobytes())
    return path


def _quiet(monkeypatch):
    monkeypatch.setitem(CONFIG, 'burn_subtitles', False)
    monkeypatch.setitem(CONFIG, 'mask_source_subtitles', False)
    monkeypatch.setitem(CONFIG, 'subtitle_original_in_gaps', False)
    monkeypatch.setitem(CONFIG, 'output_max_height', 0)
    monkeypatch.setitem(CONFIG, 'bgm_path', 'hostile-unused-bgm.wav')
    monkeypatch.setitem(CONFIG, 'final_loudnorm', True)
    monkeypatch.setitem(CONFIG, 'narration_speed', 1.15)


@pytest.fixture
def explicit_case(tmp_path):
    """One bound voice over a prepared source+score bed; the caller renders picture.mp4."""
    picture = tmp_path / 'picture.mp4'
    work = tmp_path / 'work'
    work.mkdir()
    beds = tmp_path / 'beds'
    beds.mkdir()
    for name, frequency in (('source_bed.wav', 220), ('score_bed.wav', 330)):
        run('ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
            f'sine=frequency={frequency}:sample_rate=48000:duration=2', '-ac', '2',
            '-c:a', 'pcm_f32le', beds / name)
    run('ffmpeg', '-v', 'error', '-y', '-i', beds / 'source_bed.wav', '-i',
        beds / 'score_bed.wav', '-filter_complex',
        '[0:a][1:a]amix=inputs=2:normalize=0[out]', '-map', '[out]',
        '-c:a', 'pcm_f32le', beds / 'prepared_bed.wav')
    identities = {
        name: source_score._output_facts(beds / name)
        for name in ('source_bed.wav', 'score_bed.wav', 'prepared_bed.wav')
    }
    receipt = beds / 'prepared_bed_receipt.json'
    receipt.write_text(json.dumps({
        'artifact': 'prepared_bed_receipt', 'schema_version': 1, 'status': 'PREPARED',
        'format': {'sample_rate': 48000, 'channels': 2, 'total_samples': 96000,
                   'codec': 'pcm_f32le'},
        'outputs': identities,
    }), encoding="utf-8")
    voice = _wav(tmp_path / 'voice.wav', 997)
    segment = tts_segment(
        index=0, start=0.25, end=1.0, narration='bound voice',
        spoken_text='bound voice', audio_path=str(voice),
        audio_duration=0.4, pause_after_ms=0, overlaps_speech=False,
        tts_rate_offset=0.0,
    )
    meta = tmp_path / 'tts_meta.json'
    meta.write_text(json.dumps({'segments': [segment]}), encoding="utf-8")
    narration = tmp_path / 'narration_adoption.json'
    narration.write_text(json.dumps({
        'artifact': 'narration_adoption', 'schema_version': 1,
        'segments': [{
            'index': 0, 'spoken_text': 'bound voice',
            'requested_provider': 'offline', 'requested_voice': 'voice-a',
        }], 'tempo_policy': STRICT_TEMPO,
    }), encoding="utf-8")
    adoption = tmp_path / 'audio_mix_adoption.json'
    adoption.write_text(json.dumps({
        'artifact': 'audio_mix_adoption', 'schema_version': 1,
        'prepared_receipt': {'path': str(receipt)},
        'format': {'sample_rate': 48000, 'channels': 2, 'total_samples': 96000},
        'segments': [{'index': 0, 'output_start_sample': 12000, 'gain': 0.5}],
        'master_gain_db': 0.75,
    }), encoding="utf-8")
    return picture, work, [segment], meta, narration, adoption


def assert_sample_clock(video):
    audio = probe_audio_packets(video, 0)
    last = audio['packets'][-1]
    end = Fraction(last['pts']) + Fraction(last['duration'])
    header_end = Fraction(audio['start_time']) + Fraction(audio['duration'])
    assert abs(end - header_end) <= Fraction(1, audio['sample_rate']), (
        end, header_end, audio['sample_rate'],
    )
    validate_pair_timing(probe_picture(video), audio)


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
    assert probe_picture(video)['frame_count'] == 32
    assert_sample_clock(video)
    return video


@pytest.mark.parametrize('rate', [48000, 44100])
def test_frozen_aac_fractional_interval_survives_adopted_assemble(tmp_path, monkeypatch, rate):
    base = base_media(tmp_path, rate)
    output_dir = tmp_path / 'output'
    commands = []
    original_run = lib.run_cmd

    def capture_command(command, *args, **kwargs):
        commands.append(list(map(str, command)))
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(lib, 'run_cmd', capture_command)
    _quiet(monkeypatch)
    monkeypatch.setitem(CONFIG, 'bgm_path', '')
    output_dir.mkdir()
    output = output_dir / 'output.mp4'
    assemble.assemble_video(base, [], output_dir, output, audio_mode='adopted-packet-copy')
    assert_sample_clock(output)
    verify_adopted_audio(base, output, 0, 0)
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
        # Force the pre-FFmpeg-9 default; 9 defaults to the track LCM, which is exact.
        command = list(command)
        if '-movie_timescale' in command:
            command[command.index('-movie_timescale') + 1] = '1000'
            changed.append(True)
        return original(command, *args, **kwargs)

    monkeypatch.setattr(lib, 'run_cmd', drop_timescale)
    output = work / 'bad_output.mp4'
    # FFmpeg 9 exports the rounded edit-list tail as packet side data, so the packet
    # comparison rejects it before the clock check does; older ffmpeg reaches the clock check.
    with pytest.raises((ValueError, RuntimeError), match='packet clock|side data'):
        assemble.assemble_video(base, [], work, output, audio_mode='adopted-packet-copy')
    assert changed == [True]
    assert not output.exists()
    assert not (work / 'assembly_manifest.json').exists()
    assert not (work / 'assembly_qc.json').exists()


@pytest.mark.parametrize('drop_timescale', [False, True])
def test_explicit_mix_produces_sample_accurate_fractional_movie_clock(
    explicit_case, monkeypatch, drop_timescale,
):
    picture, work, segments, meta, narration, adoption = explicit_case
    _quiet(monkeypatch)
    run('ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=black:size=64x48:rate=24:duration=1.333333333333',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an', picture)
    chosen = json.loads(adoption.read_text(encoding="utf-8"))
    receipt = Path(chosen['prepared_receipt']['path'])
    prepared = json.loads(receipt.read_text(encoding="utf-8"))
    for name, old in prepared['outputs'].items():
        path = Path(old['path'])
        trimmed = path.with_name('trimmed_' + name)
        run('ffmpeg', '-v', 'error', '-i', path, '-af', 'atrim=end_sample=64000',
            '-c:a', 'pcm_f32le', trimmed)
        trimmed.replace(path)
        prepared['outputs'][name] = source_score._output_facts(path)
    prepared['format']['total_samples'] = 64000
    receipt.write_text(json.dumps(prepared), encoding="utf-8")
    chosen['format']['total_samples'] = 64000
    adoption.write_text(json.dumps(chosen), encoding="utf-8")
    output = work / 'output.mp4'
    if drop_timescale:
        original = lib.run_cmd

        def lose_container_precision(command, *args, **kwargs):
            # Force the pre-FFmpeg-9 default; 9 defaults to the track LCM, which is exact.
            command = list(command)
            if '-movie_timescale' in command:
                command[command.index('-movie_timescale') + 1] = '1000'
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


def test_millisecond_quantized_header_is_rejected(tmp_path):
    base = base_media(tmp_path, 48000)
    bad = tmp_path / 'coarse_header.mp4'
    run('ffmpeg', '-v', 'error', '-i', base, '-c', 'copy', '-movie_timescale', '1000', bad)
    # FFmpeg 9 already rejects it here (the rounded tail becomes last-packet side data);
    # older ffmpeg passes the packet comparison and relies on the clock gate below.
    try:
        verify_adopted_audio(base, bad, 0, 0)
    except RuntimeError as exc:
        assert 'side data' in str(exc)
    with pytest.raises(ValueError, match='packet clock'):
        validate_pair_timing(probe_picture(bad), probe_audio_packets(bad, 0))
