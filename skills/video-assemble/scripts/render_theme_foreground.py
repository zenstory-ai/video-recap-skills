#!/usr/bin/env python3
"""Render a validated finite-role theme foreground into an exact RGBA PNG sequence."""

import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from theme_foreground import run_producer


def _endcard_for_composition(plan_path, endcard_path):
    # Reuse the compositor's one tail contract before spending time capturing fonts.
    from compose_foreground import validate_endcard

    video = json.loads(Path(plan_path).read_text(encoding="utf-8"))["video"]
    endcard = (json.loads(Path(endcard_path).read_text(encoding="utf-8"))
               if endcard_path else {"kind": "none"})
    normalized, _ = validate_endcard(
        endcard, foreground_end=video["body_end_frame"], total_frames=video["total_frames"],
        width=video["width"], height=video["height"],
    )
    return normalized


def _compose_produced_foreground(receipt, output_dir, endcard):
    from compose_foreground import run_compose

    directory = Path(output_dir).resolve()
    receipt_path = directory / "producer_receipt.json"
    video = receipt["inputs"]["video"]
    fps = Fraction(video["fps"])
    plan = {
        "artifact": "foreground_compose_plan", "schema_version": 1,
        "base": receipt["inputs"]["base"],
        "video": {"width": video["width"], "height": video["height"],
                  "fps": f"{fps.numerator}/{fps.denominator}", "total_frames": video["total_frames"]},
        "foreground": receipt["foreground"], "endcard": endcard,
        "producer_receipt": {"path": str(receipt_path),
                             "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest()},
    }
    plan_path = directory / "compose_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run_compose(plan_path, directory / "composed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--compose", action="store_true",
                        help="also compose the produced foreground over the locked base; no cut or TTS")
    parser.add_argument("--endcard",
                        help="existing compositor endcard descriptor JSON; required for a reserved tail")
    parser.add_argument("--node-executable", default="node")
    parser.add_argument("--browser-executable",
                        help="Chrome/Chromium capture binary; defaults to THEME_FOREGROUND_BROWSER "
                             "or the first Chrome/Chromium found on this machine")
    args = parser.parse_args()
    if args.endcard and not args.compose:
        parser.error("--endcard requires --compose")
    if args.compose and args.plan_only:
        parser.error("--compose cannot be combined with --plan-only; preflight the producer separately")
    endcard = _endcard_for_composition(args.plan, args.endcard) if args.compose else None
    result = run_producer(args.plan, args.output_dir, plan_only=args.plan_only,
                          node_executable=args.node_executable,
                          browser_executable=args.browser_executable)
    if args.compose:
        result = _compose_produced_foreground(result, args.output_dir, endcard)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
