"""Write shift-left and final QC stage reports."""

import json
from pathlib import Path

import final_qc
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

    This is a local contract artifact only: no model calls, no repair, and no
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
    """Called after video-voiceover exited 0, which always writes tts_meta.json."""
    work_dir = Path(work_dir)
    metadata = {"tts_meta": json.loads((work_dir / "tts_meta.json").read_text(encoding="utf-8"))}
    tts_dir = work_dir / "tts_segments"
    if tts_dir.is_dir():
        metadata["tts_segments"] = [
            p.relative_to(work_dir).as_posix() for p in sorted(tts_dir.iterdir()) if p.is_file()
        ]
    return metadata


def _post_render_qc_metadata(work_dir, final_output):
    """Called after video-assemble exited 0, which always writes assembly_manifest.json."""
    manifest = Path(work_dir) / ASSEMBLY_MANIFEST
    return {
        "final_output": str(final_output),
        "assembly_manifest": json.loads(manifest.read_text(encoding="utf-8")),
    }


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
