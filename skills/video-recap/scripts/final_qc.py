#!/usr/bin/env python3
"""Final post-render QC report (final_qc.json) for video-recap.

Local deterministic/report-only checks only: no network, no repair. Every finding is
a blocker; ``ok`` / ``blocker_count`` are what ``--require-final-qc`` and the dashboard read.
The ffprobe result is trimmed to the stream/format fields the checks use, so container
tags copied from the source (comment/purl URLs) never reach the report.
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

from lib import load_json, read_json_object

SCHEMA_VERSION = 2
FINAL_QC_ARTIFACT = "final_qc.json"
# Summarised in metadata only. video-assemble exits nonzero on a blocking assembly/visual
# QC before recap reaches final_qc, so their verdicts are recorded, not re-raised.
_COLLECT_ARTIFACTS = ("assembly_manifest.json", "assembly_qc.json", "visual_qc.json")
_SUMMARY_KEYS = ("schema_version", "verdict", "blocking", "blocking_codes")
_PROBE_STREAM_KEYS = (
    "index", "codec_type", "codec_name", "profile", "pix_fmt", "width", "height",
    "avg_frame_rate", "r_frame_rate", "fps", "frame_rate", "duration",
    "sample_rate", "channels",
)
_PROBE_FORMAT_KEYS = ("format_name", "duration", "size", "bit_rate", "nb_streams")
ProbeRunner = Callable[[Path], Mapping[str, Any]]


def _load_fixture(value: Any) -> Any:
    """A fixture is an in-memory JSON value or the path of a JSON file."""
    if isinstance(value, (Mapping, list)):
        return value
    return load_json(value)


def _resolve_in_work_dir(work_dir: Path, path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else work_dir / p


def _final_output_path(work_dir: Path, final_output: str | Path | None) -> Path | None:
    """The rendered final output: the caller's path, else assembly_manifest.final_output
    (video-assemble always writes it; output.mp4 is only the assembler's intermediate).
    None when neither exists yet, which the report surfaces as a missing final output."""
    if final_output is None:
        manifest = work_dir / "assembly_manifest.json"
        if not manifest.is_file():
            return None
        final_output = load_json(manifest)["final_output"]
    return _resolve_in_work_dir(work_dir, final_output)


def _file_metadata(path: Path | None, work_dir: Path) -> dict[str, Any]:
    if path is None:
        return {"path": "(assembly_manifest.json missing)", "exists": False, "bytes": 0}
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
        data = read_json_object(path)
        if data is None:
            meta["summary"] = {"invalid": True}
        else:
            meta["summary"] = {key: data.get(key) for key in _SUMMARY_KEYS}
    return meta


def _finding(code: str, message: str, *, evidence: Mapping[str, Any],
             next_action: str) -> dict[str, Any]:
    """One deterministic blocker; next_action is a repair hint an agent can act on."""
    return {"code": code, "message": message, "blocking": True,
            "evidence": dict(evidence), "next_action": next_action}


def _pick(mapping: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    return {key: mapping[key] for key in keys if key in mapping}


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
        return {
            "streams": [_pick(stream, _PROBE_STREAM_KEYS) for stream in streams],
            "format": _pick(format_info, _PROBE_FORMAT_KEYS),
        }, None
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


def _probe_contract_findings(probe: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    video_stream = _first_video_stream(probe)
    if video_stream is None:
        findings.append(_finding(
            "missing_video_stream",
            "final output probe metadata has no video stream",
            evidence={"streams": probe.get("streams")},
            next_action="rerender_final_output_with_video_stream",
        ))

    _duration, problem, raw = _probe_duration(probe)
    if problem is not None:
        findings.append(_finding(
            f"{problem}_duration",
            "final output probe metadata is missing a positive finite duration" if problem == "missing" else "final output probe metadata duration is not positive and finite",
            evidence={"duration": raw},
            next_action="rerender_final_output_with_valid_duration",
        ))

    if not (video_stream or {}).get("codec_name"):
        findings.append(_finding(
            "missing_codec",
            "final output probe metadata is missing a video codec",
            evidence={"video_stream": video_stream},
            next_action="rerender_final_output_with_video_codec",
        ))

    _fps, problem, raw = _probe_fps(video_stream)
    if problem is not None:
        findings.append(_finding(
            f"{problem}_fps",
            "final output probe metadata is missing a positive finite video fps" if problem == "missing" else "final output probe metadata video fps is not positive and finite",
            evidence={"fps": raw, "video_stream": video_stream},
            next_action="rerender_final_output_with_valid_fps",
        ))
    return findings


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
        "warnings": _render_warnings(root, selected),
        "auto_repair": False,
    }


def _render_warnings(work_dir: Path, final_output: Path | None) -> list[dict[str, Any]]:
    """Non-blocking render facts an agent must relay, e.g. a default subtitle burn that
    degraded to the .srt sidecar because ffmpeg lacks libass (from visual_qc.json).

    Only relayed when assembly_manifest.json says video-assemble rendered `final_output`:
    a mode that delivers without it (dub) would otherwise inherit a stale visual_qc.json
    left in the work_dir by an earlier full/cut run."""
    manifest = read_json_object(work_dir / "assembly_manifest.json") or {}
    rendered = manifest.get("final_output")
    if final_output is None or not isinstance(rendered, str) or \
            _resolve_in_work_dir(work_dir, rendered).resolve() != final_output.resolve():
        return []
    data = read_json_object(work_dir / "visual_qc.json")
    warnings = (data or {}).get("warnings")
    if not isinstance(warnings, list):
        return []
    return [dict(item) for item in warnings if isinstance(item, Mapping) and item.get("code")]


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
            "missing_final_output",
            "final output mp4 is missing",
            evidence={"final_output": final_meta},
            next_action="render_final_output",
        ))
    elif final_meta["bytes"] == 0:
        findings.append(_finding(
            "empty_final_output",
            "final output mp4 is empty",
            evidence={"final_output": final_meta},
            next_action="rerender_final_output",
        ))
    elif metadata["probe_error"] is not None:
        findings.append(_finding(
            "probe_failed",
            "ffprobe failed or was unavailable for existing non-empty final output",
            evidence=metadata["probe_error"],
            next_action="inspect_or_rerender_final_output",
        ))
    else:
        probe_findings = _probe_contract_findings(metadata["probe"])
        findings.extend(probe_findings)
        # Header probing cannot see a container-valid but media-truncated/corrupt payload.
        # A cheap tail decode catches it; skip for offline fixtures and when ffmpeg is absent
        # (decode_ok is None). Only add on a definite decode failure to avoid false-blocking.
        if probe_fixture is None and not probe_findings:
            decode_ok, decode_detail = (decode_runner or _tail_decode_check)(selected)
            if decode_ok is False:
                findings.append(_finding(
                    "undecodable_stream",
                    "final output tail failed to decode (truncated or corrupt media payload)",
                    evidence={"decode_error": decode_detail},
                    next_action="rerender_final_output",
                ))
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact": FINAL_QC_ARTIFACT,
        "ok": not findings,
        "blocker_count": len(findings),
        "finding_count": len(findings),
        "findings": findings,
        "metadata": metadata,
    }


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(work_dir: str | Path, final_output: str | Path | None = None,
        probe_fixture: Any = None, probe_runner: ProbeRunner | None = None) -> dict[str, Any]:
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    report = build_final_qc(root, final_output=final_output, probe_fixture=probe_fixture, probe_runner=probe_runner)
    _write_report(root / FINAL_QC_ARTIFACT, report)
    return {
        "work_dir": str(root),
        "written": [FINAL_QC_ARTIFACT],
        "final_qc": {"ok": report["ok"], "blocker_count": report["blocker_count"],
                     "warnings": [item["code"] for item in report["metadata"]["warnings"]]},
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write final_qc.json for a video-recap work_dir.")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--final-output", default=None)
    ap.add_argument("--probe-fixture", default=None)
    args = ap.parse_args(argv)
    summary = run(args.work_dir, final_output=args.final_output, probe_fixture=args.probe_fixture)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
