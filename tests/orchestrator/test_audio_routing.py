"""Audio ownership routing across recap edit modes."""

import json
import sys

import pytest

import recap_runner
import recap_cli
import recap_runtime
import recap_timeline
from _helpers import manifest_args as _args


def _finish_stubs(monkeypatch, work, calls):
    def fake_run(skill, script, *args):
        args = [str(value) for value in args]
        calls.append((skill, script, args))
        if script == "cut.py":
            (work / "edited_source.mp4").write_bytes(b"edited")
            (work / "clip_plan_validated.json").write_text(
                json.dumps({"clips": [], "qc": {"status": "pass"}}), encoding="utf-8"
            )
        if script == "assemble.py":
            (work / "assembly_manifest.json").write_text(
                json.dumps({"final_output": str(work / "final.mp4")}), encoding="utf-8"
            )

    monkeypatch.setattr(recap_runner, "_run", fake_run)
    monkeypatch.setattr(recap_runner, "_preflight_burn_subtitles", lambda _args: None)
    monkeypatch.setattr(recap_runner, "_surface_cut_qc", lambda _work: {"status": "pass"})
    monkeypatch.setattr(
        recap_runner,
        "_write_final_qc_reports",
        lambda *_: {"final_qc": {"ok": True, "blocker_count": 0}},
    )
    monkeypatch.setattr(recap_runner, "_print_final_qc_pointer", lambda *_: None)
    return fake_run


def test_full_source_audio_routes_directly_to_assemble_without_tts(monkeypatch, tmp_path):
    audio_mode = "source-mix"
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    work.mkdir()
    (work / "tts_meta.json").write_text("not-json", encoding="utf-8")
    calls = []
    _finish_stubs(monkeypatch, work, calls)
    monkeypatch.setattr(
        sys, "argv",
        ["recap.py", str(video), "--work-dir", str(work), "--audio-mode", audio_mode],
    )

    recap_runner.main()

    scripts = [script for _, script, _ in calls]
    assert scripts == ["assemble.py"]
    assemble = calls[0][2]
    assert assemble[:1] == [str(video.resolve())]
    assert assemble[assemble.index("--audio-mode") + 1] == audio_mode
    assert "--tts-meta" not in assemble
    manifest = json.loads((work / "recap_run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["audio"] == {"mode": audio_mode, "selected_stream_index": 0}


def test_single_cut_source_audio_uses_existing_plan_but_never_pauses_for_narration(
    monkeypatch, tmp_path
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    work.mkdir()
    (work / "clip_plan.json").write_text('{"clips":[]}', encoding="utf-8")
    args = _args(edit_mode="cut", audio_mode="source-mix")
    recap_runtime._write_run_manifest(work, video, args)
    calls = []
    _finish_stubs(monkeypatch, work, calls)
    monkeypatch.setattr(
        sys, "argv",
        ["recap.py", str(video), "--work-dir", str(work), "--edit-mode", "cut",
         "--audio-mode", "source-mix"],
    )

    recap_runner.main()

    assert [script for _, script, _ in calls] == ["cut.py", "assemble.py"]
    assemble = calls[-1][2]
    assert assemble[0] == str(work / "edited_source.mp4")
    assert "--source-video" in assemble
    assert assemble[assemble.index("--audio-mode") + 1] == "source-mix"


def test_multi_source_cut_source_audio_routes_cut_then_assemble(monkeypatch, tmp_path):
    videos = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for video in videos:
        video.write_bytes(video.stem.encode())
    work = tmp_path / "work"
    work.mkdir()
    (work / "clip_plan.json").write_text('{"clips":[]}', encoding="utf-8")
    args = _args(edit_mode="cut", audio_mode="source-mix")
    records = recap_runtime._build_multi_source_records(videos, args)
    recap_runtime._write_project_run_manifest(work, args, records)
    calls = []
    _finish_stubs(monkeypatch, work, calls)
    monkeypatch.setattr(
        sys, "argv",
        ["recap.py", *map(str, videos), "--work-dir", str(work), "--edit-mode", "cut",
         "--audio-mode", "source-mix"],
    )

    recap_runner.main()

    assert [script for _, script, _ in calls] == ["cut.py", "assemble.py"]
    assemble = calls[-1][2]
    assert assemble[0] == str(work / "edited_source.mp4")
    assert assemble[assemble.index("--audio-mode") + 1] == "source-mix"


def test_cut_source_audio_without_plan_keeps_analysis_pause_and_continuation_mode(
    monkeypatch, tmp_path, capsys
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    monkeypatch.setattr(recap_runner, "_preflight_burn_subtitles", lambda _args: None)
    monkeypatch.setattr(
        recap_runner, "_run_or_restore_understanding",
        lambda *_: (work / "agent_narration_brief.md").write_text("# brief", encoding="utf-8"),
    )
    monkeypatch.setattr(
        sys, "argv",
        ["recap.py", str(video), "--work-dir", str(work), "--edit-mode", "cut",
         "--audio-mode", "source-mix"],
    )

    recap_runner.main()

    output = capsys.readouterr().out
    assert "clip_plan.json" in output
    assert "--audio-mode source-mix" in output
    assert not (work / "narration.json").exists()


@pytest.mark.parametrize("argv", [["--doctor"], ["--doctor", "--json"], ["input.mp4", "--doctor"]])
def test_removed_doctor_flag_points_at_doctor_py(monkeypatch, capsys, argv):
    calls = []
    monkeypatch.setattr(recap_runner, "_run", lambda *args: calls.append(args))
    monkeypatch.setattr(recap_runner, "_preflight_burn_subtitles", lambda _args: None)
    monkeypatch.setattr(sys, "argv", ["recap.py", *argv])
    with pytest.raises(SystemExit) as exc:
        recap_runner.main()
    assert exc.value.code == 2
    assert "doctor.py" in capsys.readouterr().err
    assert calls == []


@pytest.mark.parametrize("ambient_provider", ["index-tts", "garbage-provider"])
def test_source_full_ignores_ambient_tts_provider(
    monkeypatch, tmp_path, ambient_provider
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    calls = []
    _finish_stubs(monkeypatch, work, calls)
    monkeypatch.setenv("TTS_PROVIDER", ambient_provider)
    monkeypatch.setenv("VOICE_REF", str(tmp_path / "missing-ambient-voice.wav"))
    monkeypatch.setenv("MIMO_TTS_VOICE", "ambient-mimo-voice")
    monkeypatch.setattr(
        sys, "argv",
        ["recap.py", str(video), "--work-dir", str(work), "--audio-mode", "source-mix"],
    )

    recap_runner.main()

    assert [script for _, script, _ in calls] == ["assemble.py"]


def test_multi_source_rejects_unbound_narration_before_manifest_or_analysis(
    monkeypatch, tmp_path
):
    videos = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for video in videos:
        video.write_bytes(b"video")
    work = tmp_path / "work"
    work.mkdir()
    (work / "narration.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(recap_runner, "_preflight_burn_subtitles", lambda _args: None)
    monkeypatch.setattr(
        recap_runner,
        "_build_multi_source_records",
        lambda *_: (_ for _ in ()).throw(AssertionError("source analysis started")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recap.py", *map(str, videos), "--work-dir", str(work),
            "--edit-mode", "cut", "--audio-mode", "source-mix",
        ],
    )

    with pytest.raises(SystemExit, match="新的 --work-dir"):
        recap_runner.main()
    assert not (work / "recap_run_manifest.json").exists()


def test_multi_source_clip_plan_without_manifest_is_refused_by_name(monkeypatch, tmp_path):
    videos = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for video in videos:
        video.write_bytes(video.stem.encode())
    work = tmp_path / "work"
    work.mkdir()
    (work / "clip_plan.json").write_text('{"clips":[]}', encoding="utf-8")
    monkeypatch.setattr(recap_runner, "_preflight_burn_subtitles", lambda _args: None)
    monkeypatch.setattr(
        sys, "argv",
        ["recap.py", *map(str, videos), "--work-dir", str(work), "--edit-mode", "cut"],
    )

    with pytest.raises(SystemExit, match="缺少 recap_run_manifest.json"):
        recap_runner.main()
    assert not (work / "multi_source_manifest.json").exists()


def test_single_video_record_keeps_the_keys_saved_materials_were_written_under(tmp_path):
    """One source gets no id suffix, so material restore and paid caches still find their files."""
    video = (tmp_path / "My Film.mp4").resolve()
    video.write_bytes(b"video")
    args = _args()

    record = recap_runtime._build_multi_source_records([video], args)[0]

    assert record["source_id"] == "src_my-film_5"
    assert record["material_id"] == "my-film-5"
    assert record["source_path"] == str(video)
    assert record["source_name"] == "My Film.mp4"
    assert record["settings"] == recap_runtime._analysis_settings(args)
    assert record["source_video_identity"]["size"] == 5


def test_source_mix_leaves_ambient_voice_ref_out_of_args_and_resume(monkeypatch, tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    seen = []
    monkeypatch.setenv("VOICE_REF", str(tmp_path / "ambient.wav"))
    monkeypatch.setattr(recap_runner, "_execute_pipeline", lambda args, videos: seen.append(args))
    monkeypatch.setattr(
        sys, "argv", ["recap.py", str(video), "--audio-mode", "source-mix"],
    )

    recap_runner.main()

    assert seen[0].voice_ref is None
    assert "--voice-ref" not in recap_runtime._resume_argv(tmp_path, seen[0])


def test_source_full_rejects_ambiguous_unbound_narration_workdir(monkeypatch, tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    work.mkdir()
    (work / "narration.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(recap_runner, "_preflight_burn_subtitles", lambda _args: None)
    monkeypatch.setattr(
        sys, "argv",
        ["recap.py", str(video), "--work-dir", str(work), "--audio-mode", "source-mix"],
    )

    with pytest.raises(SystemExit, match="新的 --work-dir"):
        recap_runner.main()


def test_audio_binding_rejects_mode_switch_and_manifest_without_audio(tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    work.mkdir()
    recap_runtime._write_run_manifest(work, video, _args(audio_mode="narration"))

    assert recap_timeline._manifest_mismatches(
        work, video, _args(audio_mode="narration")
    ) == []
    assert any(
        "audio" in mismatch
        for mismatch in recap_timeline._manifest_mismatches(
            work, video, _args(audio_mode="source-mix")
        )
    )

    # A manifest lacking `audio` (older or hand-edited) is reported as a mismatch so the
    # user gets the "use a new --work-dir" message, not a traceback.
    manifest = json.loads((work / "recap_run_manifest.json").read_text(encoding="utf-8"))
    manifest.pop("audio")
    (work / "recap_run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert any(
        mismatch.startswith("audio:")
        for mismatch in recap_timeline._manifest_mismatches(work, video, _args(audio_mode="narration"))
    )


def test_multi_source_manifest_from_older_release_is_a_mismatch_not_a_crash(tmp_path):
    videos = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for video in videos:
        video.write_bytes(video.stem.encode())
    work = tmp_path / "work"
    work.mkdir()
    args = _args(edit_mode="cut")
    records = recap_runtime._build_multi_source_records(videos, args)
    recap_runtime._write_project_run_manifest(work, args, records)
    assert recap_timeline._multi_manifest_mismatches(work, args, records) == []

    # A manifest written before source_videos was dropped still resumes: the key is not compared.
    manifest = json.loads((work / "recap_run_manifest.json").read_text(encoding="utf-8"))
    assert "source_videos" not in manifest
    manifest["source_videos"] = [str(video) for video in videos]
    (work / "recap_run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert recap_timeline._multi_manifest_mismatches(work, args, records) == []

    # 0.5.0 recorded a content fingerprint where the manifest now keeps a file identity.
    manifest = json.loads((work / "recap_run_manifest.json").read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        source["source_video_fingerprint"] = "0" * 64
        del source["source_video_identity"]
    (work / "recap_run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SystemExit, match="新的 --work-dir"):
        recap_runner._reject_stale_multi_manifest(work, args, records)


@pytest.mark.parametrize(
    ("option", "extra"),
    [
        ("--tts-provider", ["--tts-provider", "auto"]),
        ("--mimo-tts-voice", ["--mimo-tts-voice", "voice-id"]),
        ("--voice-ref", ["--voice-ref", "voice.wav"]),
        ("--allow-partial-tts", ["--allow-partial-tts"]),
    ],
)
def test_source_mix_refuses_every_tts_option_before_any_stage(monkeypatch, capsys, tmp_path, option, extra):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    calls = []
    monkeypatch.setattr(recap_runner, "_run", lambda *args: calls.append(args))
    monkeypatch.setattr(recap_runner, "_preflight_burn_subtitles", lambda _args: None)
    monkeypatch.setattr(sys, "argv", ["recap.py", str(video), "--audio-mode", "source-mix", *extra])

    with pytest.raises(SystemExit) as exc:
        recap_runner.main()

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "cannot use TTS" in err and option in err
    assert calls == []


def _rewrite_manifest(work, change):
    path = work / "recap_run_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    change(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _single_run(tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    work.mkdir()
    recap_runtime._write_run_manifest(work, video, _args())
    return video, work


def test_same_path_replaced_video_is_a_mismatch(tmp_path):
    video, work = _single_run(tmp_path)
    video.write_bytes(b"another video")
    assert any(
        m.startswith("source_video_identity:")
        for m in recap_timeline._manifest_mismatches(work, video, _args())
    )


def test_moved_video_is_a_mismatch(tmp_path):
    video, work = _single_run(tmp_path)
    moved = tmp_path / "moved.mp4"
    video.replace(moved)
    assert any(
        m.startswith("source_video:")
        for m in recap_timeline._manifest_mismatches(work, moved, _args())
    )


def test_work_dir_with_the_removed_overview_on_names_it(tmp_path):
    video, work = _single_run(tmp_path)
    _rewrite_manifest(work, lambda m: m["settings"].update(mimo_video_overview=True))
    mismatches = recap_timeline._manifest_mismatches(work, video, _args())
    assert len(mismatches) == 1
    assert "--mimo-video-overview" in mismatches[0] and "v0.6.2" in mismatches[0]


def _multi_run(tmp_path):
    videos = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for video in videos:
        video.write_bytes(video.stem.encode())
    work = tmp_path / "work"
    work.mkdir()
    args = _args(edit_mode="cut")
    records = recap_runtime._build_multi_source_records(videos, args)
    recap_runtime._write_project_run_manifest(work, args, records)
    assert recap_timeline._multi_manifest_mismatches(work, args, records) == []
    return work, args, records


def test_multi_run_rejects_a_changed_setting_and_audio_mode(tmp_path):
    work, args, records = _multi_run(tmp_path)
    assert any(m.startswith("settings:") for m in recap_timeline._multi_manifest_mismatches(
        work, _args(edit_mode="cut", style="别的"), records))
    assert any(m.startswith("audio:") for m in recap_timeline._multi_manifest_mismatches(
        work, _args(edit_mode="cut", audio_mode="source-mix"), records))


def test_multi_run_rejects_a_single_source_manifest(tmp_path):
    work, args, records = _multi_run(tmp_path)
    _rewrite_manifest(work, lambda m: m.pop("mode"))
    assert recap_timeline._multi_manifest_mismatches(work, args, records)[0].startswith("mode:")


@pytest.mark.parametrize("key", ["source_id", "source_path"])
def test_multi_run_rejects_a_changed_source_record(tmp_path, key):
    work, args, records = _multi_run(tmp_path)
    _rewrite_manifest(work, lambda m: m["sources"][0].update({key: "other"}))
    assert any(
        m.startswith("sources:") for m in recap_timeline._multi_manifest_mismatches(work, args, records)
    )


def test_full_source_mix_resume_with_a_stale_manifest_never_renders(monkeypatch, tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    work.mkdir()
    recap_runtime._write_run_manifest(work, video, _args(audio_mode="source-mix"))
    video.write_bytes(b"another video")
    calls = []
    _finish_stubs(monkeypatch, work, calls)
    monkeypatch.setattr(sys, "argv", ["recap.py", str(video), "--work-dir", str(work),
                                      "--audio-mode", "source-mix"])

    with pytest.raises(SystemExit, match="新的 --work-dir"):
        recap_runner.main()

    assert calls == []


def test_index_tts_is_an_explicit_narration_provider_and_forwards(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys, "argv", ["recap.py", "input.mp4", "--tts-provider", "index-tts"]
    )

    _, parsed = recap_cli.parse_args()
    voiceover = recap_runner._voiceover_args(
        tmp_path, tmp_path / "narration.json", _args(tts_provider=parsed.tts_provider)
    )

    assert parsed.tts_provider == "index-tts"
    assert voiceover[voiceover.index("--tts-provider") + 1] == "index-tts"


@pytest.mark.parametrize(
    "extra",
    [
        ["--tts-provider", "index-tts", "--voice-ref", "voice.wav"],
        ["--tts-provider", "index-tts", "--mimo-tts-voice", "voice-id"],
    ],
)
def test_index_tts_incompatible_voice_options_fail_early(
    monkeypatch, tmp_path, extra
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    calls = []
    monkeypatch.setattr(recap_runner, "_run", lambda *args: calls.append(args))
    monkeypatch.setattr(recap_runner, "_preflight_burn_subtitles", lambda _args: None)
    monkeypatch.setattr(sys, "argv", ["recap.py", str(video), *extra])

    with pytest.raises(SystemExit):
        recap_runner.main()
    assert calls == []


@pytest.mark.parametrize(
    "flag, extra",
    [
        ("--mimo-video-overview", []),
        ("--consolidate-asr", []),
        ("--preserve-approved-text", []),
        ("--audio-stream-index", ["0"]),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_removed_flags_fail_at_the_parser_instead_of_being_ignored(tmp_path, capsys, flag, extra):
    with pytest.raises(SystemExit) as raised:
        recap_cli.parse_args([str(tmp_path / "in.mp4"), flag, *extra])
    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "unrecognized arguments" in err and flag in err


def test_multi_source_speech_rows_ignore_an_old_clean_transcript_left_behind(tmp_path):
    (tmp_path / "asr_result.json").write_text(
        json.dumps([{"start": 0.0, "end": 2.0, "text": "原始转写"}]), encoding="utf-8")
    (tmp_path / "asr_clean.json").write_text(
        json.dumps({"segments": [{"start": 0.0, "end": 2.0, "text": "清洗旧稿"}]}), encoding="utf-8")
    assert [row["text"] for row in recap_timeline._source_speech_rows(tmp_path)] == ["原始转写"]


@pytest.mark.parametrize(
    "section, key, value, resumes",
    [
        ("settings", "consolidate_asr", True, True),
        ("settings", "mimo_video_overview", True, False),
        ("audio", "selected_stream_index", 1, False),
    ],
)
def test_resume_of_a_manifest_recording_a_removed_feature(tmp_path, section, key, value, resumes):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"video")
    _, args = recap_cli.parse_args([str(video)])
    manifest = recap_runtime._run_manifest_payload(video, args)
    (tmp_path / recap_runtime.RUN_MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    assert recap_timeline._manifest_mismatches(tmp_path, video, args) == []

    manifest[section][key] = value
    (tmp_path / recap_runtime.RUN_MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")

    assert (recap_timeline._manifest_mismatches(tmp_path, video, args) == []) is resumes


def test_tts_provider_propagates_to_brief_across_cli_project_pass2_and_materials(
    monkeypatch, tmp_path
):
    from briefing.builder import build_agent_brief, _resolve_tts_provider_hint
    import materials as material_lib

    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    monkeypatch.setitem(recap_runner.project_binding.__dict__, "sync_production_reference", lambda *_: None)
    video = tmp_path / "ep1.mp4"
    video.write_bytes(b"video-ep1")

    # 1. Single-video full Pass 1 with explicit --tts-provider index-tts (before manifest exists)
    work_cli = tmp_path / "work_cli"
    seen_cli_args = []

    def fake_understand_cli(stage, script, *cli_args):
        seen_cli_args.append((stage, script, list(cli_args)))
        assert "--tts-provider" in cli_args
        idx = cli_args.index("--tts-provider")
        assert cli_args[idx + 1] == "index-tts"
        monkeypatch.setitem(
            __import__("lib").CONFIG, "_explicit_tts_provider", cli_args[idx + 1]
        )
        build_agent_brief(
            [{"scene_id": 0, "start": 0.0, "end": 8.0, "description": "门口对峙"}],
            [],
            [],
            8.0,
            work_cli,
        )
        monkeypatch.setitem(__import__("lib").CONFIG, "_explicit_tts_provider", None)

    monkeypatch.setattr(recap_runner, "_run", fake_understand_cli)
    monkeypatch.setattr(recap_runner, "_preflight_burn_subtitles", lambda _args: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["recap.py", str(video), "--work-dir", str(work_cli), "--tts-provider", "index-tts"],
    )
    recap_runner.main()
    brief_cli = (work_cli / "agent_narration_brief.md").read_text(encoding="utf-8")
    assert '"emotion"' not in brief_cli
    assert "当前 TTS 供应商为 `index-tts`" in brief_cli

    # 2. Project-bound index-tts voice (omitted from manifest['argv'] by _resume_argv)
    lib_dir = tmp_path / "lib"
    voice_dir = lib_dir / "resources" / "voice" / "idx-narrator"
    voice_dir.mkdir(parents=True)
    (lib_dir / "library.json").write_text(
        json.dumps({"schema": "video-recap.library.v1", "name": "测试库"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (voice_dir / "resource.json").write_text(
        json.dumps(
            {
                "schema": "video-recap.resource.v1",
                "id": "idx-narrator",
                "kind": "voice",
                "title": "自建旁白",
                "files": [],
                "voice": {"provider": "index-tts", "voice_id": "narrator_v1"},
                "origin": {"creator": "local", "url": ""},
                "license": {"status": "owned", "terms": "自建推理", "evidence": "local"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    proj_file = tmp_path / "recap_project.json"
    proj_file.write_text(
        json.dumps(
            {
                "schema": "video-recap.project.v1",
                "name": "idx-series",
                "library": str(lib_dir),
                "bindings": {"voice": "idx-narrator"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    work_proj = tmp_path / "work_proj"
    monkeypatch.setenv("INDEX_TTS_ENDPOINT", "http://127.0.0.1:8000/tts")
    seen_proj_args = []
    monkeypatch.setattr(
        recap_runner,
        "_run",
        lambda stage, script, *cli: seen_proj_args.append((stage, script, list(cli))),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["recap.py", str(video), "--work-dir", str(work_proj), "--project", str(proj_file)],
    )
    recap_runner.main()
    assert "--tts-provider" in seen_proj_args[0][2]
    proj_manifest = json.loads((work_proj / "recap_run_manifest.json").read_text(encoding="utf-8"))
    assert "--tts-provider" not in proj_manifest["argv"]
    assert proj_manifest["tts_provider"] == "index-tts"
    assert _resolve_tts_provider_hint(work_proj) == "index-tts"

    # 3. Material restore refreshes cached auto/MiMo brief when --tts-provider index-tts is used
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "agent_narration_brief.md").write_text('[{"emotion": "平静"}]', encoding="utf-8")
    (seed / "scenes.json").write_text("[]", encoding="utf-8")
    mat_dir = tmp_path / "materials"
    _, base_args = recap_cli.parse_args([str(video)])
    material_lib.save_material(
        mat_dir, seed, video, recap_runtime.file_identity(video), recap_runtime._analysis_settings(base_args)
    )
    work_mat = tmp_path / "work_mat"
    seen_mat_args = []
    monkeypatch.setattr(
        recap_runner,
        "_run",
        lambda stage, script, *cli: seen_mat_args.append((stage, script, list(cli))),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recap.py",
            str(video),
            "--work-dir",
            str(work_mat),
            "--material-library-dir",
            str(mat_dir),
            "--use-materials",
            "--tts-provider",
            "index-tts",
        ],
    )
    recap_runner.main()
    assert len(seen_mat_args) == 1
    assert "--brief-only" in seen_mat_args[0][2]
    assert "--tts-provider" in seen_mat_args[0][2]

    # 4. Multi-source cut Pass 2 adding --tts-provider index-tts omits emotion in output brief
    video2 = tmp_path / "ep2.mp4"
    video2.write_bytes(b"video-ep2-different-size")
    work_multi = tmp_path / "work_multi"
    work_multi.mkdir()
    _, m_args_pass1 = recap_cli.parse_args([str(video), str(video2), "--edit-mode", "cut"])
    records = recap_runtime._build_multi_source_records([video, video2], m_args_pass1)
    for rec in records:
        swd = work_multi / rec["source_work_dir"]
        swd.mkdir(parents=True)
        (swd / "asr_result.json").write_text("[]", encoding="utf-8")
        (swd / "speech_boundary_anchors.json").write_text('{"sentence_anchors":[]}', encoding="utf-8")
    recap_runtime._write_project_run_manifest(work_multi, m_args_pass1, records)
    (work_multi / "clip_plan.json").write_text(
        json.dumps(
            {"clips": [{"source_id": records[0]["source_id"], "start": 0.0, "end": 4.0, "reason": "开场"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_multi_cut(stage, script, *cli):
        if script == "cut.py":
            (work_multi / "edited_source.mp4").write_bytes(b"edited")
            (work_multi / "clip_plan_validated.json").write_text(
                json.dumps(
                    {
                        "clips": [
                            {
                                "source_id": records[0]["source_id"],
                                "source_start": 0.0,
                                "source_end": 4.0,
                                "output_start": 0.0,
                                "output_end": 4.0,
                                "reason": "开场",
                            }
                        ],
                        "qc": {
                            "status": "pass",
                            "target_duration_status": "not_requested",
                            "total_duration": 4.0,
                            "clip_count": 1,
                            "join_fade_ms": 30.0,
                            "output_geometry": {"width": 1280, "height": 720, "fps": 25.0},
                            "output_geometry_reason": "first_clip",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

    monkeypatch.setattr(recap_runner, "_run", fake_multi_cut)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recap.py",
            str(video),
            str(video2),
            "--edit-mode",
            "cut",
            "--work-dir",
            str(work_multi),
            "--tts-provider",
            "index-tts",
        ],
    )
    recap_runner.main()
    multi_brief = (work_multi / "agent_narration_brief.md").read_text(encoding="utf-8")
    assert '"emotion"' not in multi_brief
    assert "当前 TTS 供应商为 `index-tts`" in multi_brief
