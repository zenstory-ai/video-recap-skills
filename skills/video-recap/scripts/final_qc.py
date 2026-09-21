#!/usr/bin/env python3
"""Final post-render QC and golden-eval reports for video-recap.

Local deterministic/report-only checks only: no network, no repair, no secrets.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any
from collections.abc import Callable, Mapping, Sequence

import qc_contract
from lib import load_json

FINAL_QC_ARTIFACT = "final_qc.json"
GOLDEN_EVAL_ARTIFACT = "golden_eval.json"
POST_RENDER_STAGE = "post_render"
GOLDEN_STAGE = "golden"
_COLLECT_ARTIFACTS = (
    "assembly_manifest.json",
    "assembly_qc.json",
    "visual_qc.json",
    "preflight_qc.json",
    "mimo_qc.json",
)
# video-assemble QC artifacts: {"verdict", "blocking", "blocking_codes": [...]}.
_UPSTREAM_QC_ARTIFACTS = ("assembly_qc.json", "visual_qc.json")
ProbeRunner = Callable[[Path], Mapping[str, Any]]


def _load_fixture(value: Any) -> Any:
    """A fixture is an in-memory JSON value or the path of a JSON file."""
    if isinstance(value, (Mapping, list)):
        return value
    return load_json(value)


def _resolve_in_work_dir(work_dir: Path, path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else work_dir / p


def _read_json_mapping(path: Path) -> Mapping[str, Any] | None:
    try:
        data = load_json(path)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, Mapping) else None


def _final_output_path(work_dir: Path, final_output: str | Path | None) -> Path:
    """The rendered final output: the caller's path, else assembly_manifest.final_output
    (video-assemble always writes it; output.mp4 is only the assembler's intermediate)."""
    if final_output is None:
        final_output = load_json(work_dir / "assembly_manifest.json")["final_output"]
    return _resolve_in_work_dir(work_dir, final_output)


def _file_metadata(path: Path, work_dir: Path) -> dict[str, Any]:
    try:
        display = path.relative_to(work_dir).as_posix()
    except ValueError:  # final outputs normally live next to work_dir, not inside it
        display = str(path)
    exists = path.is_file()
    return {
        "path": display,
        "exists": exists,
        "bytes": path.stat().st_size if exists else 0,
    }


def _artifact_summary(work_dir: Path, name: str) -> dict[str, Any]:
    path = work_dir / name
    meta = _file_metadata(path, work_dir)
    if meta["exists"]:
        data = _read_json_mapping(path)
        if data is None:
            meta["summary"] = {"invalid": True}
        else:
            meta["summary"] = {
                key: data.get(key)
                for key in ("schema_version", "artifact", "stage", "ok", "blocker_count", "finding_count")
            }
    return meta


def _finding(*, finding_id: str, code: str, message: str, category: str = "schema_invalid",
             stage: str = POST_RENDER_STAGE, source: Mapping[str, Any] | None = None,
             evidence: Mapping[str, Any] | None = None,
             next_action: str = "manual_review") -> dict[str, Any]:
    return qc_contract.build_finding(
        finding_id=finding_id,
        stage=stage,
        severity="blocker",
        confidence="objective",
        sample_policy={"type": "deterministic"},
        category=category,
        code=code,
        message=message,
        deterministic=True,
        blocking=True,
        source=source,
        evidence=evidence,
        next_action=next_action,
        model_used="local_deterministic_final_qc_v1",
    )


def _run_ffprobe(path: Path) -> Mapping[str, Any]:
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe unavailable")
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout or "ffprobe failed").strip())
    try:
        return json.loads(res.stdout)
    except ValueError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON: {exc}") from exc


def _tail_decode_check(path: Path) -> tuple[bool | None, str | None]:
    """Cheaply verify the final ~2s actually decodes. Returns (ok, detail).

    Header probing (ffprobe -show_format/-show_streams) passes a container-valid but
    media-truncated/corrupt payload (moov intact + mdat cut — realistic with +faststart on
    disk-full or partial upload). Decoding the tail catches it. Returns (None, ...) when ffmpeg
    is unavailable or the probe cannot run, so the caller skips rather than false-blocks.
    """
    if shutil.which("ffmpeg") is None:
        return None, "ffmpeg unavailable"
    try:
        res = subprocess.run(
            ["ffmpeg", "-v", "error", "-xerror", "-sseof", "-2", "-i", str(path), "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"decode probe could not run: {exc}"
    if res.returncode != 0:
        return False, (res.stderr or "tail decode failed").strip()[:500]
    return True, None


def _probe_metadata(path: Path, *, probe_fixture: Any = None, probe_runner: ProbeRunner | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (probe, error); an ffprobe failure on existing media becomes a deterministic blocker."""
    try:
        raw = _load_fixture(probe_fixture) if probe_fixture is not None else (probe_runner or _run_ffprobe)(path)
        if not isinstance(raw, Mapping):
            raise TypeError("probe metadata must be a JSON object")
        streams = raw.get("streams", [])
        format_info = raw.get("format", {})
        if not isinstance(streams, list) or any(not isinstance(stream, Mapping) for stream in streams):
            raise TypeError("probe metadata streams must be an array of objects")
        if not isinstance(format_info, Mapping):
            raise TypeError("probe metadata format must be an object")
        normalized = dict(raw)
        normalized["streams"] = [dict(stream) for stream in streams]
        normalized["format"] = dict(format_info)
        return normalized, None
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, TypeError) as exc:
        return None, {"code": "probe_failed", "message": str(exc) or "ffprobe failed"}


def _first_video_stream(probe: Mapping[str, Any]) -> Mapping[str, Any] | None:
    return next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)


def _positive_finite(value: Any) -> float | None:
    """Parse an ffprobe number or rational ("30000/1001"); None unless positive and finite."""
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            number = float(numerator) / float(denominator)
        else:
            number = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _first_positive(candidates: Sequence[Any]) -> tuple[float | None, str | None, Any]:
    """(parsed, problem, raw): the first positive finite candidate wins; otherwise problem is
    'missing' (no candidate at all) or 'invalid' (with the first raw candidate)."""
    seen = [value for value in candidates if value not in (None, "")]
    for raw in seen:
        parsed = _positive_finite(raw)
        if parsed is not None:
            return parsed, None, raw
    if not seen:
        return None, "missing", None
    return None, "invalid", seen[0]


def _probe_duration(probe: Mapping[str, Any]) -> tuple[float | None, str | None, Any]:
    return _first_positive([probe.get("format", {}).get("duration")])


def _probe_fps(video_stream: Mapping[str, Any] | None) -> tuple[float | None, str | None, Any]:
    if video_stream is None:
        return None, "missing", None
    return _first_positive([
        video_stream.get(key)
        for key in ("avg_frame_rate", "r_frame_rate", "fps", "frame_rate")
    ])


def _probe_contract_findings(probe: Mapping[str, Any], *, final_meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    source = {"artifact": final_meta["path"]}
    video_stream = _first_video_stream(probe)
    if video_stream is None:
        findings.append(_finding(
            finding_id="final-qc-missing-video-stream",
            code="missing_video_stream",
            message="final output probe metadata has no video stream",
            category="stream",
            source=source,
            evidence={"streams": probe.get("streams")},
            next_action="rerender_final_output_with_video_stream",
        ))

    _duration, problem, raw = _probe_duration(probe)
    if problem is not None:
        findings.append(_finding(
            finding_id=f"final-qc-{problem}-duration",
            code=f"{problem}_duration",
            message="final output probe metadata is missing a positive finite duration" if problem == "missing" else "final output probe metadata duration is not positive and finite",
            category="duration",
            source=source,
            evidence={"duration": raw},
            next_action="rerender_final_output_with_valid_duration",
        ))

    if not (video_stream or {}).get("codec_name"):
        findings.append(_finding(
            finding_id="final-qc-missing-codec",
            code="missing_codec",
            message="final output probe metadata is missing a video codec",
            category="stream",
            source=source,
            evidence={"video_stream": video_stream},
            next_action="rerender_final_output_with_video_codec",
        ))

    _fps, problem, raw = _probe_fps(video_stream)
    if problem is not None:
        findings.append(_finding(
            finding_id=f"final-qc-{problem}-fps",
            code=f"{problem}_fps",
            message="final output probe metadata is missing a positive finite video fps" if problem == "missing" else "final output probe metadata video fps is not positive and finite",
            category="stream",
            source=source,
            evidence={"fps": raw, "video_stream": video_stream},
            next_action="rerender_final_output_with_valid_fps",
        ))
    return findings


def _upstream_blockers(work_dir: Path, artifact_name: str) -> list[dict[str, Any]]:
    """Roll a blocking video-assemble QC artifact into one final blocker per blocking code."""
    path = work_dir / artifact_name
    if not path.exists():
        return []
    data = _read_json_mapping(path)
    if data is None:  # unreadable upstream QC is itself a deterministic blocker
        return [_finding(
            finding_id=f"final-qc-invalid-upstream-{artifact_name}",
            code=f"upstream_{artifact_name.replace('.', '_')}_schema_invalid",
            message=f"{artifact_name} is not a valid deterministic QC report",
            source={"artifact": artifact_name},
            evidence={"schema_invalid": True},
            next_action="regenerate_upstream_qc",
        )]
    return [
        _finding(
            finding_id=f"final-qc-upstream-{artifact_name}-{idx}",
            code=f"upstream_{artifact_name.replace('.', '_')}_{code}",
            message=f"{artifact_name} reported {code}",
            source={"artifact": artifact_name},
            evidence={"upstream_code": code, "upstream_verdict": data["verdict"]},
            next_action="fix_upstream_qc_blocker",
        )
        for idx, code in enumerate(data["blocking_codes"])
    ]


def collect_metadata(work_dir: str | Path, *, final_output: str | Path | None = None,
                     probe_fixture: Any = None, probe_runner: ProbeRunner | None = None) -> dict[str, Any]:
    root = Path(work_dir)
    selected = _final_output_path(root, final_output)
    probe = probe_error = None
    final_meta = _file_metadata(selected, root)
    if final_meta["exists"] and final_meta["bytes"] > 0:
        probe, probe_error = _probe_metadata(selected, probe_fixture=probe_fixture, probe_runner=probe_runner)
    return {
        "work_dir": str(root),
        "final_output": final_meta,
        "artifacts": {name: _artifact_summary(root, name) for name in _COLLECT_ARTIFACTS},
        "probe": probe,
        "probe_error": probe_error,
        # mimo_qc.json is advisory metadata only and is not rolled into final blockers.
        "auto_repair": False,
    }


def build_final_qc(work_dir: str | Path, final_output: str | Path | None = None,
                   probe_fixture: Any = None, probe_runner: ProbeRunner | None = None,
                   decode_runner: Callable[[Path], tuple[bool | None, str | None]] | None = None) -> dict[str, Any]:
    root = Path(work_dir)
    selected = _final_output_path(root, final_output)
    metadata = collect_metadata(root, final_output=final_output, probe_fixture=probe_fixture, probe_runner=probe_runner)
    final_meta = metadata["final_output"]
    findings: list[dict[str, Any]] = []
    if not final_meta["exists"]:
        findings.append(_finding(
            finding_id="final-qc-missing-final-output",
            code="missing_final_output",
            message="final output mp4 is missing",
            category="missing_artifact",
            source={"artifact": str(final_output) if final_output else "final_output"},
            evidence={"final_output": final_meta},
            next_action="render_final_output",
        ))
    elif final_meta["bytes"] == 0:
        findings.append(_finding(
            finding_id="final-qc-empty-final-output",
            code="empty_final_output",
            message="final output mp4 is empty",
            category="missing_artifact",
            source={"artifact": final_meta["path"]},
            evidence={"final_output": final_meta},
            next_action="rerender_final_output",
        ))
    elif metadata["probe_error"] is not None:
        findings.append(_finding(
            finding_id="final-qc-probe-failed",
            code="probe_failed",
            message="ffprobe failed or was unavailable for existing non-empty final output",
            category="stream",
            source={"artifact": final_meta["path"]},
            evidence=metadata["probe_error"],
            next_action="inspect_or_rerender_final_output",
        ))
    else:
        probe_findings = _probe_contract_findings(metadata["probe"], final_meta=final_meta)
        findings.extend(probe_findings)
        # Header probing cannot see a container-valid but media-truncated/corrupt payload.
        # A cheap tail decode catches it; skip for offline fixtures and when ffmpeg is absent
        # (decode_ok is None). Only add on a definite decode failure to avoid false-blocking.
        if probe_fixture is None and not probe_findings:
            decode_ok, decode_detail = (decode_runner or _tail_decode_check)(selected)
            if decode_ok is False:
                findings.append(_finding(
                    finding_id="final-qc-undecodable-stream",
                    code="undecodable_stream",
                    message="final output tail failed to decode (truncated or corrupt media payload)",
                    category="stream",
                    source={"artifact": final_meta["path"]},
                    evidence={"decode_error": decode_detail},
                            next_action="rerender_final_output",
                ))
    for name in _UPSTREAM_QC_ARTIFACTS:
        findings.extend(_upstream_blockers(root, name))
    return qc_contract.build_report(
        artifact=FINAL_QC_ARTIFACT,
        stage=POST_RENDER_STAGE,
        findings=findings,
        metadata=metadata,
    )


def _load_or_build_final_qc(work_dir: Path, final_qc_report: Mapping[str, Any] | None) -> dict[str, Any]:
    if final_qc_report is not None:
        return dict(final_qc_report)
    path = work_dir / FINAL_QC_ARTIFACT
    return load_json(path) if path.exists() else build_final_qc(work_dir)


def build_golden_eval(work_dir: str | Path, final_qc_report: Mapping[str, Any] | None = None,
                      golden_fixture: Any = None) -> dict[str, Any]:
    root = Path(work_dir)
    final_report = _load_or_build_final_qc(root, final_qc_report)
    fixture = _load_fixture(golden_fixture) if golden_fixture is not None else {}
    metadata = {
        "work_dir": str(root),
        "fixture": fixture,
        "final_qc": {key: final_report[key] for key in ("ok", "blocker_count", "artifact", "stage")},
        "auto_repair": False,
    }
    findings: list[dict[str, Any]] = []
    expected_ok = fixture.get("expected_final_qc_ok", True)
    if final_report["ok"] != expected_ok:
        findings.append(_finding(
            finding_id="golden-final-qc-ok-mismatch",
            stage=GOLDEN_STAGE,
            code="expected_final_qc_ok_mismatch",
            message="final_qc ok state does not match golden expectation",
            category="schema_invalid",
            source={"artifact": FINAL_QC_ARTIFACT},
            evidence={"expected": expected_ok, "actual": final_report["ok"]},
            next_action="fix_final_qc_blockers",
        ))
    final_meta = final_report["metadata"]["final_output"]
    probe = final_report["metadata"]["probe"]
    duration = _probe_duration(probe)[0] if probe else None
    video_stream = _first_video_stream(probe) if probe else None
    codec = video_stream.get("codec_name") if video_stream else None
    min_duration = fixture.get("min_duration")
    if min_duration is not None and (duration is None or duration < min_duration):
        findings.append(_finding(
            finding_id="golden-min-duration-mismatch",
            stage=GOLDEN_STAGE,
            code="min_duration_mismatch",
            message="final output duration is below golden minimum",
            category="duration",
            source={"artifact": final_meta["path"]},
            evidence={"expected_min_duration": min_duration, "actual_duration": duration},
            next_action="adjust_render_duration",
        ))
    max_duration = fixture.get("max_duration")
    if max_duration is not None and (duration is None or duration > max_duration):
        findings.append(_finding(
            finding_id="golden-max-duration-mismatch",
            stage=GOLDEN_STAGE,
            code="max_duration_mismatch",
            message="final output duration is above golden maximum",
            category="duration",
            source={"artifact": final_meta["path"]},
            evidence={"expected_max_duration": max_duration, "actual_duration": duration},
            next_action="adjust_render_duration",
        ))
    expected_codec = fixture.get("expected_codec")
    if expected_codec is not None and codec != expected_codec:
        findings.append(_finding(
            finding_id="golden-codec-mismatch",
            stage=GOLDEN_STAGE,
            code="codec_mismatch",
            message="final output video codec does not match golden expectation",
            category="stream",
            source={"artifact": final_meta["path"]},
            evidence={"expected_codec": expected_codec, "actual_codec": codec},
            next_action="adjust_render_codec",
        ))
    for idx, name in enumerate(fixture.get("required_artifacts", [])):
        artifact_path = root / name
        if not artifact_path.is_file() or artifact_path.stat().st_size == 0:
            findings.append(_finding(
                finding_id=f"golden-required-artifact-missing-{idx}",
                stage=GOLDEN_STAGE,
                code="required_artifact_missing",
                message="golden fixture requires an artifact that is missing or empty",
                category="missing_artifact",
                source={"artifact": name},
                evidence={"required_artifact": name},
                next_action="produce_required_artifact",
            ))
    metadata["observed"] = {"duration": duration, "codec": codec, "final_output": final_meta}
    return qc_contract.build_report(
        artifact=GOLDEN_EVAL_ARTIFACT,
        stage=GOLDEN_STAGE,
        findings=findings,
        metadata=metadata,
    )


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(work_dir: str | Path, final_output: str | Path | None = None,
        probe_fixture: Any = None, golden_fixture: Any = None,
        probe_runner: ProbeRunner | None = None, only: str = "all") -> dict[str, Any]:
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    mode = {"final": "final_qc", "golden": "golden_eval"}.get(only, only)
    if mode not in {"all", "final_qc", "golden_eval"}:
        raise ValueError("only must be one of all, final_qc, golden_eval, final, golden")
    result: dict[str, Any] = {"work_dir": str(root), "written": []}
    final_report: dict[str, Any] | None = None
    if mode in {"all", "final_qc"}:
        final_report = build_final_qc(root, final_output=final_output, probe_fixture=probe_fixture, probe_runner=probe_runner)
        _write_report(root / FINAL_QC_ARTIFACT, final_report)
        result["final_qc"] = {"ok": final_report["ok"], "blocker_count": final_report["blocker_count"]}
        result["written"].append(FINAL_QC_ARTIFACT)
    if mode in {"all", "golden_eval"}:
        golden_report = build_golden_eval(root, final_qc_report=final_report, golden_fixture=golden_fixture)
        _write_report(root / GOLDEN_EVAL_ARTIFACT, golden_report)
        result["golden_eval"] = {"ok": golden_report["ok"], "blocker_count": golden_report["blocker_count"]}
        result["written"].append(GOLDEN_EVAL_ARTIFACT)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write final_qc.json and golden_eval.json for a video-recap work_dir.")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--final-output", default=None)
    ap.add_argument("--probe-fixture", default=None)
    ap.add_argument("--golden-fixture", default=None)
    ap.add_argument("--only", choices=["all", "final_qc", "golden_eval", "final", "golden"], default="all")
    args = ap.parse_args(argv)
    summary = run(
        args.work_dir,
        final_output=args.final_output,
        probe_fixture=args.probe_fixture,
        golden_fixture=args.golden_fixture,
        only=args.only,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
