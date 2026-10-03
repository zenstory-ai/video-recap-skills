import json
import sys

import pytest

import final_qc
import recap_runner
from _helpers import seed_full_work, stub_child_run

FINDING_KEYS = {"code", "message", "blocking", "evidence", "next_action"}


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _probe(duration=12.5, codec="h264"):
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": codec,
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30000/1001",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": str(duration), "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    }


def _assert_valid(report):
    """The final_qc.json shape that --require-final-qc and the dashboard read."""
    assert report["schema_version"] == final_qc.SCHEMA_VERSION
    assert report["artifact"] == "final_qc.json"
    assert report["blocker_count"] == report["finding_count"] == len(report["findings"])
    assert report["ok"] is (report["blocker_count"] == 0)
    for finding in report["findings"]:
        assert set(finding) == FINDING_KEYS
        assert finding["blocking"] is True
        assert finding["next_action"]
    return True


@pytest.mark.parametrize(
    "decode_result, ok",
    [
        pytest.param((False, "Invalid NAL unit size (505 > 107)"), False, id="undecodable"),
        pytest.param((None, "ffmpeg unavailable"), True, id="decode-skipped"),
    ],
)
def test_tail_decode_blocks_only_a_real_decode_failure(tmp_path, decode_result, ok):
    """A container-valid but truncated render is a blocker; a skipped decode (no ffmpeg) is not."""
    (tmp_path / "output.mp4").write_bytes(b"\x00" * 4096)
    report = final_qc.build_final_qc(
        tmp_path,
        final_output="output.mp4",
        probe_runner=lambda p: _probe(),  # header probe passes cleanly
        decode_runner=lambda p: decode_result,
    )
    assert report["ok"] is ok
    assert (report["blocker_count"] == 0) is ok
    assert any(f["code"] == "undecodable_stream" for f in report["findings"]) is not ok


def test_missing_and_empty_final_output_are_valid_blockers(tmp_path):
    missing = tmp_path / "missing.mp4"
    report = final_qc.build_final_qc(
        tmp_path,
        final_output=missing,
        probe_runner=lambda p: (_ for _ in ()).throw(AssertionError("no probe")),
    )

    assert report["ok"] is False
    assert report["findings"][0]["code"] == "missing_final_output"
    assert report["findings"][0]["next_action"] == "render_final_output"
    assert _assert_valid(report)

    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    report = final_qc.build_final_qc(
        tmp_path,
        final_output=empty,
        probe_runner=lambda p: (_ for _ in ()).throw(AssertionError("no probe")),
    )

    assert report["ok"] is False
    assert any(f["code"] == "empty_final_output" for f in report["findings"])
    assert _assert_valid(report)


def test_probe_fixture_success_writes_only_a_valid_final_qc(tmp_path):
    output = tmp_path / "recap.mp4"
    output.write_bytes(b"fake mp4 bytes")
    _write_json(tmp_path / "assembly_manifest.json", {"final_output": str(output)})
    probe_fixture = tmp_path / "probe.json"
    _write_json(probe_fixture, _probe(duration=9.5, codec="h264"))

    summary = final_qc.run(tmp_path, final_output=output, probe_fixture=probe_fixture)
    final_report = json.loads((tmp_path / "final_qc.json").read_text(encoding="utf-8"))

    assert summary["final_qc"] == {"ok": True, "blocker_count": 0, "warnings": []}
    assert summary["written"] == ["final_qc.json"]
    assert not (tmp_path / "golden_eval.json").exists()
    assert _assert_valid(final_report)
    assert final_report["metadata"]["probe"]["format"]["duration"] == "9.5"


def test_probe_fixture_missing_objective_media_metadata_are_valid_blockers(tmp_path):
    output = tmp_path / "recap.mp4"
    output.write_bytes(b"fake mp4 bytes")

    report = final_qc.build_final_qc(
        tmp_path,
        final_output=output,
        probe_fixture={
            "streams": [{"codec_type": "audio", "codec_name": "aac"}],
            "format": {},
        },
    )

    codes = {f["code"] for f in report["findings"]}
    assert {
        "missing_video_stream",
        "missing_duration",
        "missing_codec",
        "missing_fps",
    } <= codes
    actions = {f["code"]: f["next_action"] for f in report["findings"]}
    assert actions["missing_video_stream"] == "rerender_final_output_with_video_stream"
    assert actions["missing_duration"] == "rerender_final_output_with_valid_duration"
    assert _assert_valid(report)


def test_probe_fixture_accepts_numeric_and_rational_video_fps(tmp_path):
    output = tmp_path / "recap.mp4"
    output.write_bytes(b"fake mp4 bytes")

    rational = final_qc.build_final_qc(
        tmp_path, final_output=output, probe_fixture=_probe()
    )
    numeric = final_qc.build_final_qc(
        tmp_path,
        final_output=output,
        probe_fixture={
            "streams": [{"codec_type": "video", "codec_name": "h264", "fps": 29.97}],
            "format": {"duration": "1.5"},
        },
    )

    assert rational["ok"] is True
    assert numeric["ok"] is True
    assert _assert_valid(rational)
    assert _assert_valid(numeric)


def _raise(error):
    return lambda _path: (_ for _ in ()).throw(error)


@pytest.mark.parametrize(
    "probe",
    [
        pytest.param({"probe_runner": _raise(RuntimeError("ffprobe boom"))}, id="runtime"),
        pytest.param({"probe_runner": _raise(OSError("missing executable"))}, id="oserror"),
        pytest.param(
            {"probe_fixture": {"streams": [None], "format": {"duration": "1"}}},
            id="malformed-metadata",
        ),
    ],
)
def test_probe_failure_on_existing_nonempty_mp4_is_deterministic_blocker(tmp_path, probe):
    output = tmp_path / "recap.mp4"
    output.write_bytes(b"fake mp4 bytes")

    report = final_qc.build_final_qc(tmp_path, final_output=output, **probe)

    assert report["ok"] is False
    assert any(f["code"] == "probe_failed" for f in report["findings"])
    assert _assert_valid(report)


def test_leftover_mimo_qc_from_an_older_run_is_not_read(tmp_path):
    output = tmp_path / "recap.mp4"
    output.write_bytes(b"fake mp4 bytes")
    (tmp_path / "mimo_qc.json").write_text("not json", encoding="utf-8")

    report = final_qc.build_final_qc(
        tmp_path, final_output=output, probe_fixture=_probe()
    )

    assert report["ok"] is True
    assert "mimo_qc.json" not in report["metadata"]["artifacts"]


def test_upstream_qc_is_summarised_not_reraised(tmp_path):
    """assemble exits nonzero on a blocking assembly/visual QC before recap reaches
    final_qc, so final_qc records their verdicts in metadata and judges only the mp4."""
    output = tmp_path / "recap.mp4"
    output.write_bytes(b"fake mp4 bytes")
    _write_json(tmp_path / "assembly_qc.json", {
        "schema_version": 1, "artifact": "assembly_qc.json", "verdict": "FAIL",
        "blocking": True, "blocking_codes": ["missing_scene"],
    })
    (tmp_path / "visual_qc.json").write_text("not json", encoding="utf-8")

    report = final_qc.build_final_qc(
        tmp_path, final_output=output, probe_fixture=_probe()
    )

    assert report["ok"] is True
    assert report["findings"] == []
    artifacts = report["metadata"]["artifacts"]
    assert artifacts["assembly_qc.json"]["summary"] == {
        "schema_version": 1, "verdict": "FAIL", "blocking": True,
        "blocking_codes": ["missing_scene"],
    }
    assert artifacts["visual_qc.json"]["summary"] == {"invalid": True}
    assert set(artifacts) == {"assembly_manifest.json", "assembly_qc.json", "visual_qc.json"}


def test_final_output_comes_from_caller_or_assembly_manifest_only(tmp_path):
    """No guessing: without an explicit final_output the assembler's manifest is the only
    source, so a corrupt manifest raises instead of QC-ing some other mp4 (output.mp4 is
    the assembler's intermediate). With an explicit path the manifest is still summarised."""
    output = tmp_path / "recap.mp4"
    output.write_bytes(b"fake mp4 bytes")
    (tmp_path / "output.mp4").write_bytes(b"intermediate, must not be selected")
    (tmp_path / "assembly_manifest.json").write_text("not json", encoding="utf-8")

    with pytest.raises(ValueError):
        final_qc.build_final_qc(tmp_path, probe_fixture=_probe())

    report = final_qc.build_final_qc(tmp_path, final_output=output, probe_fixture=_probe())
    assert report["metadata"]["final_output"]["path"] == "recap.mp4"
    assert report["metadata"]["artifacts"]["assembly_manifest.json"]["summary"] == {
        "invalid": True
    }

    _write_json(tmp_path / "assembly_manifest.json", {"final_output": str(output)})
    report = final_qc.build_final_qc(tmp_path, probe_fixture=_probe())
    assert report["metadata"]["final_output"]["path"] == "recap.mp4"


def test_probe_is_trimmed_so_source_tags_never_reach_final_qc(tmp_path):
    """assemble does not strip container metadata, so ffprobe tags copied from a downloaded
    source (comment/purl URLs) would land in final_qc.json; only checked fields are kept."""
    output = tmp_path / "recap.mp4"
    output.write_bytes(b"fake mp4 bytes")
    _write_json(
        tmp_path / "assembly_manifest.json",
        {"final_output": str(output), "api_key": "sk-manifest-secret"},
    )

    final_qc.run(
        tmp_path,
        final_output=output,
        probe_fixture={
            "format": {
                "duration": "3.0", "format_name": "mov,mp4",
                "tags": {"comment": "https://user:pass@example.test/v?token=tp-tag-secret"},
                "filename": "/tmp/sk-probe-secret.mp4",
            },
            "streams": [{
                "codec_type": "video", "codec_name": "h264", "avg_frame_rate": "30/1",
                "tags": {"handler_name": "sk-stream-secret"},
                "disposition": {"default": 1},
            }],
        },
    )
    text = (tmp_path / "final_qc.json").read_text(encoding="utf-8")
    report = json.loads(text)

    for secret in ("sk-manifest-secret", "tp-tag-secret", "user:pass",
                   "sk-probe-secret", "sk-stream-secret"):
        assert secret not in text
    assert report["metadata"]["probe"] == {
        "format": {"duration": "3.0", "format_name": "mov,mp4"},
        "streams": [{"codec_type": "video", "codec_name": "h264", "avg_frame_rate": "30/1"}],
    }
    assert _assert_valid(report)


def test_missing_assembly_manifest_without_explicit_output_is_a_blocker(tmp_path):
    """No --final-output and no assembly_manifest.json: report a missing final output,
    never a traceback (the --require-final-qc contract keeps the report)."""
    report = final_qc.build_final_qc(
        tmp_path,
        probe_runner=lambda p: (_ for _ in ()).throw(AssertionError("no probe")),
    )

    assert report["ok"] is False
    assert report["findings"][0]["code"] == "missing_final_output"
    assert report["metadata"]["final_output"]["exists"] is False
    assert _assert_valid(report)


def test_full_run_ignores_a_corrupt_leftover_preflight_ledger(monkeypatch, tmp_path):
    """preflight_qc.json is no longer written or read: a corrupt copy from an older run
    used to crash the narration path at pre_tts, after the paid review call."""
    video, work = seed_full_work(tmp_path)
    (work / "preflight_qc.json").write_text("stale-not-json", encoding="utf-8")
    final = tmp_path / "recap_video.mp4"
    calls = []
    monkeypatch.setattr("recap_runner._run", stub_child_run(work, final, calls))
    monkeypatch.setattr("recap_runner._preflight_burn_subtitles", lambda args: None)
    monkeypatch.setattr(
        "recap_runner._write_final_qc_reports",
        lambda work_dir, output: final_qc.run(work_dir, final_output=output, probe_fixture=_probe()),
    )
    monkeypatch.setattr(sys, "argv", ["recap.py", str(video), "--work-dir", str(work)])

    recap_runner.main()

    assert [script for _, script, _ in calls][-1] == "assemble.py"
    assert (work / "preflight_qc.json").read_text(encoding="utf-8") == "stale-not-json"
    assert _assert_valid(json.loads((work / "final_qc.json").read_text(encoding="utf-8")))
