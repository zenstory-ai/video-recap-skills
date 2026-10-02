"""Write final_qc.json after render and enforce --require-final-qc."""

from pathlib import Path

import final_qc


def _write_final_qc_reports(work_dir, final_output):
    """Write the report-only final_qc.json after render.

    final_qc.run converts ffprobe unavailability/failure into deterministic
    blockers; only unexpected schema/write errors propagate.
    """
    return final_qc.run(work_dir, final_output=final_output)


def _print_final_qc_pointer(result):
    """Surface a report-only final_qc FAIL so the final QC is not a silent
    no-op. Advisory only: it never changes the exit status."""
    summary = result["final_qc"]
    if summary.get("ok") is False:
        print(
            "[video-recap] ⚠️  最终 QC 未通过（仅报告，不阻断）: "
            f"final_qc blocker_count={summary.get('blocker_count', '?')}"
            "；详见 final_qc.json"
        )


def _require_final_qc(result, work_dir):
    """Fail closed unless the final_qc summary is a literal blocker-free pass."""
    print(f"[video-recap] 最终 QC 报告: {Path(work_dir) / 'final_qc.json'}")
    summary = result.get("final_qc") if isinstance(result, dict) else None
    blockers = summary.get("blocker_count") if isinstance(summary, dict) else None
    invalid = not isinstance(summary, dict) or summary.get("ok") is not True or \
        type(blockers) is not int or blockers != 0
    if invalid:
        raise SystemExit("严格最终 QC 未通过或摘要格式无效: final_qc")
