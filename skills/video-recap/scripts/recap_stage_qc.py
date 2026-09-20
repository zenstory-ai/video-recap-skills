"""Write shift-left, MiMo, and final QC stage reports."""

import json
from pathlib import Path

import final_qc
import mimo_qc
import qc_contract

ASSEMBLY_MANIFEST = "assembly_manifest.json"
PREFLIGHT_QC = "preflight_qc.json"


def _load_preflight_stage_reports(work_dir):
    path = Path(work_dir) / PREFLIGHT_QC
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["metadata"]["stages"]


def _write_shift_left_stage_qc(work_dir, stage, metadata, findings=None):
    """Write/roll up local shift-left QC for one pipeline stage.

    This is a local contract artifact only: no MiMo/deep eval calls, no repair, and no
    credential persistence (qc_contract redacts every report it builds).
    """
    stage_report = qc_contract.build_report(
        artifact=PREFLIGHT_QC, stage=stage, findings=findings, metadata=metadata
    )
    stage_reports = _load_preflight_stage_reports(work_dir)
    stage_reports[stage] = stage_report
    top_metadata = dict(stage_report["metadata"])
    top_metadata["latest_stage"] = stage
    top_metadata["stages"] = stage_reports
    top_report = qc_contract.build_report(
        artifact=PREFLIGHT_QC,
        stage=stage,
        findings=[f for report in stage_reports.values() for f in report["findings"]],
        metadata=top_metadata,
    )
    (Path(work_dir) / PREFLIGHT_QC).write_text(
        json.dumps(top_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return top_report


def _tts_qc_metadata(work_dir):
    work_dir = Path(work_dir)
    metadata = {}
    tts_meta = work_dir / "tts_meta.json"
    if tts_meta.exists():
        metadata["tts_meta"] = json.loads(tts_meta.read_text(encoding="utf-8"))
    tts_dir = work_dir / "tts_segments"
    if tts_dir.is_dir():
        metadata["tts_segments"] = [
            p.relative_to(work_dir).as_posix() for p in sorted(tts_dir.iterdir()) if p.is_file()
        ]
    return metadata


def _post_render_qc_metadata(work_dir, final_output):
    work_dir = Path(work_dir)
    metadata = {"final_output": str(final_output)}
    manifest = work_dir / ASSEMBLY_MANIFEST
    if manifest.exists():
        metadata["assembly_manifest"] = json.loads(
            manifest.read_text(encoding="utf-8")
        )
    return metadata


def _write_final_qc_reports(work_dir, final_output):
    """Write report-only final QC artifacts after render.

    final_qc.run converts ffprobe unavailability/failure into deterministic
    blockers; only unexpected schema/write errors propagate.
    """
    return final_qc.run(work_dir, final_output=final_output)


def _print_final_qc_pointer(result):
    """Surface a report-only final_qc/golden_eval FAIL so the shift-left QC is
    not a silent no-op. Advisory only: it never changes the exit status."""
    problems = [
        f"{key} blocker_count={result[key].get('blocker_count', '?')}"
        for key in ("final_qc", "golden_eval")
        if result[key].get("ok") is False
    ]
    if problems:
        print(
            "[video-recap] ⚠️  最终 QC 未通过（仅报告，不阻断）: "
            + "; ".join(problems)
            + "；详见 final_qc.json / golden_eval.json"
        )


def _require_final_qc(result, work_dir):
    """Fail closed unless both final summaries are literal blocker-free passes."""
    paths = [Path(work_dir) / name for name in ("final_qc.json", "golden_eval.json")]
    print("[video-recap] 最终 QC 报告: " + "; ".join(map(str, paths)))
    invalid = []
    for name in ("final_qc", "golden_eval"):
        summary = result.get(name) if isinstance(result, dict) else None
        blockers = summary.get("blocker_count") if isinstance(summary, dict) else None
        if not isinstance(summary, dict) or summary.get("ok") is not True or \
                type(blockers) is not int or blockers != 0:
            invalid.append(name)
    if invalid:
        raise SystemExit(
            "严格最终 QC 未通过或摘要格式无效: " + ", ".join(invalid)
        )


def _mimo_qc_stage_enabled(args, stage):
    return (
        args.mimo_qc == "both"
        or (args.mimo_qc == "pre-assemble" and stage == "pre_assemble")
        or (args.mimo_qc == "post-render" and stage == "post_render")
    )


def _prepare_mimo_qc(work_dir, args):
    """Remove an old advisory artifact when this run has MiMo QC disabled."""
    if args.mimo_qc == "off":
        mimo_qc.clear_report(work_dir)


def _print_mimo_qc_pointer(result, stage):
    report = result["report"]
    metadata = report["metadata"]
    status = metadata["status"]
    stage_findings = [f for f in report["findings"] if f["stage"] == stage]
    if status in {"failed", "unavailable"}:
        print(
            f"[video-recap] ⚠ MiMo QC {stage}: {status} ({metadata['error']})；建议性检查不可用，继续流水线"
        )
        return
    print(
        f"[video-recap] ℹ MiMo QC {stage}: {status}, {len(stage_findings)} 条建议；详见 {result['path']}"
    )
    for finding in stage_findings[:5]:
        print(f"[video-recap]   - {finding['message']}")


def _run_mimo_qc_stage(work_dir, args, stage, *, final_output=None):
    """Run one selected advisory stage and never propagate a failure."""
    if not _mimo_qc_stage_enabled(args, stage):
        return None
    try:
        result = mimo_qc.run(
            work_dir,
            stage=stage,
            live=True,
            refresh=args.mimo_qc_refresh,
            final_output=final_output,
        )
    except Exception as exc:
        print(
            f"[video-recap] ⚠ MiMo QC {stage}: {type(exc).__name__}；"
            "建议性检查失败，继续流水线"
        )
        return None
    _print_mimo_qc_pointer(result, stage)
    return result
