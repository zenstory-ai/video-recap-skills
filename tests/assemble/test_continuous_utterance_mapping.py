"""A continuous spoken name must survive picture-only cuts as one cue."""
import sys
import json
import shutil
import subprocess
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills/video-assemble/scripts'))
from source_subtitles import _map_asr_to_output, _combined_subtitle_entries
from lib import CONFIG
from subtitles.render import _generate_ass


def span(source_start, source_end, output_start):
    return {'source_start': source_start, 'source_end': source_end,
            'output_start': output_start, 'output_end': output_start + source_end - source_start}


def test_same_utterance_spans_continuous_picture_cut_without_repeat():
    rows = [{'start': 10.8, 'end': 11.6, 'text': '老大'}]
    result = _map_asr_to_output(rows, [span(10, 11, 0), span(11, 13, 1)])
    assert len(result) == 1
    assert result[0] == {'start': pytest.approx(0.8), 'end': pytest.approx(1.6), 'text': '老大'}


def test_short_fragments_are_joined_before_readability_filter():
    rows = [{'start': 9.96, 'end': 10.04, 'text': '哥'}]
    result = _map_asr_to_output(rows, [span(9, 10, 0), span(10, 11, 1)])
    assert len(result) == 1
    assert result[0]['text'] == '哥'
    assert abs(result[0]['start'] - 0.96) < 1e-9
    assert abs(result[0]['end'] - 1.04) < 1e-9


def test_deleted_source_interval_is_not_treated_as_continuous_speech():
    rows = [{'start': 10, 'end': 14, 'text': '不能把删去的话补回来'}]
    result = _map_asr_to_output(rows, [span(10, 11, 0), span(13, 14, 1)])
    assert len(result) == 2


def test_repeated_playback_and_distinct_utterances_stay_separate():
    rows = [{'start': 10, 'end': 11, 'text': '哥'}, {'start': 11, 'end': 12, 'text': '哥'}]
    result = _map_asr_to_output(rows, [span(10, 12, 0), span(10, 12, 2)])
    assert len(result) == 4


def test_output_gap_is_not_bridged():
    result = _map_asr_to_output([{'start': 10, 'end': 12, 'text': '老大'}],
                                [span(10, 11, 0), span(11, 12, 2)])
    assert len(result) == 2


def test_different_source_files_do_not_merge_at_same_numeric_boundary():
    first, second = span(10, 11, 0), span(11, 12, 1)
    first['entry'] = {'source_path': 'one.mp4'}
    second['entry'] = {'source_path': 'two.mp4'}
    assert len(_map_asr_to_output([{'start': 10, 'end': 12, 'text': '老大'}],
                                 [first, second])) == 2


def test_different_source_ids_sharing_a_path_stay_separate():
    first, second = span(10, 11, 0), span(11, 12, 1)
    first['entry'] = {'source_path': 'shared.mp4', 'source_id': 'episode-a'}
    second['entry'] = {'source_path': 'shared.mp4', 'source_id': 'episode-b'}
    assert len(_map_asr_to_output([{'start': 10, 'end': 12, 'text': '不应跨源'}],
                                 [first, second])) == 2


def test_source_srt_through_ass_keeps_first_syllable_and_continuous_display(tmp_path, monkeypatch):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg unavailable')
    monkeypatch.setitem(CONFIG, 'burn_subtitles', True)
    monkeypatch.setitem(CONFIG, 'subtitle_original_in_gaps', True)
    monkeypatch.setitem(CONFIG, 'source_subtitle_mask_policy', 'off')
    (tmp_path / 'user_subtitles.srt').write_text(
        '1\n00:00:10,800 --> 00:00:11,600\n老大\n', encoding='utf-8')
    # Same source, continuous speech, but a new picture-shot boundary at output 1s.
    (tmp_path / 'clip_plan_validated.json').write_text(json.dumps({'clips': [
        {'source_start': 10, 'source_end': 11, 'output_start': 0, 'output_end': 1},
        {'source_start': 11, 'source_end': 13, 'output_start': 1, 'output_end': 3}]}))
    entries = _combined_subtitle_entries([], tmp_path, 3)
    assert len(entries) == 1
    assert entries[0]['start'] == pytest.approx(0.8)
    assert entries[0]['end'] == pytest.approx(1.6)
    ass = _generate_ass([], tmp_path, 3, {'width': 320, 'height': 240})
    assert ass.read_text(encoding='utf-8').count('Dialogue:') == 1
    command = ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=black:s=320x240:r=24:d=3',
               '-vf', f"subtitles={ass},select='eq(n,19)+eq(n,20)+eq(n,23)+eq(n,24)+eq(n,25)+eq(n,39)'",
               '-fps_mode', 'passthrough', '-an', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    raw = subprocess.run(command, capture_output=True, check=True).stdout
    size = 320 * 240 * 3
    assert len(raw) == size * 6
    frames = [raw[i * size:(i + 1) * size] for i in range(6)]
    assert max(frames[0]) == max(frames[-1]) == 0
    assert min(max(frame) for frame in frames[1:-1]) > 100
    assert frames[1] == frames[2] == frames[3] == frames[4]


def test_join_survives_four_decimal_timeline_serialization_of_the_cut_point():
    """timeline.json floors starts and ceils ends onto a 1e-4 grid; joins must hold."""
    from timeline import _ceil_time, _floor_time

    cut = 3.00005  # deliberately between two serialization ticks
    spans = [
        {'source_start': _floor_time(1.0), 'source_end': _ceil_time(cut),
         'output_start': _floor_time(0.0), 'output_end': _ceil_time(cut - 1.0),
         'source_id': 's', 'source_path': 's.mp4'},
        {'source_start': _floor_time(cut), 'source_end': _ceil_time(5.0),
         'output_start': _floor_time(cut - 1.0), 'output_end': _ceil_time(4.0),
         'source_id': 's', 'source_path': 's.mp4'},
    ]

    result = _map_asr_to_output([{'start': 1.5, 'end': 4.5, 'text': '跨过剪辑点的一句'}], spans)

    assert len(result) == 1
    assert result[0]['start'] == pytest.approx(0.5, abs=2e-4)
    assert result[0]['end'] == pytest.approx(3.5, abs=2e-4)
