import json
import os
import re
import sys
from pathlib import Path

import pytest

import doctor
import materials as material_lib
from lib import file_identity
import recap_cli
import recap_runner as recap
import recap_runtime
import recap_timeline
from _helpers import (
    manifest_args,
    multi_cut_clip,
    seed_cut_work,
    seed_full_work,
    seed_multi_work,
    stub_child_run,
    write_assemble_output,
    write_cut_output,
)


def _argv(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["recap_runner.py", *map(str, args)])


def _call_args(calls, skill, script):
    return next(call[2] for call in calls if call[:2] == (skill, script))


_OVER_BUDGET_TEXT = (
    "少年停在了门外。他终于明白同伴为什么坚持等候，"
    "也决定先把受伤的人送回家再去寻找失踪的同伴。"
)


@pytest.mark.parametrize(
    "end, reaches_voiceover", [(30, True), (3, False)], ids=["fits", "over_budget"]
)
def test_recap_full_validator_never_rewrites_text_before_voiceover(
    monkeypatch, tmp_path, end, reaches_voiceover
):
    """Default full mode keeps the agent's text: it fits and reaches TTS unchanged, or it
    fails lint and goes back to the author."""
    approved = [
        {"start": 0, "end": end, "narration": _OVER_BUDGET_TEXT, "overlaps_speech": False}
    ]
    video, work = seed_full_work(tmp_path, approved)
    raw = (work / "narration.json").read_text(encoding="utf-8")

    class VoiceoverReached(Exception):
        pass

    def run_through_validation(skill, script, *cli_args):
        if (skill, script) == ("script", "validate.py"):
            return recap_runtime._run(skill, script, *cli_args)
        if (skill, script) == ("voiceover", "voiceover.py"):
            consumed = json.loads(
                (work / "narration.json").read_text(encoding="utf-8")
            )
            assert consumed == approved
            raise VoiceoverReached
        raise AssertionError(f"unexpected child before voiceover: {skill}/{script}")

    monkeypatch.setattr("recap_runner._run", run_through_validation)
    _argv(
        monkeypatch,
        video,
        "--work-dir",
        work,
    )

    if reaches_voiceover:
        with pytest.raises(VoiceoverReached):
            recap.main()
        return
    with pytest.raises(SystemExit, match=r"script/validate\.py 失败 \(exit 1\)"):
        recap.main()
    assert (work / "narration.json").read_text(encoding="utf-8") == raw
    lint = json.loads((work / "narration_lint.json").read_text(encoding="utf-8"))
    assert [error["code"] for error in lint["errors"]] == ["over_budget"]


def _write_stale_lint_pass(work):
    (work / "narration_lint.json").write_text(
        json.dumps(
            {
                "ok": True,
                "error_count": 0,
                "warning_count": 0,
                "stale": True,
                "errors": [],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )


def _assert_validation_replaced_stale_pass(work, expected_code):
    lint = json.loads((work / "narration_lint.json").read_text(encoding="utf-8"))
    assert lint["ok"] is False
    assert lint["error_count"] == len(lint["errors"]) >= 1
    assert lint["errors"][0]["code"] == expected_code
    assert "stale" not in lint


def _real_validation_then_stop(calls, work=None, clips=None):
    def run(skill, script, *cli_args):
        calls.append((skill, script))
        if (skill, script) == ("cut", "cut.py"):
            write_cut_output(work, clips)
            return None
        if (skill, script) == ("script", "validate.py"):
            return recap_runtime._run(skill, script, *cli_args)
        raise AssertionError(
            f"downstream stage ran after failed validation: {skill}/{script}"
        )

    return run


_RAW_APPROVED = json.dumps(
    [{"start": 0, "end": 2, "narration": "这段合法批准稿完整保留。"}],
    ensure_ascii=False,
    separators=(",", ":"),
)


def test_recap_full_validate_failure_stops_before_tts_and_assemble(
    monkeypatch, tmp_path
):
    video, work = seed_full_work(
        tmp_path,
        [{"start": 0, "end": 1, "narration": 7}],
    )
    _write_stale_lint_pass(work)
    calls = []
    monkeypatch.setattr("recap_runner._run", _real_validation_then_stop(calls))
    _argv(
        monkeypatch,
        video,
        "--work-dir",
        work,
    )

    with pytest.raises(SystemExit, match=r"script/validate\.py 失败 \(exit 1\)"):
        recap.main()

    assert calls == [("script", "validate.py")]
    _assert_validation_replaced_stale_pass(work, "invalid_narration")


def test_recap_single_cut_validate_failure_stops_before_tts_and_assemble(
    monkeypatch, tmp_path
):
    video, work = seed_cut_work(
        tmp_path,
        clips=[{"start": 0, "end": 2}],
        narration=None,
    )
    narration_path = work / "narration.json"
    narration_path.write_text(_RAW_APPROVED, encoding="utf-8")
    _write_stale_lint_pass(work)
    calls = []
    monkeypatch.setattr("recap_runner._run", _real_validation_then_stop(calls, work))
    monkeypatch.setattr("recap_runner._read_video_duration_or_raise", lambda path: 1.0)
    _argv(
        monkeypatch,
        video,
        "--work-dir",
        work,
        "--edit-mode",
        "cut",
    )

    with pytest.raises(SystemExit, match=r"script/validate\.py 失败 \(exit 1\)"):
        recap.main()

    assert calls == [("cut", "cut.py"), ("script", "validate.py")]
    assert narration_path.read_text(encoding="utf-8") == _RAW_APPROVED
    _assert_validation_replaced_stale_pass(work, "invalid_output_timeline")


def test_recap_multi_cut_validate_failure_stops_before_tts_and_assemble(
    monkeypatch, tmp_path
):
    args = manifest_args(edit_mode="cut")
    videos, work, records = seed_multi_work(tmp_path, args)
    narration_path = work / "narration.json"
    narration_path.write_text(_RAW_APPROVED, encoding="utf-8")
    _write_stale_lint_pass(work)
    calls = []
    monkeypatch.setattr(
        "recap_runner._run",
        _real_validation_then_stop(calls, work, [multi_cut_clip(records, videos)]),
    )
    monkeypatch.setattr("recap_runner._read_video_duration_or_raise", lambda path: 1.0)

    with pytest.raises(SystemExit, match=r"script/validate\.py 失败 \(exit 1\)"):
        recap._run_multi_cut(videos, work, args)

    assert calls == [("cut", "cut.py"), ("script", "validate.py")]
    assert narration_path.read_text(encoding="utf-8") == _RAW_APPROVED
    _assert_validation_replaced_stale_pass(work, "invalid_output_timeline")


def _tools_present(monkeypatch, filters=("subtitles", "ass", "drawtext")):
    monkeypatch.setattr("doctor.ffmpeg_filters", lambda: set(filters))
    monkeypatch.setattr(
        "doctor._command_path",
        lambda name: f"/usr/bin/{name}" if name in ("ffmpeg", "ffprobe") else None,
    )


def _all_mimo_keys(monkeypatch, value="tp-test-key"):
    for k in ("api_key", "mimo_asr_api_key", "mimo_tts_api_key"):
        monkeypatch.setitem(doctor.CONFIG, k, value)


def test_doctor_ok_when_tools_and_mimo_key_present(monkeypatch):
    _tools_present(monkeypatch)
    _all_mimo_keys(monkeypatch)

    report = doctor.build_report()

    assert report["ok"] is True
    assert report["failures"] == []
    assert set(report) == {"ok", "repo_root", "checks", "failures", "warnings"}
    assert report["warnings"] == []


def test_doctor_fails_without_mimo_key(monkeypatch):
    _tools_present(monkeypatch)
    _all_mimo_keys(monkeypatch, "")

    report = doctor.build_report()

    assert report["ok"] is False
    assert any("MIMO_API_KEY" in f for f in report["failures"])


def test_doctor_accepts_fish_audio_as_the_selected_tts_provider(monkeypatch):
    _tools_present(monkeypatch)
    _all_mimo_keys(monkeypatch, "tp-x")
    monkeypatch.setitem(doctor.CONFIG, "mimo_tts_api_key", "")
    monkeypatch.setitem(doctor.CONFIG, "tts_provider", "fish-audio")
    monkeypatch.setitem(doctor.CONFIG, "fish_api_key", "sk-fish-test")

    report = doctor.build_report()

    assert report["ok"] is True
    assert report["checks"]["tts"]["provider"] == "fish-audio"
    assert report["warnings"] == []


def test_doctor_warns_when_selected_fish_audio_has_no_key(monkeypatch):
    _tools_present(monkeypatch)
    _all_mimo_keys(monkeypatch, "tp-x")
    monkeypatch.setitem(doctor.CONFIG, "fish_api_key", "")

    report = doctor.build_report(tts_provider="fish-audio")

    assert report["ok"] is True
    assert report["warnings"] == [
        "TTS provider fish-audio not configured: set FISH_API_KEY before voiceover"
    ]


def test_doctor_provider_override_does_not_mutate_global_config(monkeypatch):
    _tools_present(monkeypatch)
    monkeypatch.setitem(doctor.CONFIG, "tts_provider", "mimo-tts")
    monkeypatch.setitem(doctor.CONFIG, "fish_api_key", "sk-fish-test")

    report = doctor.build_report(tts_provider="fish-audio")

    assert report["checks"]["tts"]["provider"] == "fish-audio"
    assert doctor.CONFIG["tts_provider"] == "mimo-tts"


def test_doctor_rejects_invalid_environment_provider(monkeypatch):
    _tools_present(monkeypatch)
    monkeypatch.setitem(doctor.CONFIG, "tts_provider", "fish")

    report = doctor.build_report()

    assert report["ok"] is False
    assert any("TTS_PROVIDER" in failure for failure in report["failures"])


def test_doctor_reports_default_fish_voice_source(monkeypatch, capsys):
    _tools_present(monkeypatch)
    monkeypatch.setitem(doctor.CONFIG, "tts_provider", "fish-audio")
    monkeypatch.setitem(doctor.CONFIG, "fish_api_key", "sk-fish-test")
    monkeypatch.setitem(doctor.CONFIG, "fish_tts_reference_id", "voice-id")
    monkeypatch.setitem(doctor.CONFIG, "fish_tts_reference_id_source", "default")

    doctor._print_human(doctor.build_report())

    assert "TTS voice reference ID: set (source: default)" in capsys.readouterr().out


def test_doctor_missing_ffmpeg_is_failure(monkeypatch):
    monkeypatch.setattr("doctor.ffmpeg_filters", lambda: set())
    monkeypatch.setattr("doctor._command_path", lambda name: None)
    monkeypatch.setitem(doctor.CONFIG, "api_key", "tp-x")

    report = doctor.build_report()

    assert report["ok"] is False
    assert any("ffmpeg" in f for f in report["failures"])


def test_doctor_warns_when_asr_unconfigured_but_key_present(monkeypatch):
    """api_key powers VLM/TTS; an empty ASR key is only a warning (use --skip-asr)."""
    _tools_present(monkeypatch)
    _all_mimo_keys(monkeypatch, "tp-x")
    monkeypatch.setitem(doctor.CONFIG, "mimo_asr_api_key", "")

    report = doctor.build_report()

    assert report["ok"] is True
    assert [w for w in report["warnings"] if "ASR not configured" in w] == [
        "ASR not configured (MIMO_API_KEY); pipeline can run with --skip-asr"
    ]


def test_doctor_warns_when_subtitle_burn_degraded(monkeypatch):
    monkeypatch.delenv("BURN_SUBTITLES", raising=False)
    _tools_present(monkeypatch, filters=("ass", "drawtext"))
    _all_mimo_keys(monkeypatch)

    report = doctor.build_report()

    assert report["ok"] is True
    assert report["checks"]["system_tools"]["subtitle_delivery"] == "sidecar_srt"
    assert len(report["warnings"]) == 1
    assert "subtitles/libass" in report["warnings"][0]
    assert ".srt sidecar" in report["warnings"][0]
    assert "--burn-subtitles fails" in report["warnings"][0]


def test_doctor_warns_explicit_burn_env_will_stop_without_libass(monkeypatch):
    monkeypatch.setenv("BURN_SUBTITLES", "1")
    _tools_present(monkeypatch, filters=("ass", "drawtext"))
    _all_mimo_keys(monkeypatch)

    report = doctor.build_report()

    assert report["checks"]["system_tools"]["subtitle_delivery"] == "fails_explicit_burn"
    assert len(report["warnings"]) == 1
    assert "stop at preflight" in report["warnings"][0]
    assert "--no-burn-subtitles" in report["warnings"][0]


def test_doctor_warns_when_drawtext_missing(monkeypatch):
    monkeypatch.delenv("BURN_SUBTITLES", raising=False)
    _tools_present(monkeypatch, filters=("subtitles", "ass"))
    _all_mimo_keys(monkeypatch)

    report = doctor.build_report()

    tools = report["checks"]["system_tools"]
    assert tools["subtitle_delivery"] == "burned"
    assert tools["ffmpeg_drawtext_filter"] is False
    assert tools["visual_overlays_ready"] is False
    assert len(report["warnings"]) == 1
    assert "drawtext" in report["warnings"][0]
    assert "before TTS" in report["warnings"][0]


def test_doctor_warns_when_tts_override_missing(monkeypatch):
    _tools_present(monkeypatch)
    monkeypatch.setitem(doctor.CONFIG, "api_key", "tp-x")
    monkeypatch.setitem(doctor.CONFIG, "mimo_asr_api_key", "tp-x")
    monkeypatch.setitem(doctor.CONFIG, "mimo_tts_api_key", "")

    report = doctor.build_report()

    assert report["ok"] is True
    assert report["warnings"] == [
        "TTS provider mimo-tts not configured: set MIMO_TTS_API_KEY or MIMO_API_KEY before voiceover",
    ]


def test_doctor_human_output_prints_warnings(monkeypatch, capsys):
    _tools_present(monkeypatch)
    _all_mimo_keys(monkeypatch)
    monkeypatch.setitem(doctor.CONFIG, "mimo_tts_api_key", "")

    doctor._print_human(doctor.build_report())
    out = capsys.readouterr().out

    assert "[capability menu]" not in out
    assert "Warnings:\n- TTS provider mimo-tts not configured" in out
    assert "Status: OK" in out


def test_recap_full_mode_passes_explicit_narration_json(monkeypatch, tmp_path):
    """Full mode: validate -> voiceover on work_dir/narration.json, no phase ledger."""
    video, work = seed_full_work(tmp_path)
    calls = []
    monkeypatch.setattr(
        "recap_runner._run", stub_child_run(work, tmp_path / "recap_video.mp4", calls)
    )
    _argv(monkeypatch, video, "--work-dir", work)

    recap.main()

    args = _call_args(calls, "voiceover", "voiceover.py")
    assert args[args.index("--narration") + 1] == str(work / "narration.json")
    order = [script for _, script, _ in calls]
    assert order.index("validate.py") < order.index("voiceover.py")
    assert not (work / "recap_phase.json").exists()


def test_recap_cut_mode_voiceover_uses_output_time_narration(monkeypatch, tmp_path):
    """Two-pass cut: narration is authored in OUTPUT time, so voiceover gets narration.json
    directly and assemble muxes onto edited_source.mp4."""
    video, work = seed_cut_work(
        tmp_path,
        narration=[{"start": 2, "end": 5, "narration": "output。"}],
    )
    calls = []
    monkeypatch.setattr(
        "recap_runner._run", stub_child_run(work, tmp_path / "recap_video.mp4", calls)
    )
    monkeypatch.setattr("recap_runner._read_video_duration_or_raise", lambda path: 10.0)
    _argv(
        monkeypatch,
        video,
        "--work-dir",
        work,
        "--edit-mode",
        "cut",
    )

    recap.main()

    vo = _call_args(calls, "voiceover", "voiceover.py")
    assert vo[vo.index("--narration") + 1] == str(work / "narration.json")
    assert "--narration" not in _call_args(calls, "cut", "cut.py")
    assert _call_args(calls, "assemble", "assemble.py")[0] == str(
        work / "edited_source.mp4"
    )
    validate_args = _call_args(calls, "script", "validate.py")
    assert validate_args[validate_args.index("--output-duration") + 1] == "10.000"
    order = [script for _, script, _ in calls]
    assert order.index("validate.py") < order.index("voiceover.py")


def test_recap_phase_b_rejects_work_dir_from_different_source(monkeypatch, tmp_path):
    """Phase B must not apply an existing narration.json to a different input video."""
    _, work = seed_full_work(tmp_path, [{"start": 0, "end": 1, "narration": "old。"}])
    new_video = tmp_path / "new.mp4"
    new_video.write_bytes(b"new-video")
    monkeypatch.setattr(
        "recap_runner._run",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("must fail before stages run")
        ),
    )
    _argv(monkeypatch, new_video, "--work-dir", work)

    with pytest.raises(SystemExit, match="work_dir 与当前 recap 输入不匹配"):
        recap.main()


@pytest.mark.parametrize(
    "name, value, flag", [("EDIT_MODE", "cut", "--edit-mode"), ("TARGET_DURATION", "10m", "--target-duration")]
)
def test_recap_refuses_removed_edit_env(monkeypatch, tmp_path, capsys, name, value, flag):
    """An unread EDIT_MODE=cut would silently run full, so the removed names fail for one release."""
    work = tmp_path / "must-not-exist"
    monkeypatch.setenv(name, value)
    _argv(monkeypatch, tmp_path / "input.mp4", "--work-dir", work)

    with pytest.raises(SystemExit) as exc:
        recap.main()

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert f"环境变量 {name} 已移除" in err and flag in err
    assert not work.exists()


@pytest.mark.parametrize("value, refused", [("1", True), ("0", False)])
def test_recap_refuses_only_a_set_narration_review_gate(monkeypatch, tmp_path, capsys, value, refused):
    """=1 asked for a gate that no longer exists, so silence would ship unreviewed narration;
    =0 never had a gate, so ignoring it changes nothing."""
    monkeypatch.setenv("REQUIRE_NARRATION_REVIEW", value)

    if refused:
        with pytest.raises(SystemExit) as exc:
            recap_cli.parse_args([tmp_path / "input.mp4"])
        assert exc.value.code == 2
        assert "环境变量 REQUIRE_NARRATION_REVIEW 已移除" in capsys.readouterr().err
    else:
        assert recap_cli.parse_args([tmp_path / "input.mp4"])[1].video


def test_recap_completion_prints_manifest_final_output(monkeypatch, tmp_path, capsys):
    """If assemble avoids a basename collision, recap should report that true path."""
    video, work = seed_full_work(tmp_path)
    collision_safe = tmp_path / "recap_video_abcd1234ef.mp4"
    monkeypatch.setattr("recap_runner._run", stub_child_run(work, collision_safe))
    _argv(monkeypatch, video, "--work-dir", work)

    recap.main()

    assert str(collision_safe) in capsys.readouterr().out


def test_recap_forwards_tts_provider_and_allow_partial_to_voiceover(monkeypatch, tmp_path):
    video, work = seed_full_work(tmp_path)
    calls = []
    monkeypatch.setattr(
        "recap_runner._run", stub_child_run(work, tmp_path / "recap_video.mp4", calls)
    )
    _argv(
        monkeypatch,
        video,
        "--work-dir",
        work,
        "--allow-partial-tts",
        "--tts-provider",
        "fish-audio",
    )

    recap.main()

    voiceover = _call_args(calls, "voiceover", "voiceover.py")
    assert "--allow-partial-tts" in voiceover
    assert voiceover[voiceover.index("--tts-provider") + 1] == "fish-audio"


def test_recap_cut_duration_read_failure_stops_before_tts(monkeypatch, tmp_path):
    video, work = seed_cut_work(
        tmp_path, narration=[{"start": 1, "end": 3, "narration": "解说。"}]
    )

    def fake_run(skill, script, *cli_args):
        if script == "cut.py":
            write_cut_output(work)
        if script in ("validate.py", "voiceover.py", "assemble.py"):
            raise AssertionError(f"{script} must not run when duration cannot be read")

    monkeypatch.setattr("recap_runner._run", fake_run)
    monkeypatch.setattr(
        "recap_runner._read_video_duration_or_raise",
        lambda path: (_ for _ in ()).throw(SystemExit("无法读取成片时长")),
    )
    _argv(monkeypatch, video, "--work-dir", work, "--edit-mode", "cut")

    with pytest.raises(SystemExit, match="无法读取成片时长"):
        recap.main()


# The settings every existing run manifest and material records. Spelled out, not derived,
# so a removed CLI flag cannot quietly change them and make saved materials miss.
_RECORDED_SETTINGS = {
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


@pytest.mark.parametrize(
    "flag", ["--review-narration", "--no-review-narration", "--require-narration-review"]
)
def test_removed_review_flags_stop_an_old_resume_command(monkeypatch, tmp_path, capsys, flag):
    video, work = seed_full_work(tmp_path)
    monkeypatch.setattr("recap_runner._run", _refuse_every_child)
    _argv(monkeypatch, video, "--work-dir", work, flag)

    with pytest.raises(SystemExit) as exc:
        recap.main()

    assert exc.value.code == 2
    assert f"unrecognized arguments: {flag}" in capsys.readouterr().err


def test_old_work_dir_resumes_and_leftover_review_files_have_no_reader(monkeypatch, tmp_path):
    """Old review output cannot affect a run: corrupt copies stay untouched and unread."""
    video, work = seed_full_work(tmp_path)
    manifest = json.loads((work / recap_runtime.RUN_MANIFEST).read_text(encoding="utf-8"))
    manifest["settings"] = dict(_RECORDED_SETTINGS)
    (work / recap_runtime.RUN_MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    leftovers = ("narration_review.json", "narration_review.md", "narration_review_override.md")
    for name in leftovers:
        (work / name).write_text("{ not json", encoding="utf-8")
    calls = []
    monkeypatch.setenv("REVIEW_NARRATION", "1")
    monkeypatch.setenv("REQUIRE_NARRATION_REVIEW", "0")
    monkeypatch.setattr(
        "recap_runner._run", stub_child_run(work, tmp_path / "recap_video.mp4", calls)
    )
    _argv(monkeypatch, video, "--work-dir", work)

    recap.main()

    assert [script for _, script, _ in calls] == ["validate.py", "voiceover.py", "assemble.py"]
    assert [(work / name).read_text(encoding="utf-8") for name in leftovers] == ["{ not json"] * 3


def test_material_saved_with_the_recorded_settings_still_restores(monkeypatch, tmp_path):
    video = tmp_path / "solo.mp4"
    video.write_bytes(b"solo source")
    assert {
        **recap_runtime._analysis_settings(recap_cli.parse_args([video])[1]),
        "consolidate": True,
    } == _RECORDED_SETTINGS

    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "scenes.json").write_text("[]", encoding="utf-8")
    (seed / "agent_narration_brief.md").write_text("# saved brief", encoding="utf-8")
    lib = tmp_path / "materials"
    material_lib.save_material(lib, seed, video, file_identity(video), _RECORDED_SETTINGS)
    work = tmp_path / "work"
    monkeypatch.setattr("recap_runner._run", _refuse_every_child)
    _argv(monkeypatch, video, "--work-dir", work, "--material-library-dir", lib, "--use-materials")

    recap.main()

    assert (work / "agent_narration_brief.md").read_text(encoding="utf-8") == "# saved brief"


def test_recap_cut_two_pass_renders_then_pauses_for_output_narration(
    monkeypatch, tmp_path
):
    """Cut mode is two-pass: with clip_plan but no narration, recap renders the cut (forwarding
    the cut flags) and pauses for OUTPUT-time narration instead of running voiceover/assemble."""
    video, work = seed_cut_work(
        tmp_path, narration=None, rendered=False, target_duration="10m"
    )
    (work / "agent_narration_brief.md").write_text("# brief\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr("recap_runner._run", stub_child_run(work, calls=calls))
    _argv(
        monkeypatch,
        video,
        "--work-dir",
        work,
        "--edit-mode",
        "cut",
        "--target-duration",
        "10m",
    )

    recap.main()  # PASS 2: render the cut, then pause (narration.json absent)

    cut_calls = [c for c in calls if c[:2] == ("cut", "cut.py")]
    assert cut_calls and all("--narration" not in c[2] for c in cut_calls)
    assert all("--normalize-only" not in c[2] for c in cut_calls)
    assert "--target-duration" in cut_calls[0][2]
    assert not any(c[1] in ("voiceover.py", "assemble.py") for c in calls)
    assert recap_timeline._read_phase_ledger(work) == {
        "clip_plan_identity": file_identity(work / "clip_plan.json")
    }


def _refuse_every_child(skill, script, *cli_args):
    raise AssertionError(f"{script} ran despite stale narration")


def test_recap_cut_rejects_stale_narration_before_recutting(monkeypatch, tmp_path):
    """A narration written for a previous clip_plan aborts the final run before cut.py
    re-renders anything, so it never reaches TTS and no re-cut is wasted on it."""
    video, work = seed_cut_work(
        tmp_path,
        narration=[{"start": 1, "end": 3, "narration": "解说。"}],
        rendered=False,
    )
    recap_timeline._write_phase_ledger(work, {"size": 0, "mtime_ns": 0})
    monkeypatch.setattr("recap_runner._run", _refuse_every_child)
    _argv(monkeypatch, video, "--work-dir", work, "--edit-mode", "cut")

    with pytest.raises(SystemExit, match="clip_plan.json 已改变"):
        recap.main()


def test_recap_cut_resume_rejects_stale_manifest_before_recutting(monkeypatch, tmp_path):
    """An existing clip_plan.json from another source must not reach cut.py."""
    _, work = seed_cut_work(tmp_path)
    new_video = tmp_path / "new.mp4"
    new_video.write_bytes(b"new-video")
    monkeypatch.setattr("recap_runner._run", _refuse_every_child)
    _argv(monkeypatch, new_video, "--work-dir", work, "--edit-mode", "cut")

    with pytest.raises(SystemExit, match="work_dir 与当前 recap 输入不匹配"):
        recap.main()


def test_recap_source_mix_pass1_rejects_stale_manifest_before_understanding(
    monkeypatch, tmp_path
):
    """Source-mix writes its manifest before pass 1, so a rerun can already be stale there."""
    _, work = seed_full_work(tmp_path, None, edit_mode="cut", audio_mode="source-mix")
    new_video = tmp_path / "new.mp4"
    new_video.write_bytes(b"new-video")
    monkeypatch.setattr("recap_runner._run", _refuse_every_child)
    _argv(monkeypatch, new_video, "--work-dir", work, "--edit-mode", "cut",
          "--audio-mode", "source-mix")

    with pytest.raises(SystemExit, match="work_dir 与当前 recap 输入不匹配"):
        recap.main()


def test_recap_multi_cut_rejects_stale_narration_before_recutting(monkeypatch, tmp_path):
    videos, work, _ = seed_multi_work(
        tmp_path,
        manifest_args(edit_mode="cut"),
        narration=[{"start": 0, "end": 1, "narration": "解说。"}],
    )
    recap_timeline._write_phase_ledger(work, {"size": 0, "mtime_ns": 0})
    monkeypatch.setattr("recap_runner._run", _refuse_every_child)
    _argv(monkeypatch, *videos, "--work-dir", work, "--edit-mode", "cut")

    with pytest.raises(SystemExit, match="clip_plan.json 已改变"):
        recap.main()


def test_recap_rejects_multi_video_non_cut(monkeypatch, tmp_path):
    v1 = tmp_path / "a.mp4"
    v2 = tmp_path / "b.mp4"
    v1.write_bytes(b"a")
    v2.write_bytes(b"b")
    monkeypatch.setattr(
        "recap_runner._run",
        lambda *args: (_ for _ in ()).throw(AssertionError("must fail first")),
    )
    _argv(monkeypatch, v1, v2, "--edit-mode", "full")

    with pytest.raises(SystemExit, match="多视频输入当前 MVP 只支持 --edit-mode cut"):
        recap.main()


def test_probe_display_height_accounts_for_rotation_and_sar(monkeypatch):
    payload = {
        "streams": [
            {
                "width": 320,
                "height": 180,
                "sample_aspect_ratio": "2:1",
                "side_data_list": [{"rotation": 90}],
            }
        ]
    }
    monkeypatch.setattr(
        recap_runtime.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Result", (), {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""}
        )(),
    )

    assert recap_runtime._probe_display_size_or_raise("rotated.mp4")[1] == 640
    with pytest.raises(SystemExit, match="near-square pixels"):
        recap_runtime._probe_display_size_or_raise(
            "rotated.mp4", require_near_square_pixels=True
        )


@pytest.mark.parametrize(
    ("sar", "rotation", "size"),
    [
        ("64:63", 0, (1950, 1080)),  # near-square: accepted, width follows the SAR
        ("63:64", 90, (1080, 1890)),  # rotated near-square: the stored width becomes rows
        ("0:1", 0, (1920, 1080)),  # unspecified SAR is square, not a 1px-wide canvas
        ("N/A", 0, (1920, 1080)),
    ],
)
def test_probe_display_size_accepts_near_square_and_unspecified_sar(
    monkeypatch, sar, rotation, size
):
    stream = {"width": 1920, "height": 1080, "sample_aspect_ratio": sar}
    if rotation:
        stream["side_data_list"] = [{"rotation": rotation}]
    monkeypatch.setattr(
        recap_runtime.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Result", (), {"returncode": 0, "stdout": json.dumps({"streams": [stream]}),
                           "stderr": ""}
        )(),
    )

    assert recap_runtime._probe_display_size_or_raise(
        "near.mp4", require_near_square_pixels=True
    ) == size


def test_recap_rejects_global_subtitle_band_for_multi_source_cut(monkeypatch, tmp_path, capsys):
    videos = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for video in videos:
        video.write_bytes(b"source")
    band = ["--subtitle-y-top", "100", "--subtitle-y-bot", "130"]
    _argv(monkeypatch, *videos, "--edit-mode", "cut", *band)

    with pytest.raises(SystemExit) as exc_info:
        recap.main()
    assert exc_info.value.code == 2
    assert "多视频 cut 暂不支持" in capsys.readouterr().err


def test_recap_subtitle_band_does_not_set_mask_env(
    monkeypatch, tmp_path
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"source")
    work = tmp_path / "work"
    monkeypatch.delenv("MASK_SOURCE_SUBTITLES", raising=False)
    monkeypatch.setattr(
        recap, "_probe_display_size_or_raise", lambda *args, **kwargs: (1280, 720)
    )
    monkeypatch.setattr(recap, "_preflight_burn_subtitles", lambda args: None)
    monkeypatch.setattr(
        recap,
        "_run_or_restore_understanding",
        lambda *args, **kwargs: (work / "agent_narration_brief.md").write_text(
            "# brief\n", encoding="utf-8"
        ),
    )
    _argv(
        monkeypatch,
        video,
        "--work-dir",
        work,
        "--subtitle-y-top",
        "610",
        "--subtitle-y-bot",
        "660",
    )

    recap.main()

    assert "MASK_SOURCE_SUBTITLES" not in os.environ


def test_recap_fails_fast_and_absolutizes_voice_reference(
    monkeypatch, tmp_path, capsys
):
    video, work = seed_full_work(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MIMO_TTS_VOICE", raising=False)
    calls = []

    class VoiceoverReached(Exception):
        pass

    def voiceover(cli):
        raise VoiceoverReached

    monkeypatch.setattr(
        "recap_runner._run", stub_child_run(work, calls=calls, voiceover=voiceover)
    )
    argv = [video, "--work-dir", work, "--voice-ref", "voice.wav"]
    _argv(monkeypatch, *argv)

    with pytest.raises(SystemExit) as exc_info:
        recap.main()
    assert exc_info.value.code == 2
    assert "reference audio does not exist" in capsys.readouterr().err
    assert calls == []

    (tmp_path / "voice.wav").write_bytes(b"reference")
    with pytest.raises(VoiceoverReached):
        recap.main()
    voiceover_args = _call_args(calls, "voiceover", "voiceover.py")
    forwarded = voiceover_args[voiceover_args.index("--voice-ref") + 1]
    assert forwarded == str((tmp_path / "voice.wav").resolve())


def _understand_writes_phase_a(cli):
    wd = Path(cli[cli.index("--work-dir") + 1])
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "agent_narration_brief.md").write_text("# per-source\n", encoding="utf-8")
    (wd / "scenes.json").write_text("[]", encoding="utf-8")


def test_recap_multi_video_phase_a_writes_manifest_and_pauses(
    monkeypatch, tmp_path, capsys
):
    v1 = tmp_path / "a.mp4"
    v2 = tmp_path / "b.mp4"
    v1.write_bytes(b"a source")
    v2.write_bytes(b"b source")
    work = tmp_path / "project"
    calls = []
    monkeypatch.setattr(
        "recap_runner._run",
        stub_child_run(work, calls=calls, understand=_understand_writes_phase_a),
    )
    _argv(
        monkeypatch,
        v1,
        v2,
        "--work-dir",
        work,
        "--edit-mode",
        "cut",
        "--material-library-dir",
        tmp_path / "lib",
        "--save-materials",
    )

    recap.main()

    manifest = json.loads(
        (work / "multi_source_manifest.json").read_text(encoding="utf-8")
    )
    assert len(manifest["sources"]) == 2
    assert all(s["source_id"].startswith("src_") for s in manifest["sources"])
    assert (work / "recap_run_manifest.json").exists()
    assert "source_id" in (work / "agent_narration_brief.md").read_text(
        encoding="utf-8"
    )
    assert (
        len([c for c in calls if c[:2] == ("understanding", "understand.py")])
        == 2
    )
    assert (tmp_path / "lib" / "materials_index.jsonl").exists()
    out = capsys.readouterr().out
    assert "clip_plan.json" in out
    assert "--material-library-dir" in out and "--save-materials" in out


def test_recap_multi_video_understanding_reads_the_project_background_research(
    monkeypatch, tmp_path, capsys
):
    """video-understanding reads research only from its own sources/<id>/ work_dir, while
    the Agent writes it once at the project level (video-recap SKILL.md §4.1)."""
    v1 = tmp_path / "a.mp4"
    v2 = tmp_path / "b.mp4"
    v1.write_bytes(b"a source")
    v2.write_bytes(b"b source")
    work = tmp_path / "project"
    work.mkdir()
    research = '{"characters": {"范闲": "主角"}}'
    (work / "background_research.json").write_text(research, encoding="utf-8")
    seen = []

    def understand(cli):
        wd = Path(cli[cli.index("--work-dir") + 1])
        seen.append((wd / "background_research.json").read_text(encoding="utf-8"))
        _understand_writes_phase_a(cli)

    monkeypatch.setattr(
        "recap_runner._run", stub_child_run(work, calls=[], understand=understand)
    )
    _argv(monkeypatch, v1, v2, "--work-dir", work, "--edit-mode", "cut")

    recap.main()

    assert seen == [research, research]


def test_project_background_research_does_not_replace_a_newer_per_source_file(tmp_path):
    project = tmp_path / "project"
    source = project / "sources" / "src_a"
    source.mkdir(parents=True)
    shared = project / "background_research.json"
    shared.write_text('{"synopsis": "project"}', encoding="utf-8")
    own = source / "background_research.json"
    own.write_text('{"synopsis": "per-source"}', encoding="utf-8")
    os.utime(shared, ns=(1_000_000_000, 1_000_000_000))
    os.utime(own, ns=(2_000_000_000, 2_000_000_000))

    recap._share_project_background_research(project, source)
    assert own.read_text(encoding="utf-8") == '{"synopsis": "per-source"}'

    os.utime(shared, ns=(3_000_000_000, 3_000_000_000))  # the project file was edited later
    recap._share_project_background_research(project, source)
    assert own.read_text(encoding="utf-8") == '{"synopsis": "project"}'
    assert own.stat().st_mtime_ns == shared.stat().st_mtime_ns


def test_recap_multi_video_phase_b_invokes_cut_with_sources_manifest(
    monkeypatch, tmp_path
):
    videos, work, records = seed_multi_work(tmp_path, manifest_args(edit_mode="cut"))
    calls = []
    monkeypatch.setattr(
        "recap_runner._run",
        stub_child_run(
            work,
            calls=calls,
            cut=lambda cli: write_cut_output(
                work, [multi_cut_clip(records, videos) | {"reason": ""}]
            ),
        ),
    )
    _argv(monkeypatch, *videos, "--work-dir", work, "--edit-mode", "cut")

    recap.main()

    cut_args = _call_args(calls, "cut", "cut.py")
    assert cut_args[cut_args.index("--sources-manifest") + 1] == str(
        work / "multi_source_manifest.json"
    )
    assert not any(c[1] in ("voiceover.py", "assemble.py") for c in calls)
    assert recap_timeline._read_phase_ledger(work) == {
        "clip_plan_identity": file_identity(work / "clip_plan.json")
    }


def test_recap_single_video_phase_a_can_save_materials(monkeypatch, tmp_path):
    video = tmp_path / "solo.mp4"
    video.write_bytes(b"solo source")
    work = tmp_path / "work"
    lib = tmp_path / "materials"

    def fake_run(skill, script, *cli_args):
        assert (skill, script) == ("understanding", "understand.py")
        _understand_writes_phase_a([str(arg) for arg in cli_args])

    monkeypatch.setattr("recap_runner._run", fake_run)
    _argv(
        monkeypatch,
        video,
        "--work-dir",
        work,
        "--material-library-dir",
        lib,
        "--save-materials",
    )

    recap.main()

    assert (lib / "materials_index.jsonl").exists()
    assert list((lib / "materials").glob("*/material.json"))
    assert (work / "recap_run_manifest.json").exists()


def _save_seed_material(tmp_path, video, args, brief):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "agent_narration_brief.md").write_text(brief, encoding="utf-8")
    (seed / "scenes.json").write_text("[]", encoding="utf-8")
    lib = tmp_path / "materials"
    material_lib.save_material(
        lib,
        seed,
        video,
        file_identity(video),
        recap_runtime._analysis_settings(args),
    )
    return lib


def test_recap_single_video_phase_a_uses_materials_without_understand(
    monkeypatch, tmp_path
):
    video = tmp_path / "solo.mp4"
    video.write_bytes(b"solo source")
    lib = _save_seed_material(
        tmp_path, video, manifest_args(), "# restored brief"
    )
    work = tmp_path / "work"

    def fake_run(*_args):
        raise AssertionError(
            "understand.py should not run when material restore matches"
        )

    monkeypatch.setattr("recap_runner._run", fake_run)
    _argv(
        monkeypatch,
        video,
        "--work-dir",
        work,
        "--material-library-dir",
        lib,
        "--use-materials",
    )

    recap.main()

    assert (work / "agent_narration_brief.md").read_text(
        encoding="utf-8"
    ) == "# restored brief"
    assert (work / "scenes.json").exists()
    assert (work / "recap_run_manifest.json").exists()


def test_recap_single_video_cut_pass2_rebuilds_output_brief_with_materials_enabled(
    monkeypatch, tmp_path
):
    """Pass 2 replaces a restored source-time brief with an OUTPUT-time brief after the cut."""
    video = tmp_path / "solo.mp4"
    video.write_bytes(b"solo source")
    work = tmp_path / "work"
    work.mkdir()
    args = manifest_args(edit_mode="cut")
    recap_runtime._write_run_manifest(work, video, args)
    (work / "clip_plan.json").write_text(
        json.dumps({"clips": [{"start": 0, "end": 1}]}), encoding="utf-8"
    )
    (work / "agent_narration_brief.md").write_text(
        "SOURCE-TIME BRIEF FROM MATERIAL", encoding="utf-8"
    )
    lib = _save_seed_material(tmp_path, video, args, "SOURCE-TIME BRIEF FROM MATERIAL")
    calls = []

    def understand(cli):
        assert "--brief-only" in cli
        (work / "agent_narration_brief.md").write_text(
            "OUTPUT-TIME BRIEF", encoding="utf-8"
        )

    monkeypatch.setattr(
        "recap_runner._run",
        stub_child_run(
            work,
            calls=calls,
            cut=lambda cli: write_cut_output(
                work,
                [
                    {
                        "source_start": 0,
                        "source_end": 1,
                        "output_start": 0,
                        "output_end": 1,
                        "duration": 1,
                    }
                ],
            ),
            understand=understand,
        ),
    )
    _argv(
        monkeypatch,
        video,
        "--work-dir",
        work,
        "--edit-mode",
        "cut",
        "--material-library-dir",
        lib,
        "--use-materials",
    )

    recap.main()

    understand_calls = [
        c for c in calls if c[:2] == ("understanding", "understand.py")
    ]
    assert len(understand_calls) == 1
    assert (work / "agent_narration_brief.md").read_text(
        encoding="utf-8"
    ) == "OUTPUT-TIME BRIEF"


def _json_fences(text):
    return [
        json.loads(raw)
        for raw in re.findall(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    ]


def test_multi_source_briefs_include_clip_and_narration_craft(tmp_path):
    work = tmp_path / "project"
    work.mkdir()
    src = work / "sources" / "src_a"
    src.mkdir(parents=True)
    (src / "agent_narration_brief.md").write_text(
        "# per-source context\n", encoding="utf-8"
    )
    (src / "speech_boundary_anchors.json").write_text(
        json.dumps(
            {
                "sentence_anchors": [
                    {
                        "time": 2.0,
                        "pause_start": 1.8,
                        "expected_time": 1.9,
                        "text_tail": "来源句子。",
                        "confidence": "high",
                    },
                    {
                        "time": 2.5,
                        "pause_start": 2.5,
                        "text_tail": "停顿与句末重合。",
                        "confidence": "medium",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (src / "asr_result.json").write_text(
        json.dumps([{"start": "0.0", "end": "4.0", "text": "来源完整讲话。"}]),
        encoding="utf-8",
    )
    (src / "silence_periods.json").write_text(
        json.dumps([{"start": "1.8", "end": "2.2", "has_speech": False}]),
        encoding="utf-8",
    )
    records = [
        {
            "source_id": "src_a",
            "source_name": "a.mp4",
            "source_path": str(tmp_path / "a.mp4"),
            "source_video_identity": {"size": 1, "mtime_ns": 1},
            "source_work_dir": "sources/src_a",
            "material_id": "mat-a",
        }
    ]
    args = manifest_args(edit_mode="cut", target_duration="1m")

    recap_timeline._write_multi_source_clip_brief(work, records, args)
    clip_text = (work / "agent_narration_brief.md").read_text(encoding="utf-8")
    clip_headings = set(re.findall(r"(?m)^## (.+)$", clip_text))
    clip_example = next(
        item
        for item in _json_fences(clip_text)
        if isinstance(item, dict) and "clips" in item
    )
    reason_parts = [
        part.strip() for part in clip_example["clips"][0]["reason"].split("|")
    ]

    assert {"创作决定", "必须写入的格式", "Sources"} <= clip_headings
    assert (
        "recap_story_plan.json" in clip_text and "visual_audio_board.json" in clip_text
    )
    assert "clip_plan.json.required_evidence" in clip_text
    assert len(reason_parts) == 7
    assert reason_parts[0].startswith("b")
    assert "→" in reason_parts[2]
    assert reason_parts[3].startswith("POV=")
    assert reason_parts[-2].startswith("入点=")
    assert reason_parts[-1].startswith("出点=")
    assert "sources/<source_id>" in clip_text

    plan = work / "clip_plan_validated.json"
    plan.write_text(
        json.dumps(
            {
                "clips": [
                    {
                        "source_id": "src_a",
                        "source_path": str(tmp_path / "a.mp4"),
                        "source_start": 1,
                        "source_end": 3,
                        "output_start": 0,
                        "output_end": 2,
                        "reason": "cold_open: reveal",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    recap_timeline._write_multi_source_output_brief(work, records, plan)
    out_text = (work / "agent_narration_brief.md").read_text(encoding="utf-8")
    output_headings = set(re.findall(r"(?m)^## (.+)$", out_text))
    narration_example = next(
        item for item in _json_fences(out_text) if isinstance(item, list)
    )

    assert {
        "更新创作决定",
        "narration.json 格式",
        "Kept clips (output → source)",
    } <= output_headings
    assert set(narration_example[0]) == {
        "start",
        "end",
        "narration",
        "pause_after_ms",
        "overlaps_speech",
        "emotion",
    }
    assert "visual_audio_board.json" in out_text
    assert "audio_owner" in out_text and "narration_job" in out_text
    assert "7:3" in out_text and "不是配额" in out_text
    assert "OUTPUT 时间线" in out_text
    output_evidence = json.loads(
        (work / "speech_boundary_anchors_output.json").read_text(encoding="utf-8")
    )
    assert output_evidence["timeline"] == "cut_output"
    assert output_evidence["sentence_anchors"][0]["time"] == 1.0
    assert output_evidence["sentence_anchors"][0]["pause_start"] == 0.8
    # Output-clock pause_end; the source value is kept under its own name.
    assert output_evidence["sentence_anchors"][0]["pause_end"] == 1.0
    assert output_evidence["sentence_anchors"][0]["source_pause_end"] == 2.0
    assert output_evidence["sentence_anchors"][0]["expected_time"] == 0.9
    assert output_evidence["sentence_anchors"][0]["source_expected_time"] == 1.9
    assert output_evidence["sentence_anchors"][1]["time"] == 1.5
    assert output_evidence["sentence_anchors"][1]["pause_start"] == 1.5
    assert output_evidence["speech_spans"][0]["start"] == 0.0
    assert output_evidence["speech_spans"][0]["end"] == 2.0
    assert output_evidence["quiet_windows"][0]["start"] == 0.8
    assert output_evidence["quiet_windows"][0]["end"] == 1.2


def test_multi_source_output_brief_labels_each_anchor_schema(tmp_path):
    """The safe-entry list labels schema-1 anchors (no `boundary_use`) `unverified` without a
    bound and drops their low-confidence rows; schema-2 rows keep their own use and bound."""
    work = tmp_path / "project"
    src = work / "sources" / "src_a"
    src.mkdir(parents=True)
    (src / "speech_boundary_anchors.json").write_text(
        json.dumps(
            {
                "sentence_anchors": [
                    {"time": 2.0, "confidence": "high"},
                    {"time": 3.0, "confidence": "medium"},
                    {"time": 4.0, "confidence": "low"},
                    {"time": 5.0, "confidence": "high", "boundary_use": "verified"},
                    {
                        "time": 6.0,
                        "confidence": "medium",
                        "boundary_use": "unverified",
                        "timing_bound_seconds": 1.5,
                    },
                    {"time": 7.0, "confidence": "high", "boundary_use": "none"},
                ]
            }
        ),
        encoding="utf-8",
    )
    records = [
        {
            "source_id": "src_a",
            "source_name": "a.mp4",
            "source_path": str(tmp_path / "a.mp4"),
            "source_work_dir": "sources/src_a",
        }
    ]
    plan = work / "clip_plan_validated.json"
    plan.write_text(
        json.dumps(
            {
                "clips": [
                    {
                        "source_id": "src_a",
                        "source_start": 1.0,
                        "source_end": 8.0,
                        "output_start": 10.0,
                        "output_end": 17.0,
                        "reason": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    recap_timeline._write_multi_source_output_brief(work, records, plan)

    text = (work / "agent_narration_brief.md").read_text(encoding="utf-8")
    section = text.split("## 原声句末安全切入点", 1)[1].split("\n## ", 1)[0]
    assert re.findall(r"(?m)^- .+$", section) == [
        "- 11.000s [unverified] (src_a)",
        "- 12.000s [unverified] (src_a)",
        "- 14.000s [high] (src_a)",
        "- 15.000s [unverified ±1.5s] (src_a)",
    ]


def test_multi_source_output_anchors_outside_the_output_are_dropped(tmp_path):
    """The 0.05 s source slack must not place an anchor before 0 or past the output end."""
    work = tmp_path / "work"
    record = {"source_id": "src_a", "source_work_dir": "sources/src_a"}
    source = work / record["source_work_dir"]
    source.mkdir(parents=True)
    (source / "speech_boundary_anchors.json").write_text(
        json.dumps({"sentence_anchors": [
            {"time": 9.97, "confidence": "high"},
            {"time": 50.0, "confidence": "high"},
            {"time": 81.40, "confidence": "high"},
            {"time": 81.42, "confidence": "high"},
        ]}),
        encoding="utf-8",
    )
    plan = {"clips": [{"source_id": "src_a", "source_start": 10.0, "source_end": 81.40,
                       "output_start": 0.0, "output_end": 71.40}]}
    (work / "clip_plan_validated.json").write_text(json.dumps(plan), encoding="utf-8")

    payload = recap_timeline._write_multi_source_output_speech_evidence(work, [record], plan)

    assert [row["time"] for row in payload["sentence_anchors"]] == [40.0, 71.4]
    assert [row["source_time"] for row in payload["sentence_anchors"]] == [50.0, 81.4]


class _ValidationPassed(Exception):
    pass


def _seed_multi_source_speech(work, record):
    """Source evidence: one sentence ends at 2.0s (anchor without pause_start), then quiet."""
    source = work / record["source_work_dir"]
    source.mkdir(parents=True, exist_ok=True)
    (source / "speech_boundary_anchors.json").write_text(
        json.dumps(
            {"sentence_anchors": [
                {"time": 2.0, "text_tail": "来源完整讲话。", "confidence": "high"}
            ]}
        ),
        encoding="utf-8",
    )
    (source / "asr_result.json").write_text(
        json.dumps([{"start": 0.0, "end": 2.0, "text": "来源完整讲话。"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (source / "silence_periods.json").write_text(
        json.dumps([{"start": 2.0, "end": 6.0, "has_speech": False}]), encoding="utf-8"
    )


def test_multi_source_anchors_stay_inside_their_own_clip_on_the_output_clock(tmp_path):
    """A pause end a few ms past a clip's frame-snapped source edge still belongs to that clip:
    mapped naively it lands inside the NEXT clip (another source) labelled with this one."""
    work = tmp_path / "project"
    records = []
    for source_id, anchors in (
        ("src_a", [{"time": 12.5, "pause_start": 12.3, "expected_time": 12.52,
                    "text_tail": "甲的句末。", "confidence": "high"},
                   {"time": 12.53, "pause_start": 12.52, "text_tail": "甲接续段。", "confidence": "high"}]),
        ("src_b", [{"time": 29.97, "text_tail": "乙开场前。", "confidence": "high"}]),
    ):
        source_dir = work / "sources" / source_id
        source_dir.mkdir(parents=True)
        (source_dir / "speech_boundary_anchors.json").write_text(
            json.dumps({"sentence_anchors": anchors}, ensure_ascii=False), encoding="utf-8")
        records.append({"source_id": source_id, "source_work_dir": f"sources/{source_id}"})
    plan = {"clips": [
        {"source_id": "src_a", "source_start": 10.0, "source_end": 12.48,
         "output_start": 0.0, "output_end": 2.48},
        {"source_id": "src_b", "source_start": 30.0, "source_end": 33.0,
         "output_start": 2.48, "output_end": 5.48},
        # The same source again from just after the first clip's end.
        {"source_id": "src_a", "source_start": 12.51, "source_end": 14.0,
         "output_start": 5.48, "output_end": 6.97},
    ]}
    (work / "clip_plan_validated.json").write_text(json.dumps(plan), encoding="utf-8")

    recap_timeline._write_multi_source_output_speech_evidence(work, records, plan)

    anchors = json.loads(
        (work / "speech_boundary_anchors_output.json").read_text(encoding="utf-8")
    )["sentence_anchors"]
    by_tail = {}
    for anchor in anchors:
        by_tail.setdefault(anchor["text_tail"], []).append(anchor)
    clip_of = {"src_a": [(0.0, 2.48), (5.48, 6.97)], "src_b": [(2.48, 5.48)]}
    for anchor in anchors:
        assert any(lo <= anchor["time"] <= hi for lo, hi in clip_of[anchor["source_id"]]), anchor
        assert any(lo <= anchor["pause_start"] <= hi for lo, hi in clip_of[anchor["source_id"]]), anchor
    # 12.5 also sits within the third clip's start tolerance: mapped there at its start edge.
    assert [a["time"] for a in by_tail["甲的句末。"]] == [2.48, 5.48]
    end_a = by_tail["甲的句末。"][0]
    assert (end_a["time"], end_a["pause_start"], end_a["expected_time"]) == (2.48, 2.3, 2.48)
    assert (end_a["source_time"], end_a["source_expected_time"]) == (12.5, 12.52)
    assert by_tail["乙开场前。"][0]["time"] == 2.48
    # 12.53 is within the first clip's edge tolerance but the third clip plays it: one anchor.
    assert [a["time"] for a in by_tail["甲接续段。"]] == [5.5]


def _multi_cut_through_real_validation(work, clip):
    """Pass 2 stubs cut.py's render; pass 3 models cut.py reusing edited_source.mp4, which
    leaves an unchanged clip_plan_validated.json untouched (pinned by the cut group).
    script/validate.py always runs for real; the advisory storyboard step is a no-op."""
    def run(skill, script, *cli_args):
        if (skill, script) == ("cut", "cut.py"):
            if not (work / "clip_plan_validated.json").exists():
                write_cut_output(work, [clip])
            return None
        if (skill, script) == ("understanding", "understand.py"):
            assert "--edited-storyboard-only" in cli_args
            return None
        if (skill, script) == ("script", "validate.py"):
            return recap_runtime._run(skill, script, *cli_args)
        raise _ValidationPassed(f"{skill}/{script}")

    return run


@pytest.mark.parametrize(
    "start, passes, error_code",
    [(2.1, True, None), (1.0, False, "interrupts_source_sentence")],
)
def test_multi_source_cut_output_validate_reads_recap_written_evidence(
    monkeypatch, tmp_path, start, passes, error_code
):
    """recap's multi-source writer feeds validate.py's cut_output lint without mocks:
    the evidence carries the plan identity the lint checks, and a later resume still
    trusts it instead of failing closed (or crashing on a missing key)."""
    args = manifest_args(edit_mode="cut")
    videos, work, records = seed_multi_work(tmp_path, args)
    _seed_multi_source_speech(work, records[0])
    clip = {
        **multi_cut_clip(records, videos),
        "source_end": 6, "output_end": 6, "duration": 6, "reason": "b1 | payoff",
    }
    monkeypatch.setattr("recap_runner._run", _multi_cut_through_real_validation(work, clip))
    monkeypatch.setattr("recap_runner._read_video_duration_or_raise", lambda path: 6.0)

    recap._run_multi_cut(videos, work, args)  # pass 2: brief + evidence, then pause

    evidence = json.loads(
        (work / "speech_boundary_anchors_output.json").read_text(encoding="utf-8")
    )
    assert evidence["clip_plan_identity"] == file_identity(
        work / "clip_plan_validated.json"
    )
    assert evidence["sentence_anchors"][0]["pause_start"] == 1.88
    (work / "narration.json").write_text(
        json.dumps(
            # Ends 1.2 s before the 6 s output, so the moved block (2.0-5.8) still fits it.
            [{"start": start, "end": 4.8, "narration": "他终于明白，弟弟一直在门口等他回家。",
              "overlaps_speech": False}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    if passes:
        with pytest.raises(_ValidationPassed):
            recap._run_multi_cut(videos, work, args)  # pass 3 continues past validation
    else:
        with pytest.raises(SystemExit, match=r"script/validate\.py 失败 \(exit 1\)"):
            recap._run_multi_cut(videos, work, args)

    lint = json.loads((work / "narration_lint.json").read_text(encoding="utf-8"))
    codes = [issue["code"] for issue in lint["errors"]]
    if passes:
        assert codes == []
    else:
        assert codes == [error_code]
        assert lint["errors"][0]["suggested_start"] == 2.0
        assert lint["errors"][0]["suggested_end"] == 5.8


@pytest.mark.parametrize("storyboard_fails", [False, True])
def test_multi_source_pass2_builds_the_output_storyboard_after_the_brief(
    monkeypatch, tmp_path, capsys, storyboard_fails
):
    """Like the single-source --brief-only rebuild, multi-source pass 2 asks
    understand.py for the cut-output storyboard once the brief exists; the step is
    advisory, so a failing child warns and the narration pause still happens."""
    args = manifest_args(edit_mode="cut")
    videos, work, records = seed_multi_work(tmp_path, args)
    calls = []

    def understand(cli):
        assert (work / "agent_narration_brief.md").read_text(encoding="utf-8").startswith(
            "# Multi-source Output Narration Brief"
        )
        if storyboard_fails:
            raise SystemExit("understanding/understand.py 失败 (exit 1)")

    monkeypatch.setattr(
        "recap_runner._run",
        stub_child_run(
            work,
            calls=calls,
            cut=lambda cli: write_cut_output(
                work, [{**multi_cut_clip(records, videos), "reason": "b1 | hook"}]
            ),
            understand=understand,
        ),
    )

    recap._run_multi_cut(videos, work, args)

    assert [(skill, script) for skill, script, _ in calls] == [
        ("cut", "cut.py"),
        ("understanding", "understand.py"),
    ]
    assert calls[1][2] == [
        str(videos[0]), "--work-dir", str(work), "--edited-storyboard-only"
    ]
    out = capsys.readouterr().out
    assert ("剪后故事板未生成" in out) is storyboard_fails
    assert "⏸" in out
    assert (work / "recap_phase.json").exists()


def test_cut_qc_summary_surfaces_canonical_cut_result(tmp_path, capsys):
    write_cut_output(
        tmp_path,
        status="warning",
        target_duration_status="under",
        total_duration=42.0,
        clip_count=3,
        warnings=["short"],
    )
    qc = recap_timeline._surface_cut_qc(tmp_path)
    assert qc["target_duration_status"] == "under"
    out = capsys.readouterr().out
    assert (
        "cut QC" in out and "target_duration_status=under" in out and "1920x1080" in out
    )


def test_recap_rewrites_visual_overlays_for_each_narration(tmp_path):
    """visual_overlays.json always mirrors the current narration: supported overlays are
    written out, and a later narration without any supported overlay empties the file."""
    work = tmp_path / "work"
    work.mkdir()
    overlays_path = work / "visual_overlays.json"
    first_narration = work / "narration.first.json"
    first_narration.write_text(
        json.dumps(
            [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "narration": "开场标题。",
                    "visual_overlays": [
                        {
                            "type": "top_title",
                            "text": "上一版标题",
                            "start": 0.0,
                            "end": 2.0,
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    second_narration = work / "narration.json"
    second_narration.write_text(
        json.dumps(
            [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "narration": "没有受支持的贴片。",
                    "visual_overlays": [
                        {
                            "type": "platform_card",
                            "text": "unsupported",
                            "start": 0.0,
                            "end": 1.0,
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    assert (
        recap_timeline._write_canonical_visual_overlays(work, first_narration)
        == overlays_path
    )
    assert json.loads(overlays_path.read_text(encoding="utf-8"))["overlays"]

    assert (
        recap_timeline._write_canonical_visual_overlays(work, second_narration)
        == overlays_path
    )
    assert json.loads(overlays_path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "overlays": [],
    }


def test_recap_writes_canonical_visual_overlays_before_assemble(monkeypatch, tmp_path):
    """Recap owns the canonical visual_overlays.json input; assemble reads it by contract."""
    video, work = seed_full_work(
        tmp_path,
        [
            {
                "start": 0.0,
                "end": 2.0,
                "narration": "开场标题。",
                "visual_overlays": [
                    {
                        "type": "top_title",
                        "text": "今日回顾",
                        "start": 0.0,
                        "end": 2.0,
                    },
                    {
                        "type": "inline_label_or_callout",
                        "text": "关键人物",
                        "start": 1.0,
                        "end": 2.0,
                        "anchor": "center",
                    },
                ],
            }
        ],
    )
    calls = []

    def assemble(cli):
        overlays_path = work / "visual_overlays.json"
        assert overlays_path.exists(), "visual_overlays.json must exist before assemble runs"
        payload = json.loads(overlays_path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert [item["type"] for item in payload["overlays"]] == [
            "top_title",
            "inline_label_or_callout",
        ]
        write_assemble_output(work, tmp_path / "recap_video.mp4")

    monkeypatch.setattr(
        "recap_runner._run", stub_child_run(work, calls=calls, assemble=assemble)
    )
    _argv(monkeypatch, video, "--work-dir", work)

    recap.main()

    assert "--visual-overlays" not in _call_args(calls, "assemble", "assemble.py")
