"""Cross-skill contract for the full-file fingerprint helpers.

Each independently shipped skill keeps the same sha256-over-content behavior. A change
to one could silently diverge — and cache provenance is compared ACROSS skills: video-cut writes
`edited_source.mp4.meta.json`, video-recap reads it, video-assemble fingerprints the
same source again. A divergence there degrades into cache misses or, worse, false
cache hits. These tests hold the implementations to one behaviour and ensure the recap
orchestrator reuses its skill-local materials implementation instead of duplicating it.
"""
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# (module path, attribute name) for every full-content fingerprint helper in the bundle.
FINGERPRINT_IMPLEMENTATIONS = (
    ("skills/video-assemble/scripts/artifacts.py", "_file_fingerprint"),
    ("skills/video-cut/scripts/cut_contract.py", "file_fingerprint"),
    ("skills/video-recap/scripts/materials.py", "file_fingerprint"),
    ("skills/video-script/scripts/lib.py", "file_fingerprint"),
    ("skills/video-understanding/scripts/lib.py", "file_fingerprint"),
    ("skills/video-voiceover/scripts/lib.py", "file_fingerprint"),
)


def _load(rel_path, index):
    """Import one skill module under a unique name, in its own module namespace.

    Every skill ships its own top-level `lib` (and `narration`, ...), which is exactly why
    the suite runs one pytest process per skill. Loading several here would otherwise
    resolve `from lib import ...` against whichever skill got imported first. So each load
    gets a clean sys.modules for the bare skill-local names, restored afterwards, letting
    this one cross-cutting test legitimately see all seven implementations at once.
    """
    import sys

    path = ROOT / rel_path
    scripts_dir = str(path.parent)
    skill_local = {p.stem for p in path.parent.glob("*.py")}
    shadowed = {name: sys.modules.pop(name) for name in skill_local if name in sys.modules}

    spec = importlib.util.spec_from_file_location(f"_fp_probe_{index}", path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, scripts_dir)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts_dir)
        for name in skill_local:
            sys.modules.pop(name, None)
        sys.modules.update(shadowed)
    return module


@pytest.fixture(scope="module")
def implementations():
    loaded = []
    for index, (rel_path, attr) in enumerate(FINGERPRINT_IMPLEMENTATIONS):
        module = _load(rel_path, index)
        assert hasattr(module, attr), f"{rel_path} lost {attr}"
        loaded.append((rel_path, module, getattr(module, attr)))
    return loaded


def test_every_skill_fingerprints_identical_bytes_identically(implementations, tmp_path):
    sample = tmp_path / "asset.bin"
    sample.write_bytes(b"video-recap-skills" * 5000)
    digests = {rel: fn(sample) for rel, _module, fn in implementations}

    assert len(set(digests.values())) == 1, f"implementations disagree: {digests}"
    # sha256 over content, so an independent copy at a different path matches
    copy = tmp_path / "copied.bin"
    copy.write_bytes(sample.read_bytes())
    for rel, _module, fn in implementations:
        assert fn(copy) == digests[rel], f"{rel} is not content-addressed"


def test_fingerprint_memo_never_serves_a_stale_digest(implementations, tmp_path):
    """The in-process memo is keyed on identity metadata, but the guarantee callers rely
    on is content-addressing: rewriting a file in place must yield a new digest."""
    for rel, _module, fn in implementations:
        target = tmp_path / f"{Path(rel).parent.parent.name}.bin"
        target.write_bytes(b"before")
        first = fn(target)

        # Same length, so size cannot distinguish the two revisions and only mtime can.
        # mtime is advanced explicitly rather than trusting the host clock resolution, so
        # the assertion exercises the memo key itself on every filesystem CI runs on.
        target.write_bytes(b"after!")
        stat = os.stat(target)
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        assert fn(target) != first, f"{rel} served a stale memoized digest"

        target.write_bytes(b"before")
        stat = os.stat(target)
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000))
        assert fn(target) == first, f"{rel} lost content-addressing on rewrite"


def test_fingerprint_is_memoized_within_a_process(implementations, tmp_path):
    """One understanding run fingerprints the source video 8-10 times and the whole
    extracted frame set 2-3 times. Without a memo that is gigabytes of redundant reads
    before any real work starts."""
    for rel, module, fn in implementations:
        target = tmp_path / f"memo_{Path(rel).parent.parent.name}.bin"
        target.write_bytes(b"x" * 100_000)
        reads = {"count": 0}
        real_open = module.open if hasattr(module, "open") else open

        def counting_open(*args, **kwargs):
            reads["count"] += 1
            return real_open(*args, **kwargs)

        module.open = counting_open
        try:
            digests = {fn(target) for _ in range(5)}
        finally:
            if real_open is open:
                del module.open
            else:  # pragma: no cover - module never shadows open today
                module.open = real_open

        assert len(digests) == 1
        assert reads["count"] == 1, f"{rel} re-read the file {reads['count']} times"


def test_memo_key_includes_size_and_mtime(implementations, tmp_path):
    target = tmp_path / "identity.bin"
    target.write_bytes(b"abc")
    for rel, module, _fn in implementations:
        identity = module._file_identity(target)
        stat = os.stat(target)
        assert identity == (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns), rel


def test_recap_runtime_reuses_materials_fingerprint(monkeypatch, tmp_path):
    runtime = _load("skills/video-recap/scripts/recap_runtime.py", "runtime")
    assert not hasattr(runtime, "_file_fingerprint")

    sample = tmp_path / "recap-source.bin"
    sample.write_bytes(b"shared recap fingerprint")
    monkeypatch.setattr(runtime.material_lib, "file_fingerprint", lambda path: f"shared:{path}")
    assert runtime._run_manifest_payload(
        sample,
        type(
            "Args",
            (),
            {
                "context": None,
                "scene_threshold": 0.3,
                "style": None,
                "edit_mode": "full",
                "target_duration": None,
                "skip_asr": False,
                "mimo_video_overview": False,
                "consolidate": False,
                "consolidate_asr": False,
                "audio_mode": "narration",
                "audio_stream_index": 0,
                "tts_meta": None,
                "narration_adoption": None,
                "audio_mix_adoption": None,
            },
        )(),
    )["source_video_fingerprint"] == f"shared:{sample}"
