#!/usr/bin/env python3
"""Public API and CLI entrypoint for advisory MiMo multimodal QC."""

import mimo_qc_report
import mimo_qc_runner
from mimo_qc_evidence import collect_evidence, safe_mimo_config
from mimo_qc_observations import normalize_observations
from mimo_qc_payload import build_payload
from mimo_qc_client import mimo_qc_api_call

sample_video_frames = mimo_qc_report.sample_video_frames
write_report = mimo_qc_report.write_report
clear_report = mimo_qc_runner.clear_report


def build_report(work_dir, **kwargs):
    """mimo_qc_report.build_report with the real MiMo transport bound at call time."""
    return mimo_qc_report.build_report(work_dir, api_call=mimo_qc_api_call, **kwargs)


def run(work_dir, **kwargs):
    """mimo_qc_runner.run with the real MiMo transport bound at call time."""
    return mimo_qc_runner.run(work_dir, api_call=mimo_qc_api_call, **kwargs)


def main(argv=None):
    return mimo_qc_runner.main(argv, run_callable=run)

__all__ = [
    "build_payload",
    "build_report",
    "clear_report",
    "collect_evidence",
    "main",
    "mimo_qc_api_call",
    "normalize_observations",
    "run",
    "sample_video_frames",
    "safe_mimo_config",
    "write_report",
]

if __name__ == "__main__":
    raise SystemExit(main())
