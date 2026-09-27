import json
import shutil
import sys
from pathlib import Path

import pytest

import recap_runner
import resource_lock
from _helpers import seed_full_work, stub_child_run

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "resource-library"


def _library(tmp_path):
    root = tmp_path / "lib"
    shutil.copytree(EXAMPLE, root)
    return root


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _finished_work(tmp_path, *, bgm=None, voice=None, font_name="Arial"):
    video, work = seed_full_work(tmp_path)
    _write(work / "tts_meta.json", {"segments": [], "engine": "mimo-tts", "voice": voice or {
        "provider": "mimo-tts", "model": "mimo-v2.5-tts", "voice_id": "冰糖", "reference": None}})
    _write(work / "assembly_manifest.json", {"assembly_settings": {
        "audio_mix": {"bgm_path": str(bgm) if bgm else ""},
        "subtitle_style": {"font_name": font_name},
    }})
    return video, work


def _by_role(lock):
    return {entry["role"]: entry for entry in lock["resources"]}


def test_lock_lists_used_resources_and_matches_registered_ones(tmp_path):
    lib = _library(tmp_path)
    bgm = lib / "resources/bgm/pulse-demo/pulse-demo.wav"
    video, work = _finished_work(tmp_path, bgm=bgm)

    lock = resource_lock.build_resource_lock(work, library_dir=lib)
    roles = _by_role(lock)

    assert lock["schema"] == "video-recap.resource-lock.v1"
    assert list(roles) == ["source_video", "voice", "bgm", "subtitle_font"]
    assert roles["source_video"]["path"] == str(video.resolve())
    assert roles["bgm"]["library"] == {"id": "pulse-demo", "kind": "bgm", "license": "owned", "consent": None}
    assert roles["voice"]["library"]["id"] == "narrator-demo"
    assert roles["subtitle_font"]["detail"] == {"family": "Arial"}
    assert [(a["code"], a["role"]) for a in lock["attention"]] == [("license_unknown", "voice")]


@pytest.mark.parametrize("with_library", [True, False], ids=["library", "no_library"])
def test_unregistered_resources_are_flagged_only_when_a_library_is_configured(tmp_path, with_library):
    lib = _library(tmp_path)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"bgm")
    _, work = _finished_work(tmp_path, bgm=outside, voice={
        "provider": "fish-audio", "model": "s2", "voice_id": "someone-else", "reference": None})

    lock = resource_lock.build_resource_lock(work, library_dir=lib if with_library else None)

    codes = {(a["code"], a["role"]) for a in lock["attention"]}
    if with_library:
        assert codes == {("unregistered", "bgm"), ("unregistered", "voice")}
        assert lock["library"] == str(lib.resolve())
    else:
        assert codes == set()
        assert lock["library"] is None


def test_reference_voice_without_granted_consent_needs_attention(tmp_path):
    lib = _library(tmp_path)
    ref = lib / "resources/voice/narrator-demo/ref.wav"
    shutil.copy(lib / "resources/bgm/pulse-demo/pulse-demo.wav", ref)
    record = lib / "resources/voice/narrator-demo/resource.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    data.update(files=[{"role": "reference", "path": "ref.wav"}], consent={"status": "unknown"},
                license={"status": "owned"})
    _write(record, data)
    _, work = _finished_work(tmp_path, voice={
        "provider": "mimo-tts", "model": "mimo-v2.5-tts-voiceclone", "voice_id": None,
        "reference": {"path": str(ref), "size": 1, "mtime_ns": 1}})

    lock = resource_lock.build_resource_lock(work, library_dir=lib)

    assert _by_role(lock)["voice"]["library"]["consent"] == "unknown"
    assert [(a["code"], a["role"]) for a in lock["attention"]] == [("consent_unknown", "voice")]


def test_finished_run_writes_the_lock_next_to_its_artifacts(monkeypatch, tmp_path):
    video, work = seed_full_work(tmp_path)
    monkeypatch.setattr(recap_runner, "_run", stub_child_run(work, tmp_path / "recap_video.mp4"))
    monkeypatch.setattr(sys, "argv", ["recap_runner.py", str(video), "--work-dir", str(work)])

    recap_runner.main()

    lock = json.loads((work / "resource_lock.json").read_text(encoding="utf-8"))
    assert [e["role"] for e in lock["resources"]][:2] == ["source_video", "voice"]
    assert lock["project"] is None


def test_voice_without_an_id_is_not_credited_to_a_reference_voice(tmp_path):
    lib = _library(tmp_path)
    record = lib / "resources/voice/narrator-demo/resource.json"
    shutil.copy(lib / "resources/bgm/pulse-demo/pulse-demo.wav", record.parent / "ref.wav")
    data = json.loads(record.read_text(encoding="utf-8"))
    data.update(files=[{"role": "reference", "path": "ref.wav"}], consent={"status": "denied"})
    data["voice"].pop("voice_id")
    _write(record, data)
    _, work = _finished_work(tmp_path, voice={"provider": "mimo-tts", "model": None, "voice_id": None,
                                              "reference": None})

    lock = resource_lock.build_resource_lock(work, library_dir=lib)

    assert _by_role(lock)["voice"]["library"] is None


def test_a_malformed_library_record_never_fails_the_lock(tmp_path):
    lib = _library(tmp_path)
    template = lib / "templates/subtitle_style/clean-white/v1/template.json"
    data = json.loads(template.read_text(encoding="utf-8"))
    data["params"]["font"] = {"resource": ["not", "an", "id"]}
    data["samples"] = 3
    _write(template, data)
    _, work = _finished_work(tmp_path)

    lock = resource_lock.build_resource_lock(work, library_dir=lib)

    assert [e["role"] for e in lock["resources"]] == ["source_video", "voice", "subtitle_font"]
