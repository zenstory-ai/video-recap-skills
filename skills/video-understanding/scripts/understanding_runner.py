"""Orchestrate the video-understanding stages."""

import argparse


import json

from pathlib import Path

from lib import CONFIG, log, get_video_duration, api_call, offline_ignored_settings

from extract import extract_frames

from detect import detect_scenes, detect_silence_periods, ensure_speech_boundary_anchors

from asr import transcribe_audio
from asr_timing_evidence import write_asr_timing_evidence

from vlm import (
    analyze_scenes,
    analyze_video_overview,
    mimo_video_overview_cache_fresh,
)

from understanding_brief import (
    _finish_brief,
    _research_context,
    _write_brief_from_existing_artifacts,
)
from understanding_storyboard import _write_edited_storyboard_only
from understanding_cache import (
    _asr_cache_payload,
    _asr_cache_state,
    _frames_cache_valid,
    _load_json,
    _merge_overview_into_scenes,
    _present_consolidation_artifacts,
    _remove_stage_meta,
    _scene_cache_payload,
    _silence_cache_payload,
    _stage_cache_valid,
    _vlm_cache_payload,
    _write_consolidation_status,
    _write_frames_manifest,
    _write_mimo_overview_status,
    _write_stage_meta,
)


def _refuse_keyless_overwrite(asr_json):
    """Stop before a key-less ASR miss replaces a real transcript with the [] placeholder.

    The cache can miss for reasons that say nothing about the transcript, e.g. a work_dir
    copied without keeping mtimes changes the recorded artifact identity."""
    if CONFIG["mimo_asr_api_key"] or not asr_json.exists():
        return
    try:
        existing = json.loads(asr_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(existing, list) and existing:
        raise SystemExit(
            "ASR 缓存与当前视频、设置或文件时间不匹配（常见原因：复制 work_dir 或视频时没保留"
            "修改时间），且未设置 MIMO_ASR_API_KEY / MIMO_API_KEY，无法重新转写；"
            f"已保留现有 asr_result.json（{len(existing)} 段），未覆盖。"
            "请用保留时间的方式重新复制（cp -p / cp -Rp / rsync -t），"
            "或设置 MIMO_ASR_API_KEY 或 MIMO_API_KEY 后重跑（会重新转写）。"
            "不要用 --skip-asr 绕过：它会把现有转写替换成 []。"
        )


def main():
    ap = argparse.ArgumentParser(
        description="Analyze a video into an understanding index + narration brief."
    )
    ap.add_argument("video")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument(
        "--context", default="", help="extra context (show name, character names, ...)"
    )
    ap.add_argument("--scene-threshold", type=float, default=None)
    ap.add_argument("--style", default="纪录片")
    ap.add_argument(
        "--edit-mode",
        default=None,
        choices=["full", "cut"],
        help="recap mode to document in the writing brief",
    )
    ap.add_argument(
        "--target-duration",
        default=None,
        help="cut-mode target duration to document in the writing brief",
    )
    ap.add_argument("--skip-asr", action="store_true")
    ap.add_argument("--mimo-video-overview", action="store_true")
    ap.add_argument(
        "--force", action="store_true", help="ignore cached artifacts and recompute"
    )
    ap.add_argument(
        "--brief-only",
        action="store_true",
        help="rebuild agent_narration_brief.md from existing artifacts only; no extraction/API",
    )
    ap.add_argument(
        "--edited-storyboard-only",
        action="store_true",
        help=(
            "write storyboard/edited_storyboard.* from clip_plan_validated.json (single- or "
            "multi-source) and add its pointer to the existing brief; no extraction/API"
        ),
    )
    ap.add_argument(
        "--consolidate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="build the global understanding story index (Pass B); default ON, --no-consolidate to skip",
    )
    ap.add_argument(
        "--consolidate-asr",
        action="store_true",
        help="also clean the ASR transcript (Pass A)",
    )
    args = ap.parse_args()
    if args.brief_only and args.edited_storyboard_only:
        ap.error("--brief-only and --edited-storyboard-only are mutually exclusive")

    video = args.video
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    # Story research (if the agent wrote background_research.json first) feeds the VLM
    # context, so scene analysis can name characters and read scenes with plot knowledge.
    research_ctx = _research_context(work_dir)
    context_parts = [p for p in (research_ctx, args.context) if p and p.strip()]
    if context_parts:
        CONFIG["context_info"] = "　".join(context_parts)
    if research_ctx:
        log(f"已并入 background_research.json 到理解上下文（{len(research_ctx)} 字）")
    if args.scene_threshold is not None:
        CONFIG["scene_threshold"] = args.scene_threshold
    if args.edit_mode is not None:
        CONFIG["edit_mode"] = args.edit_mode
    if args.target_duration is not None:
        CONFIG["target_duration"] = args.target_duration
    if args.mimo_video_overview:
        CONFIG["mimo_video_overview"] = True

    video_duration = get_video_duration(video)
    if CONFIG["fps"] <= 0:
        CONFIG["fps"] = (
            2 if video_duration <= 60 else (1.5 if video_duration <= 300 else 1)
        )
    log(f"FPS: {CONFIG['fps']} (视频时长: {video_duration:.1f}s)")

    if args.brief_only:
        _write_brief_from_existing_artifacts(video, work_dir, args, video_duration)
        return
    if args.edited_storyboard_only:
        _write_edited_storyboard_only(video, work_dir)
        return

    scenes_json = work_dir / "scenes.json"
    asr_json = work_dir / "asr_result.json"
    silence_json = work_dir / "silence_periods.json"
    vlm_json = work_dir / "vlm_analysis.json"
    frames_dir = work_dir / "frames"

    # Step 1: frame extraction
    if not args.force and _frames_cache_valid(video, work_dir, CONFIG["fps"]):
        frames = sorted(frames_dir.glob("frame_*.jpg"))
        log(f"跳过帧提取（缓存匹配 {len(frames)} 帧）")
    else:
        frames = extract_frames(video, work_dir)
        _write_frames_manifest(work_dir, video, CONFIG["fps"], frames)

    # Step 2: scene detection
    scenes_meta = _scene_cache_payload(video)
    if not args.force and _stage_cache_valid(scenes_json, scenes_meta):
        scenes = _load_json(scenes_json)
        log(f"跳过场景检测（已存在 {len(scenes)} 个场景）")
    else:
        scenes = detect_scenes(video, work_dir, CONFIG["scene_threshold"])
        _write_stage_meta(scenes_json, scenes_meta)

    # The VLM cache does not depend on ASR, so an offline run that would stop at Step 4 stops
    # here instead, before Step 3 can rewrite the transcript or the silence windows.
    vlm_meta = _vlm_cache_payload(video, work_dir, scenes_json, frames)
    vlm_offline = offline_ignored_settings(CONFIG["api_key"], "api_url")
    vlm_cached = not args.force and _stage_cache_valid(
        vlm_json, vlm_meta, ignore_settings=vlm_offline
    )
    if not vlm_cached and not CONFIG["api_key"]:
        key_name = CONFIG["api_env_var"]
        raise SystemExit(f"请设置 {key_name} 环境变量（VLM 画面分析需要）")

    # Step 3: ASR
    asr_meta = _asr_cache_payload(video, skip_asr=args.skip_asr)
    cache_state = None
    if not args.skip_asr and not args.force:
        cache_state = _asr_cache_state(asr_json, asr_meta, video)
    if args.skip_asr:
        asr_result = []
        asr_json.write_text(
            json.dumps(asr_result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_stage_meta(asr_json, asr_meta)
        write_asr_timing_evidence(
            work_dir, video, "EXPLICITLY_SKIPPED", final_segments=asr_result
        )
        log("跳过 ASR（--skip-asr）")
    elif cache_state == "FRESH":
        asr_result = _load_json(asr_json)
        log(f"跳过 ASR（证据匹配，已存在 {len(asr_result)} 段）")
    else:
        _refuse_keyless_overwrite(asr_json)
        try:
            asr_result = transcribe_audio(video, work_dir)
        except Exception as e:
            _remove_stage_meta(asr_json)
            asr_json.unlink(missing_ok=True)
            raise RuntimeError(
                f"ASR 失败；未写入可复用缓存，请修复后重试或显式使用 --skip-asr: {e}"
            ) from e
        _write_stage_meta(asr_json, asr_meta)

    # Step 3.5: silence detection
    silence_meta = _silence_cache_payload(video, asr_json)
    if not args.force and _stage_cache_valid(silence_json, silence_meta):
        silence_periods = _load_json(silence_json)
        log(f"跳过静音检测（已存在 {len(silence_periods)} 个窗口）")
        ensure_speech_boundary_anchors(work_dir, asr_result)
    else:
        silence_periods = detect_silence_periods(video, work_dir, asr_result)
        _write_stage_meta(silence_json, silence_meta)

    # Step 4: VLM analysis (the only stage that requires the chat API key)
    if vlm_cached:
        vlm_analysis = _load_json(vlm_json)
        log(f"跳过 VLM 分析（已存在 {len(vlm_analysis)} 个场景）")
    else:
        log("VLM API 连通性预检...")
        api_call(
            {
                "model": CONFIG["vlm_model"],
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5,
            }
        )
        vlm_analysis = analyze_scenes(scenes, frames, work_dir, resume=not args.force)
        _write_stage_meta(vlm_json, vlm_meta)

    # Step 4.1: optional MiMo scene-chunk video understanding
    overview_path = work_dir / "mimo_video_overview.json"
    if CONFIG["mimo_video_overview"]:
        if not CONFIG["mimo_video_api_key"] and mimo_video_overview_cache_fresh(
            overview_path, video, scenes
        ):
            log("未设置 MIMO_API_KEY，复用已缓存的 MiMo 分片视频概览")
            _write_mimo_overview_status(
                work_dir, "cached", "未设置 MIMO_API_KEY，复用缓存", overview_path.name
            )
        elif not CONFIG["mimo_video_api_key"]:
            log("跳过 MiMo 分片视频概览：未设置 MIMO_API_KEY")
            overview_path.unlink(missing_ok=True)
            _write_mimo_overview_status(
                work_dir,
                "skipped_no_key",
                "未设置 MIMO_API_KEY，MiMo 分片视频概览未运行",
                None,
            )
        elif mimo_video_overview_cache_fresh(overview_path, video, scenes):
            log("跳过 MiMo 分片视频概览（缓存匹配）")
            _write_mimo_overview_status(
                work_dir, "cached", "缓存匹配", overview_path.name
            )
        else:
            overview_path.unlink(missing_ok=True)
            try:
                overview = analyze_video_overview(video, work_dir, scenes)
            except Exception as e:
                log(f"MiMo 分片视频概览失败（忽略）: {e}")
                _write_mimo_overview_status(work_dir, "failed", e, None)
            else:
                if overview:
                    _write_mimo_overview_status(
                        work_dir, "ok", "MiMo 分片视频概览完成", overview_path.name
                    )
                else:
                    _write_mimo_overview_status(
                        work_dir, "failed", "MiMo 分片视频概览未产出有效 artifact", None
                    )
    else:
        overview_path.unlink(missing_ok=True)
        _write_mimo_overview_status(
            work_dir, "disabled", "MiMo 分片视频概览未启用", None, enabled=False
        )

    # Make the video-overview the primary per-scene description (frame_facts stay the anchor).
    # No-op/revert when overview is absent, so disabling it cleanly returns to frame descriptions.
    vlm_analysis = _merge_overview_into_scenes(vlm_analysis, overview_path)

    # optional consolidation (整理): build the understanding index before the brief folds it in
    enabled = bool(args.consolidate or args.consolidate_asr)
    status, message, artifacts = "disabled", "consolidation 未启用", []
    if enabled:
        from consolidate import consolidate

        failure = None
        result = {}
        try:
            result = consolidate(
                work_dir, do_asr=args.consolidate_asr, do_index=args.consolidate
            )
        except Exception as e:
            log(f"consolidate 跳过（忽略）: {e}")
            failure = e
        artifacts = _present_consolidation_artifacts(work_dir)
        no_key = result.get("skipped_no_key") or []
        if failure is not None:
            status, message = "failed", failure
        else:
            expected = []
            skipped = []
            if args.consolidate and "index" not in no_key:
                if vlm_analysis:
                    expected.append("understanding_index.json")
                else:
                    skipped.append("无 vlm_analysis，跳过 index")
            if args.consolidate_asr and "asr" not in no_key:
                if asr_result:
                    expected.append("asr_clean.json")
                else:
                    skipped.append("无 ASR 文本，跳过 ASR 清洗")
            missing = [name for name in expected if name not in artifacts]
            if missing:
                status, message = "failed", f"未产出预期 artifact: {', '.join(missing)}"
            elif no_key:
                status, message = (
                    "skipped_no_key",
                    f"未设置 {CONFIG['api_env_var']}，consolidation（{', '.join(no_key)}）未发送请求",
                )
            elif expected:
                status, message = "ok", "consolidation 完成"
            else:
                status, message = "skipped", "；".join(skipped) or "无可整理输入"
    _write_consolidation_status(
        work_dir,
        status,
        message,
        artifacts,
        enabled=enabled,
        do_asr=args.consolidate_asr,
        do_index=args.consolidate,
    )

    _finish_brief(
        video, work_dir, args, video_duration,
        storyboard_scenes=scenes, brief_scenes=vlm_analysis,
        asr_result=asr_result, silence_periods=silence_periods,
        status="analyzed", done_label="理解完成", force=args.force,
    )
