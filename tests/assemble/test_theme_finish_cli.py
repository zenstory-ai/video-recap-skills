"""The existing theme command can finish packaging, without another author schema."""

import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).parents[2] / "skills/video-assemble" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import render_theme_foreground as cli


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    plan = tmp_path / "theme.json"
    document = {"video": {"width": 640, "height": 360, "fps": "24/1",
                          "total_frames": 48, "body_end_frame": 48}}
    plan.write_text(json.dumps(document))
    output = tmp_path / "variant"
    calls = []

    def produce(path, directory, **kwargs):
        calls.append(("produce", path, kwargs))
        directory = Path(directory)
        directory.mkdir(exist_ok=False)
        if kwargs["plan_only"]:
            return {"status": "PLANNED"}
        receipt = {
            "inputs": {"base": {"path": "frozen.mp4", "sha256": "a" * 64},
                       "video": {**json.loads(plan.read_text())["video"], "fps": "24"}},
            "foreground": {"directory": str(directory / "frames"),
                           "pattern": "frame_%06d.png", "start_frame": 0,
                           "end_frame": json.loads(plan.read_text())["video"]["body_end_frame"],
                           "ordered_sha256": "b" * 64},
        }
        (directory / "producer_receipt.json").write_text(json.dumps(receipt))
        return receipt

    def compose(path, directory):
        calls.append(("compose", json.loads(Path(path).read_text()), directory))
        return {"status": "FOREGROUND_RENDERED", "output": {"path": str(Path(directory) / "foreground.mp4")}}

    monkeypatch.setattr(cli, "run_producer", produce)
    # The real command imports the existing same-skill compositor, not a substitute pipeline.
    import compose_foreground
    monkeypatch.setattr(compose_foreground, "run_compose", compose)
    return plan, output, document, calls


def invoke(monkeypatch, plan, output, *flags):
    monkeypatch.setattr(sys, "argv", ["render_theme_foreground.py", str(plan),
                                     "--output-dir", str(output), *flags])
    cli.main()


def test_default_keeps_producer_only(pipeline, monkeypatch):
    plan, output, _, calls = pipeline
    invoke(monkeypatch, plan, output)
    assert [row[0] for row in calls] == ["produce"]
    assert not (output / "compose_plan.json").exists()


def test_compose_uses_receipt_not_a_second_caption_or_audio_plan(pipeline, monkeypatch, capsys):
    plan, output, _, calls = pipeline
    before = plan.read_bytes()
    invoke(monkeypatch, plan, output, "--compose")
    assert [row[0] for row in calls] == ["produce", "compose"]
    command = calls[1][1]
    assert command["base"] == {"path": "frozen.mp4", "sha256": "a" * 64}
    assert command["video"] == {"width": 640, "height": 360, "fps": "24/1", "total_frames": 48}
    assert command["endcard"] == {"kind": "none"}
    assert command["foreground"]["end_frame"] == 48
    receipt = output / "producer_receipt.json"
    assert command["producer_receipt"] == {"path": str(receipt),
        "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()}
    assert "subtitle" not in command and "content" not in command
    assert Path(calls[1][2]) == output / "composed"
    assert plan.read_bytes() == before
    assert json.loads(capsys.readouterr().out)["output"]["path"].endswith("composed/foreground.mp4")


@pytest.mark.parametrize("flags", [("--compose", "--plan-only"), ("--endcard", "unused.json")])
def test_unsupported_flag_pairs_fail_before_production(pipeline, monkeypatch, flags):
    plan, output, _, calls = pipeline
    with pytest.raises(SystemExit) as error:
        invoke(monkeypatch, plan, output, *flags)
    assert error.value.code == 2
    assert not calls and not output.exists()


def test_no_tail_cannot_fill_a_reserved_tail(pipeline, monkeypatch):
    plan, output, document, calls = pipeline
    document["video"]["body_end_frame"] = 36
    plan.write_text(json.dumps(document))
    with pytest.raises((ValueError, SystemExit)):
        invoke(monkeypatch, plan, output, "--compose")
    assert not calls and not output.exists()


def test_plan_only_still_does_not_compose(pipeline, monkeypatch):
    plan, output, _, calls = pipeline
    invoke(monkeypatch, plan, output, "--plan-only")
    assert [row[0] for row in calls] == ["produce"]
    assert not (output / "compose_plan.json").exists()


def test_supplied_tail_uses_existing_validator_and_exact_descriptor(pipeline, monkeypatch):
    plan, output, document, calls = pipeline
    document["video"]["body_end_frame"] = 36
    plan.write_text(json.dumps(document))
    endcard = {"kind": "sequence", "directory": "selected_tail", "pattern": "frame_%06d.png",
               "start_frame": 36, "end_frame": 48, "ordered_sha256": "c" * 64}
    path = plan.with_name("endcard.json")
    path.write_text(json.dumps(endcard))
    original = path.read_bytes()
    import compose_foreground

    def validate(value, *, foreground_end, total_frames, width, height):
        assert value == endcard
        assert (foreground_end, total_frames, width, height) == (36, 48, 640, 360)
        return copy.deepcopy(value), []

    monkeypatch.setattr(compose_foreground, "validate_endcard", validate)
    invoke(monkeypatch, plan, output, "--compose", "--endcard", str(path))
    assert calls[1][1]["endcard"] == endcard
    assert path.read_bytes() == original


def test_invalid_tail_fails_before_browser(pipeline, monkeypatch):
    plan, output, _, calls = pipeline
    path = plan.with_name("endcard.json")
    path.write_text(json.dumps({"kind": "none", "path": "should-not-be-ignored.png"}))
    with pytest.raises((ValueError, SystemExit)):
        invoke(monkeypatch, plan, output, "--compose", "--endcard", str(path))
    assert not calls and not output.exists()


def test_failed_producer_never_invokes_composition(pipeline, monkeypatch):
    plan, output, _, calls = pipeline

    def fail(*args, **kwargs):
        raise RuntimeError("font unavailable")

    monkeypatch.setattr(cli, "run_producer", fail)
    with pytest.raises(RuntimeError, match="font unavailable"):
        invoke(monkeypatch, plan, output, "--compose")
    assert not calls and not (output / "composed").exists()


def test_composition_failure_is_not_reported_as_a_finished_video(pipeline, monkeypatch, capsys):
    plan, output, _, calls = pipeline
    import compose_foreground

    def fail(*args, **kwargs):
        raise ValueError("audio packet identity changed")

    monkeypatch.setattr(compose_foreground, "run_compose", fail)
    with pytest.raises(ValueError, match="audio packet identity"):
        invoke(monkeypatch, plan, output, "--compose")
    assert [row[0] for row in calls] == ["produce"]
    assert (output / "producer_receipt.json").exists()
    assert capsys.readouterr().out == ""


def test_missing_endcard_asset_does_not_start_the_producer(pipeline, monkeypatch):
    plan, output, document, calls = pipeline
    document["video"]["body_end_frame"] = 36
    plan.write_text(json.dumps(document))
    path = plan.with_name("endcard.json")
    path.write_text(json.dumps({"kind": "still", "path": str(plan.parent / "missing.png"),
                               "sha256": "d" * 64, "start_frame": 36, "end_frame": 48}))
    with pytest.raises((ValueError, SystemExit)):
        invoke(monkeypatch, plan, output, "--compose", "--endcard", str(path))
    assert not calls and not output.exists()
