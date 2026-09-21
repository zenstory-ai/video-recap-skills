import json

import materials

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
    assert materials.source_id_for(a) == "src_ep-01_5"
    dup = materials.assign_source_ids([{"source_path": a}, {"source_path": copy}])
    assert [r["source_id"] for r in dup] == ["src_ep-01_5", "src_ep-01_5_2"]


def test_material_id_stable_for_same_basename_and_size(tmp_path):
    video = tmp_path / "Episode 1.mp4"
    assert materials.material_id_for(video, IDENTITY) == materials.material_id_for(video, IDENTITY)
    assert materials.material_id_for(video, IDENTITY) == "episode-1-1234"


def test_save_material_copies_allowed_files_writes_md_and_append_index(tmp_path):
    lib = tmp_path / "library"
    work = tmp_path / "work"
    work.mkdir()
    (work / "scenes.json").write_text(json.dumps([{"start": 0, "end": 1}]), encoding="utf-8")
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
    assert (mdir / "artifacts" / "asr_clean.json").exists()
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
    (work / "asr_clean.json").write_text(json.dumps({"segments": [{"text": "hello。"}]}), encoding="utf-8")
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
    assert (dest / "asr_clean.json").exists()


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


def test_restore_overwrite_false_does_not_prune_then_lose_staged_file(tmp_path):
    """FF-B: prune_stale_allowed + overwrite=False must NOT prune a staged file and then skip
    restoring it. The staged file is preserved; only true (non-staged) stale orphans are pruned."""
    lib = tmp_path / "library"
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "scenes.json").write_text(json.dumps([{"start": 0, "end": 1}]), encoding="utf-8")
    meta = materials.save_material(lib, seed, tmp_path / "ep.mp4", IDENTITY, SETTINGS)

    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "scenes.json").write_text(json.dumps([{"keep": "existing"}]), encoding="utf-8")  # staged name, present
    (dest / "vlm_analysis.json").write_text(json.dumps({"stale": 1}), encoding="utf-8")        # non-staged orphan

    res = materials.restore_material(lib, dest, source_path=tmp_path / "ep.mp4", source_identity=IDENTITY,
                                     settings=SETTINGS, material_id=meta["material_id"], overwrite=False)

    assert (dest / "scenes.json").exists(), "staged file must survive (not pruned-then-skipped)"
    assert json.loads((dest / "scenes.json").read_text(encoding="utf-8")) == [{"keep": "existing"}]
    assert not (dest / "vlm_analysis.json").exists(), "true stale orphan is pruned"
    assert "vlm_analysis.json" in res["pruned_artifacts"]
    assert "scenes.json" not in res["pruned_artifacts"]


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


def test_material_lookup_skips_corrupt_unrelated_cache_entry(tmp_path):
    lib = tmp_path / "library"
    work = tmp_path / "work"
    work.mkdir()
    (work / "scenes.json").write_text("[]", encoding="utf-8")
    valid = materials.save_material(lib, work, tmp_path / "episode.mp4", IDENTITY, SETTINGS)
    corrupt = lib / "materials" / "corrupt" / "material.json"
    corrupt.parent.mkdir()
    corrupt.write_text("not json", encoding="utf-8")

    found = materials.find_material_by_source(lib, tmp_path / "episode.mp4", IDENTITY)

    assert found["material_id"] == valid["material_id"]


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
