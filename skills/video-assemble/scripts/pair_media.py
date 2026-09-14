#!/usr/bin/env python3
"""Pair explicit picture and adopted audio assets without re-encoding either stream.

This is asset pairing, not a claim of source reconstruction, correct editorial
selection, speech alignment, or release approval. Existing assemble then consumes
paired.mp4; its subtitle binding must be newly computed on that container/a:0.
"""

import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import subprocess

from assemble_constants import SUPPORTED_PICTURE_CODECS
from frozen_audio import probe_audio_packets, verify_adopted_audio


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _save(path, value):
    temporary = path.with_suffix('.writing.json')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def _fields(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError(f'Expected exactly these fields: {required}')


def _asset(value, audio=False):
    _fields(value, ['path', 'sha256', 'selected_stream'] if audio else ['path', 'sha256'])
    if not isinstance(value['path'], str) or not value['path'] or '://' in value['path']:
        raise ValueError('Asset requires a local file path')
    if not isinstance(value['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', value['sha256']):
        raise ValueError('Asset requires lowercase SHA256')
    if audio and (type(value['selected_stream']) is not int or value['selected_stream'] < 0):
        raise ValueError('Audio selected_stream must be a nonnegative integer ordinal')
    path = Path(value['path']).resolve()
    if not path.is_file() or _sha256(path) != value['sha256']:
        raise ValueError('Asset identity mismatch or file missing')
    return {**value, 'path': str(path)}


def _probe(path, *args):
    command = ['ffprobe', '-v', 'error', *args, '-of', 'json', str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if result.returncode or result.stderr.strip():
        raise ValueError(f'Media probe failed: {result.stderr.strip()}')
    return json.loads(result.stdout)


def _time(value):
    if value in (None, 'N/A'):
        raise ValueError('Missing media timing')
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError('Invalid media timing') from exc


def probe_picture(path):
    """Actual full CFR presentation clock plus codec-order compressed packets."""
    data = _probe(path, '-select_streams', 'v:0', '-show_streams', '-show_packets',
                  '-show_format', '-show_data_hash', 'sha256', '-show_entries',
                  'format=format_name:stream=codec_name,profile,level,width,height,pix_fmt,'
                  'sample_aspect_ratio,field_order,color_range,color_space,color_transfer,'
                  'color_primaries,chroma_location,time_base,start_pts,duration_ts,avg_frame_rate,'
                  'extradata_hash:packet=pts,dts,duration,size,data_hash,side_data_list')
    streams = data.get('streams', [])
    if len(streams) != 1 or streams[0].get('codec_name') not in SUPPORTED_PICTURE_CODECS:
        raise ValueError('Pairing requires one selected H264/HEVC picture stream')
    if 'mp4' not in data.get('format', {}).get('format_name', '').split(','):
        raise ValueError('Pairing currently requires MP4-family picture container')
    v = streams[0]
    fps, tb = _time(v.get('avg_frame_rate')), _time(v.get('time_base'))
    if not 1 <= fps <= 120 or tb <= 0 or v.get('start_pts') != 0:
        raise ValueError('Picture requires positive CFR and a known zero start')
    duration = _time(v.get('duration_ts')) * tb
    frames = _probe(path, '-select_streams', 'v:0', '-show_frames',
                    '-show_entries', 'frame=pts')['frames']
    pts = [_time(frame.get('pts')) * tb for frame in frames]
    if not pts or any(t != Fraction(i, fps) for i, t in enumerate(pts)) or duration != len(pts) / fps:
        raise ValueError('Picture requires complete zero-origin CFR frame clock and exact duration')
    extradata = v.get('extradata_hash', '')
    if not re.fullmatch('SHA256:[a-fA-F0-9]{64}', extradata):
        raise ValueError('Picture missing decoder extradata hash')
    decoder_keys = ['codec_name', 'profile', 'level', 'width', 'height', 'pix_fmt',
                    'sample_aspect_ratio', 'field_order', 'color_range', 'color_space',
                    'color_transfer', 'color_primaries', 'chroma_location', 'extradata_hash']
    packets = []
    for packet in data.get('packets', []):
        digest = packet.get('data_hash', '')
        if not re.fullmatch('SHA256:[a-fA-F0-9]{64}', digest):
            raise ValueError('Picture packet missing payload hash')
        ticks = {key: str(_time(packet.get(key)) * tb) for key in ['pts', 'dts', 'duration']}
        if _time(ticks['duration']) <= 0:
            raise ValueError('Picture packet has invalid duration')
        packets.append({**ticks, 'payload': digest, 'size': packet['size'],
                        'side_data_list': packet.get('side_data_list', [])})
    if len(packets) != len(pts):
        raise ValueError('Picture packet/frame counts disagree')
    return {'decoder': {key: v.get(key) for key in decoder_keys}, 'packets': packets,
            'frame_pts': [str(t) for t in pts], 'frame_count': len(pts), 'fps': str(fps),
            'duration': str(duration), 'start': '0'}


def validate_aac_packet_interval(audio):
    """Validate exact AAC packet continuity against the stream-header interval."""
    if audio['codec'] != 'aac':
        raise ValueError('Pairing requires AAC adopted audio')
    packets = audio['packets']
    rate = audio['sample_rate']
    if type(rate) is not int or rate <= 0 or len(packets) < 2:
        raise ValueError('AAC packet timing is incomplete')
    nominal = _time(packets[0]['duration'])
    # Accept known AAC frame sample counts, never an arbitrary huge duration.
    if nominal * rate not in {960, 1024, 1920, 2048}:
        raise ValueError('Unsupported AAC nominal packet duration')
    for i, packet in enumerate(packets):
        d = _time(packet['duration'])
        if d <= 0 or d > nominal or (i < len(packets) - 1 and d != nominal):
            raise ValueError('Nonuniform or invalid AAC packet duration')
        p, t = _time(packet['pts']), _time(packet['dts'])
        if p != t:
            raise ValueError('Unsupported AAC PTS/DTS difference')
        if i and p != _time(packets[i-1]['pts']) + _time(packets[i-1]['duration']):
            raise ValueError('AAC packet clock has gaps or overlaps')
    audio_start, audio_duration = _time(audio['start_time']), _time(audio['duration'])
    if audio_duration <= 0:
        raise ValueError('Invalid audio interval')
    audio_end = audio_start + audio_duration
    first_pts = _time(packets[0]['pts'])
    last_end = _time(packets[-1]['pts']) + _time(packets[-1]['duration'])
    if (not 0 <= audio_start - first_pts <= nominal
            or abs(last_end - audio_end) > Fraction(1, rate)):
        raise ValueError('AAC packet clock does not match stream interval')
    return audio_start, audio_end, nominal


def validate_pair_timing(picture, audio):
    """Narrow AAC/CFR compatibility; not a perceptual synchronization verdict."""
    audio_start, audio_end, nominal = validate_aac_packet_interval(audio)
    tolerance = max(1 / Fraction(picture['fps']), nominal)
    end_delta = audio_end - Fraction(picture['duration'])
    if abs(audio_start) > tolerance or abs(end_delta) > tolerance:
        raise ValueError('Picture/audio interval mismatch; no implicit trim, padding, offset or retime')
    return {'start_delta': str(audio_start),
            'end_delta': str(end_delta),
            'tolerance': str(tolerance), 'nominal_aac_packet': str(nominal)}


def _run_mux(command, directory):
    _save(directory / 'mux.command.json', command)
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    (directory / 'mux.log').write_text(result.stderr)
    if result.returncode:
        raise RuntimeError('Pair mux failed; see mux.log')


def _verify_output(path):
    data = _probe(path, '-show_streams')
    if [stream.get('codec_type') for stream in data['streams']] != ['video', 'audio']:
        raise ValueError('Paired output must contain only v:0 and a:0')
    result = subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-threads', '2', '-i', str(path),
                             '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'],
                            capture_output=True, text=True, timeout=600)
    if result.returncode or result.stderr.strip():
        raise ValueError('Paired output full decode failed')


def run_pair(plan_path, output_dir, *, plan_only=False):
    plan_path, directory = Path(plan_path).resolve(), Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    report_path = directory / 'pair_run.json'
    staged, output = directory / 'paired.rendering.mp4', directory / 'paired.mp4'
    report = {'artifact': 'media_pair_run', 'schema_version': 1, 'status': 'PREPARING',
              'direct_listening': 'NOT_CHECKED', 'normal_speed_review': 'NOT_CHECKED',
              'release_approved': False}
    _save(report_path, report)
    try:
        raw = plan_path.read_bytes()
        plan_hash = hashlib.sha256(raw).hexdigest()
        plan = json.loads(raw)
        _fields(plan, ['artifact', 'schema_version', 'picture', 'audio'])
        if plan['artifact'] != 'media_pair' or type(plan['schema_version']) is not int or plan['schema_version'] != 1:
            raise ValueError('Unsupported media_pair schema')
        picture, audio = _asset(plan['picture']), _asset(plan['audio'], audio=True)
        report.update(plan={'path': str(plan_path), 'sha256': plan_hash},
                      inputs={'picture': picture, 'audio': audio})
        def assert_inputs():
            if _sha256(plan_path) != plan_hash:
                raise ValueError('Pair plan changed during operation')
            for asset in [picture, audio]:
                if _sha256(asset['path']) != asset['sha256']:
                    raise ValueError('Pair input changed during operation')
        video_facts = probe_picture(picture['path'])
        audio_facts = probe_audio_packets(audio['path'], audio['selected_stream'])
        report['timing'] = validate_pair_timing(video_facts, audio_facts)
        assert_inputs()
        report['picture'] = {k: v for k, v in video_facts.items() if k not in ['packets', 'frame_pts']}
        report['audio'] = {'input_stream': audio['selected_stream'], 'output_stream': 0,
                           'packet_count': audio_facts['packet_count']}
        if plan_only:
            report['status'] = 'PLANNED'
            _save(report_path, report)
            return report
        command = ['ffmpeg', '-nostdin', '-v', 'error', '-n', '-copyts',
                   '-i', picture['path'], '-i', audio['path'], '-map', '0:v:0',
                   '-map', f"1:a:{audio['selected_stream']}", '-c', 'copy',
                   '-movie_timescale', str(audio_facts['sample_rate']),
                   '-movflags', '+faststart', str(staged)]
        _run_mux(command, directory)
        assert_inputs()
        if probe_picture(staged) != video_facts:
            raise ValueError('Paired picture packets, decoder, geometry/color or full frame clock changed')
        proof = verify_adopted_audio(audio['path'], staged, audio['selected_stream'], 0)
        output_timing = validate_pair_timing(video_facts, proof['output'])
        if output_timing != report['timing']:
            raise ValueError('Output audio presentation interval changed')
        report['output_timing'] = output_timing
        _verify_output(staged)
        assert_inputs()
        _save(directory / 'picture_identity.json', video_facts)
        _save(directory / 'adopted_audio_identity.json', proof)
        report['output'] = {'path': str(output), 'sha256': _sha256(staged), 'full_decode': 'PASS',
                            'picture_identity': 'EXACT', 'audio_packet_identity': 'EXACT'}
        staged.rename(output)
        report['status'] = 'PAIR_RENDERED'
        _save(report_path, report)
        return report
    except Exception as exc:
        # A partial unique run is evidence, never a final/current asset. Keep logs.
        staged.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        report.pop('output', None)
        report.update(status='FAILED', error=f'{type(exc).__name__}: {exc}')
        _save(report_path, report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    report = run_pair(args.plan, args.output_dir, plan_only=args.plan_only)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
