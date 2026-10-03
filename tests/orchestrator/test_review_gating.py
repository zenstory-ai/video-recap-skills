"""video-script's review output feeds video-recap's strict pre-TTS gate.

The review runs in-process with a stubbed model call so the gate sees a real
narration_review.json. video-script's modules (its own `lib` included) are loaded only
for the duration of each test and then removed, so they never shadow video-recap's
modules for the rest of the group.
"""

import importlib
import json
import sys
from pathlib import Path

import pytest

import recap_review

SCRIPT_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "video-script" / "scripts"


def _is_script_module(module):
    origin = getattr(module, "__file__", None)
    return bool(origin) and Path(origin).resolve().is_relative_to(SCRIPT_SCRIPTS)


@pytest.fixture
def review_runner(monkeypatch):
    names = {path.stem for path in SCRIPT_SCRIPTS.glob("*.py")}
    shadowed = {name: sys.modules.pop(name) for name in names if name in sys.modules}
    monkeypatch.syspath_prepend(str(SCRIPT_SCRIPTS))
    try:
        yield importlib.import_module("review_runner")
    finally:
        for name, module in list(sys.modules.items()):
            if _is_script_module(module):
                del sys.modules[name]
        sys.modules.update(shadowed)


def _seed_work_dir(work_dir):
    (work_dir / "narration.json").write_text(
        json.dumps([{"start": 1, "end": 4, "narration": "测试。"}]), encoding="utf-8"
    )
    (work_dir / "vlm_analysis.json").write_text("[]", encoding="utf-8")
    (work_dir / "asr_result.json").write_text("[]", encoding="utf-8")


def _run_review_with_finding(review_runner, monkeypatch, work_dir, finding):
    _seed_work_dir(work_dir)
    payloads = []

    def fake_api(payload):
        payloads.append(payload)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "verdict": "REVISE",
                                "summary": "s",
                                "findings": [finding],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(review_runner, "api_call", fake_api)
    review_runner.review_narration(work_dir)
    return payloads


def test_review_payload_is_deterministic(review_runner, monkeypatch, tmp_path):
    """Q3: re-running review on identical input must be deterministic — the payload
    pins temperature to 0 and carries a fixed integer seed."""
    payloads = _run_review_with_finding(
        review_runner,
        monkeypatch,
        tmp_path,
        {
            "segment": 0,
            "severity": "warning",
            "category": "weak_hook",
            "issue": "i",
            "fix": "f",
        },
    )
    payload = payloads[0]
    assert payload["temperature"] == 0
    assert isinstance(payload["seed"], int)


def test_craft_error_is_clamped_and_does_not_gate(review_runner, monkeypatch, tmp_path):
    """Q4: a craft finding (weak_hook) marked error is clamped to warning, so it does
    NOT count as a gating error in recap_review.review_result_status."""
    _run_review_with_finding(
        review_runner,
        monkeypatch,
        tmp_path,
        {
            "segment": 0,
            "severity": "error",
            "category": "weak_hook",
            "issue": "开头平淡",
            "fix": "加悬念",
        },
    )

    review_json = json.loads(
        (tmp_path / "narration_review.json").read_text(encoding="utf-8")
    )
    assert review_json["findings"][0]["severity"] == "warning"

    status = recap_review.review_result_status(tmp_path)
    assert status["errors"] == 0
    assert status["ok"] is True


def test_hallucination_error_still_gates(review_runner, monkeypatch, tmp_path):
    """Q4 counter-case: a factual finding (hallucination) marked error keeps its
    severity and DOES gate strict mode."""
    _run_review_with_finding(
        review_runner,
        monkeypatch,
        tmp_path,
        {
            "segment": 0,
            "severity": "error",
            "category": "hallucination",
            "issue": "凭空虚构",
            "fix": "删掉",
        },
    )

    review_json = json.loads(
        (tmp_path / "narration_review.json").read_text(encoding="utf-8")
    )
    assert review_json["findings"][0]["severity"] == "error"

    status = recap_review.review_result_status(tmp_path)
    assert status["errors"] == 1
    assert status["ok"] is False
