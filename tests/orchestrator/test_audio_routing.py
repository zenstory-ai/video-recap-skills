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
    monkeypatch.setattr(recap_runner, "_write_final_qc_reports", lambda *_: {})
    monkeypatch.setattr(recap_runner, "_print_final_qc_pointer", lambda *_: None)
    monkeypatch.setattr(
        recap_runner, "run_narration_review",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("narration review ran")),
    )
    return fake_run


@pytest.mark.parametrize("audio_mode", ["source-mix", "adopted-packet-copy"])
def test_full_source_audio_routes_directly_to_assemble_without_tts(
    monkeypatch, tmp_path, audio_mode
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    work.mkdir()
    (work / "tts_meta.json").write_text("not-json", encoding="utf-8")
    overlays = work / "visual_overlays.json"
    overlays.write_text('{"author":"keep"}', encoding="utf-8")
    (work / "preflight_qc.json").write_text("stale-not-json", encoding="utf-8")
    (work / "narration_review.json").write_text("stale-not-read", encoding="utf-8")
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
    assert overlays.read_text(encoding="utf-8") == '{"author":"keep"}'
    manifest = json.loads((work / "recap_run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["audio"] == {"mode": audio_mode, "selected_stream_index": 0}
    stages = json.loads((work / "preflight_qc.json").read_text(encoding="utf-8"))
    policy = stages["metadata"]["stages"]["pre_assemble"]["metadata"]
    assert policy["tts"] == "not_applicable"
    assert policy["narration_review"] == "not_applicable"


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
    recap_runtime._write_project_run_manifest(work, videos, args, records)
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
         "--audio-mode", "adopted-packet-copy"],
    )

    recap_runner.main()

    output = capsys.readouterr().out
    assert "clip_plan.json" in output
    assert "--audio-mode adopted-packet-copy" in output
    assert not (work / "narration.json").exists()


def test_source_mix_rejects_explicit_subtitle_track_before_cut(monkeypatch, tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    work.mkdir()
    (work / "subtitle_track.json").write_text("{}", encoding="utf-8")
    (work / "clip_plan.json").write_text('{"clips":[]}', encoding="utf-8")
    calls = []
    monkeypatch.setattr(recap_runner, "_run", lambda *args: calls.append(args))
    monkeypatch.setattr(recap_runner, "_preflight_burn_subtitles", lambda _args: None)
    monkeypatch.setattr(
        sys, "argv",
        ["recap.py", str(video), "--work-dir", str(work), "--edit-mode", "cut",
         "--audio-mode", "source-mix"],
    )

    with pytest.raises(SystemExit, match="subtitle_track"):
        recap_runner.main()
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


def test_full_adopted_audio_forwards_selected_stream(monkeypatch, tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    calls = []
    _finish_stubs(monkeypatch, work, calls)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recap.py", str(video), "--work-dir", str(work),
            "--audio-mode", "adopted-packet-copy", "--audio-stream-index", "1",
        ],
    )

    recap_runner.main()

    assemble = calls[0][2]
    assert assemble[assemble.index("--audio-stream-index") + 1] == "1"
    manifest = json.loads((work / "recap_run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["audio"]["selected_stream_index"] == 1


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


def test_audio_binding_rejects_mode_switch_but_legacy_manifest_defaults_to_narration(
    tmp_path
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    work.mkdir()
    recap_runtime._write_run_manifest(work, video, _args(audio_mode="narration"))
    manifest = json.loads((work / "recap_run_manifest.json").read_text(encoding="utf-8"))
    manifest.pop("audio")
    (work / "recap_run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert recap_timeline._manifest_mismatches(
        work, video, _args(audio_mode="narration")
    ) == []
    assert any(
        "audio" in mismatch
        for mismatch in recap_timeline._manifest_mismatches(
            work, video, _args(audio_mode="source-mix")
        )
    )


@pytest.mark.parametrize(
    "extra",
    [
        ["--edit-mode", "dub", "--audio-mode", "source-mix"],
        ["--audio-mode", "source-mix", "--tts-provider", "auto"],
        ["--audio-mode", "source-mix", "--allow-partial-tts"],
        ["--audio-mode", "source-mix", "--no-review-narration"],
        ["--audio-mode", "source-mix", "--require-narration-review"],
        ["--audio-mode", "source-mix", "--preserve-approved-text"],
        ["--edit-mode", "cut", "--audio-mode", "source-mix", "--audio-stream-index", "1"],
    ],
)
def test_unsupported_audio_combinations_fail_before_pipeline(monkeypatch, tmp_path, extra):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(recap_runner, "_preflight_burn_subtitles", lambda _args: None)
    monkeypatch.setattr(sys, "argv", ["recap.py", str(video), *extra])

    with pytest.raises(SystemExit):
        recap_runner.main()


def test_narration_preserve_approved_text_forwards_and_continues(monkeypatch, tmp_path):
    args = _args(preserve_approved_text=True)
    voiceover = recap_runner._voiceover_args(tmp_path, tmp_path / "narration.json", args)
    continuation = recap_timeline._continuation_command(tmp_path / "in.mp4", tmp_path, args)

    assert "--preserve-approved-text" in voiceover
    assert "--preserve-approved-text" in continuation


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
        ["--edit-mode", "dub", "--tts-provider", "index-tts"],
    ],
)
def test_index_tts_incompatible_voice_and_dub_options_fail_early(
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
