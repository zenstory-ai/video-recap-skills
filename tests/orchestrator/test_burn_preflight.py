"""Orchestrator preflight: on an ffmpeg without the libass `subtitles` filter, recap.py must
reject an EXPLICIT burn request BEFORE any understand/VLM/ASR/TTS spend, let the default
burn degrade to the .srt sidecar with a warning, and surface the advisory narration review
to the user at delivery."""

import json
import shutil
from argparse import Namespace

import pytest

import recap_runtime as recap
import recap_timeline


def _args(burn_subtitles=None, edit_mode="full"):
    return Namespace(burn_subtitles=burn_subtitles, edit_mode=edit_mode)


@pytest.mark.parametrize(
    "env, cli, expected",
    [
        pytest.param(None, None, True, id="default-on"),
        pytest.param(None, False, False, id="cli-off"),
        pytest.param("0", True, True, id="cli-beats-env"),
        *[
            pytest.param(raw, None, expected, id=f"env-{raw.strip() or 'blank'}")
            for raw, expected in [
                ("on", True),
                ("yes", True),
                ("TRUE", True),
                ("1", True),
                (" 1 ", True),
                ("0", False),
                ("no", False),
                ("off", False),
                ("false", False),
                ("garbage", False),
            ]
        ],
    ],
)
def test_burn_intended_resolves_cli_over_env_token(monkeypatch, env, cli, expected):
    monkeypatch.delenv("BURN_SUBTITLES", raising=False)
    if env is not None:
        monkeypatch.setenv("BURN_SUBTITLES", env)
    assert recap._burn_subtitles_intended(_args(burn_subtitles=cli)) is expected


@pytest.mark.parametrize(
    "env, cli",
    [pytest.param(None, True, id="cli-burn"), pytest.param("1", None, id="env-burn")],
)
def test_preflight_raises_when_burn_is_explicit_but_cannot_burn(monkeypatch, env, cli):
    monkeypatch.delenv("BURN_SUBTITLES", raising=False)
    if env is not None:
        monkeypatch.setenv("BURN_SUBTITLES", env)
    monkeypatch.setattr(recap, "_ffmpeg_present_but_cannot_burn", lambda: True)
    with pytest.raises(SystemExit, match="subtitles/libass"):
        recap._preflight_burn_subtitles(_args(burn_subtitles=cli))


def test_preflight_default_burn_degrades_to_sidecar_with_warning(monkeypatch, capsys):
    """Stock Homebrew ffmpeg has no libass: the default run goes on and assemble ships a
    .srt sidecar; the warning names the machine-readable code final_qc carries."""
    monkeypatch.delenv("BURN_SUBTITLES", raising=False)
    monkeypatch.setattr(recap, "_ffmpeg_present_but_cannot_burn", lambda: True)
    args = _args()
    recap._preflight_burn_subtitles(args)  # must not raise
    assert args.burn_subtitles is None  # assemble settles the degrade and records it
    out = capsys.readouterr().out
    assert "subtitle_burn_degraded" in out and ".srt" in out


def test_preflight_skips_dub_which_never_burns(monkeypatch):
    monkeypatch.delenv("BURN_SUBTITLES", raising=False)
    monkeypatch.setattr(recap, "_ffmpeg_present_but_cannot_burn", lambda: True)
    recap._preflight_burn_subtitles(_args(edit_mode="dub"))  # must not raise


def test_preflight_ok_when_can_burn(monkeypatch):
    monkeypatch.delenv("BURN_SUBTITLES", raising=False)
    monkeypatch.setattr(recap, "_ffmpeg_present_but_cannot_burn", lambda: False)
    recap._preflight_burn_subtitles(_args())  # must not raise


def test_preflight_does_not_probe_when_burn_off(monkeypatch):
    def _boom():
        raise AssertionError("must not probe ffmpeg when burn is off")

    monkeypatch.setattr(recap, "_ffmpeg_present_but_cannot_burn", _boom)
    recap._preflight_burn_subtitles(_args(burn_subtitles=False))  # must not raise


@pytest.mark.parametrize(
    "ffmpeg_path, has_filter, expected",
    [
        # ffmpeg absent entirely -> this guard stays out of it (reported by doctor)
        pytest.param(None, True, False, id="ffmpeg-absent"),
        pytest.param("/usr/bin/ffmpeg", False, True, id="present-without-filter"),
        pytest.param("/usr/bin/ffmpeg", True, False, id="present-with-filter"),
    ],
)
def test_cannot_burn_only_when_ffmpeg_is_present_without_the_filter(
    monkeypatch, ffmpeg_path, has_filter, expected
):
    monkeypatch.setattr(shutil, "which", lambda _n: ffmpeg_path)
    monkeypatch.setattr(recap, "ffmpeg_has_subtitles_filter", lambda: has_filter)
    assert recap._ffmpeg_present_but_cannot_burn() is expected


def test_review_pointer_prints_verdict(tmp_path, capsys):
    (tmp_path / "narration_review.md").write_text("# review", encoding="utf-8")
    (tmp_path / "narration_review.json").write_text(
        json.dumps(
            {
                "verdict": "REVISE",
                "findings": [{"severity": "error"}, {"severity": "warning"}],
            }
        ),
        encoding="utf-8",
    )
    recap_timeline._print_narration_review_pointer(tmp_path)
    out = capsys.readouterr().out
    assert "REVISE" in out
    assert "narration_review.md" in out
    assert "error 1" in out


def test_review_pointer_silent_when_review_did_not_run(tmp_path, capsys):
    """review_ran=False (disabled/failed) prints nothing, even with stale artifacts around."""
    (tmp_path / "narration_review.md").write_text("# stale", encoding="utf-8")
    recap_timeline._print_narration_review_pointer(tmp_path, review_ran=False)
    assert capsys.readouterr().out == ""


def test_review_pointer_reads_the_review_by_contract_when_it_ran(tmp_path):
    """review_ran=True means video-script wrote narration_review.json; a corrupt one raises."""
    (tmp_path / "narration_review.md").write_text("# review", encoding="utf-8")
    (tmp_path / "narration_review.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        recap_timeline._print_narration_review_pointer(tmp_path)


# ── drawtext overlays: authored content fails fast, before TTS ─────────────────────────


def _narration_with_overlays(work_dir, overlays):
    (work_dir / "narration.json").write_text(
        json.dumps([{"start": 0.0, "end": 2.0, "narration": "解说。", "visual_overlays": overlays}],
                   ensure_ascii=False),
        encoding="utf-8",
    )


def _ffmpeg_with(monkeypatch, filters):
    monkeypatch.setattr(recap_timeline.shutil, "which", lambda _n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(recap_timeline, "ffmpeg_filters", lambda: set(filters))


def test_overlay_preflight_fails_without_drawtext(tmp_path, monkeypatch):
    _narration_with_overlays(tmp_path, [{"type": "top_title", "text": "第一章"}])
    _ffmpeg_with(monkeypatch, {"scale", "drawbox"})
    with pytest.raises(SystemExit, match="drawtext") as exc:
        recap_timeline._preflight_visual_overlays(tmp_path, narration=True)
    assert "narration.json" in str(exc.value)


def test_overlay_preflight_passes_with_drawtext(tmp_path, monkeypatch):
    _narration_with_overlays(tmp_path, [{"type": "top_title", "text": "第一章"}])
    _ffmpeg_with(monkeypatch, {"drawtext"})
    recap_timeline._preflight_visual_overlays(tmp_path, narration=True)  # must not raise


def test_overlay_preflight_ignores_types_recap_never_hands_to_assemble(tmp_path, monkeypatch):
    _narration_with_overlays(tmp_path, [{"type": "sticker", "text": "x"}])

    def _boom():
        raise AssertionError("must not probe ffmpeg without renderable overlays")

    monkeypatch.setattr(recap_timeline.shutil, "which", lambda _n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(recap_timeline, "ffmpeg_filters", _boom)
    recap_timeline._preflight_visual_overlays(tmp_path, narration=True)


def test_overlay_preflight_source_modes_read_existing_overlay_file(tmp_path, monkeypatch):
    (tmp_path / "visual_overlays.json").write_text(
        json.dumps({"schema_version": 1, "overlays": [{"type": "top_title", "text": "片头"}]}),
        encoding="utf-8",
    )
    _ffmpeg_with(monkeypatch, set())
    with pytest.raises(SystemExit, match="visual_overlays.json"):
        recap_timeline._preflight_visual_overlays(tmp_path, narration=False)


def test_overlay_preflight_noop_when_ffmpeg_absent(tmp_path, monkeypatch):
    _narration_with_overlays(tmp_path, [{"type": "top_title", "text": "第一章"}])
    monkeypatch.setattr(recap_timeline.shutil, "which", lambda _n: None)
    recap_timeline._preflight_visual_overlays(tmp_path, narration=True)  # must not raise


def test_deliver_runs_overlay_preflight_before_tts(tmp_path, monkeypatch):
    import recap_runner

    def _stop(work_dir, *, narration):
        assert narration is True
        raise SystemExit("drawtext missing")

    def _narrate(*_a, **_k):
        raise AssertionError("TTS must not start before the overlay preflight")

    monkeypatch.setattr(recap_runner, "_preflight_visual_overlays", _stop)
    monkeypatch.setattr(recap_runner, "_narrate", _narrate)
    args = Namespace(resolved_project=None, audio_mode="narration")
    with pytest.raises(SystemExit, match="drawtext missing"):
        recap_runner._deliver(tmp_path, args, tmp_path / "in.mp4", "in", timeline=None)


# ── degraded burn is reported, never a plain success ─────────────────────────────────


def test_finish_prints_degraded_subtitle_warning_and_sidecar(tmp_path, capsys):
    import recap_stage_qc

    (tmp_path / "assembly_manifest.json").write_text(
        json.dumps({"subtitle_sidecar": "/out/recap_x.srt"}), encoding="utf-8"
    )
    result = {"final_qc": {"ok": True, "blocker_count": 0,
                           "warnings": ["subtitle_burn_degraded"]}}
    recap_stage_qc._print_render_warnings(result, tmp_path)
    out = capsys.readouterr().out
    assert "libass" in out and "/out/recap_x.srt" in out and "metadata.warnings" in out


def test_final_qc_carries_visual_qc_warnings_in_metadata(tmp_path):
    import final_qc

    warning = {"code": "subtitle_burn_degraded", "reason": "ffmpeg_missing_libass",
               "delivered": "sidecar_srt", "mask_dropped": False,
               "message": "m", "next_action": "n"}
    (tmp_path / "visual_qc.json").write_text(
        json.dumps({"verdict": "PASS", "blocking": False, "blocking_codes": [],
                    "warnings": [warning]}),
        encoding="utf-8",
    )
    report = final_qc.build_final_qc(tmp_path)
    assert report["metadata"]["warnings"] == [warning]
    assert report["ok"] is False  # missing output still blocks; the warning never does
    assert all(f["code"] != "subtitle_burn_degraded" for f in report["findings"])
