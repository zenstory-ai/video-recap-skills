"""Own recap timeline artifacts, continuation state, and cut QC surfaces."""

import json
import os
import shlex
import sys
from pathlib import Path

from briefing.builder import (
    write_multi_source_clip_brief as _write_multi_source_clip_brief,
    write_multi_source_output_brief as _write_multi_source_output_brief,
)
from briefing.timeline import (
    source_speech_rows as _source_speech_rows,
    write_multi_source_output_speech_evidence as _write_multi_source_output_speech_evidence,
)
from lib import load_json
import resources.project_binding as project_binding
from library import LIBRARY_ENV
from recap_runtime import (
    _entry,
    _load_run_manifest,
    _multi_run_manifest_payload,
    _resume_argv,
    _run_manifest_payload,
)

__all__ = [
    "ASSEMBLY_MANIFEST",
    "PHASE_LEDGER",
    "_continuation_command",
    "_cut_narration_is_stale",
    "_manifest_mismatches",
    "_material_library_dir",
    "_materials_enabled",
    "_multi_manifest_mismatches",
    "_pause_for_agent",
    "_read_assembly_output",
    "_read_phase_ledger",
    "_save_materials_enabled",
    "_source_speech_rows",
    "_source_work_dir",
    "_surface_cut_qc",
    "_understand_args_for_source",
    "_write_multi_source_clip_brief",
    "_write_multi_source_output_brief",
    "_write_multi_source_output_speech_evidence",
    "_write_phase_ledger",
]

ASSEMBLY_MANIFEST = "assembly_manifest.json"
PHASE_LEDGER = "recap_phase.json"


def _settings_for_compare(settings):
    """Settings that, if changed, invalidate reusing an existing work_dir on resume.

    Retired `consolidate` and `consolidate_asr` keys are excluded: an old manifest may
    still carry them, and their extra artifacts are simply no longer read.
    """
    s = dict(settings)
    s.pop("consolidate", None)
    s.pop("consolidate_asr", None)
    return s


def _settings_and_audio_mismatches(actual, expected):
    mismatches = []
    if actual["settings"].get("mimo_video_overview"):
        mismatches.append(
            "settings: 这个 work_dir 开启过已移除的 --mimo-video-overview，不能续跑；"
            "仍需这条路径请使用 v0.6.2"
        )
    elif _settings_for_compare(actual["settings"]) != _settings_for_compare(expected["settings"]):
        mismatches.append("settings: 当前 CLI 参数与 Phase A manifest 不匹配")
    if actual.get("audio") != expected["audio"]:
        mismatches.append(f"audio: expected {expected['audio']!r}, got {actual.get('audio')!r}")
    return mismatches


def _manifest_mismatches(work_dir, video, args):
    expected = _run_manifest_payload(video, args)
    actual = _load_run_manifest(work_dir)
    if actual is None:
        return ["缺少 recap_run_manifest.json；不能证明 work_dir 属于当前视频/参数"]
    mismatches = [
        f"{key}: expected {expected[key]!r}, got {actual.get(key)!r}"
        for key in ("source_video", "source_video_identity")
        if actual.get(key) != expected[key]
    ]
    return mismatches + _settings_and_audio_mismatches(actual, expected)


def _multi_manifest_mismatches(work_dir, args, source_records):
    expected = _multi_run_manifest_payload(args, source_records)
    actual = _load_run_manifest(work_dir)
    if actual is None:
        return ["缺少 recap_run_manifest.json；不能证明 work_dir 属于当前多视频/参数"]
    if actual.get("mode") != "multi_source":
        return [f"mode: expected 'multi_source', got {actual.get('mode')!r}"]
    mismatches = []
    identity = ("source_id", "source_path", "source_video_identity")
    if [{k: s.get(k) for k in identity} for s in actual["sources"]] != [
        {k: s[k] for k in identity} for s in expected["sources"]
    ]:
        mismatches.append(
            "sources: 当前输入视频列表/顺序/source_id/identity 与 Phase A manifest 不匹配"
        )
    return mismatches + _settings_and_audio_mismatches(actual, expected)


def _read_assembly_output(work_dir):
    return Path(load_json(Path(work_dir) / ASSEMBLY_MANIFEST)["final_output"])


def _read_phase_ledger(work_dir):
    """Phase ledger (cut mode with narration): the clip_plan identity the narration is
    written against — the guard that keeps a narration written for one clip_plan from
    silently driving a different cut into TTS. None before pass 2 has recorded it.
    """
    path = Path(work_dir) / PHASE_LEDGER
    return load_json(path) if path.exists() else None


def _write_phase_ledger(work_dir, clip_plan_identity):
    ledger = {"clip_plan_identity": clip_plan_identity}
    (Path(work_dir) / PHASE_LEDGER).write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return ledger


def _cut_narration_is_stale(ledger, current_clip_plan_identity):
    return ledger is not None and ledger["clip_plan_identity"] != current_clip_plan_identity


def _continuation_command(work_dir, args):
    """Shell command that resumes this run: the original argv, replayable from any cwd."""
    parts = [sys.executable, str(_entry("recap.py")), *_resume_argv(work_dir, args)]
    return " ".join(shlex.quote(part) for part in parts)


def _source_work_dir(project_work_dir, source_record):
    return Path(project_work_dir) / source_record["source_work_dir"]


def _understand_args_for_source(source_record, source_work_dir, args):
    uargs = [
        source_record["source_path"],
        "--work-dir",
        str(source_work_dir),
        "--style",
        args.style,
        "--edit-mode",
        args.edit_mode,
    ]
    if args.context:
        uargs += ["--context", args.context]
    if args.scene_threshold is not None:
        uargs += ["--scene-threshold", str(args.scene_threshold)]
    if args.target_duration:
        uargs += ["--target-duration", args.target_duration]
    if args.skip_asr:
        uargs.append("--skip-asr")
    return uargs


def _surface_cut_qc(work_dir):
    """Print the cut QC cut.py recorded; cut.py itself already fails on blocking QC."""
    qc = load_json(Path(work_dir) / "clip_plan_validated.json")["qc"]
    geometry = qc["output_geometry"]
    parts = [
        f"target_duration_status={qc['target_duration_status']}",
        f"total_duration={qc['total_duration']}",
        f"clip_count={qc['clip_count']}",
        f"join_fade_ms={qc['join_fade_ms']}",
        f"output_geometry={geometry['width']}x{geometry['height']}@{geometry['fps']}fps"
        f" reason={qc['output_geometry_reason']}",
    ]
    if qc.get("warnings"):
        parts.append(f"warnings={len(qc['warnings'])}")
    print("[video-recap] cut QC: " + "; ".join(parts), flush=True)
    return qc


def _material_library_dir(args):
    return args.material_library_dir or os.environ.get(LIBRARY_ENV) or None


def _materials_enabled(args):
    return bool(_material_library_dir(args) and args.use_materials)


def _save_materials_enabled(args):
    return bool(_material_library_dir(args) and args.save_materials)


def _pause_for_agent(work_dir, need_text, args, inspect_hint=None):
    brief = Path(work_dir) / "agent_narration_brief.md"
    print("=" * 50)
    print(f"[video-recap] ⏸  阅读 {brief}（按 video-recap 规则）后写入 {need_text}")
    if inspect_hint:
        print(f"[video-recap]    先核对状态/时间轴（建议性）: {inspect_hint}")
    reference_note = project_binding.bound_reference_note(
        work_dir, getattr(args, "resolved_project", None)
    )
    if reference_note:
        print(f"[video-recap]    {reference_note}")
    print(f"[video-recap]    写完后重跑继续: {_continuation_command(work_dir, args)}")
    print("=" * 50)
