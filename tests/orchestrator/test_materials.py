import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import materials
import recap_cli
import recap_runtime
import recap_timeline
from lib import material_id_for, source_id_for

IDENTITY = {"size": 1234, "mtime_ns": 1_700_000_000_000_000_000}
SETTINGS = {"style": "s1", "context": None}


def test_source_id_is_stem_plus_size_and_same_id_gets_numbered_suffix(tmp_path):
    a = tmp_path / "Ep 01.mp4"
    b = tmp_path / "b.mp4"
    copy = tmp_path / "nested" / "Ep 01.mp4"
    copy.parent.mkdir()
    a.write_bytes(b"x" * 5)
    b.write_bytes(b"xx")
    copy.write_bytes(b"y" * 5)

    first = materials.assign_source_ids([{"source_path": b}, {"source_path": a}])
    second = materials.assign_source_ids([{"source_path": a}, {"source_path": b}])

    assert {r["source_path"]: r["source_id"] for r in first} == {
        r["source_path"]: r["source_id"] for r in second
    }
    assert source_id_for(a) == "src_ep-01_5"
    dup = materials.assign_source_ids([{"source_path": a}, {"source_path": copy}])
    assert [r["source_id"] for r in dup] == ["src_ep-01_5", "src_ep-01_5_2"]


def test_material_id_stable_for_same_basename_and_size(tmp_path):
    video = tmp_path / "Episode 1.mp4"
    assert material_id_for(video, IDENTITY) == material_id_for(video, IDENTITY)
    assert material_id_for(video, IDENTITY) == "episode-1-1234"


def test_save_material_copies_allowed_files_writes_md_and_append_index(tmp_path):
    lib = tmp_path / "library"
    work = tmp_path / "work"
    work.mkdir()
    (work / "scenes.json").write_text(json.dumps([{"start": 0, "end": 1}]), encoding="utf-8")
    (work / "asr_result.json").write_text(json.dumps([{"text": "raw"}]), encoding="utf-8")
    (work / "asr_clean.json").write_text(json.dumps({"segments": [{"text": "clean"}]}), encoding="utf-8")
    (work / "understanding_index.json").write_text(json.dumps({
        "characters": [{"name": "英雄"}], "relationships": [], "plot_points": [],
        "entities": [{"name": "hero-sword"}], "research_glossary": [],
    }), encoding="utf-8")
    (work / "audio.wav").write_bytes(b"raw audio should not copy")
    (work / "secret.json").write_text("tp-secret", encoding="utf-8")

    meta = materials.save_material(lib, work, tmp_path / "ep1.mp4", IDENTITY, SETTINGS, source_id="src_ep1_1234")

    mdir = lib / "materials" / meta["material_id"]
    assert (mdir / "material.json").exists()
    assert (mdir / "material.md").exists()
    assert (mdir / "artifacts" / "scenes.json").exists()
    assert (mdir / "artifacts" / "asr_result.json").exists()
    assert not (mdir / "artifacts" / "asr_clean.json").exists()
    assert not (mdir / "artifacts" / "audio.wav").exists()
    assert not (mdir / "artifacts" / "secret.json").exists()
    lines = (lib / "materials_index.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event"] == "saved"
    assert rec["summary"].startswith("Analyzed video material: ep1.mp4")
    assert "英雄" in rec["tags"] and "hero-sword" in rec["tags"]
    assert meta["source_video_identity"] == IDENTITY and meta["settings"] == SETTINGS
    assert all(set(a) == {"name", "path", "bytes"} for a in meta["artifacts"])

    materials.save_material(lib, work, tmp_path / "ep1.mp4", IDENTITY, SETTINGS, source_id="src_ep1_1234")
    assert len((lib / "materials_index.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_restore_material_requires_matching_path_identity_and_settings(tmp_path):
    lib = tmp_path / "library"
    work = tmp_path / "work"
    work.mkdir()
    (work / "asr_result.json").write_text(json.dumps([{"text": "hello"}]), encoding="utf-8")
    video = tmp_path / "ep.mp4"
    meta = materials.save_material(lib, work, video, IDENTITY, SETTINGS)

    dest = tmp_path / "dest"
    rewritten = {**IDENTITY, "mtime_ns": IDENTITY["mtime_ns"] + 1}
    for source_path, identity, settings in (
        (video, rewritten, SETTINGS),
        (tmp_path / "other.mp4", IDENTITY, SETTINGS),
        (video, IDENTITY, {**SETTINGS, "style": "s2"}),
    ):
        mismatch = materials.restore_material(
            lib, dest, source_path=source_path, source_identity=identity, settings=settings,
            material_id=meta["material_id"],
        )
        assert mismatch["restored"] is False
        assert not dest.exists()

    ok = materials.restore_material(
        lib, dest, source_path=video, source_identity=IDENTITY, settings=dict(SETTINGS),
        material_id=meta["material_id"],
    )
    assert ok["restored"] is True
    assert (dest / "asr_result.json").exists()


def test_consolidation_status_round_trips_but_identity_bound_asr_evidence_does_not(tmp_path):
    """brief-only on a restored work_dir still sees a failed consolidate; the ASR timing
    sidecar binds files a restore rewrites or never copies, so it is not stored at all."""
    lib = tmp_path / "library"
    work = tmp_path / "work"
    work.mkdir()
    status = {
        "stage": "consolidation", "enabled": True, "do_index": True,
        "status": "failed", "message": "MiMo timeout", "artifacts": [],
    }
    (work / "scenes.json").write_text(json.dumps([{"start": 0, "end": 1}]), encoding="utf-8")
    (work / "consolidation.status.json").write_text(json.dumps(status), encoding="utf-8")
    (work / "asr_timing_evidence.json").write_text(json.dumps({"status": "x"}), encoding="utf-8")
    video = tmp_path / "ep.mp4"
    meta = materials.save_material(lib, work, video, IDENTITY, SETTINGS)
    assert {a["name"] for a in meta["artifacts"]} == {"scenes.json", "consolidation.status.json"}

    dest = tmp_path / "dest"
    restored = materials.restore_material(
        lib, dest, source_path=video, source_identity=IDENTITY, settings=SETTINGS,
        material_id=meta["material_id"],
    )
    assert restored["restored"] is True
    assert json.loads((dest / "consolidation.status.json").read_text(encoding="utf-8")) == status
    assert not (dest / "asr_timing_evidence.json").exists()


def test_restore_material_prunes_stale_allowed_artifacts_before_copy(tmp_path):
    lib = tmp_path / "library"
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "scenes.json").write_text(json.dumps([{"start": 0, "end": 1}]), encoding="utf-8")
    meta = materials.save_material(lib, seed, tmp_path / "ep.mp4", IDENTITY, SETTINGS)

    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "vlm_analysis.json").write_text(json.dumps({"summary": "stale"}), encoding="utf-8")
    (dest / "narration.json").write_text("[]", encoding="utf-8")

    restored = materials.restore_material(
        lib,
        dest,
        source_path=tmp_path / "ep.mp4",
        source_identity=IDENTITY,
        settings=SETTINGS,
        material_id=meta["material_id"],
    )

    assert restored["restored"] is True
    assert (dest / "scenes.json").exists()
    assert not (dest / "vlm_analysis.json").exists()
    assert (dest / "narration.json").exists(), "non-material files are not pruned"
    assert "vlm_analysis.json" in restored["pruned_artifacts"]


def test_allowed_artifacts_redact_secret_values_but_keep_legitimate_words(tmp_path):
    """Redaction drops credential VALUES but keeps ordinary analysis words and field names."""
    lib = tmp_path / "library"
    work = tmp_path / "work"
    work.mkdir()
    (work / "understanding_index.json").write_text(
        json.dumps({
            "characters": [], "relationships": [], "plot_points": [], "entities": [],
            "research_glossary": [],
            "summary": "主角发现了一个秘密 secret，一枚 token 在黑市流通",   # legit words -> must survive
            "api_key": "tp-abcdef12345678",                                  # credential key -> value dropped
            "token_economy": "影片解释 token 的发行机制",                     # benign name containing 'token' -> kept
            "notes": "调试时漏了 key: sk-ABCDEFGHIJKLMNOP1234 和 tp-zzzzzzzz9999",  # value shapes -> redacted
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (work / "agent_narration_brief.md").write_text(
        "MIMO_API_KEY=tp-another-secret-value\n剧情梗概：一个关于 secret 和 token 的故事", encoding="utf-8")
    meta = materials.save_material(lib, work, tmp_path / "ep.mp4", IDENTITY, SETTINGS)

    persisted = "\n".join(p.read_text(encoding="utf-8") for p in (lib / "materials").rglob("*") if p.is_file())
    # secret VALUES are gone
    for secret in ("tp-abcdef12345678", "sk-ABCDEFGHIJKLMNOP1234", "tp-zzzzzzzz9999", "tp-another-secret-value"):
        assert secret not in persisted, secret
    # legitimate words and benign field names PRESERVED (the over-redaction fix)
    assert "主角发现了一个秘密" in persisted
    assert "secret" in persisted and "token" in persisted
    assert "token_economy" in persisted

    idx = json.loads((lib / "materials" / meta["material_id"] / "artifacts" / "understanding_index.json")
                     .read_text(encoding="utf-8"))
    assert idx["api_key"] == "[redacted]"                 # value dropped, key name kept, not coalesced
    assert "secret" in idx["summary"] and "token" in idx["summary"]
    assert idx["token_economy"] == "影片解释 token 的发行机制"

    dest = tmp_path / "dest"
    materials.restore_material(lib, dest, source_path=tmp_path / "ep.mp4", source_identity=IDENTITY,
                               settings=SETTINGS, material_id=meta["material_id"])
    restored = (dest / "understanding_index.json").read_text(encoding="utf-8")
    assert "tp-abcdef12345678" not in restored
    assert "secret" in restored and "token" in restored


def test_redact_json_keeps_distinct_secret_named_keys_without_coalescing(tmp_path):
    """A dict with multiple credential-named keys must keep every key (each value dropped),
    never collapse them into one 'redacted_key', and must not touch benign look-alike names."""
    out = materials._redact_json({
        "api_key": "tp-realvalue123456",
        "access_token": "sk-ABCDEFGHIJKLMNOP1234",
        "tokenized_scenes": ["镜头1", "镜头2"],   # benign name with 'token' substring -> untouched
        "secrets_revealed": "结局揭晓的秘密",       # benign name with 'secret' substring -> untouched
        "title": "ok",
    })
    assert out["api_key"] == "[redacted]"
    assert out["access_token"] == "[redacted]"
    assert "redacted_key" not in out                 # no coalescing
    assert out["tokenized_scenes"] == ["镜头1", "镜头2"]
    assert out["secrets_revealed"] == "结局揭晓的秘密"
    assert out["title"] == "ok"


def test_redact_text_leaves_plain_words_but_strips_token_shapes():
    assert materials._redact_text("the secret garden hides a golden token") == \
        "the secret garden hides a golden token"
    assert "tp-" not in materials._redact_text("key is tp-abcdef12345678 ok")
    assert "ghp_" not in materials._redact_text("token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")


def test_save_material_reconciles_orphan_artifacts_on_resave(tmp_path):
    """FF-C: re-saving with fewer artifacts removes the orphan from artifacts/ so the on-disk
    set matches material.json (no stale blob lingering for greps)."""
    lib = tmp_path / "library"
    work = tmp_path / "work"
    work.mkdir()
    (work / "scenes.json").write_text("[]", encoding="utf-8")
    (work / "asr_result.json").write_text(json.dumps([{"text": "hi"}]), encoding="utf-8")
    meta = materials.save_material(lib, work, tmp_path / "ep.mp4", IDENTITY, SETTINGS)
    adir = lib / "materials" / meta["material_id"] / "artifacts"
    assert (adir / "asr_result.json").exists()

    (work / "asr_result.json").unlink()  # a smaller / partial re-analysis
    meta2 = materials.save_material(lib, work, tmp_path / "ep.mp4", IDENTITY, SETTINGS)

    assert not (adir / "asr_result.json").exists(), "orphan artifact removed on re-save"
    assert (adir / "scenes.json").exists()
    assert {a["name"] for a in meta2["artifacts"]} == {"scenes.json"}


def test_save_material_refreshes_malformed_existing_metadata(tmp_path):
    lib = tmp_path / "library"
    work = tmp_path / "work"
    work.mkdir()
    first = materials.save_material(
        lib, work, tmp_path / "episode.mp4", IDENTITY, SETTINGS, now="2026-01-01T00:00:00Z"
    )
    meta_path = lib / "materials" / first["material_id"] / "material.json"
    meta_path.write_text("{}", encoding="utf-8")

    refreshed = materials.save_material(
        lib, work, tmp_path / "episode.mp4", IDENTITY, SETTINGS, now="2026-02-01T00:00:00Z"
    )

    assert refreshed["created_at"] == "2026-02-01T00:00:00Z"
    assert refreshed["updated_at"] == "2026-02-01T00:00:00Z"


# The exact settings dict a run with default flags recorded before --mimo-video-overview and
# --consolidate-asr were removed. Written out by hand: it must not follow _analysis_settings.
_SETTINGS_BEFORE_FLAG_REMOVAL = {
    "context": "",
    "scene_threshold": None,
    "style": "纪录片",
    "edit_mode": "full",
    "target_duration": None,
    "skip_asr": False,
    "mimo_video_overview": False,
    "consolidate": True,
    "consolidate_asr": False,
}


def _material_saved_before_flag_removal(lib, video, settings):
    """A library entry as the previous release wrote it, asr_clean.json artifact included."""
    mid = material_id_for(video, IDENTITY)
    artifacts = lib / "materials" / mid / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "asr_result.json").write_text(
        json.dumps([{"start": 0.0, "end": 1.0, "text": "原始转写"}]), encoding="utf-8")
    (artifacts / "asr_clean.json").write_text(
        json.dumps({"segments": [{"start": 0.0, "end": 1.0, "text": "清洗旧稿"}]}), encoding="utf-8")
    listed = [{"name": name, "path": f"artifacts/{name}", "bytes": 1}
              for name in ("asr_clean.json", "asr_result.json")]
    (lib / "materials" / mid / "material.json").write_text(json.dumps({
        "material_id": mid, "source_path": str(video.resolve()),
        "source_video_identity": IDENTITY, "settings": settings,
        "updated_at": "2026-10-03T00:00:00Z", "artifacts": listed,
    }), encoding="utf-8")
    return mid


def test_entry_saved_before_the_flag_removal_still_restores(tmp_path):
    video = tmp_path / "ep.mp4"
    lib = tmp_path / "library"
    mid = _material_saved_before_flag_removal(lib, video, _SETTINGS_BEFORE_FLAG_REMOVAL)
    _, args = recap_cli.parse_args([str(video)])
    settings = recap_runtime._analysis_settings(args)
    assert settings == _SETTINGS_BEFORE_FLAG_REMOVAL

    dest = tmp_path / "dest"
    restored = materials.restore_material(
        lib, dest, source_path=video, source_identity=IDENTITY, settings=settings, material_id=mid,
    )

    assert restored["restored"] is True
    assert sorted(restored["artifacts"]) == ["asr_clean.json", "asr_result.json"]
    # The restored leftover is inert: readers take the raw transcript.
    assert [row["text"] for row in recap_timeline._source_speech_rows(dest)] == ["原始转写"]


def test_entry_listing_the_removed_markdown_index_still_restores(tmp_path):
    video = tmp_path / "ep.mp4"
    lib = tmp_path / "library"
    mid = _material_saved_before_flag_removal(lib, video, _SETTINGS_BEFORE_FLAG_REMOVAL)
    entry = lib / "materials" / mid
    (entry / "artifacts" / "understanding_index.md").write_text("# 旧索引", encoding="utf-8")
    meta = json.loads((entry / "material.json").read_text(encoding="utf-8"))
    meta["artifacts"].append(
        {"name": "understanding_index.md", "path": "artifacts/understanding_index.md", "bytes": 1})
    (entry / "material.json").write_text(json.dumps(meta), encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "narration.json").write_text("[]", encoding="utf-8")

    restored = materials.restore_material(
        lib, dest, source_path=video, source_identity=IDENTITY,
        settings=_SETTINGS_BEFORE_FLAG_REMOVAL, material_id=mid,
    )

    assert restored["restored"] is True
    assert (dest / "understanding_index.md").read_text(encoding="utf-8") == "# 旧索引"
    assert (dest / "narration.json").read_text(encoding="utf-8") == "[]"


def test_entry_analyzed_with_a_removed_feature_on_is_reanalyzed(tmp_path):
    video = tmp_path / "ep.mp4"
    lib = tmp_path / "library"
    mid = _material_saved_before_flag_removal(
        lib, video, {**_SETTINGS_BEFORE_FLAG_REMOVAL, "consolidate_asr": True})
    _, args = recap_cli.parse_args([str(video)])

    restored = materials.restore_material(
        lib, tmp_path / "dest", source_path=video, source_identity=IDENTITY,
        settings=recap_runtime._analysis_settings(args), material_id=mid,
    )

    assert restored == {"restored": False, "reason": "settings mismatch", "material_id": mid}


def _restore(lib, dest, video, meta):
    return materials.restore_material(
        lib, dest, source_path=video, source_identity=IDENTITY, settings=SETTINGS,
        material_id=meta["material_id"],
    )


def test_agent_research_survives_a_restore_from_an_entry_without_research(tmp_path):
    lib, seed, dest, video = tmp_path / "library", tmp_path / "seed", tmp_path / "dest", tmp_path / "ep.mp4"
    seed.mkdir()
    dest.mkdir()
    (seed / "scenes.json").write_text("[]", encoding="utf-8")
    meta = materials.save_material(lib, seed, video, IDENTITY, SETTINGS)
    (dest / "background_research.json").write_text('{"story": "agent"}', encoding="utf-8")

    restored = _restore(lib, dest, video, meta)

    assert restored["pruned_artifacts"] == []
    assert json.loads((dest / "background_research.json").read_text(encoding="utf-8")) == {"story": "agent"}


def test_restore_keeps_newer_agent_research_and_replaces_older(tmp_path):
    lib, seed, dest, video = tmp_path / "library", tmp_path / "seed", tmp_path / "dest", tmp_path / "ep.mp4"
    seed.mkdir()
    dest.mkdir()
    (seed / "background_research.json").write_text('{"story": "saved"}', encoding="utf-8")
    meta = materials.save_material(lib, seed, video, IDENTITY, SETTINGS)
    saved = lib / "materials" / meta["material_id"] / "artifacts" / "background_research.json"
    mine = dest / "background_research.json"
    mine.write_text('{"story": "agent"}', encoding="utf-8")

    os.utime(mine, ns=(saved.stat().st_mtime_ns + 10**9, saved.stat().st_mtime_ns + 10**9))
    _restore(lib, dest, video, meta)
    assert json.loads(mine.read_text(encoding="utf-8")) == {"story": "agent"}

    os.utime(mine, ns=(saved.stat().st_mtime_ns - 10**9, saved.stat().st_mtime_ns - 10**9))
    _restore(lib, dest, video, meta)
    assert json.loads(mine.read_text(encoding="utf-8")) == {"story": "saved"}


def _save_analyzed(lib, seed, video):
    seed.mkdir()
    (seed / "scenes.json").write_text(
        json.dumps([{"scene_id": 0, "start": 0.0, "end": 1.0, "duration": 1.0}]), encoding="utf-8")
    (seed / "vlm_analysis.json").write_text(
        json.dumps([{"scene_id": 0, "start": 0.0, "end": 1.0, "description": "门开了"}]), encoding="utf-8")
    (seed / "asr_result.json").write_text("[]", encoding="utf-8")
    (seed / "understanding_index.json").write_text(json.dumps({
        "characters": [{"name": "老王", "description": "门卫"}], "relationships": [],
        "plot_points": [], "entities": [], "research_glossary": [],
    }), encoding="utf-8")
    (seed / "consolidation.status.json").write_text(json.dumps({
        "stage": "consolidation", "enabled": True, "status": "ok", "message": "",
    }), encoding="utf-8")
    return materials.save_material(lib, seed, video, IDENTITY, SETTINGS)


def test_restore_binds_the_index_to_the_restored_analysis(tmp_path):
    lib, dest, video = tmp_path / "library", tmp_path / "dest", tmp_path / "ep.mp4"
    meta = _save_analyzed(lib, tmp_path / "seed", video)

    _restore(lib, dest, video, meta)

    sidecar = json.loads((dest / "understanding_index.json.meta.json").read_text(encoding="utf-8"))
    stat = (dest / "vlm_analysis.json").stat()
    assert sidecar["source"] == {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    assert sidecar["scene_count"] == 1
    assert sidecar["model"] == materials.CONFIG["vlm_model"]


@pytest.mark.parametrize("status", ["failed", "disabled", None])
def test_restore_does_not_vouch_for_an_index_without_an_ok_status(tmp_path, status):
    """A stale index left by a failed rerun must not gain provenance through a restore."""
    lib, seed, dest, video = tmp_path / "library", tmp_path / "seed", tmp_path / "dest", tmp_path / "ep.mp4"
    meta = _save_analyzed(lib, seed, video)
    saved_status = lib / "materials" / meta["material_id"] / "artifacts" / "consolidation.status.json"
    if status is None:
        saved_status.unlink()
        material_json = saved_status.parent.parent / "material.json"
        record = json.loads(material_json.read_text(encoding="utf-8"))
        record["artifacts"] = [a for a in record["artifacts"] if a["name"] != "consolidation.status.json"]
        material_json.write_text(json.dumps(record), encoding="utf-8")
        # A leftover "ok" in the reused work_dir is pruned, so it cannot vouch either.
        dest.mkdir()
        (dest / "consolidation.status.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    else:
        saved_status.write_text(json.dumps({"stage": "consolidation", "status": status}), encoding="utf-8")

    assert _restore(lib, dest, video, meta)["restored"] is True

    assert (dest / "understanding_index.json").exists()
    assert not (dest / "understanding_index.json.meta.json").exists()


def test_restore_without_an_index_writes_no_sidecar(tmp_path):
    lib, seed, dest, video = tmp_path / "library", tmp_path / "seed", tmp_path / "dest", tmp_path / "ep.mp4"
    seed.mkdir()
    (seed / "vlm_analysis.json").write_text("[]", encoding="utf-8")
    meta = materials.save_material(lib, seed, video, IDENTITY, SETTINGS)
    _restore(lib, dest, video, meta)
    assert not (dest / "understanding_index.json.meta.json").exists()


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="requires media tools")
def test_brief_rebuilt_after_a_restore_keeps_the_story_index(tmp_path):
    """The rebuild cut pass 2 runs: understand.py --brief-only over a restored work_dir."""
    video = tmp_path / "ep.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=160x120:r=12:d=1",
                    "-c:v", "libx264", str(video)], check=True, capture_output=True)
    lib, dest = tmp_path / "library", tmp_path / "dest"
    meta = _save_analyzed(lib, tmp_path / "seed", video)
    _restore(lib, dest, video, meta)

    understand = Path(__file__).resolve().parents[2] / "skills/video-understanding/scripts/understand.py"
    result = subprocess.run(
        [sys.executable, str(understand), str(video), "--work-dir", str(dest), "--brief-only"],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stderr
    brief = (dest / "agent_narration_brief.md").read_text(encoding="utf-8")
    assert "## Understanding index" in brief and "老王" in brief
    assert "missing_index" not in brief
