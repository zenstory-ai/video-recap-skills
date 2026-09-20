"""Real CLI proof that multi-source picture cuts feed the existing full adoption path."""

import array
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import wave

import pytest


ROOT = Path(__file__).resolve().parents[2]
CUT = ROOT / "skills/video-cut/scripts/cut.py"
RECAP = ROOT / "skills/video-recap/scripts/recap.py"
SOURCE_SCORE = ROOT / "skills/video-assemble/scripts/source_score.py"
RATE = 48_000
STRICT_TEMPO = {
    "global_atempo": 1.0,
    "bounded_segment_fit": False,
    "segment_tempo_max": 1.0,
    "cumulative_tempo_max": 1.0,
    "cumulative_tempo_hard_max": 1.0,
}


pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="requires local ffmpeg/ffprobe",
)


def _run(command, *, env=None, check=True, timeout=120):
    return subprocess.run(
        list(map(str, command)),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        timeout=timeout,
    )


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_tone(path, frequency, seconds=0.4):
    samples = array.array(
        "h",
        (
            int(8_000 * math.sin(2 * math.pi * frequency * index / RATE))
            for index in range(round(RATE * seconds))
        ),
    )
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, RATE, len(samples), "NONE", "not compressed"))
        output.writeframes(samples.tobytes())
    return path


def _make_source(path, color, frequency):
    _run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color={color}:size=64x48:rate=12:duration=1",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate={RATE}:duration=1",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            path,
        ]
    )
    return path


def _cut(sources, order, work, env):
    work.mkdir()
    manifest = work / "multi_source_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [
                    {"source_id": source_id, "source_path": str(path), "duration": 1}
                    for source_id, path in sources.items()
                ],
            }
        ),
        encoding="utf-8",
    )
    (work / "clip_plan.json").write_text(
        json.dumps(
            [{"source_id": source_id, "start": 0, "end": 1} for source_id in order]
        ),
        encoding="utf-8",
    )
    result = _run(
        [
            sys.executable,
            CUT,
            next(iter(sources.values())),
            "--work-dir",
            work,
            "--sources-manifest",
            manifest,
            "--no-narration-map",
        ],
        env=env,
    )
    assert "剪辑模式" in result.stdout
    return work / "edited_source.mp4"


def _picture_clock(path):
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=avg_frame_rate,nb_read_frames",
            "-of",
            "json",
            path,
        ]
    )
    stream = json.loads(result.stdout)["streams"][0]
    return stream["avg_frame_rate"], int(stream["nb_read_frames"])


def _dominant_channel(path, second):
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-ss",
            str(second),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        timeout=30,
    )
    channels = [sum(raw[offset::3]) for offset in range(3)]
    return max(range(3), key=channels.__getitem__)


def _prepare_bed(sources, order, directory, env):
    plan = directory.with_suffix(".json")
    plan.write_text(
        json.dumps(
            {
                "artifact": "source_score_plan",
                "schema_version": 1,
                "output": {
                    "sample_rate": RATE,
                    "channels": 2,
                    "total_samples": 2 * RATE,
                },
                "source_segments": [
                    {
                        "id": f"source-{position}",
                        "path": str(sources[source_id]),
                        "sha256": _sha(sources[source_id]),
                        "audio_stream": 0,
                        "source_fps": "12/1",
                        "source_start_frame": 0,
                        "source_end_frame": 12,
                        "output_start_sample": position * RATE,
                        "gain": 1.0,
                        "fade_in_samples": 0,
                        "fade_out_samples": 0,
                        "fade_shape": "linear",
                        "role": "protected_original",
                    }
                    for position, source_id in enumerate(order)
                ],
                "source_silence": [],
                "score": {"kind": "none"},
            }
        ),
        encoding="utf-8",
    )
    _run([sys.executable, SOURCE_SCORE, plan, "--output-dir", directory], env=env)
    return directory / "prepared_bed_receipt.json"


def _write_mix_adoption(path, picture, receipt, narration_adoption, wav, start_sample):
    path.write_text(
        json.dumps(
            {
                "artifact": "audio_mix_adoption",
                "schema_version": 1,
                "picture_sha256": _sha(picture),
                "prepared_receipt": {"path": str(receipt), "sha256": _sha(receipt)},
                "narration_adoption_sha256": _sha(narration_adoption),
                "format": {
                    "sample_rate": RATE,
                    "channels": 2,
                    "total_samples": 2 * RATE,
                },
                "segments": [
                    {
                        "index": 0,
                        "processed_wav_sha256": _sha(wav),
                        "output_start_sample": start_sample,
                        "gain": 0.5,
                    }
                ],
                "master_gain_db": 0.0,
            }
        ),
        encoding="utf-8",
    )
    return path


def _adopt(picture, work, delivery, meta, narration, mix, env, *, check=True):
    return _run(
        [
            sys.executable,
            RECAP,
            picture,
            "--work-dir",
            work,
            "--output-dir",
            delivery,
            "--tts-meta",
            meta,
            "--narration-adoption",
            narration,
            "--audio-mix-adoption",
            mix,
            "--no-burn-subtitles",
        ],
        env=env,
        check=check,
    )


def test_reordered_multi_cut_reuses_wav_through_existing_full_adoption(tmp_path):
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "TMPDIR", "LANG")
        if key in os.environ
    }
    env.update(
        PYTHONNOUSERSITE="1",
        SCENE_CUT_SNAP="0",
        SNAP_CLIP_LINE_END="0",
        CLIP_PADDING="0",
        OUTPUT_MAX_HEIGHT="0",
    )
    sources = {
        "red": _make_source(tmp_path / "red.mp4", "red", 330),
        "blue": _make_source(tmp_path / "blue.mp4", "blue", 660),
    }
    current = _cut(sources, ["red", "blue"], tmp_path / "cut-current", env)
    reordered = _cut(sources, ["blue", "red"], tmp_path / "cut-reordered", env)

    assert _picture_clock(current) == _picture_clock(reordered) == ("12/1", 24)
    assert [_dominant_channel(current, at) for at in (0.25, 1.25)] == [0, 2]
    assert [_dominant_channel(reordered, at) for at in (0.25, 1.25)] == [2, 0]
    assert _sha(current) != _sha(reordered)

    # This deterministic sine WAV exercises binding/placement, not speech quality.
    wav = _write_tone(tmp_path / "narration_signal.wav", 997)
    wav_hash = _sha(wav)
    segment = {
        "index": 0,
        "start": 0.25,
        "end": 1.0,
        "narration": "本地测试信号",
        "spoken_text": "本地测试信号",
        "audio_path": str(wav),
        "audio_duration": 0.4,
        "pause_after_ms": 0,
        "overlaps_speech": False,
        "tts_rate_offset": 0.0,
        "processed_wav_sha256": wav_hash,
    }
    meta = tmp_path / "tts_meta.json"
    meta.write_text(json.dumps({"segments": [segment]}), encoding="utf-8")
    narration = tmp_path / "narration_adoption.json"
    narration.write_text(
        json.dumps(
            {
                "artifact": "narration_adoption",
                "schema_version": 1,
                "tts_meta_sha256": _sha(meta),
                "segments": [
                    {
                        "index": 0,
                        "spoken_text": "本地测试信号",
                        "processed_wav_sha256": wav_hash,
                        "requested_provider": "offline-fixture",
                        "requested_voice": "sine-signal",
                    }
                ],
                "tempo_policy": STRICT_TEMPO,
            }
        ),
        encoding="utf-8",
    )
    sealed_inputs = {path: _sha(path) for path in (wav, meta, narration)}

    current_receipt = _prepare_bed(
        sources, ["red", "blue"], tmp_path / "bed-current", env
    )
    current_mix = _write_mix_adoption(
        tmp_path / "mix-current.json",
        current,
        current_receipt,
        narration,
        wav,
        12_000,
    )
    first = _adopt(
        current,
        tmp_path / "adopt-current",
        tmp_path / "delivery-current",
        meta,
        narration,
        current_mix,
        env,
    )
    assert "video-understanding/" not in first.stdout
    assert "video-voiceover/" not in first.stdout
    first_manifest = json.loads(
        (tmp_path / "adopt-current/assembly_manifest.json").read_text(encoding="utf-8")
    )
    assert first_manifest["audio_segments"][0]["actual_place_start"] == pytest.approx(
        0.25
    )

    stale = _adopt(
        reordered,
        tmp_path / "adopt-stale",
        tmp_path / "delivery-stale",
        meta,
        narration,
        current_mix,
        env,
        check=False,
    )
    assert stale.returncode != 0
    assert "picture" in (stale.stdout + stale.stderr).lower()
    assert "video-understanding/" not in stale.stdout
    assert "video-voiceover/" not in stale.stdout
    assert not (tmp_path / "adopt-stale/output.mp4").exists()
    assert not (tmp_path / "delivery-stale/recap_edited_source.mp4").exists()

    reordered_receipt = _prepare_bed(
        sources, ["blue", "red"], tmp_path / "bed-reordered", env
    )
    reordered_mix = _write_mix_adoption(
        tmp_path / "mix-reordered.json",
        reordered,
        reordered_receipt,
        narration,
        wav,
        60_000,
    )
    final_work = tmp_path / "adopt-reordered"
    final = _adopt(
        reordered,
        final_work,
        tmp_path / "delivery-reordered",
        meta,
        narration,
        reordered_mix,
        env,
    )
    assert "video-understanding/" not in final.stdout
    assert "video-voiceover/" not in final.stdout
    assert {path: _sha(path) for path in sealed_inputs} == sealed_inputs
    assert _sha(current_receipt) != _sha(reordered_receipt)
    current_bed = json.loads(current_receipt.read_text(encoding="utf-8"))
    reordered_bed = json.loads(reordered_receipt.read_text(encoding="utf-8"))
    assert (
        current_bed["outputs"]["prepared_bed.wav"]["pcm_payload_sha256"]
        != (reordered_bed["outputs"]["prepared_bed.wav"]["pcm_payload_sha256"])
    )

    binding = json.loads((final_work / "audio_mix_binding.json").read_text(encoding="utf-8"))
    assert binding["picture"]["sha256"] == _sha(reordered)
    assert binding["prepared_receipt"]["sha256"] == _sha(reordered_receipt)
    assert binding["segments"][0]["processed_wav_sha256"] == wav_hash
    assert binding["segments"][0]["output_start_sample"] == 60_000
    narration_binding = json.loads(
        (final_work / "narration_input_binding.json").read_text(encoding="utf-8")
    )
    assert narration_binding["segments"][0]["original"]["sha256"] == wav_hash
    manifest = json.loads((final_work / "assembly_manifest.json").read_text(encoding="utf-8"))
    assert json.loads(meta.read_text(encoding="utf-8"))["segments"][0]["start"] == pytest.approx(0.25)
    assert manifest["audio_segments"][0]["actual_place_start"] == pytest.approx(1.25)
    assert manifest["audio_segments"][0]["actual_place_end"] == pytest.approx(1.65)

    timeline = json.loads((final_work / "timeline.json").read_text(encoding="utf-8"))
    narration_track = next(
        track for track in timeline["tracks"] if track["name"] == "narration"
    )
    subtitle_track = next(
        track for track in timeline["tracks"] if track["name"] == "subtitle"
    )
    assert narration_track["segments"][0]["timeline_start"] == pytest.approx(1.25)
    assert narration_track["segments"][0]["output_start_sample"] == 60_000
    assert subtitle_track["segments"][0]["timeline_start"] == pytest.approx(1.25)
    srt = (final_work / "subtitles.srt").read_text(encoding="utf-8")
    assert "00:00:01,250 --> 00:00:01,650" in srt
    assert "00:00:00,250 -->" not in srt

    output = tmp_path / "delivery-reordered/recap_edited_source.mp4"
    assert output.is_file()
    _run(["ffmpeg", "-v", "error", "-xerror", "-i", output, "-f", "null", "-"])
