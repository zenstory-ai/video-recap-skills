"""Bind an explicit output-clock subtitle track to media before any consumer uses it.

The first render integration deliberately supports adopted AAC only. A future
newly mixed narration track needs its own final-mix identity, not this input's
soundtrack. Bound cue timing is a declared decision, not proof of speech onset.
"""

import bisect
import json
from fractions import Fraction
from pathlib import Path

from artifacts import file_identity
from adoption.frozen_audio import probe_audio_packets
from lib import run_cmd
from subtitles.track import load_subtitle_track

TRACK = 'subtitle_track.json'
VALIDATION = 'subtitle_track_validation.json'
VALIDATION_SCHEMA = 1
PROJECTOR_VERSION = 1


def current_bindings(video, selected_audio_stream=0, *, edit_plan_path=None):
    """Compute the binding facts independently of the supplied subtitle track.

    The picture binding is the resolved media path (plus the edit plan path when one
    exists); the audio binding is the selected stream ordinal with its sample rate and
    packet count.
    """
    packets = probe_audio_packets(video, selected_audio_stream)
    picture = {'path': str(Path(video).resolve())}
    if edit_plan_path is not None:
        picture['edit_plan'] = str(Path(edit_plan_path).resolve())
    return {'picture': picture,
            'audio': {'selected_stream': selected_audio_stream,
                      'sample_rate': packets['sample_rate'],
                      'packet_count': packets['packet_count']}}


def _picture_clock(video):
    result = run_cmd([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_streams', '-show_frames',
        '-show_entries', 'stream=time_base,start_pts,duration_ts:frame=pts', '-of', 'json', str(video),
    ])
    if result.returncode:
        raise ValueError(f'Cannot verify subtitle picture clock: {result.stderr}')
    data = json.loads(result.stdout)
    stream = data['streams'][0]
    timebase = Fraction(stream['time_base'])
    if int(stream['start_pts']) != 0:
        raise ValueError('Bound subtitle render requires an output-clock video starting at zero')
    duration = int(stream['duration_ts']) * timebase
    pts = [int(frame['pts']) * timebase for frame in data['frames']]
    if not pts or pts[0] != 0 or any(b <= a for a, b in zip(pts, pts[1:])) or pts[-1] >= duration:
        raise ValueError('Cannot verify picture frame clock: expected strictly increasing PTS within duration')
    return pts, duration


def _project_boundary(target, frame_pts, duration):
    """Choose an ASS centisecond that changes on the first frame at/after a cue.

    ASS's 10ms clock is coarser than most frame clocks and can otherwise round a cue
    forward by one frame. Do not silently degrade if two relevant frames cannot be
    separated in that clock.
    libass receives presentation time in integer milliseconds from ffmpeg.
    """
    index = bisect.bisect_left(frame_pts, target)
    chosen = frame_pts[index] if index < len(frame_pts) else duration
    centiseconds = chosen * 100 // 1
    threshold_ms = int(centiseconds) * 10
    previous_ms = int(frame_pts[index - 1] * 1000) if index else -1
    if threshold_ms <= previous_ms:
        raise ValueError('ASS 10ms clock cannot represent this cue boundary; use a frame-capable renderer')
    ass_time = f'{centiseconds // 360000}:{centiseconds // 6000 % 60:02d}:{centiseconds // 100 % 60:02d}.{centiseconds % 100:02d}'
    return {'requested_time': str(target), 'frame_index': index, 'pts': str(chosen),
            'delta': str(chosen - target), 'ass_time': ass_time}


def prepare_subtitle_track(input_video, work_dir, video_duration, *, audio_mode,
                           selected_audio_stream=0, edit_plan_path=None,
                           reject_legacy_estimate=False):
    """Validate and project an explicit track before SRT/ASS/timeline/QC consumers.

    No track preserves legacy behavior. A present but invalid track fails instead
    of falling back to proportional timing. The persisted record is not approval.
    """
    work = Path(work_dir)
    path = work / TRACK
    validation = work / VALIDATION
    validation.unlink(missing_ok=True)
    if not path.exists():
        return None
    if audio_mode != 'adopted-packet-copy':
        raise ValueError('Explicit subtitle_track currently requires adopted-packet-copy; new mix is not bound')
    document = json.loads(path.read_bytes())
    bindings = current_bindings(input_video, selected_audio_stream, edit_plan_path=edit_plan_path)
    frame_pts, duration = _picture_clock(input_video)
    if abs(float(duration) - float(video_duration)) > 0.05:
        raise ValueError('Picture duration differs from assembly duration')
    loaded = load_subtitle_track(
        document, expected_picture_identity=bindings['picture'], expected_audio_identity=bindings['audio'],
        expected_duration_seconds=duration, reject_legacy_estimate=reject_legacy_estimate,
    )
    clock = document['clock']['timebase']
    tick = Fraction(clock['numerator'], clock['denominator'])
    for entry, cue in zip(loaded['entries'], document['cues']):
        start = _project_boundary(cue['start_tick'] * tick, frame_pts, duration)
        end = _project_boundary(cue['end_tick'] * tick, frame_pts, duration)
        if end['frame_index'] <= start['frame_index']:
            raise ValueError('Subtitle cue has no visible frame in the actual picture clock')
        entry.update(_bound_track=True, ass_start=start['ass_time'], ass_end=end['ass_time'],
                     start=float(Fraction(start['pts'])), end=float(Fraction(end['pts'])))
        entry['frame_projection'] = {'requested_ticks': [cue['start_tick'], cue['end_tick']],
                                     'start': start, 'end': end}
    loaded['validation_schema'] = VALIDATION_SCHEMA
    loaded['projector_version'] = PROJECTOR_VERSION
    loaded['binding'] = {
        'input_video': str(Path(input_video).resolve()), 'identities': bindings,
        'inputs': {'track': file_identity(path), 'video': file_identity(input_video),
                   'edit_plan': file_identity(edit_plan_path) if edit_plan_path is not None else None},
        'duration': str(duration), 'frame_count': len(frame_pts),
        'edit_plan': str(Path(edit_plan_path).resolve()) if edit_plan_path is not None else None,
        'verification': 'media_binding_and_declared_frame_projection_only',
        'direct_listening': 'NOT_CHECKED', 'acoustic_alignment': 'NOT_CHECKED',
    }
    validation.write_text(json.dumps(loaded, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return loaded


def _load_validation(work):
    path = Path(work) / VALIDATION
    if not path.exists():
        raise ValueError('Explicit subtitle track must be prepared against current media before use')
    loaded = json.loads(path.read_text(encoding='utf-8'))
    if (type(loaded.get('validation_schema')) is not int or loaded['validation_schema'] != VALIDATION_SCHEMA
            or type(loaded.get('projector_version')) is not int or loaded['projector_version'] != PROJECTOR_VERSION):
        raise ValueError('Subtitle validation/projector version changed; prepare again')
    return loaded


def bound_subtitle_entries(work_dir, video_duration):
    """Return prepared exact entries, or None only when no explicit track exists."""
    if work_dir is None:
        return None
    work = Path(work_dir)
    if not (work / TRACK).exists():
        return None
    loaded = _load_validation(work)
    record = loaded['binding']
    inputs = record['inputs']
    if file_identity(work / TRACK) != inputs['track']:
        raise ValueError('stale subtitle track: author file changed after prepare')
    video = Path(record['input_video'])
    if not video.is_file() or file_identity(video) != inputs['video']:
        raise ValueError('stale subtitle track: adopted media changed after prepare')
    if record['edit_plan'] and (not Path(record['edit_plan']).is_file()
                                or file_identity(record['edit_plan']) != inputs['edit_plan']):
        raise ValueError('stale subtitle track: edit plan changed after prepare')
    if abs(float(Fraction(record['duration'])) - float(video_duration)) > 0.05:
        raise ValueError('stale subtitle track: consumer duration changed after prepare')
    return loaded['entries']


def verify_rendered_picture(work_dir, output_path):
    """Check the actual rendered frame clock, not just pre-render declarations."""
    work = Path(work_dir)
    if not (work / TRACK).exists():
        return None
    path = work / VALIDATION
    loaded = json.loads(path.read_text(encoding='utf-8'))
    bound_subtitle_entries(work, float(Fraction(loaded['binding']['duration'])))
    loaded['rendered_picture'] = {'output': str(Path(output_path).resolve()),
                                  'frame_clock_verified': False}
    path.write_text(json.dumps(loaded, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if _picture_clock(output_path) != _picture_clock(loaded['binding']['input_video']):
        raise ValueError('Rendered frame clock changed; subtitle frame projection is no longer valid')
    loaded['rendered_picture'].update(frame_clock_verified=True,
                                      frame_count=loaded['binding']['frame_count'],
                                      identity=file_identity(output_path))
    path.write_text(json.dumps(loaded, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return loaded['rendered_picture']


def manifest_subtitle_evidence(work_dir, input_video, output_path):
    """Only expose evidence for the actual input and verified output of this render."""
    work = Path(work_dir)
    if not (work / TRACK).exists():
        return None
    loaded = _load_validation(work)
    bound_subtitle_entries(work, float(Fraction(loaded['binding']['duration'])))
    rendered = loaded.get('rendered_picture', {})
    if rendered.get('frame_clock_verified') is not True:
        raise ValueError('Subtitle output frame clock has not been verified')
    if str(Path(input_video).resolve()) != loaded['binding']['input_video']:
        raise ValueError('stale subtitle manifest: input media differs')
    if not Path(output_path).is_file() or file_identity(output_path) != rendered.get('identity'):
        raise ValueError('stale subtitle manifest: actual output differs from verified render')
    return {'validation_path': str((work / VALIDATION).resolve()),
            'binding': loaded['binding'], 'metadata': loaded['metadata'], 'rendered_picture': rendered}
