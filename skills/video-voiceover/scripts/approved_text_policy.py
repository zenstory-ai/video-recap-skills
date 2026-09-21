"""Fail-closed approved narration policy and current-metadata lifecycle helpers."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


LEGACY_TEXT_POLICY = "legacy-auto-truncate-v1"
PRESERVE_APPROVED_TEXT_POLICY = "preserve-approved-text-v1"


class ApprovedTextPolicyError(RuntimeError):
    def __init__(self, message, evidence):
        self.evidence = evidence
        super().__init__(
            f"{message}: " + json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        )


class ApprovedTextDurationError(ApprovedTextPolicyError):
    """Approved narration cannot fit its authored window without changing text."""

    def __init__(self, evidence):
        super().__init__(
            "批准旁白无法在不删改文本的前提下安全放入时间窗；请扩大窗口或由作者修订文本",
            evidence,
        )


def policy_name(preserve_approved_text):
    if preserve_approved_text:
        return PRESERVE_APPROVED_TEXT_POLICY
    return LEGACY_TEXT_POLICY


def validate_required_texts(narration, clean_text, preserve_approved_text):
    """Reject authored segments whose provider-cleaned spoken text is empty."""
    if not preserve_approved_text:
        return
    for index, segment in enumerate(narration):
        spoken_text = clean_text(segment["narration"])
        if spoken_text:
            continue
        evidence = {
            "failure_kind": "approved_text_empty_after_cleanup",
            "policy": PRESERVE_APPROVED_TEXT_POLICY,
            "required": True,
            "index": index,
            "segment": index + 1,
            "authored_text": segment["narration"],
            "spoken_text": spoken_text,
            "window_start": segment["start"],
            "window_end": segment["end"],
        }
        raise ApprovedTextPolicyError(
            "批准稿必需段经显式格式/舞台提示清理后没有可合成文本；请修订或删除该作者段",
            evidence,
        )


def enforce_duration(index, segment, authored_text, spoken_text, audio_duration,
                     available_duration, max_raw_duration, preserve_approved_text,
                     default_pause_ms):
    if not preserve_approved_text or audio_duration <= max_raw_duration:
        return
    evidence = {
        "failure_kind": "approved_text_duration_conflict",
        "policy": PRESERVE_APPROVED_TEXT_POLICY,
        "index": index,
        "segment": index + 1,
        "authored_text": authored_text,
        "spoken_text": spoken_text,
        "audio_duration": round(audio_duration, 6),
        "window_start": segment["start"],
        "window_end": segment["end"],
        "window_duration": round(segment["end"] - segment["start"], 6),
        "pause_after_ms": segment.get("pause_after_ms", default_pause_ms),
        "available_duration": round(available_duration, 6),
        "max_raw_duration": round(max_raw_duration, 6),
    }
    raise ApprovedTextDurationError(evidence)


def archive_current_meta(meta_path):
    """Atomically move a current meta file into timestamp-named history."""
    path = Path(meta_path)
    if not path.is_file():
        return None
    history_dir = path.parent / "tts_meta.history"
    history_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    archived = history_dir / f"{stamp}.json"
    counter = 0
    while archived.exists():
        counter += 1
        archived = history_dir / f"{stamp}-{counter}.json"
    os.replace(path, archived)
    return archived


def write_json_atomically(path, payload):
    """Replace JSON only after a complete same-directory write and fsync."""
    destination = Path(path)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.stem}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
