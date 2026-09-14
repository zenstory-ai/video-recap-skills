"""Required story moments survive normalization, snapping and cached cut reuse."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills/video-cut/scripts'))
import cut_cli
import media_geometry
from lib import CONFIG


@pytest.fixture
def run_cut(tmp_path, monkeypatch):
    video = tmp_path / 'source.mp4'
    video.write_bytes(b'fixture')
    monkeypatch.setattr(cut_cli, 'get_video_duration', lambda _: 10)
    monkeypatch.setattr(media_geometry, '_probe_video_geometry', lambda _: media_geometry._geometry_from_stream({
        'width': 320, 'height': 240, 'r_frame_rate': '24/1'}))
    monkeypatch.setitem(CONFIG, 'scene_cut_snap', False)
    monkeypatch.setitem(CONFIG, 'snap_clip_line_end', False)
    monkeypatch.setattr(cut_cli, 'enforce_clip_sentence_boundaries', lambda plan, *a: plan)

    def run(plan, *options):
        (tmp_path / 'clip_plan.json').write_text(json.dumps(plan), encoding='utf-8')
        monkeypatch.setattr(sys, 'argv', ['cut.py', str(video), '--work-dir', str(tmp_path), *options])
        cut_cli.main()
        return json.loads((tmp_path / 'clip_plan_validated.json').read_text())

    return run, video


def contract(video, track='video'):
    return {'nodes': [
        {'id': 'refusal', 'source': str(video), 'start': 2.25, 'end': 3.75,
         'track': track, 'content': '拒绝请求，触发后续决定'},
        {'id': 'response', 'source': str(video), 'start': 4.25, 'end': 5.75,
         'track': 'video', 'content': '接收信息后作出决定'}], 'before': [['refusal', 'response']]}


def test_missing_premise_blocks_before_cached_reuse_and_clears_stale_delivery(run_cut, tmp_path, monkeypatch):
    run, video = run_cut
    (tmp_path / 'cut_delivery_qc.json').write_text('{"rendered":true}')
    # Old media remains for diagnosis; it cannot authorize the revised plan.
    (tmp_path / 'edited_source.mp4').write_bytes(b'old approved media')
    monkeypatch.setattr(cut_cli, 'should_reuse_edited_source',
                        lambda *a: pytest.fail('invalid plan reached render-cache reuse'))
    raw = {'clips': [{'start': 0, 'end': 2}, {'start': 4, 'end': 6,
                     'reason': '拒绝请求→作出决定；标题/说明不能补被删前提'}],
           'required_evidence': contract(video)}
    with pytest.raises(SystemExit, match='QC blocking'):
        run(raw, '--allow-sparse-cut', '--allow-duration-drift', '--no-narration-map')
    current = json.loads((tmp_path / 'clip_plan_validated.json').read_text())
    assert current['qc']['required_evidence']['selection_status'] == 'BLOCK'
    assert current['qc']['blocking']
    assert not (tmp_path / 'cut_delivery_qc.json').exists()
    assert (tmp_path / 'edited_source.mp4').read_bytes() == b'old approved media'


def test_valid_moments_use_actual_post_snap_plan(run_cut, monkeypatch):
    run, video = run_cut
    raw = {'clips': [{'start': 2, 'end': 4}, {'start': 4, 'end': 6}],
           'required_evidence': contract(video)}
    result = run(raw, '--normalize-only')
    assert result['qc']['required_evidence']['selection_status'] == 'PASS'
    assert result['qc']['required_evidence']['semantic_status'] == 'NOT_CHECKED'

    def trim_premise(plan, *args, **kwargs):
        plan['clips'][0]['source_start'] = 2.5
        plan['clips'][0]['duration'] = 1.5
        plan['clips'][0]['output_end'] = 1.5
        plan['clips'][1]['output_start'] = 1.5
        plan['clips'][1]['output_end'] = 3.5
        plan['total_duration'] = 3.5
        return plan

    monkeypatch.setitem(CONFIG, 'scene_cut_snap', True)
    monkeypatch.setattr(cut_cli, 'snap_clips_off_shot_changes', trim_premise)
    with pytest.raises(SystemExit, match='QC blocking'):
        run(raw, '--normalize-only')


@pytest.mark.parametrize('early_start', [4, 5])
def test_each_response_needs_premise_unless_order_is_not_required(run_cut, early_start):
    run, video = run_cut
    raw = {'clips': [{'start': early_start, 'end': 6}, {'start': 2, 'end': 4}, {'start': 4, 'end': 6}],
           'required_evidence': contract(video)}
    with pytest.raises(SystemExit, match='QC blocking'):
        run(raw, '--normalize-only', '--allow-overlap')
    raw['required_evidence']['before'] = []  # Explicitly chosen cold-open structure.
    assert run(raw, '--normalize-only', '--allow-overlap')['qc']['required_evidence']['selection_status'] == 'PASS'


def test_audio_requirement_cannot_be_met_by_silent_picture(run_cut, monkeypatch):
    run, video = run_cut
    monkeypatch.setattr(cut_cli, '_has_audio_stream', lambda _: False, raising=False)
    raw = {'clips': [{'start': 2, 'end': 6}], 'required_evidence': contract(video, 'audio')}
    with pytest.raises(SystemExit, match='QC blocking'):
        run(raw, '--normalize-only')
    monkeypatch.setattr(cut_cli, '_has_audio_stream', lambda _: True)
    assert run(raw, '--normalize-only')['qc']['required_evidence']['selection_status'] == 'PASS'


@pytest.mark.parametrize('bad', [None, {}, {'nodes': [], 'before': []}])
def test_present_invalid_contract_is_not_ignored(run_cut, bad):
    run, _ = run_cut
    with pytest.raises(SystemExit, match='QC blocking'):
        run({'clips': [{'start': 0, 'end': 2}], 'required_evidence': bad}, '--normalize-only')


def test_absent_requirements_preserve_legacy_normalize(run_cut, monkeypatch):
    run, _ = run_cut
    monkeypatch.setattr(cut_cli, '_has_audio_stream', lambda _: pytest.fail('unneeded audio probe'), raising=False)
    assert 'required_evidence' not in run({'clips': [{'start': 0, 'end': 2}]}, '--normalize-only')['qc']


@pytest.fixture
def real_source(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg/ffprobe required for real cut integration')
    source = tmp_path / 'real_source.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'testsrc2=size=96x64:rate=24:duration=8', '-f', 'lavfi', '-i',
        'sine=frequency=440:sample_rate=48000:duration=8', '-c:v', 'libx264',
        '-threads', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source),
    ], check=True, capture_output=True)
    return source


def run_real_cut(source, work, raw, *options):
    work.mkdir(exist_ok=True)
    (work / 'clip_plan.json').write_text(json.dumps(raw), encoding='utf-8')
    result = subprocess.run([
        sys.executable, str(Path(cut_cli.__file__).with_name('cut.py')),
        str(source), '--work-dir', str(work), '--no-narration-map', *options,
    ], env={**os.environ, 'SCENE_CUT_SNAP': '0', 'SNAP_CLIP_LINE_END': '0',
            'CLIP_PADDING': '0'}, capture_output=True, text=True)
    return result, json.loads((work / 'clip_plan_validated.json').read_text())


def test_real_render_and_cache_recheck_declared_premise(real_source, tmp_path):
    work = tmp_path / 'render'
    raw = {'clips': [{'start': 2, 'end': 4}, {'start': 4, 'end': 6}],
           'required_evidence': contract(real_source, 'audio')}
    first, validated = run_real_cut(real_source, work, raw)
    assert first.returncode == 0, first.stdout + first.stderr
    assert validated['qc']['required_evidence']['selection_status'] == 'PASS'
    media = work / 'edited_source.mp4'
    original_bytes, original_mtime = media.read_bytes(), media.stat().st_mtime_ns
    probe = subprocess.run([
        'ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(media),
    ], check=True, capture_output=True, text=True)
    streams = json.loads(probe.stdout)['streams']
    assert {stream['codec_type'] for stream in streams} == {'video', 'audio'}
    video = next(stream for stream in streams if stream['codec_type'] == 'video')
    assert int(video['nb_frames']) == 96
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(media), '-f', 'null', '-'],
                   check=True, capture_output=True)

    cached, validated = run_real_cut(real_source, work, raw)
    assert cached.returncode == 0, cached.stdout + cached.stderr
    assert '复用剪辑源视频' in cached.stdout + cached.stderr
    assert validated['qc']['required_evidence']['selection_status'] == 'PASS'
    assert media.stat().st_mtime_ns == original_mtime

    # Identical media plan, but the revised editorial requirement is not in it.
    raw['required_evidence']['nodes'][0].update(start=0.25, end=1.75)
    blocked, validated = run_real_cut(real_source, work, raw, '--allow-sparse-cut')
    assert blocked.returncode != 0
    assert 'QC blocking' in blocked.stdout + blocked.stderr
    assert validated['qc']['required_evidence']['selection_status'] == 'BLOCK'
    assert media.read_bytes() == original_bytes
    assert media.stat().st_mtime_ns == original_mtime
    assert not (work / 'cut_delivery_qc.json').exists()


def test_multi_source_audio_must_come_from_the_declared_source(real_source, tmp_path):
    silent = tmp_path / 'silent.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(real_source),
                    '-map', '0:v:0', '-c:v', 'copy', '-an', str(silent)],
                   check=True, capture_output=True)
    manifest = tmp_path / 'sources.json'
    manifest.write_text(json.dumps({'sources': [
        {'source_id': 'spoken', 'source_path': str(real_source)},
        {'source_id': 'silent', 'source_path': str(silent)},
    ]}))
    raw = {'clips': [{'source_id': 'silent', 'start': 2, 'end': 6}],
           'required_evidence': contract(silent, 'audio')}
    blocked, validated = run_real_cut(real_source, tmp_path / 'multi', raw,
                                     '--sources-manifest', str(manifest), '--normalize-only')
    assert blocked.returncode != 0
    assert any(f['code'] == 'REQUIRED_EVIDENCE_AUDIO_UNAVAILABLE'
               for f in validated['qc']['required_evidence']['findings'])
    for node in raw['required_evidence']['nodes']:
        node.update(source=str(real_source), source_id='spoken')
    raw['clips'][0]['source_id'] = 'spoken'
    passed, validated = run_real_cut(real_source, tmp_path / 'multi', raw,
                                    '--sources-manifest', str(manifest), '--normalize-only')
    assert passed.returncode == 0, passed.stdout + passed.stderr
    assert validated['qc']['required_evidence']['selection_status'] == 'PASS'
