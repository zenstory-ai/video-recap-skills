"""Narration block audio is cached by what it says and how, never by its position.

Deleting a block (what assemble's no_safe_fit advice suggests) used to shift every later
index, so the per-index cache missed and every unchanged block after it was re-synthesized
and re-billed.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-voiceover" / "scripts"))
from lib import CONFIG  # noqa: E402
import voiceover  # noqa: E402

BLOCKS = [f"第{n}块旁白讲到这里" for n in "一二三四五"]


def _narration(texts, window=3.0):
    return [{"start": i * window, "end": (i + 1) * window, "narration": text}
            for i, text in enumerate(texts)]


@pytest.fixture
def engine_calls(monkeypatch):
    """Offline MiMo engine writing `audio:<text>`; returns the texts it was asked to speak."""
    calls = []

    def fake_run(_engine, text, output_wav, **_kwargs):
        calls.append(text)
        output_wav.write_bytes(f"audio:{text}".encode("utf-8"))

    monkeypatch.setitem(CONFIG, "tts_segment_normalize", False)
    monkeypatch.setitem(CONFIG, "tts_provider", "mimo-tts")
    monkeypatch.setitem(CONFIG, "mimo_tts_api_key", "tp-test")
    monkeypatch.setitem(CONFIG, "voice_ref", "")
    monkeypatch.setitem(CONFIG, "preserve_approved_text", False)
    monkeypatch.setattr(voiceover, "_run_tts_engine", fake_run)
    monkeypatch.setattr(voiceover, "get_video_duration", lambda _path: 1.0)
    return calls


def _audio_by_index(segments):
    return [Path(s["audio_path"]).read_bytes().decode("utf-8") for s in segments]


def test_deleting_a_middle_block_resynthesizes_nothing(engine_calls, tmp_path):
    voiceover.synthesize_tts(_narration(BLOCKS), tmp_path)
    engine_calls.clear()

    kept = BLOCKS[:2] + BLOCKS[3:]
    segments, _engine, failures = voiceover.synthesize_tts(_narration(kept), tmp_path)

    assert engine_calls == []
    assert failures == []
    # The index-named files downstream reads now hold the blocks that moved into those slots.
    assert [Path(s["audio_path"]).name for s in segments] == [f"narr_{i:03d}.wav" for i in range(4)]
    assert _audio_by_index(segments) == [f"audio:{text}" for text in kept]


def test_only_inserted_and_edited_blocks_reach_the_engine(engine_calls, tmp_path):
    voiceover.synthesize_tts(_narration(BLOCKS), tmp_path)
    engine_calls.clear()

    edited = BLOCKS[:2] + ["新插入的一块旁白"] + BLOCKS[2:]
    edited[4] = "第四块改过的旁白"
    segments, _engine, _failures = voiceover.synthesize_tts(_narration(edited), tmp_path)

    assert sorted(engine_calls) == sorted(["新插入的一块旁白", "第四块改过的旁白"])
    assert _audio_by_index(segments) == [f"audio:{text}" for text in edited]


def test_mimo_resynthesizes_only_a_block_whose_instruction_changes(engine_calls, tmp_path):
    """Position sets the nominal rate (+5% inside, -2% second-to-last, -5% last), but MiMo
    only hears it through the instruction wording, which +5% and -2% share."""
    voiceover.synthesize_tts(_narration(BLOCKS), tmp_path)
    engine_calls.clear()

    # Deleting the last block: block 3 goes +5% -> -2% (same wording), block 4 goes
    # -2% -> -5% ("语速略慢").
    segments, _engine, _failures = voiceover.synthesize_tts(_narration(BLOCKS[:4]), tmp_path)

    assert engine_calls == [BLOCKS[3]]
    # The reused take reports its new position's rate, exactly as a fresh synthesis would.
    assert [s["tts_rate_offset"] for s in segments] == [0.05, 0.05, -0.02, -0.05]
    assert _audio_by_index(segments) == [f"audio:{text}" for text in BLOCKS[:4]]


def test_fish_resynthesizes_every_block_whose_numeric_speed_changes(
    engine_calls, monkeypatch, tmp_path
):
    """Fish Audio receives the rate as a number, so every rate change is a new request."""
    monkeypatch.setitem(CONFIG, "tts_provider", "fish-audio")
    monkeypatch.setitem(CONFIG, "fish_api_key", "fish-test")
    voiceover.synthesize_tts(_narration(BLOCKS), tmp_path)
    engine_calls.clear()

    voiceover.synthesize_tts(_narration(BLOCKS[:4]), tmp_path)

    assert sorted(engine_calls) == sorted(BLOCKS[2:4])


def test_cache_key_is_the_request_the_provider_receives(monkeypatch):
    monkeypatch.setitem(CONFIG, "voice_ref", "")
    monkeypatch.setitem(CONFIG, "preserve_approved_text", False)
    seg = {"start": 0.0, "end": 3.0, "narration": "同一句。"}

    def key(engine, rate, pitch="+0Hz", emotion=None):
        block = {**seg, "emotion": emotion} if emotion else seg
        return voiceover._tts_segment_cache_inputs(engine, block, "同一句。", rate, pitch)

    # MiMo: +5% and -2% both read "语速中等"; +6% and -3% change the wording.
    assert key("mimo-tts", "+5%") == key("mimo-tts", "-2%") == key("mimo-tts", "+0%")
    assert key("mimo-tts", "+6%") != key("mimo-tts", "+5%")
    assert key("mimo-tts", "-3%") != key("mimo-tts", "-2%")
    assert key("mimo-tts", "+0%", emotion="紧张") != key("mimo-tts", "+0%")
    # Fish Audio: the numeric speed is sent, pitch and emotion are not.
    assert key("fish-audio", "+5%") != key("fish-audio", "-2%")
    assert key("fish-audio", "+5%", pitch="+3Hz", emotion="紧张") == key("fish-audio", "+5%")
    assert key("fish-audio", "+5%")["provider_request"] == {"speed": 1.05}


def test_moving_a_window_reuses_the_audio(engine_calls, tmp_path):
    voiceover.synthesize_tts(_narration(BLOCKS), tmp_path)
    engine_calls.clear()

    segments, _engine, _failures = voiceover.synthesize_tts(_narration(BLOCKS, window=4.0), tmp_path)

    assert engine_calls == []
    assert [s["end"] for s in segments] == [4.0, 8.0, 12.0, 16.0, 20.0]


def test_an_approved_block_moved_into_a_short_window_fails_without_a_provider_call(
    engine_calls, monkeypatch, tmp_path
):
    monkeypatch.setitem(CONFIG, "preserve_approved_text", True)
    narration = _narration(BLOCKS[:2])
    voiceover.synthesize_tts(narration, tmp_path)
    engine_calls.clear()
    narration[1]["end"] = narration[1]["start"] + 0.6

    with pytest.raises(voiceover.ApprovedTextDurationError) as raised:
        voiceover.synthesize_tts(narration, tmp_path)

    assert engine_calls == []
    assert raised.value.evidence["segment"] == 2


def test_block_files_share_the_cached_audio_instead_of_copying_it(engine_calls, tmp_path):
    segments, _engine, _failures = voiceover.synthesize_tts(_narration(BLOCKS[:2]), tmp_path)

    stored = sorted((tmp_path / "tts_segments" / "cache").glob("*.wav"))
    assert len(stored) == 2
    for segment in segments:
        audio = Path(segment["audio_path"])
        assert any(os.path.samefile(audio, entry) for entry in stored)


def test_a_changed_block_file_is_restored_from_the_cache_or_resynthesized(engine_calls, tmp_path):
    """narr_NNN.wav is only a view: replacing it is repaired from the store, while writing into
    the shared audio in place invalidates the entry (its size/mtime no longer match)."""
    narration = _narration(BLOCKS[:1])
    voiceover.synthesize_tts(narration, tmp_path)
    wav = tmp_path / "tts_segments" / "narr_000.wav"

    wav.unlink()
    wav.write_bytes(b"replaced")
    voiceover.synthesize_tts(narration, tmp_path)
    assert engine_calls == [BLOCKS[0]]
    assert wav.read_bytes() == f"audio:{BLOCKS[0]}".encode("utf-8")

    with open(wav, "ab") as handle:
        handle.write(b"-mutated")
    voiceover.synthesize_tts(narration, tmp_path)
    assert engine_calls == [BLOCKS[0], BLOCKS[0]]


def test_materialize_replaces_a_slot_holding_another_take_with_the_same_size_and_mtime(tmp_path):
    """Two different takes of equal length written within one timestamp tick must not be
    mistaken for each other (a coarse-mtime filesystem made the old shortcut keep stale audio)."""
    import tts_cache

    tts_dir = tmp_path
    third, fourth = {"source_text": "第三块"}, {"source_text": "第四块"}
    for name, inputs, payload in (("a.wav", third, b"audio:third"), ("b.wav", fourth, b"audio:forth")):
        wav = tts_dir / name  # one file per take: store() hard-links it into the cache
        wav.write_bytes(payload)
        tts_cache.store(wav, inputs, {})
    slot = tts_dir / "narr_002.wav"
    tts_cache.materialize(tts_dir, third, slot)
    stored_fourth, _ = tts_cache.cache_entry(tts_dir, fourth)
    tick = 1_700_000_000_000_000_000
    for path in (slot, stored_fourth):
        os.utime(path, ns=(tick, tick))
    # The precondition the old shortcut tripped on: same size, same mtime, different bytes.
    assert slot.stat().st_size == stored_fourth.stat().st_size
    assert slot.stat().st_mtime_ns == stored_fourth.stat().st_mtime_ns

    tts_cache.materialize(tts_dir, fourth, slot)

    assert slot.read_bytes() == b"audio:forth"
