"""Independent complete-PCM and real-output tests for the adopted audio mixer."""

from array import array
import copy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_narration_adoption import _adoption

SCRIPTS = Path(__file__).resolve().parents[2] / 'skills/video-assemble/scripts'
sys.path.insert(0, str(SCRIPTS))
import assemble  # noqa: E402
import adoption.audio_mix_binding as audio_mix_binding  # noqa: E402
from lib import CONFIG  # noqa: E402
import source_score  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (shutil.which('ffmpeg') and shutil.which('ffprobe')),
    reason='actual adopted-mix test requires ffmpeg and ffprobe',
)


def run(*command, data=None):
    return subprocess.run(list(map(str, command)), input=data, capture_output=True, check=True)


def floats(data):
    values = array('f')
    values.frombytes(data)
    if sys.byteorder != 'little':
        values.byteswap()
    return values


def pcm(path, channels=2):
    return floats(run('ffmpeg', '-v', 'error', '-i', path, '-map', '0:a:0',
                      '-ar', '48000', '-ac', channels, '-f', 'f32le', '-').stdout)


def write_float(path, values, *, rate=48000, channels=2):
    samples = array('f', values)
    if sys.byteorder != 'little':
        samples.byteswap()
    run('ffmpeg', '-v', 'error', '-f', 'f32le', '-ar', rate, '-ac', channels,
        '-i', '-', '-c:a', 'pcm_f32le', path, data=samples.tobytes())
    return path


@pytest.fixture
def adopted_case(tmp_path, monkeypatch):
    for key, value in {
        'burn_subtitles': False, 'mask_source_subtitles': False,
        'subtitle_original_in_gaps': False, 'export_jianying': False,
        'output_max_height': 0, 'force_video_reencode': False,
        # Hostile defaults must not become audio instructions in the explicit branch.
        'narration_speed': 1.15,
        'fade_ms': 50, 'final_loudnorm': True,
        'idle_orig_volume': 1, 'speech_ducking_volume': .8,
        'ducking_narr_weight': .1, 'bgm_path': '/missing/not-adopted-score.wav',
    }.items():
        monkeypatch.setitem(CONFIG, key, value)
    picture = tmp_path / 'picture.mp4'
    run('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'color=black:s=160x120:r=24:d=2', '-f', 'lavfi', '-i',
        'sine=frequency=123:sample_rate=48000:duration=2',
        '-c:v', 'libx264', '-threads', '2', '-c:a', 'aac', picture)
    score = write_float(tmp_path / 'quiet.wav', [0]*192000)
    plan = tmp_path / 'bed-plan.json'
    plan.write_text(json.dumps({
        'artifact': 'source_score_plan', 'schema_version': 1,
        'output': {'sample_rate': 48000, 'channels': 2, 'total_samples': 96000},
        'source_segments': [],
        'source_silence': [{'output_start_sample': 0, 'output_end_sample': 96000,
                            'role': 'silence'}],
        'score': {'kind': 'frozen', 'path': str(score), 'audio_stream': 0},
    }), encoding="utf-8")
    source_score.prepare_source_score(plan, tmp_path / 'bed')
    prepared = tmp_path / 'bed/prepared_bed_receipt.json'
    # Stereo L/R are intentionally different; a mono fold cannot reconstruct them.
    stereo = []
    opposite = []
    for i in range(12000):
        stereo.extend([.2*math.sin(i*.13), .15*math.cos(i*.27)])
        x = .12*math.sin(i*.11)
        opposite.extend([x, -x])
    stereo[:2] = [.5, -.25]
    stereo[-2:] = [-.375, .1875]
    opposite[:2], opposite[-2:] = [.125, -.125], [-.25, .25]
    files = [write_float(tmp_path / 'stereo.wav', stereo),
             write_float(tmp_path / 'opposite.wav', opposite),
             write_float(tmp_path / 'mono.wav', [.1*math.sin(i*.17) for i in range(5513)],
                         rate=22050, channels=1)]
    starts, gains = [12345, 34567, 67891], [.5, .375, .25]
    segments = []
    for i, (path, start) in enumerate(zip(files, starts)):
        duration = (12000/48000 if i < 2 else 5513/22050)
        segments.append({
            'index': i, 'start': start/48000, 'end': start/48000+duration,
            'narration': 'a', 'spoken_text': 'a', 'audio_path': str(path),
            'audio_duration': duration, 'pause_after_ms': 0, 'overlaps_speech': False,
            'tts_rate_offset': 0.0,
        })
    narration, meta = _adoption(tmp_path, segments)
    adoption = tmp_path / 'mix-adoption.json'
    document = {
        'artifact': 'audio_mix_adoption', 'schema_version': 1,
        'prepared_receipt': {'path': str(prepared)},
        'format': {'sample_rate': 48000, 'channels': 2, 'total_samples': 96000},
        'segments': [{'index': i, 'output_start_sample': start, 'gain': gain}
                     for i, (start, gain) in enumerate(zip(starts, gains))],
        'master_gain_db': -3.0,
    }
    adoption.write_text(json.dumps(document), encoding="utf-8")
    return {'picture': picture, 'files': files, 'meta': meta, 'narration': narration,
            'adoption': adoption, 'segments': segments, 'document': document}


def render(case, tmp_path, name='render'):
    work = tmp_path / name
    work.mkdir()
    segments = copy.deepcopy(case['segments'])
    output = assemble.assemble_video(
        case['picture'], segments, work, work / 'output.mp4',
        narration_adoption_path=case['narration'], tts_meta_path=case['meta'],
        audio_mix_adoption_path=case['adoption'],
    )
    return output, work, segments


def test_real_native_stereo_anti_phase_and_whole_voice_bus(adopted_case, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('strict adopted mixer entered legacy mono/automatic sound path')
    for name in ['_apply_narration_speed', '_build_timed_narration']:
        monkeypatch.setattr(assemble.narration_audio, name, forbidden)
    monkeypatch.setattr(assemble.audio_mix, '_apply_source_sentence_handoffs', forbidden)
    monkeypatch.setattr(assemble.audio_mix, '_build_audio_filter_complex', forbidden)
    output, work, _ = render(adopted_case, tmp_path)
    binding = json.loads((work / 'narration_input_binding.json').read_text(encoding="utf-8"))
    mix = json.loads((work / 'audio_mix_binding.json').read_text(encoding="utf-8"))
    expected_bus = [0.0]*192000
    for i, item in enumerate(binding['segments']):
        assert item['placed']['pcm']['sample_rate'] == '48000'
        placed = pcm(item['placed']['path'])
        original = pcm(adopted_case['files'][i])
        assert len(placed) == len(original)
        assert max(abs(a-b) for a, b in zip(placed, original)) < 1e-7
        if i < 2:
            assert placed[:2] == original[:2] and placed[-2:] == original[-2:]
            assert len(placed) == 24000  # native48k must not gain a sample or fade edges
        start = adopted_case['document']['segments'][i]['output_start_sample']
        gain = adopted_case['document']['segments'][i]['gain']
        expected_bus[2*start:2*start+len(placed)] = [value*gain for value in placed]
    bus = pcm(binding['narration_bus']['path'])
    assert len(bus) == len(expected_bus)
    assert max(abs(a-b) for a, b in zip(bus, expected_bus)) < 1e-7
    assert binding['narration_bus']['consumption_status'] == 'CONSUMED_BY_EXPLICIT_MIX'
    assert mix['voice_bus']['path'] == binding['narration_bus']['path']
    assert mix['narration_input_binding'] == {
        'path': str((work/'narration_input_binding.json').resolve()), 'status': 'FINALIZED'}
    assert mix['status'] == 'FINALIZED'
    # The untagged copy-safe picture is stream-copied; only its colour labels change.
    assert mix['output_picture']['packet_identity'] == 'EXACT'
    assert [(item['output_start_sample'], item['gain']) for item in mix['segments']] == [
        (item['output_start_sample'], item['gain'])
        for item in adopted_case['document']['segments']]
    master = pcm(mix['master']['path'])
    master_gain = 10**(-3/20)
    assert max(abs(a-b*master_gain) for a, b in zip(master, expected_bus)) < 1e-7
    assert max(abs(x) for x in master[:20000]) == 0  # old picture audio never enters mix
    decoded = pcm(output)
    assert max(abs(x) for x in decoded[:18000]) < 1e-5
    qc = json.loads((work/'assembly_qc.json').read_text(encoding="utf-8"))
    assert qc['audio_operations']['explicit_audio_mix'] is True
    for operation in ['ducking', 'loudness_normalization', 'limiter', 'tempo']:
        assert qc['audio_operations'][operation] is False
    assert qc['loudness_mode'] == 'fixed_master_gain_no_loudnorm'


def test_segment_level_blocking_stops_explicit_mix_before_the_video_encode(
        adopted_case, tmp_path, monkeypatch):
    monkeypatch.setattr(
        assemble.assembly_contract, '_placed_audio_matches_timeline', lambda _seg: False)
    with pytest.raises(assemble.AssemblyBlockedBeforeRender, match='timeline_audio_mismatch'):
        render(adopted_case, tmp_path)
    work = tmp_path / 'render'
    assert not (work / 'output.mp4').exists()
    qc = json.loads((work / 'assembly_qc.json').read_text(encoding="utf-8"))
    assert qc['blocking_codes'] == ['timeline_audio_mismatch']
    assert qc['delivery_qc']['video_encode_passes'] == 0


def test_integer_mono_is_converted_to_float_before_equal_power_pan(adopted_case, tmp_path):
    # Real providers also return integer PCM. Panning s16 directly quantizes the
    # channel matrix before resampling even if the final output claims float PCM.
    integer_voice = tmp_path / 'mono_s16.wav'
    run('ffmpeg', '-v', 'error', '-i', adopted_case['files'][2],
        '-c:a', 'pcm_s16le', integer_voice)
    adopted_case['files'][2] = integer_voice
    adopted_case['segments'][2].update(audio_path=str(integer_voice))
    narration, meta = _adoption(tmp_path, adopted_case['segments'])
    adopted_case.update(narration=narration, meta=meta)
    _, work, _ = render(adopted_case, tmp_path)
    binding = json.loads((work/'narration_input_binding.json').read_text(encoding="utf-8"))
    converted = pcm(binding['segments'][2]['placed']['path'])
    independent = pcm(integer_voice)
    assert len(converted) == len(independent)
    assert max(abs(a-b) for a, b in zip(converted, independent)) < 1e-6


def test_explicit_mix_allows_requested_reencode_without_changing_frame_clock(
    adopted_case, tmp_path, monkeypatch,
):
    monkeypatch.setitem(CONFIG, 'force_video_reencode', True)
    monkeypatch.setitem(CONFIG, 'output_crf', 18)
    monkeypatch.setitem(CONFIG, 'output_preset', 'veryfast')
    output, work, _ = render(adopted_case, tmp_path)
    assert output.is_file()
    mix = json.loads((work/'audio_mix_binding.json').read_text(encoding="utf-8"))
    assert mix['output_picture']['frame_count'] == 48
    assert mix['output_picture']['fps'] in ['24', '24/1']
    assert mix['output_picture']['packet_identity'] == 'REENCODED_CLOCK_MATCH'


def test_packet_identity_ignores_only_the_colour_labels():
    picture = {
        'decoder': {'codec_name': 'h264', 'pix_fmt': 'yuv420p', 'color_range': None,
                    'color_space': None, 'color_transfer': None, 'color_primaries': None},
        'packets': [{'pts': '0', 'dts': '0', 'duration': '1/24', 'size': 100}],
        'frame_count': 1,
    }
    labelled = copy.deepcopy(picture)
    labelled['decoder'].update(color_range='tv', color_space='bt709',
                               color_transfer='bt709', color_primaries='bt709')
    assert audio_mix_binding._without_color_tags(labelled) == \
        audio_mix_binding._without_color_tags(picture)
    resized = copy.deepcopy(labelled)
    resized['packets'][0]['size'] = 101
    assert audio_mix_binding._without_color_tags(resized) != \
        audio_mix_binding._without_color_tags(picture)
    repacked = copy.deepcopy(labelled)
    repacked['decoder']['pix_fmt'] = 'yuvj420p'
    assert audio_mix_binding._without_color_tags(repacked) != \
        audio_mix_binding._without_color_tags(picture)


def test_load_adoption_binds_picture_receipt_narration_and_segments(adopted_case):
    context = audio_mix_binding.load_adoption(
        adopted_case['adoption'], input_video=adopted_case['picture'],
        narration_adoption_path=adopted_case['narration'],
        tts_segments=adopted_case['segments'],
    )
    assert context['format']['total_samples'] == 96000
    assert context['prepared']['prepared_bed.wav']['pcm']['samples'] == 96000
    assert context['narration_adoption'] == {
        'path': str(Path(adopted_case['narration']).resolve())}
    assert context['segments'] == adopted_case['document']['segments']


@pytest.mark.parametrize('field', ['prepared_receipt', 'format'])
def test_missing_top_level_field_fails_before_snapshot(adopted_case, tmp_path, field):
    document = copy.deepcopy(adopted_case['document'])
    del document[field]
    adopted_case['adoption'].write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match='(?i)(field|adoption)'):
        render(adopted_case, tmp_path)
    work = tmp_path/'render'
    assert not (work/'.narration_input_snapshots').exists()
    assert not (work/'output.mp4').exists()


def test_isolated_copied_skill_cli_publishes_new_alias_and_manifest(adopted_case, tmp_path):
    copied = tmp_path / 'copied-skill'
    shutil.copytree(SCRIPTS.parent, copied, ignore=shutil.ignore_patterns('__pycache__'))
    work = tmp_path / 'cli-work'
    work.mkdir()
    delivery = tmp_path / 'delivery'
    # -I drops caller/repo imports; only the copied scripts directory is importable.
    launcher = (
        'import runpy,sys;sys.path.insert(0,sys.argv[1]);sys.argv=sys.argv[2:];'
        "runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    env = {**os.environ, 'BGM_PATH': '/missing/ambient.wav', 'FINAL_LOUDNORM': '1',
           'NARRATION_SPEED': '1.15', 'OUTPUT_MAX_HEIGHT': '0'}
    command = [
        sys.executable, '-I', '-c', launcher, copied / 'scripts',
        copied / 'scripts/assemble.py', adopted_case['picture'], '--work-dir', work,
        '--tts-meta', adopted_case['meta'], '--narration-adoption', adopted_case['narration'],
        '--audio-mix-adoption', adopted_case['adoption'], '--no-burn-subtitles',
        '--output-dir', delivery, '--recap-stem', 'strict',
    ]
    result = subprocess.run(tuple(map(str, command)), env=env, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    alias = delivery / 'recap_strict.mp4'
    manifest = json.loads((work / 'assembly_manifest.json').read_text(encoding="utf-8"))
    assert alias.is_file()
    assert manifest['audio_mix_binding']['status'] == 'FINALIZED'
    assert manifest['assembly_settings']['audio']['path'] == 'explicit_adopted_full_sound'
    second = subprocess.run(tuple(map(str, command)), env=env, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=120)
    assert second.returncode != 0
    assert alias.is_file(), 'exclusive strict retry must not remove an older delivery'


@pytest.mark.parametrize('mutation', [
    lambda d: d['format'].update(total_samples=95000),
    lambda d: d['prepared_receipt'].update(path='/nonexistent/prepared_bed_receipt.json'),
    lambda d: d['segments'][1].update(output_start_sample=13000),
    lambda d: d['segments'][2].update(output_start_sample=95000),
])
def test_bad_adopted_receipt_or_sample_window_never_publishes(adopted_case, tmp_path, mutation):
    document = copy.deepcopy(adopted_case['document'])
    mutation(document)
    adopted_case['adoption'].write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError)):
        render(adopted_case, tmp_path)
    work = tmp_path/'render'
    assert not (work/'output.mp4').exists()
    assert not (work/'narration_input_binding.json').exists()
    assert not (work/'audio_mix_binding.json').exists()


@pytest.mark.parametrize('failed_call', [1, 2])
def test_both_bindings_and_video_rollback_on_qc_failure(adopted_case, tmp_path, monkeypatch,
                                                      failed_call):
    original = assemble.assembly_contract._build_assembly_qc
    work = tmp_path/'render'
    published_during_qc = []
    def qc(*args, **kwargs):
        published_during_qc.append((work/'output.mp4').exists())
        result = original(*args, **kwargs)
        if len(published_during_qc) == failed_call:
            result.update(blocking=True, verdict='FAIL', blocking_codes=['test_failure'])
        return result
    monkeypatch.setattr(assemble.assembly_contract, '_build_assembly_qc', qc)
    with pytest.raises(RuntimeError, match='QC'):
        render(adopted_case, tmp_path)
    assert len(published_during_qc) == failed_call
    # The strict final name stays invisible until the first QC pass has cleared.
    assert published_during_qc[0] is False
    assert not (work/'output.mp4').exists()
    assert not (work/'narration_input_binding.json').exists()
    assert not (work/'audio_mix_binding.json').exists()
