"""video-script's review output feeds video-recap's strict pre-TTS gate.

The review runs in-process with a stubbed model call so the gate sees a real
narration_review.json. video-script's modules (its own `lib` included) are loaded only
for the duration of each test and then removed, so they never shadow video-recap's
modules for the rest of the group.
"""

import json

import pytest

import recap_review
import review_runner as review_runner_module


@pytest.fixture
def review_runner():
    return review_runner_module


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
