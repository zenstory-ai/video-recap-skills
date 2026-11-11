"""Failure-isolated optional JianYing export invoked after canonical rendering."""

from pathlib import Path

from lib import CONFIG, log

def maybe_export_jianying(work_dir, out_dir, stem):
    """Lazy-import the optional 剪映 exporter and write a draft from timeline.json.

    The export is a documented fail-open sidecar: any failure is logged and never
    fails the already-rendered recap."""
    try:
        from export_jianying import export_timeline_to_jianying
        from timeline import load_timeline
        parent = out_dir or CONFIG["jianying_draft_dir"] or str(work_dir)
        draft_dir, notes = export_timeline_to_jianying(
            load_timeline(Path(work_dir) / "timeline.json"), parent, draft_name=f"recap_{stem}",
            bundle_media=CONFIG["jianying_bundle_media"])
        for n in notes:
            log(f"  注意: {n}")
        log(f"剪映草稿已导出: {draft_dir}")
    except Exception as exc:
        log(f"  ⚠️ 剪映导出失败（不影响成片）: {exc}")
