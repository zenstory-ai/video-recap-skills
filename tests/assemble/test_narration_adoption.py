"""Regression tests for explicit narration adoption and byte identity binding."""

import array
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import wave

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "skills/video-assemble/scripts"
sys.path.insert(0, str(SCRIPTS))

import assemble  # noqa: E402
from lib import CONFIG  # noqa: E402
import narration_binding  # noqa: E402


HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
STRICT_TEMPO = {
    "global_atempo": 1.0,
    "bounded_segment_fit": False,
    "segment_tempo_max": 1.0,
    "cumulative_tempo_max": 1.0,
    "cumulative_tempo_hard_max": 1.0,
}


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _tone(path, frequency=997, *, seconds=1.0):
    sample_rate = 44100
    frames = int(sample_rate * seconds)
    pcm = array.array(
        "h",
        (
            int(6000 * math.sin(2 * math.pi * frequency * sample / sample_rate))
            for sample in range(frames)
        ),
    )
    if sys.byteorder != "little":
        pcm.byteswap()
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, sample_rate, frames, "NONE", "not compressed"))
        audio.writeframes(pcm.tobytes())
    return path


def _segment(audio_path, *, seconds=1.0, with_hash=True, receipt=None):
    segment = {
        "index": 0,
        "start": 0.25,
        "end": 1.75,
        "narration": "identity fixture",
        "spoken_text": "identity fixture",
        "audio_path": str(audio_path),
        "audio_duration": seconds,
        "pause_after_ms": 0,
        "overlaps_speech": False,
        "tts_rate_offset": 0.0,
    }
    if with_hash:
        segment["processed_wav_sha256"] = _sha256(audio_path)
    if receipt is not None:
        segment["provider_receipt"] = receipt
    return segment


def _adoption(tmp_path, segments, *, provider="offline-provider", voice="offline-voice"):
    meta = tmp_path / "tts_meta.json"
    meta.write_text(json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8")
    adoption = tmp_path / "narration_adoption.json"
    adoption.write_text(
        json.dumps(
            {
                "artifact": "narration_adoption",
                "schema_version": 1,
                "tts_meta_sha256": _sha256(meta),
                "segments": [
                    {
                        "index": item["index"],
                        "spoken_text": item["spoken_text"],
                        "processed_wav_sha256": item["processed_wav_sha256"],
                        "requested_provider": provider,
                        "requested_voice": voice,
                    }
                    for item in segments
                ],
                "tempo_policy": STRICT_TEMPO,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return adoption, meta


def _rewrite(path, mutate):
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_load_adoption_accepts_exact_v1_contract(tmp_path):
    audio = _tone(tmp_path / "voice.wav")
    segments = [_segment(audio)]
    adoption, meta = _adoption(tmp_path, segments)

    loaded = narration_binding.load_adoption(
        adoption, tts_meta_path=meta, tts_segments=segments
    )

    assert loaded["sha256"] == _sha256(adoption)
    assert loaded["tts_meta"] == {"path": str(meta.resolve()), "sha256": _sha256(meta)}
    assert loaded["tempo_policy"] == STRICT_TEMPO


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(schema_version=2),
        lambda value: value["segments"][0].update(unrecognized=True),
    ],
    ids=("future-version", "extra-segment-field"),
)
def test_load_adoption_rejects_contract_shape_changes(tmp_path, mutate):
    audio = _tone(tmp_path / "voice.wav")
    segments = [_segment(audio)]
    adoption, meta = _adoption(tmp_path, segments)
    _rewrite(adoption, mutate)

    with pytest.raises(ValueError, match="(?i)(schema|field)"):
        narration_binding.load_adoption(adoption, tts_meta_path=meta, tts_segments=segments)


@pytest.mark.parametrize(
    "field,value",
    [
        ("global_atempo", 0.0),
        ("global_atempo", -1.1),
        ("global_atempo", 2.0),
        ("bounded_segment_fit", "yes"),
        ("segment_tempo_max", 0.9),
        ("cumulative_tempo_max", 0.5),
        ("cumulative_tempo_hard_max", "1.4"),
    ],
)
def test_load_adoption_rejects_out_of_bounds_tempo_policy(tmp_path, field, value):
    audio = _tone(tmp_path / "voice.wav")
    segments = [_segment(audio)]
    adoption, meta = _adoption(tmp_path, segments)
    _rewrite(adoption, lambda payload: payload["tempo_policy"].update({field: value}))

    with pytest.raises(ValueError, match="(?i)tempo"):
        narration_binding.load_adoption(adoption, tts_meta_path=meta, tts_segments=segments)


def test_load_adoption_honours_a_valid_non_default_tempo_policy(tmp_path):
    audio = _tone(tmp_path / "voice.wav")
    segments = [_segment(audio)]
    adoption, meta = _adoption(tmp_path, segments)
    declared = {
        "global_atempo": 1.15,
        "bounded_segment_fit": True,
        "segment_tempo_max": 1.2,
        "cumulative_tempo_max": 1.35,
        "cumulative_tempo_hard_max": 1.4,
    }
    _rewrite(adoption, lambda payload: payload.update(tempo_policy=dict(declared)))

    loaded = narration_binding.load_adoption(
        adoption, tts_meta_path=meta, tts_segments=segments
    )

    assert loaded["tempo_policy"] == declared


def test_load_adoption_rejects_stale_tts_meta_bytes(tmp_path):
    audio = _tone(tmp_path / "voice.wav")
    segments = [_segment(audio)]
    adoption, meta = _adoption(tmp_path, segments)
    meta.write_bytes(meta.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="(?i)(tts_meta|bytes|hash)"):
        narration_binding.load_adoption(adoption, tts_meta_path=meta, tts_segments=segments)


def test_load_adoption_rejects_in_memory_segments_different_from_bound_file(tmp_path):
    audio = _tone(tmp_path / "voice.wav")
    segments = [_segment(audio)]
    adoption, meta = _adoption(tmp_path, segments)
    changed = [{**segments[0], "spoken_text": "new edit"}]

    with pytest.raises(ValueError, match="(?i)(memory|segment|tts_meta)"):
        narration_binding.load_adoption(adoption, tts_meta_path=meta, tts_segments=changed)


@pytest.mark.parametrize(
    "field,value",
    [("index", 9), ("spoken_text", "stale words"), ("processed_wav_sha256", "0" * 64)],
)
def test_load_adoption_rejects_segment_identity_different_from_tts_meta(
    tmp_path, field, value
):
    audio = _tone(tmp_path / "voice.wav")
    segments = [_segment(audio)]
    adoption, meta = _adoption(tmp_path, segments)
    _rewrite(adoption, lambda payload: payload["segments"][0].update({field: value}))

    with pytest.raises(ValueError, match="(?i)(segment|hash|spoken|tts_meta)"):
        narration_binding.load_adoption(adoption, tts_meta_path=meta, tts_segments=segments)


def test_declared_hash_mismatch_rejects_even_without_adoption(tmp_path):
    actual = _tone(tmp_path / "actual.wav", 330)
    intended = _tone(tmp_path / "intended.wav", 997)
    segment = _segment(actual)
    segment["processed_wav_sha256"] = _sha256(intended)

    with pytest.raises(ValueError, match="(?i)(hash|identity)"):
        narration_binding.prepare_binding([segment], tmp_path / "work")


def test_old_metadata_without_hash_is_explicitly_legacy_unverified(tmp_path):
    audio = _tone(tmp_path / "voice.wav")
    segment = _segment(audio, with_hash=False)

    context = narration_binding.prepare_binding([segment], tmp_path / "work")

    assert context["identity_status"] == "LEGACY_UNVERIFIED"
    assert context["active"] is False
    assert segment["audio_path"] == str(audio)


def test_hash_bound_metadata_without_adoption_is_snapshotted_but_not_promoted(tmp_path):
    audio = _tone(tmp_path / "voice.wav")
    segment = _segment(audio)

    context = narration_binding.prepare_binding([segment], tmp_path / "work")

    assert context["identity_status"] == "DECLARED_HASH_BOUND_UNADOPTED"
    assert context["adoption"] is None
    assert Path(segment["audio_path"]).read_bytes() == audio.read_bytes()
    assert Path(segment["audio_path"]).resolve() != audio.resolve()


def test_adoption_without_receipt_keeps_request_evidence_unknown(tmp_path):
    audio = _tone(tmp_path / "voice.wav")
    segments = [_segment(audio)]
    adoption, meta = _adoption(tmp_path, segments)

    context = narration_binding.prepare_binding(
        segments,
        tmp_path / "work",
        narration_adoption_path=adoption,
        tts_meta_path=meta,
    )

    assert context["identity_status"] == "BOUND_TO_ADOPTION"
    assert context["segments"][0]["request_evidence"] == "UNKNOWN"


def test_matching_provider_receipt_records_request_only_not_acoustic_voice_pass(tmp_path):
    audio = _tone(tmp_path / "voice.wav")
    digest = _sha256(audio)
    receipt = {
        "provider": "offline-provider",
        "requested_voice": "offline-voice",
        "processed_wav_sha256": digest,
    }
    segments = [_segment(audio, receipt=receipt)]
    adoption, meta = _adoption(tmp_path, segments)

    context = narration_binding.prepare_binding(
        segments,
        tmp_path / "work",
        narration_adoption_path=adoption,
        tts_meta_path=meta,
    )

    assert context["segments"][0]["request_evidence"] == "RECEIPT_MATCHED"
    assert not any("voice" in key.lower() and value == "PASS" for key, value in context.items())


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "different-provider"),
        ("processed_wav_sha256", "0" * 64),
    ],
)
def test_provider_receipt_contradictions_are_rejected(tmp_path, field, value):
    audio = _tone(tmp_path / "voice.wav")
    receipt = {
        "provider": "offline-provider",
        "requested_voice": "offline-voice",
        "processed_wav_sha256": _sha256(audio),
    }
    receipt[field] = value
    segments = [_segment(audio, receipt=receipt)]
    adoption, meta = _adoption(tmp_path, segments)

    with pytest.raises(ValueError, match="(?i)(receipt|contradict)"):
        narration_binding.prepare_binding(
            segments,
            tmp_path / "work",
            narration_adoption_path=adoption,
            tts_meta_path=meta,
        )


@pytest.mark.parametrize("changed", ["audio", "metadata", "adoption"])
def test_assert_current_rejects_post_preflight_identity_changes(tmp_path, changed):
    audio = _tone(tmp_path / "voice.wav")
    segments = [_segment(audio)]
    adoption, meta = _adoption(tmp_path, segments)
    context = narration_binding.prepare_binding(
        segments,
        tmp_path / "work",
        narration_adoption_path=adoption,
        tts_meta_path=meta,
    )
    selected = {"audio": audio, "metadata": meta, "adoption": adoption}[changed]
    selected.write_bytes(selected.read_bytes() + b"changed")

    with pytest.raises(ValueError, match="(?i)(changed|identity)"):
        narration_binding.assert_current(context)


@pytest.fixture
def render_media(tmp_path, monkeypatch):
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe required for full-chain narration adoption")
    for key, value in {
        "narration_speed": 1.15,
        "narration_tighten": False,
        "narration_delay_seconds": 0.0,
        "narration_tail_pad_seconds": 0.0,
        "tts_segment_tempo_max": 1.2,
        "narration_cumulative_tempo_max": 1.3,
        "narration_cumulative_tempo_hard_max": 1.4,
        "fade_ms": 0.0,
        "burn_subtitles": False,
        "export_jianying": False,
        "mask_source_subtitles": False,
        "subtitle_original_in_gaps": False,
        "output_max_height": 0,
        "bgm_path": "",
        "final_loudnorm": False,
        "speech_ducking_volume": 0.0,
        "idle_orig_volume": 0.0,
    }.items():
        monkeypatch.setitem(CONFIG, key, value)
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=black:s=160x120:r=24:d=2",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", "2", "-c:v", "libx264", "-threads", "2", "-c:a", "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    work = tmp_path / "work"
    work.mkdir()
    return source, work


def _frequency_magnitudes(video):
    raw = subprocess.check_output(
        [
            "ffmpeg", "-v", "error", "-i", str(video), "-ss", "0.4", "-t", "0.5",
            "-vn", "-ac", "1", "-ar", "44100", "-f", "s16le", "-",
        ]
    )
    pcm = array.array("h", raw)
    if sys.byteorder != "little":
        pcm.byteswap()
    result = {}
    for frequency in (330, 997):
        real = sum(
            value * math.cos(2 * math.pi * frequency * sample / 44100)
            for sample, value in enumerate(pcm)
        )
        imaginary = sum(
            value * math.sin(2 * math.pi * frequency * sample / 44100)
            for sample, value in enumerate(pcm)
        )
        result[frequency] = math.hypot(real, imaginary) / len(pcm)
    return result


def test_adopted_f32_stereo_input_is_decoded_and_bound_through_final_mix(
    render_media, tmp_path
):
    source, work = render_media
    adopted = tmp_path / "adopted-f32-stereo.wav"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
            "sine=frequency=997:sample_rate=48000:duration=1", "-ac", "2",
            "-c:a", "pcm_f32le", str(adopted),
        ],
        check=True,
        capture_output=True,
    )
    segments = [_segment(adopted)]
    adoption, meta = _adoption(tmp_path, segments)
    output = work / "output.mp4"

    assemble.assemble_video(
        source,
        segments,
        work,
        output,
        narration_adoption_path=adoption,
        tts_meta_path=meta,
    )

    magnitudes = _frequency_magnitudes(output)
    assert magnitudes[997] > 100 * magnitudes[330], magnitudes
    report = json.loads((work / "narration_input_binding.json").read_text())
    segment_report = report["segments"][0]
    assert report["identity_status"] == "BOUND_TO_ADOPTION"
    assert segment_report["original"]["sha256"] == _sha256(adopted)
    assert segment_report["conversion"]["applied"] is True
    assert segment_report["conversion"]["pcm"]["sample_rate"] == "44100"
    assert segment_report["conversion"]["pcm"]["channels"] == 1
    assert segment_report["placed"]["sha256"] == _sha256(segment_report["placed"]["path"])
    assert report["narration_bus"]["sha256"] == _sha256(report["narration_bus"]["path"])
    assert report["final_output"]["sha256"] == _sha256(output)
    assert report["voice_authentication"] == "NOT_CHECKED"


def test_strict_adoption_overrides_ambient_speed_without_segment_fit(render_media, tmp_path):
    source, work = render_media
    audio = _tone(tmp_path / "voice.wav", seconds=1.0)
    segments = [_segment(audio)]
    adoption, meta = _adoption(tmp_path, segments)

    assemble.assemble_video(
        source,
        segments,
        work,
        work / "output.mp4",
        narration_adoption_path=adoption,
        tts_meta_path=meta,
    )

    assert not list(work.glob("_spd_*.wav"))
    assert not list(work.glob("*_adj.wav"))
    assert segments[0]["global_narration_speed"] == 1.0
    assert segments[0]["segment_tempo_factor"] == 1.0
    report = json.loads((work / "narration_input_binding.json").read_text())
    assert report["adoption"]["tempo_policy"] == STRICT_TEMPO


def test_overlong_strict_adoption_fails_without_speedup_or_speech_cut(render_media, tmp_path):
    source, work = render_media
    audio = _tone(tmp_path / "overlong.wav", seconds=1.7)
    segments = [_segment(audio, seconds=1.7)]
    segments[0]["end"] = 1.0
    adoption, meta = _adoption(tmp_path, segments)
    output = work / "output.mp4"

    with pytest.raises(RuntimeError, match="(?i)(fit|tempo|QC|安全)"):
        assemble.assemble_video(
            source,
            segments,
            work,
            output,
            narration_adoption_path=adoption,
            tts_meta_path=meta,
        )

    assert not output.exists()
    assert not list(work.glob("_spd_*.wav"))
    assert not list(work.glob("*_adj.wav"))
    assert segments[0]["fit_status"] == "no_safe_fit"
    assert segments[0]["placed_audio_duration"] == 0.0


def test_post_preflight_source_change_rejects_without_overwriting_old_output(
    render_media, tmp_path, monkeypatch
):
    source, work = render_media
    audio = _tone(tmp_path / "voice.wav")
    segments = [_segment(audio)]
    adoption, meta = _adoption(tmp_path, segments)
    output = work / "output.mp4"
    previous = b"previous successful delivery"
    output.write_bytes(previous)
    original_copy = narration_binding._copy_snapshot

    def copy_then_mutate(source_path, destination):
        original_copy(source_path, destination)
        Path(source_path).write_bytes(Path(source_path).read_bytes() + b"changed")

    monkeypatch.setattr(narration_binding, "_copy_snapshot", copy_then_mutate)

    with pytest.raises(ValueError, match="(?i)(changed|identity)"):
        assemble.assemble_video(
            source,
            segments,
            work,
            output,
            narration_adoption_path=adoption,
            tts_meta_path=meta,
        )

    assert output.read_bytes() == previous
    assert not (work / "narration_input_binding.json").exists()


def test_cli_help_exposes_explicit_narration_adoption_option():
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "assemble.py"), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--narration-adoption" in result.stdout
