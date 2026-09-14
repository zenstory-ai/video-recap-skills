"""End-to-end regressions for the explicitly adopted full-sound consumer."""

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


SCRIPTS = Path(__file__).resolve().parents[2] / "skills/video-assemble/scripts"
sys.path.insert(0, str(SCRIPTS))

import assemble  # noqa: E402
import audio_mix_binding  # noqa: E402
from lib import CONFIG  # noqa: E402
import source_score  # noqa: E402


pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe required",
)

STRICT_TEMPO = {
    "global_atempo": 1.0, "bounded_segment_fit": False,
    "segment_tempo_max": 1.0, "cumulative_tempo_max": 1.0,
    "cumulative_tempo_hard_max": 1.0,
}


def _run(*args):
    subprocess.run(tuple(map(str, args)), check=True, capture_output=True)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _wav(path, frequency, seconds=0.4, *, channels=1):
    rate = 48_000
    data = array.array("h")
    for index in range(round(rate * seconds)):
        value = int(8000 * math.sin(2 * math.pi * frequency * index / rate))
        data.extend([value] * channels)
    if sys.byteorder != "little":
        data.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setparams((channels, 2, rate, len(data) // channels, "NONE", "not compressed"))
        output.writeframes(data.tobytes())
    return path


def _quiet(monkeypatch):
    monkeypatch.setitem(CONFIG, "burn_subtitles", False)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", False)
    monkeypatch.setitem(CONFIG, "subtitle_original_in_gaps", False)
    monkeypatch.setitem(CONFIG, "output_max_height", 0)
    monkeypatch.setitem(CONFIG, "bgm_path", "hostile-unused-bgm.wav")
    monkeypatch.setitem(CONFIG, "final_loudnorm", True)
    monkeypatch.setitem(CONFIG, "narration_speed", 1.15)


@pytest.fixture
def explicit_case(tmp_path):
    picture = tmp_path / "picture.mp4"
    _run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=black:size=64x48:rate=12:duration=2", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-an", picture)
    work = tmp_path / "work"
    work.mkdir()
    beds = tmp_path / "beds"
    beds.mkdir()
    for name, frequency in (("source_bed.wav", 220), ("score_bed.wav", 330)):
        _run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
             f"sine=frequency={frequency}:sample_rate=48000:duration=2", "-ac", "2",
             "-c:a", "pcm_f32le", beds / name)
    _run("ffmpeg", "-v", "error", "-y", "-i", beds / "source_bed.wav", "-i",
         beds / "score_bed.wav", "-filter_complex",
         "[0:a][1:a]amix=inputs=2:normalize=0[out]", "-map", "[out]",
         "-c:a", "pcm_f32le", beds / "prepared_bed.wav")
    identities = {
        name: source_score._output_identity(beds / name)
        for name in ("source_bed.wav", "score_bed.wav", "prepared_bed.wav")
    }
    receipt = beds / "prepared_bed_receipt.json"
    receipt.write_text(json.dumps({
        "artifact": "prepared_bed_receipt", "schema_version": 1, "status": "PREPARED",
        "format": {"sample_rate": 48000, "channels": 2, "total_samples": 96000,
                   "codec": "pcm_f32le"},
        "outputs": identities,
    }))
    voice = _wav(tmp_path / "voice.wav", 997)
    segment = {
        "index": 0, "start": 0.25, "end": 1.0, "narration": "bound voice",
        "spoken_text": "bound voice", "audio_path": str(voice),
        "audio_duration": 0.4, "pause_after_ms": 0, "overlaps_speech": False,
        "tts_rate_offset": 0.0, "processed_wav_sha256": _sha(voice),
    }
    meta = tmp_path / "tts_meta.json"
    meta.write_text(json.dumps({"segments": [segment]}))
    narration = tmp_path / "narration_adoption.json"
    narration.write_text(json.dumps({
        "artifact": "narration_adoption", "schema_version": 1,
        "tts_meta_sha256": _sha(meta), "segments": [{
            "index": 0, "spoken_text": "bound voice",
            "processed_wav_sha256": _sha(voice),
            "requested_provider": "offline", "requested_voice": "voice-a",
        }], "tempo_policy": STRICT_TEMPO,
    }))
    adoption = tmp_path / "audio_mix_adoption.json"
    adoption.write_text(json.dumps({
        "artifact": "audio_mix_adoption", "schema_version": 1,
        "picture_sha256": _sha(picture),
        "prepared_receipt": {"path": str(receipt), "sha256": _sha(receipt)},
        "narration_adoption_sha256": _sha(narration),
        "format": {"sample_rate": 48000, "channels": 2, "total_samples": 96000},
        "segments": [{"index": 0, "processed_wav_sha256": _sha(voice),
                      "output_start_sample": 12000, "gain": 0.5}],
        "master_gain_db": 0.75,
    }))
    return picture, work, [segment], meta, narration, adoption


def test_load_adoption_binds_picture_receipt_narration_and_segments(explicit_case):
    picture, _work, segments, _meta, narration, adoption = explicit_case
    context = audio_mix_binding.load_adoption(
        adoption, input_video=picture, narration_adoption_path=narration,
        tts_segments=segments,
    )
    assert context["format"]["total_samples"] == 96000
    assert context["conversion_policy"] == "mono_equal_power_stereo_identity"
    assert context["prepared"]["prepared_bed.wav"]["sha256"]


def test_explicit_branch_renders_actual_bindings_and_ignores_ambient_mix(
    explicit_case, tmp_path, monkeypatch
):
    picture, work, segments, meta, narration, adoption = explicit_case
    _quiet(monkeypatch)
    output = work / "output.mp4"
    assemble.assemble_video(
        picture, segments, work, output, narration_adoption_path=narration,
        tts_meta_path=meta, audio_mix_adoption_path=adoption,
    )
    assert output.is_file()
    narration_report = json.loads((work / "narration_input_binding.json").read_text())
    mix_report = json.loads((work / "audio_mix_binding.json").read_text())
    qc = json.loads((work / "assembly_qc.json").read_text())
    assert narration_report["narration_bus"]["consumption_status"] == \
        "CONSUMED_BY_EXPLICIT_MIX"
    assert narration_report["segments"][0]["placed"]["pcm"]["sample_rate"] == "48000"
    assert mix_report["status"] == "FINALIZED"
    assert mix_report["segments"][0]["output_start_sample"] == 12000
    assert mix_report["segments"][0]["gain"] == 0.5
    assert qc["audio_operations"]["explicit_audio_mix"] is True
    assert qc["audio_operations"]["ducking"] is False
    assert qc["audio_operations"]["loudness_normalization"] is False


def test_explicit_branch_allows_visual_reencode_but_preserves_picture_clock(
    explicit_case, monkeypatch
):
    picture, work, segments, meta, narration, adoption = explicit_case
    _quiet(monkeypatch)
    monkeypatch.setitem(CONFIG, "force_video_reencode", True)
    assemble.assemble_video(
        picture, segments, work, work / "output.mp4",
        narration_adoption_path=narration, tts_meta_path=meta,
        audio_mix_adoption_path=adoption,
    )
    report = json.loads((work / "audio_mix_binding.json").read_text())
    output_picture = report["output_picture"]
    assert output_picture["packet_identity"] == "REENCODED_CLOCK_MATCH"
    assert output_picture["fps"] == report["picture"]["clock"]["fps"]
    assert output_picture["frame_count"] == report["picture"]["clock"]["frame_count"]


@pytest.mark.parametrize("field", ["picture_sha256", "narration_adoption_sha256"])
def test_stale_top_level_identity_fails_before_snapshot(explicit_case, field):
    picture, work, segments, meta, narration, adoption = explicit_case
    value = json.loads(adoption.read_text())
    value[field] = "0" * 64
    adoption.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="(?i)(identity|hash|picture|narration)"):
        assemble.assemble_video(
            picture, segments, work, work / "output.mp4",
            narration_adoption_path=narration, tts_meta_path=meta,
            audio_mix_adoption_path=adoption,
        )
    assert not (work / ".narration_input_snapshots").exists()
    assert not (work / "output.mp4").exists()


@pytest.mark.parametrize("asset,consumer", [
    ("placed_0000_0.wav", "voice_bus"),
    ("voice_bus.wav", "premaster"),
    ("premaster.wav", "master"),
])
@pytest.mark.parametrize("when", ["before", "after"])
def test_each_derived_pcm_is_sealed_across_its_consumer(
    explicit_case, monkeypatch, asset, consumer, when
):
    picture, work, segments, meta, narration, adoption = explicit_case
    original = audio_mix_binding._run

    def intercepted(command, directory, label):
        target = Path(directory) / asset
        if label == consumer and when == "before":
            target.write_bytes(target.read_bytes() + b"changed")
        result = original(command, directory, label)
        if label == consumer and when == "after":
            target.write_bytes(target.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(audio_mix_binding, "_run", intercepted)
    with pytest.raises((ValueError, RuntimeError), match="(?i)(changed|identity|seal)"):
        assemble.assemble_video(
            picture, segments, work, work / "output.mp4",
            narration_adoption_path=narration, tts_meta_path=meta,
            audio_mix_adoption_path=adoption,
        )
    assert not (work / "output.mp4").exists()
    assert not (work / "narration_input_binding.json").exists()
    assert not (work / "audio_mix_binding.json").exists()


@pytest.mark.parametrize("when", ["before", "after"])
def test_master_is_sealed_across_final_aac_render(explicit_case, monkeypatch, when):
    picture, work, segments, meta, narration, adoption = explicit_case
    original = assemble.lib.run_cmd

    def intercepted(command, *args, **kwargs):
        if "-c:a" not in command or not str(command[-1]).endswith(".mp4"):
            return original(command, *args, **kwargs)
        target = work / ".explicit_audio_mix/master.wav"
        if when == "before":
            target.write_bytes(target.read_bytes() + b"changed")
        result = original(command, *args, **kwargs)
        if when == "after":
            target.write_bytes(target.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(assemble.lib, "run_cmd", intercepted)
    with pytest.raises((ValueError, RuntimeError), match="(?i)(changed|identity|seal)"):
        assemble.assemble_video(
            picture, segments, work, work / "output.mp4",
            narration_adoption_path=narration, tts_meta_path=meta,
            audio_mix_adoption_path=adoption,
        )
    assert not (work / "output.mp4").exists()


@pytest.mark.parametrize("when", ["before", "after"])
def test_rendered_container_is_stable_across_final_probes(
    explicit_case, monkeypatch, when
):
    picture, work, segments, meta, narration, adoption = explicit_case
    original = audio_mix_binding._run

    def intercepted(command, directory, label):
        if label != "final_decode":
            return original(command, directory, label)
        rendered = Path(command[command.index("-i") + 1])
        if when == "before":
            rendered.write_bytes(rendered.read_bytes() + b"changed")
        result = original(command, directory, label)
        if when == "after":
            rendered.write_bytes(rendered.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(audio_mix_binding, "_run", intercepted)
    with pytest.raises((ValueError, RuntimeError), match="(?i)(changed|identity)"):
        assemble.assemble_video(
            picture, segments, work, work / "output.mp4",
            narration_adoption_path=narration, tts_meta_path=meta,
            audio_mix_adoption_path=adoption,
        )
    assert not (work / "output.mp4").exists()
    assert not (work / "narration_input_binding.json").exists()
    assert not (work / "audio_mix_binding.json").exists()


def test_isolated_copied_skill_cli_publishes_new_alias_and_manifest(explicit_case, tmp_path):
    picture, _work, _segments, meta, narration, adoption = explicit_case
    copied = tmp_path / "copied-skill"
    shutil.copytree(SCRIPTS.parent, copied, ignore=shutil.ignore_patterns("__pycache__"))
    work = tmp_path / "cli-work"
    work.mkdir()
    delivery = tmp_path / "delivery"
    launcher = (
        "import runpy,sys;sys.path.insert(0,sys.argv[1]);sys.argv=sys.argv[2:];"
        "runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    env = {**os.environ, "BGM_PATH": "/missing/ambient.wav", "FINAL_LOUDNORM": "1",
           "NARRATION_SPEED": "1.15", "OUTPUT_MAX_HEIGHT": "0"}
    command = [
        sys.executable, "-I", "-c", launcher, copied / "scripts",
        copied / "scripts/assemble.py", picture, "--work-dir", work,
        "--tts-meta", meta, "--narration-adoption", narration,
        "--audio-mix-adoption", adoption, "--no-burn-subtitles",
        "--output-dir", delivery, "--recap-stem", "strict",
    ]
    result = subprocess.run(tuple(map(str, command)), env=env, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    alias = delivery / "recap_strict.mp4"
    manifest = json.loads((work / "assembly_manifest.json").read_text())
    assert alias.is_file()
    assert manifest["audio_mix_binding"]["status"] == "FINALIZED"
    assert manifest["assembly_settings"]["audio"]["path"] == \
        "explicit_adopted_full_sound"
    second = subprocess.run(tuple(map(str, command)), env=env, capture_output=True,
                            text=True, timeout=120)
    assert second.returncode != 0
    assert alias.is_file(), "exclusive strict retry must not remove an older delivery"
