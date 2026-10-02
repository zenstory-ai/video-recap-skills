"""Exact picture and AAC clocks for the frozen-audio adoption paths.

``probe_picture`` reads the full CFR presentation clock of a picture stream;
``validate_aac_packet_interval`` proves an AAC stream's packets tile its header
interval; ``validate_pair_timing`` checks the two intervals agree within one frame
or one AAC packet. These are narrow container-clock gates, not a perceptual
synchronization verdict.
"""

from fractions import Fraction

from assemble_constants import SUPPORTED_PICTURE_CODECS
from adoption.strict_inputs import probe_json


def _time(value):
    if value in (None, 'N/A'):
        raise ValueError('Missing media timing')
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError('Invalid media timing') from exc


def probe_picture(path):
    """Actual full CFR presentation clock plus codec-order packet sizes and timestamps."""
    data = probe_json(path, '-select_streams', 'v:0', '-show_streams', '-show_packets',
                  '-show_format', '-show_entries',
                  'format=format_name:stream=codec_name,profile,level,width,height,pix_fmt,'
                  'sample_aspect_ratio,field_order,color_range,color_space,color_transfer,'
                  'color_primaries,chroma_location,time_base,start_pts,duration_ts,avg_frame_rate'
                  ':packet=pts,dts,duration,size,side_data_list')
    streams = data.get('streams', [])
    if len(streams) != 1 or streams[0].get('codec_name') not in SUPPORTED_PICTURE_CODECS:
        raise ValueError('Picture clock requires one selected H264/HEVC picture stream')
    if 'mp4' not in data.get('format', {}).get('format_name', '').split(','):
        raise ValueError('Picture clock currently requires an MP4-family container')
    v = streams[0]
    fps, tb = _time(v.get('avg_frame_rate')), _time(v.get('time_base'))
    if not 1 <= fps <= 120 or tb <= 0 or v.get('start_pts') != 0:
        raise ValueError('Picture requires positive CFR and a known zero start')
    duration = _time(v.get('duration_ts')) * tb
    frames = probe_json(path, '-select_streams', 'v:0', '-show_frames',
                    '-show_entries', 'frame=pts')['frames']
    pts = [_time(frame.get('pts')) * tb for frame in frames]
    if not pts or any(t != Fraction(i, fps) for i, t in enumerate(pts)) or duration != len(pts) / fps:
        raise ValueError('Picture requires complete zero-origin CFR frame clock and exact duration')
    decoder_keys = ['codec_name', 'profile', 'level', 'width', 'height', 'pix_fmt',
                    'sample_aspect_ratio', 'field_order', 'color_range', 'color_space',
                    'color_transfer', 'color_primaries', 'chroma_location']
    packets = []
    for packet in data.get('packets', []):
        if packet.get('size') is None:
            raise ValueError('Picture packet missing size')
        ticks = {key: str(_time(packet.get(key)) * tb) for key in ['pts', 'dts', 'duration']}
        if _time(ticks['duration']) <= 0:
            raise ValueError('Picture packet has invalid duration')
        packets.append({**ticks, 'size': int(packet['size']),
                        'side_data_list': packet.get('side_data_list', [])})
    if len(packets) != len(pts):
        raise ValueError('Picture packet/frame counts disagree')
    return {'decoder': {key: v.get(key) for key in decoder_keys}, 'packets': packets,
            'frame_pts': [str(t) for t in pts], 'frame_count': len(pts), 'fps': str(fps),
            'duration': str(duration), 'start': '0'}


def validate_aac_packet_interval(audio):
    """Validate exact AAC packet continuity against the stream-header interval."""
    if audio['codec'] != 'aac':
        raise ValueError('Adopted audio must be AAC')
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
