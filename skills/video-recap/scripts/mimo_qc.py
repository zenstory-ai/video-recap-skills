#!/usr/bin/env python3
"""Public API and CLI entrypoint for advisory MiMo multimodal QC."""

import qc.mimo_runner as mimo_qc_runner
from qc.mimo_client import mimo_qc_api_call

clear_report = mimo_qc_runner.clear_report


def run(work_dir, **kwargs):
    """mimo_qc_runner.run with the real MiMo transport bound at call time."""
    return mimo_qc_runner.run(work_dir, api_call=mimo_qc_api_call, **kwargs)


def main(argv=None):
    return mimo_qc_runner.main(argv, run_callable=run)


__all__ = ["clear_report", "main", "mimo_qc_api_call", "run"]

if __name__ == "__main__":
    raise SystemExit(main())
