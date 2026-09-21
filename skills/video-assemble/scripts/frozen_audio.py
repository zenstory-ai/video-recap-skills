"""Probe and verify an adopted AAC stream without decoding or rewriting it."""

import hashlib
import json
from fractions import Fraction
from pathlib import Path

from lib import run_cmd


def _fraction(value):
    return Fraction(str(value))


def _rational_ticks(value, time_base):
    if value in (None, "N/A"):
        return None
    value = Fraction(int(value)) * Fraction(time_base)
    return f"{value.numerator}/{value.denominator}"


def _probe(path, *args):
    result = run_cmd([
        "ffprobe", "-v", "error", *args, "-of", "json", str(path),
    ])
    if result.returncode != 0:
        raise RuntimeError(f"无法探测媒体 {path}: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"ffprobe 返回无效 JSON: {path}") from exc


def probe_audio_packets(path, audio_stream_index):
    """Return packet payload and rational timestamp identity for one audio stream.

    ``audio_stream_index`` is the zero-based audio-stream ordinal accepted by
    ffmpeg's ``0:a:N`` selector, not the file-wide absolute stream index.
    """
    payload = _probe(
        path,
        "-select_streams", f"a:{audio_stream_index}",
        "-show_streams", "-show_packets", "-show_data_hash", "sha256",
        "-show_entries",
        "stream=index,codec_name,time_base,start_pts,start_time,duration_ts,duration,sample_rate,channels,"
        "channel_layout,extradata_hash:packet=pts,dts,duration,size,data_hash,side_data_list",
    )
    streams = payload.get("streams", [])
    if len(streams) != 1:
        raise RuntimeError(f"找不到音频流 a:{audio_stream_index}: {Path(path)}")
    stream = streams[0]
    time_base = stream.get("time_base")
    if not time_base:
        raise RuntimeError(f"音频流 a:{audio_stream_index} 缺少 time_base")
    codec = stream.get("codec_name")
    extradata_hash = stream.get("extradata_hash", "")
    extradata_sha256 = (
        extradata_hash.removeprefix("SHA256:").lower() if extradata_hash else None
    )
    if codec == "aac" and (
        extradata_sha256 is None
        or len(extradata_sha256) != 64
        or any(char not in "0123456789abcdef" for char in extradata_sha256)
    ):
        raise RuntimeError(
            f"AAC 音频流 a:{audio_stream_index} 缺少有效 decoder extradata hash"
        )
    sample_rate = int(stream["sample_rate"]) if stream.get("sample_rate") else None
    channels = int(stream["channels"]) if stream.get("channels") else None
    channel_layout = stream.get("channel_layout")
    decoder = {
        "codec": codec,
        "sample_rate": sample_rate,
        "channels": channels,
        "channel_layout": channel_layout,
        "extradata_sha256": extradata_sha256,
    }
    packets = []
    ordered_payload_hashes = hashlib.sha256()
    for packet in payload.get("packets", []):
        data_hash = packet.get("data_hash")
        if not data_hash:
            raise RuntimeError(f"音频流 a:{audio_stream_index} 的 packet 缺少 payload hash")
        digest = data_hash.removeprefix("SHA256:").lower()
        ordered_payload_hashes.update(bytes.fromhex(digest))
        packets.append({
            "payload_sha256": digest,
            "size": int(packet["size"]),
            "pts": _rational_ticks(packet.get("pts"), time_base),
            "dts": _rational_ticks(packet.get("dts"), time_base),
            "duration": _rational_ticks(packet.get("duration"), time_base),
            "side_data_list": packet.get("side_data_list", []),
        })
    return {
        "selected_audio_stream_index": audio_stream_index,
        "absolute_stream_index": int(stream["index"]),
        "codec": codec,
        "time_base": time_base,
        "start_time": stream.get("start_time"),
        "duration": stream.get("duration"),
        "sample_rate": sample_rate,
        "channels": channels,
        "channel_layout": channel_layout,
        "extradata_sha256": extradata_sha256,
        "decoder": decoder,
        "packet_count": len(packets),
        "payload_sha256": ordered_payload_hashes.hexdigest(),
        "packets": packets,
    }


def _picture_interval(path):
    payload = _probe(
        path,
        "-select_streams", "v:0", "-show_streams",
        "-show_entries", "stream=time_base,start_pts,start_time,duration_ts,duration,avg_frame_rate",
    )
    streams = payload.get("streams", [])
    if len(streams) != 1:
        raise RuntimeError(f"找不到主画面流 v:0: {Path(path)}")
    stream = streams[0]
    start = _fraction(stream.get("start_time", "0"))
    if stream.get("duration") in (None, "N/A"):
        raise RuntimeError("主画面流缺少可验证时长")
    duration = _fraction(stream["duration"])
    frame_rate = Fraction(stream.get("avg_frame_rate", "0/1"))
    frame = Fraction(1, 1000) if frame_rate <= 0 else 1 / frame_rate
    return start, duration, frame


def validate_adopted_source(path, audio_stream_index):
    """Fail unless the selected input is copyable AAC spanning the picture interval."""
    audio = probe_audio_packets(path, audio_stream_index)
    if audio["codec"] != "aac":
        raise RuntimeError(
            f"adopted-packet-copy 当前只支持 MP4 AAC stream-copy；"
            f"a:{audio_stream_index} 是 {audio['codec'] or 'unknown'}"
        )
    if not audio["packets"]:
        raise RuntimeError(f"音频流 a:{audio_stream_index} 没有 packet，不能采用")
    picture_start, picture_duration, frame_tolerance = _picture_interval(path)
    if audio["start_time"] in (None, "N/A") or audio["duration"] in (None, "N/A"):
        raise RuntimeError(f"音频流 a:{audio_stream_index} 缺少可验证起止时间")
    audio_start = _fraction(audio["start_time"])
    audio_duration = _fraction(audio["duration"])
    packet_durations = [
        _fraction(packet["duration"])
        for packet in audio["packets"] if packet["duration"] is not None
    ]
    tolerance = max([frame_tolerance, Fraction(1, 1000), *packet_durations])
    if (
        abs(audio_start - picture_start) > tolerance
        or abs(audio_duration - picture_duration) > tolerance
    ):
        raise RuntimeError(
            "采用音频与画面时长/起点不兼容: "
            f"picture={float(picture_start):.6f}+{float(picture_duration):.6f}s, "
            f"audio={float(audio_start):.6f}+{float(audio_duration):.6f}s"
        )
    return audio


def verify_adopted_audio(input_path, output_path, input_stream_index, output_stream_index=0):
    """Probe the rendered output and prove packet payload/timing identity."""
    expected = probe_audio_packets(input_path, input_stream_index)
    actual = probe_audio_packets(output_path, output_stream_index)
    if expected["decoder"] != actual["decoder"]:
        raise RuntimeError("采用音频 decoder identity 已改变")
    if expected["packet_count"] != actual["packet_count"]:
        raise RuntimeError(
            f"采用音频 packet count 已改变: {expected['packet_count']} -> {actual['packet_count']}"
        )
    if expected["payload_sha256"] != actual["payload_sha256"]:
        raise RuntimeError("采用音频 packet payload hash 不一致")
    expected_packet_core = [
        {key: value for key, value in packet.items() if key != "side_data_list"}
        for packet in expected["packets"]
    ]
    actual_packet_core = [
        {key: value for key, value in packet.items() if key != "side_data_list"}
        for packet in actual["packets"]
    ]
    if expected_packet_core != actual_packet_core:
        raise RuntimeError("采用音频 packet PTS/DTS/duration/size 不一致")
    if [packet["side_data_list"] for packet in expected["packets"]] != [
        packet["side_data_list"] for packet in actual["packets"]
    ]:
        raise RuntimeError("采用音频 packet side data 已改变")
    return {
        "verified": True,
        "selected_audio_stream_index": input_stream_index,
        "output_audio_stream_index": output_stream_index,
        "input": expected,
        "output": actual,
    }
