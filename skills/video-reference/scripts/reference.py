#!/usr/bin/env python3
"""On-demand production reference from a finished video: measure → (agent labels) → check → export.

    python3 scripts/reference.py measure <video> --work-dir U [--scene-threshold 10] [--no-scale]
    python3 scripts/reference.py check   --work-dir U [--json]
    python3 scripts/reference.py export  --work-dir U --out <dir>/production_reference.json

Exit codes: 0 ok, 1 errors found (nothing exported), 2 bad input or ffmpeg failure.
"""
import argparse
import json
import sys

from reference_check import run_check, run_export
from reference_measure import DEFAULT_SCENE_THRESHOLD, measure

__all__ = ["main"]


def _print_report(report, as_json):
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if report.get("derived") is not None:
        print("== derived（只在内存里；target 用 derived.* 路径引用）==")
        print(json.dumps(report["derived"], ensure_ascii=False, indent=2))
    for title, key, tag in (("errors", "errors", "ERROR"), ("warnings", "warnings", "WARN")):
        print(f"== {title} ({len(report[key])}) ==")
        for line in report[key]:
            print(f"{tag} {line}")
    if report.get("written"):
        print(f"已导出 {report['written']}")
    print("FAIL" if report["errors"] else "OK")


def main(argv=None):
    parser = argparse.ArgumentParser(description="把成片拆成可复用的制作参考（按需，不在生产路径上）")
    sub = parser.add_subparsers(dest="command", required=True)
    p_measure = sub.add_parser("measure", help="一次 ffmpeg：镜头切点与响度，按文件身份缓存")
    p_measure.add_argument("video")
    p_measure.add_argument("--work-dir", required=True)
    p_measure.add_argument("--scene-threshold", type=float, default=DEFAULT_SCENE_THRESHOLD)
    p_measure.add_argument("--no-scale", action="store_true", help="scdet 前不缩放到 320 宽")
    p_check = sub.add_parser("check", help="R1–R7 校验 reference_breakdown.json 并打印派生值")
    p_check.add_argument("--work-dir", required=True)
    p_check.add_argument("--json", action="store_true")
    p_export = sub.add_parser("export", help="零 error 时写 production_reference.json（R8 复扫）")
    p_export.add_argument("--work-dir", required=True)
    p_export.add_argument("--out", required=True)
    p_export.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.command == "measure":
            payload = measure(args.video, args.work_dir, threshold=args.scene_threshold,
                              scaled=not args.no_scale)
            shots, loudness = payload["shots"], payload.get("loudness") or {}
            print(f"镜头 {shots['count']} 个，中位 {shots['median_s']}s，{shots['cuts_per_min']} 切/分钟；"
                  f"整体响度 {loudness.get('integrated_lufs')} LUFS")
            return 0
        report = run_check(args.work_dir) if args.command == "check" else run_export(args.work_dir, args.out)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[video-reference] {exc}", file=sys.stderr)
        return 2
    _print_report(report, args.json)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
