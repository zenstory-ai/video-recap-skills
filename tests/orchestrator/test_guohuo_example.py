import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "guohuo-60s"


def _load(name):
    return json.loads((EXAMPLE / name).read_text(encoding="utf-8"))


def _provider_values(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if "provider" in key.lower():
                yield child
            yield from _provider_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _provider_values(child)


def _compact_text(value):
    return re.sub(r"[\W_]+", "", str(value), flags=re.UNICODE)


def test_guohuo_public_artifacts_are_portable_and_self_consistent():
    assert EXAMPLE.is_dir()
    json_paths = sorted(EXAMPLE.rglob("*.json"))
    assert json_paths
    for path in json_paths:
        json.loads(path.read_text(encoding="utf-8"))

    assert _load("captions.json") == _load("remotion/src/captions.json")

    manifest = _load("workflow-manifest.json")
    for stage in manifest["stages"]:
        for relative_path in stage.get("included", []):
            assert (EXAMPLE / relative_path).exists(), relative_path

    public_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in EXAMPLE.rglob("*")
        if path.is_file()
    )
    case_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in EXAMPLE.rglob("*")
        if path.is_file() and path.name != "package-lock.json"
    )
    assert "/Users/" not in public_text
    assert "/Volumes/" not in public_text
    assert "file://" not in public_text
    assert "overdo-" not in public_text
    assert "output_sfx_norm" not in public_text
    assert not re.search(
        r"(?i)\b(?:fingerprint|sha256|md5|checksum|material_id)\b", case_text
    )
    assert not re.search(r"\b(?:sk|tp)-[A-Za-z0-9_-]{8,}\b", public_text)
    assert not re.search(r"\bAKIA[0-9A-Z]{16}\b", public_text)
    assert not re.search(
        r"(?i)\b(?:api[_-]?key|secret|password|authorization|bearer)\s*[:=]\s*[^\s,]+",
        public_text,
    )
    assert not re.search(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", public_text
    )

    assert not (EXAMPLE / "tts_meta.json").exists()
    assert not (EXAMPLE / "clip_plan_validated.json").exists()
    assert not (EXAMPLE / "speech_boundary_anchors_output.json").exists()
    assert not (EXAMPLE / "narration_lint.json").exists()
    assert not (EXAMPLE / "deslop_qc.json").exists()
    provider_values = [
        provider
        for path in json_paths
        for provider in _provider_values(json.loads(path.read_text(encoding="utf-8")))
    ]
    assert provider_values == ["Fish Audio"]

    media_suffixes = {".mp4", ".mov", ".mkv", ".wav", ".mp3", ".png", ".jpg"}
    assert not [
        path for path in EXAMPLE.rglob("*") if path.suffix.lower() in media_suffixes
    ]


def test_guohuo_timeline_delivery_and_source_ids_agree():
    delivery = _load("delivery-qc.json")
    timeline = _load("timeline.json")
    clip_plan = _load("clip_plan.json")
    sources = _load("multi_source_manifest.json")

    media = delivery["media"]
    assert timeline["duration"] == media["duration_seconds"]
    assert timeline["canvas"]["fps"] == media["fps"]
    assert media["frames"] == round(media["duration_seconds"] * media["fps"])

    known_source_ids = {item["source_id"] for item in sources["sources"]}
    clip_source_ids = {item["source_id"] for item in clip_plan["clips"]}
    assert clip_source_ids <= known_source_ids

    overlay = _load("remotion/src/overlay.json")
    assert overlay["durationInFrames"] == media["frames"]
    assert overlay["fps"] == media["fps"]
    assert (overlay["width"], overlay["height"]) == (1920, 1080)


def test_guohuo_overlay_timing_lives_in_data_not_tsx():
    """Re-timing for a new run edits overlay.json / captions.json only."""
    overlay = _load("remotion/src/overlay.json")
    duration = overlay["durationInFrames"] / overlay["fps"]
    tsx = "\n".join(
        (EXAMPLE / "remotion/src" / name).read_text(encoding="utf-8")
        for name in ("index.tsx", "RecapOverlay.tsx")
    )
    assert str(overlay["durationInFrames"]) not in tsx
    assert overlay["title"]["text"] not in tsx
    for cue in overlay["flowerCues"]:
        assert cue["text"] not in tsx
        assert 0 <= cue["start"] < cue["end"] <= duration
    for window in overlay["title"]["windows"]:
        assert 0 <= window["start"] < window["end"] <= duration
    assert "calculateMetadata" in tsx


def _load_sync_overlay(monkeypatch):
    # No __pycache__ inside the public example: its .pyc embeds this machine's paths.
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location(
        "guohuo_sync_overlay", EXAMPLE / "remotion" / "sync_overlay.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _srt_time(seconds):
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def test_sync_overlay_rebuilds_the_published_captions_and_length_from_a_run(
    monkeypatch, tmp_path
):
    """An SRT shaped like assemble's subtitles.srt for the adopted cut reproduces the
    published captions.json and durationInFrames exactly."""
    sync = _load_sync_overlay(monkeypatch)
    captions = _load("captions.json")
    srt = tmp_path / "subtitles.srt"
    srt.write_text(
        "\n".join(
            f"{index}\n{_srt_time(cue['start'])} --> {_srt_time(cue['end'])}\n{cue['text']}\n"
            for index, cue in enumerate(captions, start=1)
        ),
        encoding="utf-8",
    )
    overlay = tmp_path / "overlay.json"
    overlay.write_text((EXAMPLE / "remotion/src/overlay.json").read_text(encoding="utf-8"),
                       encoding="utf-8")
    out = tmp_path / "captions.json"
    duration = _load("delivery-qc.json")["media"]["duration_seconds"]

    assert sync.main([
        "--srt", str(srt), "--duration", str(duration),
        "--overlay", str(overlay), "--out", str(out),
    ]) == 0

    assert json.loads(out.read_text(encoding="utf-8")) == captions
    assert overlay.read_text(encoding="utf-8") == (
        EXAMPLE / "remotion/src/overlay.json"
    ).read_text(encoding="utf-8")


def test_sync_overlay_flags_title_and_flower_cues_past_a_shorter_master(
    monkeypatch, tmp_path, capsys
):
    sync = _load_sync_overlay(monkeypatch)
    srt = tmp_path / "subtitles.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,500\n新的解说\n", encoding="utf-8")
    overlay = tmp_path / "overlay.json"
    overlay.write_text((EXAMPLE / "remotion/src/overlay.json").read_text(encoding="utf-8"),
                       encoding="utf-8")
    out = tmp_path / "captions.json"

    assert sync.main([
        "--srt", str(srt), "--duration", "40", "--overlay", str(overlay), "--out", str(out),
    ]) == 1

    assert json.loads(out.read_text(encoding="utf-8")) == [
        {"start": 1.0, "end": 2.5, "text": "新的解说"}
    ]
    assert json.loads(overlay.read_text(encoding="utf-8"))["durationInFrames"] == 1000
    err = capsys.readouterr().err
    assert "title window 42.2-50.3s" in err
    assert "'重逢已迟'" in err and "'本能不会说谎'" in err
    assert "'旧情难藏'" not in err


def test_guohuo_story_picture_audio_and_caption_contracts_agree():
    story = _load("recap_story_plan.json")
    board = _load("visual_audio_board.json")
    clip_plan = _load("clip_plan.json")
    narration = _load("narration.json")
    timeline = _load("timeline.json")
    assembly = _load("assembly_manifest.json")
    captions = _load("captions.json")

    story_beats = {item["beat_id"] for item in story["beats"]}
    board_beats = {item["beat_id"] for item in board["items"]}
    clip_beats = set()
    for clip in clip_plan["clips"]:
        reason = [part.strip() for part in clip["reason"].split("|")]
        assert len(reason) == 7
        assert reason[3].startswith("POV=")
        assert reason[5].startswith("入点=")
        assert reason[6].startswith("出点=")
        clip_beats.add(reason[0])
    assert clip_beats <= story_beats
    assert clip_beats <= board_beats

    narration_text = [item["narration"] for item in narration]
    timeline_narration = next(
        track
        for track in timeline["tracks"]
        if track.get("kind") == "audio" and track.get("role") == "narration"
    )
    assert [item["text"] for item in timeline_narration["segments"]] == narration_text
    assert [item["narration"] for item in assembly["audio_segments"]] == narration_text
    assert _compact_text("".join(item["text"] for item in captions)) == _compact_text(
        "".join(narration_text)
    )


def test_guohuo_narration_uses_the_skill_contract_on_the_output_clock():
    duration = _load("delivery-qc.json")["media"]["duration_seconds"]
    narration = _load("narration.json")

    assert len(narration) == 7
    previous_end = 0.0
    for segment in narration:
        assert {
            "start",
            "end",
            "narration",
            "emotion",
            "pause_after_ms",
            "overlaps_speech",
        } <= segment.keys()
        assert previous_end <= segment["start"] < segment["end"] <= duration
        assert isinstance(segment["overlaps_speech"], bool)
        previous_end = segment["end"]
