"""Orchestrate single- and multi-source recap pipelines."""

import os
import shutil
from pathlib import Path

import materials as material_lib
import resources.project_binding as project_binding
import resources.lock as resource_lock

from lib import TTS_PROVIDERS, file_identity, read_json_object
from recap_cli import parse_args
from recap_runtime import (
    RUN_MANIFEST,
    _build_multi_source_records,
    _entry,
    _preflight_burn_subtitles,
    _probe_display_size_or_raise,
    _read_video_duration_or_raise,
    _run,
    _write_multi_source_manifest,
    _write_project_run_manifest,
    _write_run_manifest,
)
from recap_stage_qc import (
    _print_final_qc_pointer,
    _print_render_warnings,
    _require_final_qc,
    _write_final_qc_reports,
)
from recap_source import (
    reject_unbound_narration_workdir,
    uses_narration,
    validate_audio_routing,
)
from recap_timeline import (
    _cut_narration_is_stale,
    _manifest_mismatches,
    _material_library_dir,
    _materials_enabled,
    _multi_manifest_mismatches,
    _pause_for_agent,
    _read_assembly_output,
    _read_phase_ledger,
    _save_materials_enabled,
    _source_work_dir,
    _surface_cut_qc,
    _understand_args_for_source,
    _write_multi_source_clip_brief,
    _write_multi_source_output_brief,
    _write_phase_ledger,
)


def _voiceover_args(work_dir, narration_path, args):
    """Build the shared full/cut invocation for voiceover.py."""
    result = ["--work-dir", str(work_dir), "--narration", str(narration_path)]
    if args.tts_provider != "auto":
        result += ["--tts-provider", args.tts_provider]
    if args.mimo_tts_voice:
        result += ["--mimo-voice", args.mimo_tts_voice]
    if args.voice_ref:
        result += ["--voice-ref", args.voice_ref]
    if args.allow_partial_tts:
        result.append("--allow-partial-tts")
    return result


def _record_resources(work_dir, args):
    """Write resource_lock.json for the finished render and surface anything needing a person."""
    try:
        lock = resource_lock.write_resource_lock(
            work_dir, library_dir=_material_library_dir(args), project=getattr(args, "resolved_project", None)
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:  # a record must never fail a finished render
        print(f"[video-recap] ⚠ 未能写出 resource_lock.json: {type(exc).__name__}: {exc}", flush=True)
        return
    print(resource_lock.summary_line(lock), flush=True)


def _finish_recap(work_dir, final_output, args):
    final_qc_result = _write_final_qc_reports(work_dir, final_output)
    if args.require_final_qc:
        _require_final_qc(final_qc_result, work_dir)
    print(f"[video-recap] ✅ 完成: {final_output}")
    _print_render_warnings(final_qc_result, work_dir, final_output)
    _print_final_qc_pointer(final_qc_result)


def _share_project_background_research(project_work_dir, source_work_dir):
    """Give one source of a multi-video run the project-level background_research.json.

    understand.py only reads background_research.json from its own work_dir
    (sources/<source_id>/), while the Agent writes the research once at the project level.
    The project file is copied in when the source has none or an older one; a per-source
    file newer than the project file is kept. copy2 keeps the mtime, so an unchanged
    project file leaves the understanding cache identity unchanged."""
    shared = Path(project_work_dir) / "background_research.json"
    if not shared.is_file():
        return
    target = Path(source_work_dir) / "background_research.json"
    if target.is_file() and target.stat().st_mtime_ns >= shared.stat().st_mtime_ns:
        return
    shutil.copy2(shared, target)


def _run_or_restore_understanding(source_record, source_work_dir, args, project_work_dir=None):
    """Run understand.py for one source, or restore it from the material library.

    `project_work_dir` is set for multi-video runs, whose sources share its research file."""
    source_work_dir = Path(source_work_dir)
    source_work_dir.mkdir(parents=True, exist_ok=True)
    if project_work_dir is not None:
        _share_project_background_research(project_work_dir, source_work_dir)
    source_path = source_record["source_path"]
    source_identity = source_record["source_video_identity"]
    settings = source_record["settings"]
    lib_dir = _material_library_dir(args)
    restored = False
    kept_local_research = False
    if _materials_enabled(args):
        result = material_lib.restore_material(
            lib_dir,
            source_work_dir,
            source_path=source_path,
            source_identity=source_identity,
            settings=settings,
            material_id=source_record["material_id"],
        )
        restored = result["restored"]
        kept_local_research = bool(result.get("kept_local_research"))
        if restored:
            print(
                f"[video-recap] ♻️  复用素材库: {result['material_id']} → {source_work_dir}",
                flush=True,
            )
        else:
            print(
                f"[video-recap] 素材库未复用 {source_record['source_name']}: {result['reason']}",
                flush=True,
            )
    if not restored:
        _run(
            "understanding",
            "understand.py",
            *_understand_args_for_source(source_record, source_work_dir, args),
        )
    elif kept_local_research:
        _run(
            "understanding",
            "understand.py",
            *_understand_args_for_source(source_record, source_work_dir, args),
            "--brief-only",
        )
    _write_run_manifest(source_work_dir, source_record["source_path"], args)
    if _save_materials_enabled(args):
        meta = material_lib.save_material(
            lib_dir,
            source_work_dir,
            source_path,
            source_identity,
            settings,
            source_id=source_record["source_id"],
            material_id=source_record["material_id"],
        )
        source_record["material_id"] = meta["material_id"]
        print(
            f"[video-recap] 💾 已沉淀素材: {meta['material_id']} → {lib_dir}",
            flush=True,
        )
    return restored


def _write_multi_source_edited_storyboard(work_dir, video):
    """Cut pass 2 for several sources: the same OUTPUT-timeline storyboard a single-source
    --brief-only rebuild produces, drawn from each source's frames, with its pointer added to
    the multi-source brief. Advisory like every storyboard: a failure warns and the pause
    still happens."""
    try:
        _run(
            "understanding", "understand.py", str(video),
            "--work-dir", str(work_dir), "--edited-storyboard-only",
        )
    except SystemExit as exc:
        print(f"[video-recap] ⚠ 剪后故事板未生成（建议性，继续）: {exc}", flush=True)


def _reject_stale(mismatches, label):
    if mismatches:
        details = "\n  - ".join(mismatches)
        raise SystemExit(
            f"work_dir 与当前{label}recap 输入不匹配，拒绝复用既有 narration/clip_plan；"
            "请使用新的 --work-dir，或删除旧产物后重新运行 Phase A。\n"
            f"  - {details}"
        )


def _reject_stale_multi_manifest(work_dir, args, source_records):
    _reject_stale(_multi_manifest_mismatches(work_dir, args, source_records), "多视频 ")


def _render_cut(video_arg, work_dir, args, *extra):
    """Render edited_source.mp4 from clip_plan.json and print its cut QC line."""
    crender = [str(video_arg), "--work-dir", str(work_dir), *extra]
    if args.target_duration:
        crender += ["--target-duration", args.target_duration]
    _run("cut", "cut.py", *crender)
    _surface_cut_qc(work_dir)


def _reject_stale_cut_narration(work_dir, clip_plan_identity):
    if _cut_narration_is_stale(_read_phase_ledger(work_dir), clip_plan_identity):
        raise SystemExit(
            "clip_plan.json 已改变，但 narration.json 仍是对旧剪辑写的，会与剪后画面对不上。"
            "请删除 narration.json，重跑后按新成片重新写解说。"
        )


def _refresh_stale_output_anchors(work_dir, source_records=None):
    """Refresh Pass-2 output speech anchors in-place when cut.py re-snapped clip_plan_validated.json."""
    validated = work_dir / "clip_plan_validated.json"
    anchors = read_json_object(work_dir / "speech_boundary_anchors_output.json")
    if not (validated.exists() and anchors and anchors.get("clip_plan_identity") != file_identity(validated)):
        return
    from briefing.timeline import (
        _sentence_entry_anchors_for_brief,
        write_multi_source_output_speech_evidence,
    )

    if source_records is not None:
        plan = read_json_object(validated)
        if plan and isinstance(plan.get("clips"), list):
            write_multi_source_output_speech_evidence(work_dir, source_records, plan)
    elif (work_dir / "edited_source.mp4").exists():
        _sentence_entry_anchors_for_brief(work_dir, "cut")


def _validate_cut_output_narration(work_dir):
    output_duration = _read_video_duration_or_raise(work_dir / "edited_source.mp4")
    _run(
        "script", "validate.py", "--work-dir", work_dir,
        "--mode", "cut_output", "--output-duration", f"{output_duration:.3f}",
    )


def _narrate(work_dir, args):
    """Run TTS synthesis for a validated narration.json."""
    narration_json = work_dir / "narration.json"
    _run(
        "voiceover", "voiceover.py",
        *_voiceover_args(work_dir, narration_json, args),
    )


def _deliver(work_dir, args, assemble_video, recap_stem, extra_assemble_args=()):
    """Shared tail of every full/cut run: narration (if owned) -> assemble -> final QC."""
    project = getattr(args, "resolved_project", None)
    project_binding.sync_packaging_layers(work_dir, project)
    if project and any(t["role"] in project_binding.GEOMETRY_ROLES for t in project["templates"]):
        import packaging as packaging_lib

        src_w, src_h = _probe_display_size_or_raise(assemble_video)
        out_canvas = packaging_lib.resolve_output_canvas(work_dir, {"width": src_w, "height": src_h})
        project_binding.check_canvas(project, out_canvas["width"], out_canvas["height"])
    if uses_narration(args):
        _narrate(work_dir, args)
    aargs = [str(assemble_video), "--work-dir", str(work_dir), "--recap-stem", recap_stem]
    if not uses_narration(args):
        aargs += ["--audio-mode", args.audio_mode]
    if args.output_dir:
        aargs += ["--output-dir", args.output_dir]
    if args.burn_subtitles is not None:
        aargs.append(
            "--burn-subtitles" if args.burn_subtitles else "--no-burn-subtitles"
        )
    if args.subtitle_y_top is not None:
        aargs += [
            "--subtitle-y-top", str(args.subtitle_y_top),
            "--subtitle-y-bot", str(args.subtitle_y_bot),
        ]
    # env-only burn intent (BURN_SUBTITLES) is propagated implicitly: assemble re-derives it
    # via the same env_bool default the preflight used, so the two agree by shared env.
    aargs += extra_assemble_args
    if args.export_jianying:
        aargs.append("--export-jianying")
    if args.jianying_bundle_media:
        aargs.append("--jianying-bundle-media")
    if args.jianying_no_bundle_media:
        aargs.append("--jianying-no-bundle-media")
    _run("assemble", "assemble.py", *aargs)

    final_output = _read_assembly_output(work_dir)
    _record_resources(work_dir, args)
    _finish_recap(work_dir, final_output, args)


def _run_multi_cut(videos, work_dir, args):
    """Multi-video MVP: cut-first/narrate-second over a project work_dir."""
    project_binding.sync_production_reference(work_dir, getattr(args, "resolved_project", None))
    source_records = _build_multi_source_records(videos, args)
    narration_json = work_dir / "narration.json"
    clip_plan_json = work_dir / "clip_plan.json"
    edited_source = work_dir / "edited_source.mp4"
    inspect_py = _entry("recap_inspect.py")
    # A project manifest that exists must match, and a clip plan must have one, before any reuse.
    if (work_dir / RUN_MANIFEST).exists() or clip_plan_json.exists():
        _reject_stale_multi_manifest(work_dir, args, source_records)
    manifest_path = _write_multi_source_manifest(work_dir, source_records)
    if not clip_plan_json.exists():
        for record in source_records:
            _run_or_restore_understanding(
                record, _source_work_dir(work_dir, record), args, project_work_dir=work_dir
            )
        # Rewrite: _run_or_restore_understanding fills in material_id per record.
        manifest_path = _write_multi_source_manifest(work_dir, source_records)
        _write_project_run_manifest(work_dir, args, source_records)
        _write_multi_source_clip_brief(work_dir, source_records, args)
        _pause_for_agent(
            work_dir,
            f"{clip_plan_json}（多视频剪辑计划；每个 clip 必须带 source_id）",
            args,
            inspect_hint=f"python3 {inspect_py} --work-dir {work_dir} state",
        )
        return

    cp_identity = file_identity(clip_plan_json)
    # A narration written for an older clip_plan aborts before cut.py re-renders anything.
    if uses_narration(args) and narration_json.exists():
        _reject_stale_cut_narration(work_dir, cp_identity)
    _render_cut(videos[0], work_dir, args, "--sources-manifest", str(manifest_path))
    if uses_narration(args):
        if not narration_json.exists():
            _write_multi_source_output_brief(
                work_dir, source_records, work_dir / "clip_plan_validated.json"
            )
            _write_multi_source_edited_storyboard(work_dir, videos[0])
            _write_phase_ledger(work_dir, cp_identity)
            _pause_for_agent(
                work_dir,
                f"{narration_json}（用成片 OUTPUT 时间轴写解说，对着 {edited_source}）",
                args,
                inspect_hint=(
                    f"python3 {inspect_py} --work-dir {work_dir} "
                    "clip-map --output-start <s> --output-end <e>"
                ),
            )
            return
        _refresh_stale_output_anchors(work_dir, source_records=source_records)
        _write_phase_ledger(work_dir, cp_identity)
        _validate_cut_output_narration(work_dir)

    _deliver(work_dir, args, edited_source, f"multi_{videos[0].stem}")


def main():
    ap, args = parse_args()

    # argparse `choices` does not cover the TTS_PROVIDER env default; source-owned audio
    # never synthesises, so an ambient provider is irrelevant there.
    if uses_narration(args) and args.tts_provider not in TTS_PROVIDERS:
        ap.error("TTS_PROVIDER/--tts-provider must be one of: " + ", ".join(TTS_PROVIDERS))

    validate_audio_routing(ap, args)
    args.resolved_project = None
    if args.project:
        args.project = str(project_binding.project_path(args.project))
        project_binding.apply_project(
            project_binding.resolve_project(
                args.project, args, include_voice=uses_narration(args)
            ),
            args,
        )

    if uses_narration(args):
        if args.voice_ref is None:
            args.voice_ref = os.environ.get("VOICE_REF", "").strip() or None
        explicit_mimo_voice = (
            args.mimo_tts_voice or os.environ.get("MIMO_TTS_VOICE", "").strip()
        )
        if explicit_mimo_voice and args.voice_ref:
            ap.error("--mimo-tts-voice and --voice-ref are mutually exclusive")
        if args.tts_provider in {"fish-audio", "index-tts"} and (
            explicit_mimo_voice or args.voice_ref
        ):
            ap.error(
                "--mimo-tts-voice/--voice-ref are only supported by --tts-provider mimo-tts"
            )
    if (args.subtitle_y_top is None) != (args.subtitle_y_bot is None):
        ap.error("--subtitle-y-top and --subtitle-y-bot must be provided together")
    if args.subtitle_y_top is not None:
        if args.subtitle_y_top < 0 or args.subtitle_y_bot <= args.subtitle_y_top:
            ap.error("subtitle Y coordinates must satisfy 0 <= top < bot")

    videos = [Path(v).resolve() for v in args.video]
    if len(videos) > 1 and args.edit_mode != "cut":
        raise SystemExit(
            "多视频输入当前 MVP 只支持 --edit-mode cut；full 请一次输入一个视频。"
        )
    if len(videos) > 1 and args.subtitle_y_top is not None:
        ap.error("多视频 cut 暂不支持全局 subtitle Y 坐标；各源字幕带可能不同")
    if args.subtitle_y_top is not None:
        _, canvas_height = _probe_display_size_or_raise(
            videos[0], require_near_square_pixels=True
        )
        if args.subtitle_y_bot > canvas_height:
            ap.error(
                f"subtitle Y coordinates exceed display canvas height {canvas_height}: "
                f"bot={args.subtitle_y_bot}"
            )
    if args.voice_ref:
        voice_ref = Path(args.voice_ref).expanduser().resolve()
        if not voice_ref.is_file():
            ap.error(f"reference audio does not exist or is not a file: {voice_ref}")
        args.voice_ref = str(voice_ref)

    _execute_pipeline(args, videos)


def _execute_pipeline(args, videos):
    # Fail fast before any expensive understanding/VLM/ASR/TTS work if the run will burn
    # subtitles but this ffmpeg can't (otherwise it only blows up at the final render).
    _preflight_burn_subtitles(args)

    prefix = "work_dir_multi_" if len(videos) > 1 else "work_dir_"
    work_dir = (
        Path(args.work_dir).resolve()
        if args.work_dir
        else videos[0].parent / f"{prefix}{videos[0].stem}"
    )
    # Validate the work-directory audio policy before any stage writes into it.
    reject_unbound_narration_workdir(work_dir, args)
    work_dir.mkdir(parents=True, exist_ok=True)
    if len(videos) > 1:
        _run_multi_cut(videos, work_dir, args)
    else:
        _run_single(videos[0], work_dir, args)


def _run_single(video, work_dir, args):
    """Single-video full or cut run; returns early at each agent pause."""
    project_binding.sync_production_reference(work_dir, getattr(args, "resolved_project", None))
    cut = args.edit_mode == "cut"
    narration_json = work_dir / "narration.json"
    clip_plan_json = work_dir / "clip_plan.json"
    edited_source = work_dir / "edited_source.mp4"
    inspect_py = _entry("recap_inspect.py")
    state_hint = f"python3 {inspect_py} --work-dir {work_dir} state"

    def _understand():
        _run_or_restore_understanding(_build_multi_source_records([video], args)[0], work_dir, args)

    def _reject_stale_manifest():
        _reject_stale(_manifest_mismatches(work_dir, video, args), " ")

    if not cut:
        # Full mode: a single pause (understand -> agent writes narration.json -> produce).
        if uses_narration(args):
            if not narration_json.exists():
                _understand()
                _pause_for_agent(work_dir, f"{narration_json}", args, inspect_hint=state_hint)
                return
            _reject_stale_manifest()
            _run("script", "validate.py", "--work-dir", work_dir, "--mode", "full")
        elif (work_dir / RUN_MANIFEST).exists():
            _reject_stale_manifest()
        else:
            _write_run_manifest(work_dir, video, args)
        _deliver(work_dir, args, video, video.stem)
        return

    # Cut mode: cut-first / narrate-second (two pauses), so narration is authored against the
    # REAL output timeline — no source-time mapping exists that could drop/clamp/desync.
    if not clip_plan_json.exists():
        # PASS 1: understand -> agent writes clip_plan.json ONLY.
        if not uses_narration(args) and (work_dir / RUN_MANIFEST).exists():
            _reject_stale_manifest()
        _understand()
        _pause_for_agent(
            work_dir,
            f"{clip_plan_json}（只写剪辑计划；解说下一步对着剪好的成片写）",
            args,
            inspect_hint=state_hint,
        )
        return
    _reject_stale_manifest()
    cp_identity = file_identity(clip_plan_json)
    # A narration written for an older clip_plan aborts before cut.py re-renders anything.
    if uses_narration(args) and narration_json.exists():
        _reject_stale_cut_narration(work_dir, cp_identity)
    _render_cut(video, work_dir, args)
    if uses_narration(args):
        if not narration_json.exists():
            # PASS 2: rebuild the brief (now an OUTPUT-timeline variant) and pause for narration.
            # Rebuilt from cached/restored analysis only: a material restore may have supplied
            # pass-1 artifacts (even a source-time brief), but must not skip this rebuild.
            _run(
                "understanding",
                "understand.py",
                *_understand_args_for_source(
                    _build_multi_source_records([video], args)[0], work_dir, args
                ),
                "--brief-only",
            )
            _write_phase_ledger(work_dir, cp_identity)
            _pause_for_agent(
                work_dir,
                f"{narration_json}（用成片 OUTPUT 时间轴写解说，对着 {edited_source}）",
                args,
                inspect_hint=(
                    f"python3 {inspect_py} --work-dir {work_dir} "
                    "clip-map --output-start <s> --output-end <e>  # 核对输出↔原片时间轴"
                ),
            )
            return
        _refresh_stale_output_anchors(work_dir)
        _write_phase_ledger(work_dir, cp_identity)
        _validate_cut_output_narration(work_dir)
    # let the timeline / 剪映 export reference the original clips, not edited_source.mp4
    _deliver(
        work_dir, args, edited_source, video.stem,
        ["--source-video", str(video)],
    )
