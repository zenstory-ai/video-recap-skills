"""Orchestrate single- and multi-source recap pipelines."""

import json
import os
from pathlib import Path

import materials as material_lib

from recap_cli import TTS_PROVIDERS, parse_args
from recap_review import run_narration_review
from recap_runtime import (
    _build_multi_source_records,
    _coerce_videos,
    _entry,
    _material_settings_fingerprint,
    _optional_env_int,
    _preflight_burn_subtitles,
    _probe_display_height_or_raise,
    _read_video_duration_or_raise,
    _run,
    _write_multi_source_manifest,
    _write_project_run_manifest,
    _write_run_manifest,
)
from recap_stage_qc import (
    _post_render_qc_metadata,
    _prepare_mimo_qc,
    _print_final_qc_pointer,
    _require_final_qc,
    _run_mimo_qc_stage,
    _tts_qc_metadata,
    _write_final_qc_reports,
    _write_shift_left_stage_qc,
)
from recap_source import (
    audio_binding,
    begin_local_adoption_qc,
    begin_non_narration_qc,
    extend_assemble_args,
    load_local_assembly_evidence,
    needs_voiceover,
    owned_local_delivery,
    reject_unbound_narration_workdir,
    reject_unsupported_subtitle_track,
    uses_local_adoption,
    uses_narration,
    validate_audio_routing,
    verify_local_assembly_evidence,
    remove_owned_local_delivery,
)
from recap_timeline import (
    _continuation_command,
    _cut_narration_is_stale,
    _file_md5,
    _manifest_mismatches,
    _material_library_dir,
    _materials_enabled,
    _multi_manifest_mismatches,
    _pause_for_agent,
    _print_narration_review_pointer,
    _read_assembly_output,
    _read_phase_ledger,
    _save_materials_enabled,
    _source_work_dir,
    _surface_cut_qc,
    _understand_args_for_source,
    _write_canonical_visual_overlays,
    _write_multi_source_clip_brief,
    _write_multi_source_output_brief,
    _write_phase_ledger,
)

RUN_MANIFEST = "recap_run_manifest.json"


def _voiceover_args(work_dir, narration_path, args):
    """Build the shared full/cut invocation for video-voiceover."""
    result = ["--work-dir", str(work_dir), "--narration", str(narration_path)]
    if args.tts_provider != "auto":
        result += ["--tts-provider", args.tts_provider]
    if args.mimo_tts_voice:
        result += ["--mimo-voice", args.mimo_tts_voice]
    if args.voice_ref:
        result += ["--voice-ref", args.voice_ref]
    if args.allow_partial_tts:
        result.append("--allow-partial-tts")
    if getattr(args, "preserve_approved_text", False):
        result.append("--preserve-approved-text")
    return result


def _approved_validation_args(args):
    return ["--preserve-approved-text"] if args.preserve_approved_text else []


def _finish_recap(work_dir, final_output, args):
    final_qc_result = _write_final_qc_reports(work_dir, final_output)
    if getattr(args, "require_final_qc", False):
        _require_final_qc(final_qc_result, work_dir)
    print(f"[video-recap] ✅ 完成: {final_output}")
    _print_final_qc_pointer(final_qc_result)


def _run_local_adoption(video, work_dir, args):
    """Run the strict local bundle directly through the authoritative assembler."""
    work_dir.mkdir(parents=True, exist_ok=False)
    manifest = _write_run_manifest(work_dir, video, args)
    def assert_manifest_inputs_current():
        if material_lib.file_fingerprint(video) != manifest["source_video_fingerprint"]:
            raise SystemExit("local adoption picture changed after run manifest was sealed")
        if audio_binding(args) != manifest["audio"]:
            raise SystemExit("local adoption audio bundle changed after run manifest was sealed")
    def record_failure(exc):
        failed = {**manifest, "local_adoption_run": {
            "status": "FAILED", "error_type": type(exc).__name__,
        }}
        (work_dir / RUN_MANIFEST).write_text(
            json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    begin_local_adoption_qc(work_dir, _write_shift_left_stage_qc)
    assemble_args = [
        str(video),
        "--work-dir", str(work_dir),
        "--recap-stem", video.stem,
        "--output-dir", args.output_dir,
        "--tts-meta", args.tts_meta,
        "--narration-adoption", args.narration_adoption,
        "--audio-mix-adoption", args.audio_mix_adoption,
    ]
    if args.burn_subtitles is not None:
        assemble_args.append(
            "--burn-subtitles" if args.burn_subtitles else "--no-burn-subtitles"
        )
    if args.subtitle_y_top is not None:
        assemble_args += [
            "--subtitle-y-top", str(args.subtitle_y_top),
            "--subtitle-y-bot", str(args.subtitle_y_bot),
        ]
    owned_delivery = None
    try:
        assert_manifest_inputs_current()
        _run("video-assemble", "assemble.py", *assemble_args)
        evidence = load_local_assembly_evidence(work_dir)
        owned_delivery = owned_local_delivery(evidence)
        assert_manifest_inputs_current()
        verify_local_assembly_evidence(evidence, manifest)
    except BaseException as exc:
        remove_owned_local_delivery(owned_delivery)
        record_failure(exc)
        raise
    final_output = _read_assembly_output(work_dir)
    _write_shift_left_stage_qc(
        work_dir,
        "post_render",
        metadata=_post_render_qc_metadata(work_dir, final_output),
    )
    _finish_recap(work_dir, final_output, args)


def _run_or_restore_understanding(source_record, source_work_dir, args):
    """Run video-understanding for one source, or restore it from the material library."""
    source_work_dir = Path(source_work_dir)
    source_work_dir.mkdir(parents=True, exist_ok=True)
    source_fp = source_record["source_video_fingerprint"]
    settings_fp = source_record["settings_fingerprint"]
    lib_dir = _material_library_dir(args)
    restored = False
    if _materials_enabled(args):
        result = material_lib.restore_material(
            lib_dir,
            source_work_dir,
            source_fingerprint=source_fp,
            settings_fp=settings_fp,
        )
        restored = result["restored"]
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
            "video-understanding",
            "understand.py",
            *_understand_args_for_source(source_record, source_work_dir, args),
        )
    _write_run_manifest(source_work_dir, source_record["source_path"], args)
    if _save_materials_enabled(args):
        meta = material_lib.save_material(
            lib_dir,
            source_work_dir,
            source_record["source_path"],
            source_fp,
            settings_fp,
            source_id=source_record["source_id"],
            material_id=source_record["material_id"],
        )
        source_record["material_id"] = meta["material_id"]
        print(
            f"[video-recap] 💾 已沉淀素材: {meta['material_id']} → {lib_dir}",
            flush=True,
        )
    return restored


def _single_source_record(video, args):
    """Identity + settings for the one source of a single-video run."""
    fp = material_lib.file_fingerprint(video)
    return {
        "source_id": material_lib.source_id_from_fingerprint(fp),
        "source_path": str(video),
        "source_name": video.name,
        "source_video_fingerprint": fp,
        "settings_fingerprint": _material_settings_fingerprint(args),
        "material_id": material_lib.material_id_for(video, fp),
    }


def _rebuild_understanding_brief(source_record, source_work_dir, args):
    """Rebuild agent_narration_brief.md from cached/restored analysis only.
    Cut pass 2 needs an OUTPUT-time brief after edited_source.mp4 exists. A
    material restore may have supplied pass-1 analysis artifacts (and even a
    source-time brief), but it must not skip this phase-specific brief rebuild.
    """
    _run(
        "video-understanding",
        "understand.py",
        *_understand_args_for_source(source_record, source_work_dir, args),
        "--brief-only",
    )


def _reject_stale(mismatches, label):
    if mismatches:
        details = "\n  - ".join(mismatches)
        raise SystemExit(
            f"work_dir 与当前{label}recap 输入不匹配，拒绝复用既有 narration/clip_plan；"
            "请使用新的 --work-dir，或删除旧产物后重新运行 Phase A。\n"
            f"  - {details}"
        )


def _reject_stale_multi_manifest(work_dir, videos, args, source_records):
    _reject_stale(_multi_manifest_mismatches(work_dir, videos, args, source_records), "多视频 ")


def _run_multi_cut(videos, work_dir, args):
    """Multi-video MVP: cut-first/narrate-second over a project work_dir."""
    videos = _coerce_videos(videos)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    reject_unbound_narration_workdir(work_dir, args)
    reject_unsupported_subtitle_track(work_dir, args)
    source_records = _build_multi_source_records(videos, args)
    narration_json = work_dir / "narration.json"
    clip_plan_json = work_dir / "clip_plan.json"
    edited_source = work_dir / "edited_source.mp4"
    inspect_py = _entry("video-recap", "recap_inspect.py")
    # If a project manifest already exists, it must match before any Phase-B reuse.
    if (work_dir / RUN_MANIFEST).exists():
        _reject_stale_multi_manifest(work_dir, videos, args, source_records)
    manifest_path = _write_multi_source_manifest(work_dir, source_records)
    if not uses_narration(args):
        begin_non_narration_qc(work_dir, args, _write_shift_left_stage_qc)
    if not clip_plan_json.exists():
        for record in source_records:
            _run_or_restore_understanding(
                record, _source_work_dir(work_dir, record), args
            )
        # Rewrite: _run_or_restore_understanding fills in material_id per record.
        manifest_path = _write_multi_source_manifest(work_dir, source_records)
        _write_project_run_manifest(work_dir, videos, args, source_records)
        _write_multi_source_clip_brief(work_dir, source_records, args)
        _pause_for_agent(
            work_dir,
            f"{clip_plan_json}（多视频剪辑计划；每个 clip 必须带 source_id）",
            _continuation_command(videos, work_dir, args),
            inspect_hint=f"python3 {inspect_py} --work-dir {work_dir} state",
        )
        return

    _reject_stale_multi_manifest(work_dir, videos, args, source_records)
    cp_fp = _file_md5(clip_plan_json)
    crender = [
        str(videos[0]),
        "--work-dir",
        str(work_dir),
        "--sources-manifest",
        str(manifest_path),
    ]
    if args.target_duration:
        crender += ["--target-duration", args.target_duration]
    if args.allow_duration_drift:
        crender.append("--allow-duration-drift")
    _run("video-cut", "cut.py", *crender)
    cut_qc = _surface_cut_qc(work_dir)
    _write_shift_left_stage_qc(work_dir, "post_cut", metadata={"cut_qc": cut_qc})
    if uses_narration(args):
        if not narration_json.exists():
            _write_multi_source_output_brief(
                work_dir, source_records, work_dir / "clip_plan_validated.json"
            )
            _write_phase_ledger(
                work_dir,
                clip_plan_fingerprint=cp_fp,
                edited_source_rendered=True,
                multi_source=True,
            )
            _pause_for_agent(
                work_dir,
                f"{narration_json}（用成片 OUTPUT 时间轴写解说，对着 {edited_source}）",
                _continuation_command(videos, work_dir, args),
                inspect_hint=(
                    f"python3 {inspect_py} --work-dir {work_dir} "
                    "clip-map --output-start <s> --output-end <e>"
                ),
            )
            return
        if _cut_narration_is_stale(_read_phase_ledger(work_dir), cp_fp):
            raise SystemExit(
                "clip_plan.json 已改变，但 narration.json 仍是对旧剪辑写的，会与剪后画面对不上。"
                "请删除 narration.json，重跑后按新成片重新写解说。"
            )
        _write_phase_ledger(
            work_dir,
            clip_plan_fingerprint=cp_fp,
            narration_fingerprint=_file_md5(narration_json),
            narration_written=True,
            multi_source=True,
        )
        output_duration = _read_video_duration_or_raise(edited_source)
        _run(
            "video-script", "validate.py", "--work-dir", work_dir,
            "--mode", "cut_output", "--output-duration", f"{output_duration:.3f}",
            *_approved_validation_args(args),
        )
        review_ran = run_narration_review(
            work_dir, args, run=_run, timeline="cut_output"
        )
        _write_shift_left_stage_qc(
            work_dir, "pre_tts",
            metadata={"review_ran": review_ran, "timeline": "cut_output"},
        )
        _run(
            "video-voiceover", "voiceover.py",
            *_voiceover_args(work_dir, narration_json, args),
        )
        _write_shift_left_stage_qc(
            work_dir, "post_tts", metadata=_tts_qc_metadata(work_dir)
        )
        overlays_path = _write_canonical_visual_overlays(work_dir, narration_json)
        _write_shift_left_stage_qc(
            work_dir, "pre_assemble", metadata={"visual_overlays": str(overlays_path)}
        )
        _run_mimo_qc_stage(work_dir, args, "pre_assemble")

    recap_stem = f"multi_{videos[0].stem}"
    aargs = [
        str(edited_source),
        "--work-dir",
        str(work_dir),
        "--recap-stem",
        recap_stem,
    ]
    extend_assemble_args(aargs, args)
    if args.output_dir:
        aargs += ["--output-dir", args.output_dir]
    if args.burn_subtitles is not None:
        aargs.append(
            "--burn-subtitles" if args.burn_subtitles else "--no-burn-subtitles"
        )
    if args.export_jianying:
        aargs.append("--export-jianying")
    if args.jianying_bundle_media:
        aargs.append("--jianying-bundle-media")
    if args.jianying_no_bundle_media:
        aargs.append("--jianying-no-bundle-media")
    _run("video-assemble", "assemble.py", *aargs)

    final_output = _read_assembly_output(work_dir)
    _write_shift_left_stage_qc(
        work_dir,
        "post_render",
        metadata=_post_render_qc_metadata(work_dir, final_output),
    )
    if uses_narration(args):
        _run_mimo_qc_stage(work_dir, args, "post_render", final_output=final_output)
    _finish_recap(work_dir, final_output, args)
    if uses_narration(args):
        _print_narration_review_pointer(work_dir, review_ran=review_ran)


def main():
    ap, args = parse_args()

    if getattr(args, "require_final_qc", False) and args.edit_mode == "dub":
        ap.error("--require-final-qc is only supported in full/cut modes, not dub")

    if args.doctor:
        if any(
            getattr(args, field) is not None
            for field in ("tts_meta", "narration_adoption", "audio_mix_adoption")
        ):
            ap.error("--doctor cannot be combined with local adoption inputs")
        if args.tts_provider not in TTS_PROVIDERS:
            ap.error(
                "TTS_PROVIDER/--tts-provider must be one of: " + ", ".join(TTS_PROVIDERS)
            )
        doctor_args = []
        if args.tts_provider != "auto":
            doctor_args += ["--tts-provider", args.tts_provider]
        _run("video-recap", "doctor.py", *doctor_args)
        return
    if not uses_local_adoption(args) and args.mimo_qc not in {
        "off", "pre-assemble", "post-render", "both"
    }:
        ap.error(
            "MIMO_QC/--mimo-qc must be one of: off, pre-assemble, post-render, both"
        )
    if (
        not uses_local_adoption(args)
        and uses_narration(args)
        and args.tts_provider not in TTS_PROVIDERS
    ):
        ap.error(
            "TTS_PROVIDER/--tts-provider must be one of: " + ", ".join(TTS_PROVIDERS)
        )
    if not args.video:
        ap.error("video is required (unless --doctor)")
    validate_audio_routing(ap, args)

    if needs_voiceover(args) and args.voice_ref is None:
        args.voice_ref = os.environ.get("VOICE_REF", "").strip() or None
    elif not needs_voiceover(args):
        # Source-owned audio must not inherit ambient narration configuration. Explicit
        # narration flags were already rejected by validate_audio_routing().
        args.voice_ref = None
    try:
        if args.subtitle_y_top is None:
            args.subtitle_y_top = _optional_env_int("SUBTITLE_Y_TOP")
        if args.subtitle_y_bot is None:
            args.subtitle_y_bot = _optional_env_int("SUBTITLE_Y_BOT")
    except ValueError as exc:
        ap.error(str(exc))

    if needs_voiceover(args):
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
    if args.edit_mode == "dub" and args.voice_ref:
        ap.error(
            "--voice-ref is only supported in full/cut modes; dub clones the source voice automatically"
        )
    if args.edit_mode == "dub" and args.tts_provider == "fish-audio":
        ap.error(
            "--tts-provider fish-audio is only supported in full/cut modes; "
            "dub uses MiMo voice cloning"
        )
    if args.edit_mode == "dub" and args.subtitle_y_top is not None:
        ap.error(
            "--subtitle-y-top/--subtitle-y-bot are only supported in full/cut modes"
        )
    if (args.subtitle_y_top is None) != (args.subtitle_y_bot is None):
        ap.error("--subtitle-y-top and --subtitle-y-bot must be provided together")
    if args.subtitle_y_top is not None:
        if args.subtitle_y_top < 0 or args.subtitle_y_bot <= args.subtitle_y_top:
            ap.error("subtitle Y coordinates must satisfy 0 <= top < bot")

    videos = _coerce_videos(args.video)
    if len(videos) > 1 and args.edit_mode != "cut":
        raise SystemExit(
            "多视频输入当前 MVP 只支持 --edit-mode cut；full/dub 请一次输入一个视频。"
        )
    if len(videos) > 1 and args.subtitle_y_top is not None:
        ap.error("多视频 cut 暂不支持全局 subtitle Y 坐标；各源字幕带可能不同")
    if args.subtitle_y_top is not None:
        canvas_height = _probe_display_height_or_raise(
            videos[0], require_square_pixels=True
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

    if uses_local_adoption(args):
        _run_local_adoption(videos[0], Path(args.work_dir), args)
        return

    if len(videos) > 1:
        work_dir = (
            Path(args.work_dir).resolve()
            if args.work_dir
            else videos[0].parent / f"work_dir_multi_{videos[0].stem}"
        )
        # Validate the work-directory audio policy before any run-local QC state is reset.
        reject_unbound_narration_workdir(work_dir, args)
        _prepare_mimo_qc(work_dir, args)
        _run_multi_cut(videos, work_dir, args)
        return

    video = videos[0]
    work_dir = (
        Path(args.work_dir).resolve()
        if args.work_dir
        else video.parent / f"work_dir_{video.stem}"
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    reject_unbound_narration_workdir(work_dir, args)
    reject_unsupported_subtitle_track(work_dir, args)
    _prepare_mimo_qc(work_dir, args)
    cut = args.edit_mode == "cut"
    narration_json = work_dir / "narration.json"
    clip_plan_json = work_dir / "clip_plan.json"

    edited_source = work_dir / "edited_source.mp4"

    def _understand():
        source_record = _single_source_record(video, args)
        _run_or_restore_understanding(source_record, work_dir, args)
        return source_record

    def _rebuild_output_brief():
        _rebuild_understanding_brief(_single_source_record(video, args), work_dir, args)

    inspect_py = _entry("video-recap", "recap_inspect.py")

    def _pause(need_text, inspect_hint=None):
        # Same banner as the multi-source flow; only the continuation command differs.
        _pause_for_agent(
            work_dir,
            need_text,
            _continuation_command(video, work_dir, args),
            inspect_hint=inspect_hint,
        )

    def _reject_stale_manifest():
        _reject_stale(_manifest_mismatches(work_dir, video, args), " ")

    if args.edit_mode == "dub":
        # Dub mode: EN→ZH translation-dub in the original cloned voice (replaces speech, not
        # overlay). One pause: prepare (ASR + sentence-seg + reference) -> agent writes the
        # Chinese translation (dub_script.json) -> render (clone TTS + full-replace mux).
        dub_script = work_dir / "dub_script.json"
        if not dub_script.exists():
            _run(
                "video-voiceover",
                "dub.py",
                "--stage",
                "prepare",
                "--video",
                str(video),
                "--work-dir",
                str(work_dir),
            )
            _write_run_manifest(work_dir, video, args)
            cont = _continuation_command(video, work_dir, args)
            print("=" * 50)
            print(
                f"[video-recap] ⏸  阅读 {work_dir / 'dub_brief.md'}，把英文原声转写切分并翻译成中文，写入 {dub_script}"
            )
            print(
                '[video-recap]    格式 [{"start": 起秒, "end": 止秒, "zh": "译文"}]（按 start 升序）；逐句忠实、跟原声节奏一致、保留原音色'
            )
            print(f"[video-recap]    写完后重跑继续: {cont}")
            print("=" * 50)
            return
        _reject_stale_manifest()
        _run(
            "video-voiceover",
            "dub.py",
            "--stage",
            "render",
            "--video",
            str(video),
            "--work-dir",
            str(work_dir),
        )
        print(f"[video-recap] ✅ 配音完成: {work_dir / ('dub_' + video.stem + '.mp4')}")
        return

    if not cut:
        # Full mode: a single pause (understand -> agent writes narration.json -> produce).
        if uses_narration(args):
            if not narration_json.exists():
                _understand()
                _write_run_manifest(work_dir, video, args)
                _pause(
                    f"{narration_json}",
                    inspect_hint=f"python3 {inspect_py} --work-dir {work_dir} state",
                )
                return
            _reject_stale_manifest()
            _run(
                "video-script", "validate.py", "--work-dir", work_dir,
                "--mode", "full", *_approved_validation_args(args),
            )
            narration_for_tts = narration_json
        elif (work_dir / RUN_MANIFEST).exists():
            _reject_stale_manifest()
        else:
            _write_run_manifest(work_dir, video, args)
        if not uses_narration(args):
            begin_non_narration_qc(work_dir, args, _write_shift_left_stage_qc)
        assemble_video_path = video
    else:
        # Cut mode: cut-first / narrate-second (two pauses), so narration is authored against the
        # REAL output timeline — no source-time mapping exists that could drop/clamp/desync.
        if not clip_plan_json.exists():
            # PASS 1: understand -> agent writes clip_plan.json ONLY.
            if not uses_narration(args) and (work_dir / RUN_MANIFEST).exists():
                _reject_stale_manifest()
            if not uses_narration(args):
                begin_non_narration_qc(work_dir, args, _write_shift_left_stage_qc)
            _understand()
            _write_run_manifest(work_dir, video, args)
            _pause(
                f"{clip_plan_json}（只写剪辑计划；解说下一步对着剪好的成片写）",
                inspect_hint=f"python3 {inspect_py} --work-dir {work_dir} state",
            )
            return
        _reject_stale_manifest()
        if not uses_narration(args):
            begin_non_narration_qc(work_dir, args, _write_shift_left_stage_qc)
        cp_fp = _file_md5(clip_plan_json)
        # Render the cut from clip_plan (narration is authored later, in OUTPUT time).
        crender = [str(video), "--work-dir", str(work_dir)]
        if args.target_duration:
            crender += ["--target-duration", args.target_duration]
        if args.allow_duration_drift:
            crender.append("--allow-duration-drift")
        _run("video-cut", "cut.py", *crender)
        cut_qc = _surface_cut_qc(work_dir)
        _write_shift_left_stage_qc(work_dir, "post_cut", metadata={"cut_qc": cut_qc})
        if uses_narration(args) and not narration_json.exists():
            # PASS 2: rebuild the brief (now an OUTPUT-timeline variant) and pause for narration.
            _rebuild_output_brief()
            _write_phase_ledger(
                work_dir, clip_plan_fingerprint=cp_fp, edited_source_rendered=True
            )
            _pause(
                f"{narration_json}（用成片 OUTPUT 时间轴写解说，对着 {edited_source}）",
                inspect_hint=(
                    f"python3 {inspect_py} --work-dir {work_dir} "
                    "clip-map --output-start <s> --output-end <e>  # 核对输出↔原片时间轴"
                ),
            )
            return
        if uses_narration(args) and _cut_narration_is_stale(
            _read_phase_ledger(work_dir), cp_fp
        ):
            raise SystemExit(
                "clip_plan.json 已改变，但 narration.json 仍是对旧剪辑写的，会与剪后画面对不上。"
                "请删除 narration.json，重跑后按新成片重新写解说。"
            )
        if uses_narration(args):
            _write_phase_ledger(
                work_dir,
                clip_plan_fingerprint=cp_fp,
                narration_fingerprint=_file_md5(narration_json),
                narration_written=True,
            )
            output_duration = _read_video_duration_or_raise(edited_source)
            _run(
                "video-script", "validate.py", "--work-dir", work_dir,
                "--mode", "cut_output", "--output-duration", f"{output_duration:.3f}",
                *_approved_validation_args(args),
            )
            narration_for_tts = narration_json
        else:
            _write_phase_ledger(
                work_dir,
                clip_plan_fingerprint=cp_fp,
                edited_source_rendered=True,
                audio_mode=args.audio_mode,
                audio_stream_index=args.audio_stream_index,
            )
        assemble_video_path = edited_source
    if uses_narration(args):
        review_ran = run_narration_review(
            work_dir,
            args,
            run=_run,
            timeline="cut_output" if cut else "source",
        )
        _write_shift_left_stage_qc(
            work_dir,
            "pre_tts",
            metadata={
                "review_ran": review_ran,
                "timeline": "cut_output" if cut else "source",
            },
        )
        _run(
            "video-voiceover", "voiceover.py",
            *_voiceover_args(work_dir, narration_for_tts, args),
        )
        _write_shift_left_stage_qc(
            work_dir, "post_tts", metadata=_tts_qc_metadata(work_dir)
        )
        overlays_path = _write_canonical_visual_overlays(work_dir, narration_for_tts)
        _write_shift_left_stage_qc(
            work_dir, "pre_assemble", metadata={"visual_overlays": str(overlays_path)}
        )
        _run_mimo_qc_stage(work_dir, args, "pre_assemble")

    aargs = [
        str(assemble_video_path),
        "--work-dir",
        str(work_dir),
        "--recap-stem",
        video.stem,
    ]
    extend_assemble_args(aargs, args)
    if args.output_dir:
        aargs += ["--output-dir", args.output_dir]
    if args.burn_subtitles is not None:
        aargs.append(
            "--burn-subtitles" if args.burn_subtitles else "--no-burn-subtitles"
        )
    if args.subtitle_y_top is not None:
        aargs += [
            "--subtitle-y-top",
            str(args.subtitle_y_top),
            "--subtitle-y-bot",
            str(args.subtitle_y_bot),
        ]
    # env-only burn intent (BURN_SUBTITLES) is propagated implicitly: assemble re-derives it
    # via the same env_bool default the preflight used, so the two agree by shared env.
    if cut:
        # let the timeline / 剪映 export reference the original clips, not edited_source.mp4
        aargs += ["--source-video", str(video)]
    if args.export_jianying:  # env EXPORT_JIANYING is honored by assemble.py itself
        aargs.append("--export-jianying")
    if args.jianying_bundle_media:
        aargs.append("--jianying-bundle-media")
    if args.jianying_no_bundle_media:
        aargs.append("--jianying-no-bundle-media")
    _run("video-assemble", "assemble.py", *aargs)

    final_output = _read_assembly_output(work_dir)
    _write_shift_left_stage_qc(
        work_dir,
        "post_render",
        metadata=_post_render_qc_metadata(work_dir, final_output),
    )
    if uses_narration(args):
        _run_mimo_qc_stage(work_dir, args, "post_render", final_output=final_output)
    _finish_recap(work_dir, final_output, args)
    if uses_narration(args):
        _print_narration_review_pointer(work_dir, review_ran=review_ran)
