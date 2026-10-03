"""Content-addressed store for synthesized narration blocks.

A block's audio depends on its text and synthesis settings, never on where the block sits in
narration.json. Entries live in `tts_segments/cache/<digest>.wav` next to a `<digest>.json`
sidecar recording the exact inputs, the stored WAV's `{size, mtime_ns}` and the facts the
rerun needs (spoken text, duration, normalization, provider receipt). The index-named
`narr_NNN.wav` files that tts_meta.json and assemble read are views of an entry: a hard link,
or a copy where the filesystem has no links. Deleting or inserting a block therefore only
re-synthesizes the blocks whose own inputs changed.
"""
import hashlib
import json
import os
import shutil
import threading
from pathlib import Path

from approved_text_policy import write_json_atomically
from lib import file_identity

CACHE_DIR_NAME = "cache"
_RECORD_KEYS = ("spoken_text", "audio_duration", "normalization", "provider_receipt")


def cache_entry(tts_dir, cache_inputs):
    """(stored WAV, sidecar) paths for one set of synthesis inputs."""
    blob = json.dumps(cache_inputs, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
    base = Path(tts_dir) / CACHE_DIR_NAME
    return base / f"{digest}.wav", base / f"{digest}.json"


def legacy_sidecar_path(output_wav):
    """The per-index sidecar earlier versions wrote; never read (its keys differ), only removed."""
    return Path(str(output_wav) + ".cache.json")


def load(tts_dir, cache_inputs):
    """The stored record for these inputs, or None when absent or its WAV changed since."""
    stored_wav, sidecar = cache_entry(tts_dir, cache_inputs)
    if not sidecar.exists():
        return None
    # This skill wrote the sidecar: one it cannot parse is a bug to surface, not a silent miss
    # that re-bills the provider.
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeError(f"TTS 缓存 sidecar 损坏: {sidecar}") from exc
    if not isinstance(data, dict) or data.get("settings") != cache_inputs:
        return None
    if not stored_wav.is_file() or stored_wav.stat().st_size == 0:
        return None
    return data if data.get("audio") == file_identity(stored_wav) else None


def store(output_wav, cache_inputs, record):
    """Publish a finished block WAV under its inputs; returns the sidecar payload."""
    stored_wav, sidecar = cache_entry(Path(output_wav).parent, cache_inputs)
    stored_wav.parent.mkdir(exist_ok=True)
    tmp = _tmp_path(stored_wav)
    _link_or_copy(output_wav, tmp)
    # Identity of exactly the bytes being published: if a concurrent worker replaces the entry
    # with another take of the same inputs, the sidecars disagree with the WAV and miss safely.
    identity = file_identity(tmp)
    os.replace(tmp, stored_wav)
    data = {"settings": cache_inputs, "audio": identity,
            **{key: record.get(key) for key in _RECORD_KEYS}}
    write_json_atomically(sidecar, data)
    return data


def materialize(tts_dir, cache_inputs, output_wav):
    """Point the index-named `output_wav` at the stored WAV for these inputs."""
    stored_wav, _sidecar = cache_entry(tts_dir, cache_inputs)
    output_wav = Path(output_wav)
    if output_wav.exists():
        try:
            if os.path.samefile(stored_wav, output_wav):
                return
        except OSError:
            pass
    # Anything else is re-linked (or re-copied): {size, mtime_ns} cannot tell two takes of the
    # same length apart when they were written within one timestamp tick, and a stale slot
    # would silently play the wrong block.
    tmp = _tmp_path(output_wav)
    _link_or_copy(stored_wav, tmp)
    os.replace(tmp, output_wav)


def _tmp_path(path):
    return path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")


def _link_or_copy(source, destination):
    destination.unlink(missing_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
