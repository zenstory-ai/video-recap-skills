"""Canonical CLI and programmatic render entry for the self-contained video-assemble skill."""

import json
import os
from pathlib import Path

import artifacts
import assemble_constants as constants
import assembly_contract
import assembly_settings
import audio_mix
import audio_mix_binding
import frozen_audio
import media
import narration_audio
import narration_binding
import pair_media
import render_preflight
import strict_publish
import subtitle_render
import subtitle_track_binding
import timeline_emit
import visual_render
import lib
from assembly_settings import assembly_settings_payload
from audio_mix import final_loudnorm_filter

__all__ = [
    "assemble_video",
    "assembly_settings_payload",
    "final_loudnorm_filter",
    "main",
]


AUDIO_MODES = ("narration", "source-mix", "adopted-packet-copy")


_current_narration_binding = strict_publish.current_narration_binding
_current_audio_mix_binding = strict_publish.current_audio_mix_binding


def assemble_video(input_video, tts_segments, work_dir, output_path, *,
                   audio_mode="narration", audio_stream_index=0,
                   narration_adoption_path=None, tts_meta_path=None,
                   audio_mix_adoption_path=None):
    """组装最终视频"""
    if audio_mode not in AUDIO_MODES:
        raise RuntimeError(f"不支持的 audio_mode: {audio_mode}")
    if isinstance(audio_stream_index, bool) or not isinstance(audio_stream_index, int) or audio_stream_index < 0:
        raise RuntimeError("audio_stream_index 必须是非负整数")
    if audio_mode == "narration" and not tts_segments:
        raise RuntimeError("tts_meta.json 没有有效解说音频，已中止以避免生成无解说视频")
    if audio_mode == "narration" and audio_stream_index != 0:
        raise RuntimeError("narration 当前不支持非零 audio_stream_index")
    if audio_mode == "source-mix" and tts_segments:
        raise RuntimeError("source-mix 与 TTS 解说不兼容")
    if audio_mode != "narration" and tts_meta_path is not None:
        raise RuntimeError(f"tts_meta 与 audio_mode {audio_mode} 不兼容")
    bgm_path = lib.CONFIG["bgm_path"]
    has_bgm = bool(bgm_path) and os.path.exists(bgm_path)
    if audio_mode == "source-mix" and bgm_path and not has_bgm:
        raise RuntimeError(f"source-mix 声明的 BGM 文件不存在: {bgm_path}")
    if (
        audio_mode != "narration"
        and audio_stream_index != 0
        and lib.CONFIG["export_jianying"]
    ):
        raise RuntimeError("剪映导出当前不支持选择非零音频流")
    if audio_mode == "adopted-packet-copy":
        if tts_segments:
            raise RuntimeError("adopted-packet-copy 与 TTS 解说不兼容")
        if bgm_path:
            raise RuntimeError("adopted-packet-copy 与 BGM 混音不兼容")
    if audio_mode != "narration" and narration_adoption_path is not None:
        raise RuntimeError("narration adoption 仅适用于 narration audio_mode")
    if audio_mix_adoption_path is not None and (
        audio_mode != "narration" or narration_adoption_path is None or tts_meta_path is None
    ):
        raise RuntimeError("audio mix adoption 要求 narration 模式及显式 narration adoption/tts_meta")

    published_output = Path(output_path)
    if audio_mix_adoption_path is not None and published_output.exists():
        raise RuntimeError("显式音频混合要求新的 output_path，不能覆盖已有成片")
    explicit_mix = (
        audio_mix_binding.load_adoption(
            audio_mix_adoption_path, input_video=input_video,
            narration_adoption_path=narration_adoption_path, tts_segments=tts_segments,
        )
        if audio_mix_adoption_path is not None else None
    )

    binding = narration_binding.prepare_binding(
        tts_segments, work_dir,
        narration_adoption_path=narration_adoption_path,
        tts_meta_path=tts_meta_path,
    ) if audio_mode == "narration" else None
    render_output = published_output
    if binding and binding["active"]:
        if published_output.exists():
            raise RuntimeError("身份约束渲染要求新的 output_path，不能覆盖已有成片")
        render_output = published_output.with_name(
            f".{published_output.stem}.narration-rendering{published_output.suffix}"
        )
        render_output.unlink(missing_ok=True)
    output_path = render_output
    video_duration = lib.get_video_duration(input_video)
    canvas = media._probe_canvas(input_video)  # drives subtitle PlayRes/scale so 竖屏 text isn't stretched
    burn_subtitles = lib.CONFIG["burn_subtitles"]
    subtitle_track_binding.prepare_subtitle_track(
        input_video,
        work_dir,
        video_duration,
        audio_mode=audio_mode,
        selected_audio_stream=audio_stream_index,
    )
    adopted_source_audio = None
    if audio_mode == "adopted-packet-copy":
        adopted_source_audio = frozen_audio.validate_adopted_source(
            input_video, audio_stream_index
        )
        if type(adopted_source_audio["sample_rate"]) is not int \
                or adopted_source_audio["sample_rate"] <= 0:
            raise RuntimeError("adopted audio sample rate must be a positive integer")
    elif audio_mode == "source-mix":
        frozen_audio.probe_audio_packets(input_video, audio_stream_index)

    # 解说整体提速（可选）后，将所有 TTS 片段按时间位置合成到与视频等长的音轨上
    narration_wav = None
    if audio_mode == "narration" and explicit_mix is not None:
        explicit_runtime = audio_mix_binding.render_explicit_mix(
            explicit_mix, binding, tts_segments, work_dir
        )
        narration_wav = Path(explicit_runtime["voice_bus"]["path"])
    elif audio_mode == "narration":
        if binding["tempo_policy"]:
            narration_audio._apply_narration_speed(
                tts_segments, work_dir, tempo_policy=binding["tempo_policy"]
            )
        else:
            narration_audio._apply_narration_speed(tts_segments, work_dir)
        narration_wav = work_dir / "narration.wav"
        if binding["tempo_policy"]:
            narration_audio._build_timed_narration(
                tts_segments, narration_wav, video_duration, work_dir,
                tempo_policy=binding["tempo_policy"],
            )
            if any(
                segment.get("blocking") or segment.get("fit_status") == "no_safe_fit"
                for segment in tts_segments
            ):
                raise RuntimeError(
                    "严格 narration adoption 存在 no_safe_fit，禁止提速或裁尾渲染"
                )
        else:
            narration_audio._build_timed_narration(
                tts_segments, narration_wav, video_duration, work_dir
            )
        handoffs = audio_mix._apply_source_sentence_handoffs(tts_segments, work_dir, video_duration)
        if handoffs:
            lib.log(
                "原声句末交接: "
                + ", ".join(
                    f"{item['end']:.2f}s→{item.get('restore_at', item['end']):.2f}s({item['status']})"
                    for item in handoffs
                )
            )
        narration_binding.seal_render_inputs(binding, tts_segments, narration_wav)

    # 始终生成 SRT 字幕文件（原声留白处补烧原声字幕，传入成片时长以计算留白区间）
    srt_path = subtitle_render._generate_srt(tts_segments, work_dir, video_duration)
    lib.log(f"字幕文件: {srt_path}")
    ass_path = None
    if burn_subtitles:
        ass_path = subtitle_render._generate_ass(tts_segments, work_dir, video_duration, canvas)
        lib.log(f"压制字幕文件: {ass_path}")

    # 可选 BGM：作为一条独立音轨（input [2:a]）混入，旁白处自动压低
    if explicit_mix is not None:
        lib.log("显式 adopted full-sound：忽略环境 BGM/duck/loudnorm/tempo 配置")
    elif bgm_path and not has_bgm:
        lib.log(f"  ⚠️ BGM 文件不存在，跳过: {bgm_path}")
    elif has_bgm:
        lib.log(f"BGM 铺底: {bgm_path} (音量 {lib.CONFIG['bgm_volume']}，旁白时 {lib.CONFIG['bgm_ducking_volume']})")

    # 多轨时间线模型（timeline.json）：canonical 渲染仍是 ffmpeg，此模型供检视/可选导出
    timeline_emit._emit_timeline(
        input_video, tts_segments, work_dir, video_duration, canvas, has_bgm,
        audio_mode=audio_mode, selected_audio_stream=audio_stream_index,
        explicit_audio_mix=(
            {**explicit_mix, **explicit_mix["runtime"]} if explicit_mix is not None else None
        ),
    )

    overlay_filters, overlay_qc = visual_render._visual_overlay_filters(work_dir, canvas, video_duration)
    mask_filter = visual_render._source_subtitle_mask_filter(canvas, work_dir, tts_segments, video_duration)
    visual_qc = visual_render._build_visual_qc(
        tts_segments,
        work_dir,
        video_duration,
        canvas,
        overlay_qc=overlay_qc,
        mask_filter=mask_filter,
    )
    visual_render._write_visual_qc(work_dir, visual_qc)
    assembly_qc_path = Path(work_dir) / constants.ASSEMBLY_QC
    assembly_qc_path.unlink(missing_ok=True)
    if visual_qc["blocking"]:
        codes = ", ".join(visual_qc["blocking_codes"])
        raise RuntimeError(f"视觉 QC 失败: {codes}；详见 {Path(work_dir) / constants.VISUAL_QC}")

    # Select exactly one of three explicit audio paths. Only narration may synthesize
    # a missing original track; adopted copy never decodes, mixes, normalizes or trims.
    source_has_audio = media._has_audio_stream(input_video)
    adopted_audio = None
    loudnorm_measurement = None
    original_audio_input = []
    bgm_input = []
    filter_complex = None
    filter_args = []
    audio_input_args = []
    fc_script = None
    if audio_mode == "adopted-packet-copy":
        audio_map = f"0:a:{audio_stream_index}"
    elif audio_mode == "source-mix":
        audio_map = "[aoutln]"
        source_label = f"0:a:{audio_stream_index}"
        filter_complex = f"[{source_label}]volume={lib.CONFIG['idle_orig_volume']}[source]"
        if has_bgm:
            bgm_input = ["-stream_loop", "-1", "-i", str(bgm_path)]
            filter_complex += (
                f";[1:a]volume={lib.CONFIG['bgm_volume']}[bgm]"
                ";[source][bgm]amix=inputs=2:duration=first:dropout_transition=0[aout]"
            )
        else:
            filter_complex += ";[source]anull[aout]"
        final_ln = audio_mix.final_loudnorm_filter()
        filter_complex += f";[aout]{final_ln}[aoutln]"
        lib.log(f"source-mix 音频处理: source volume + {final_ln}")
    elif explicit_mix is not None:
        audio_map = "1:a:0"
        audio_input_args = ["-i", explicit_mix["runtime"]["master"]["path"]]
    else:
        # 混合原始音频 + 解说音频（+ 可选 BGM）
        if source_has_audio:
            original_audio_label = "0:a"
            bgm_audio_label = "2:a"
        else:
            lib.log("源视频无音轨，使用静音原声音轨进行混音")
            original_audio_input = [
                "-f", "lavfi", "-t", str(video_duration),
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
            original_audio_label = "2:a"
            bgm_audio_label = "3:a"
        filter_complex = audio_mix._build_audio_filter_complex(
            tts_segments,
            has_bgm,
            original_audio_label=original_audio_label,
            bgm_audio_label=bgm_audio_label,
        )
        # BGM is input [2:a]; -stream_loop -1 loops it to cover the whole timeline (amix
        # duration=first + -t trim it back to the video length).
        bgm_input = ["-stream_loop", "-1", "-i", str(bgm_path)] if has_bgm else []
        # 末端整体响度归一：ducking 只管相对平衡，这一步统一成片绝对响度
        loudnorm_measurement = audio_mix._run_loudnorm_first_pass(
            input_video,
            narration_wav,
            original_audio_input,
            bgm_input,
            filter_complex,
            work_dir,
        )
        final_ln = audio_mix.final_loudnorm_filter(loudnorm_measurement)
        filter_complex += f";[aout]{final_ln}[aoutln]"
        lib.log(f"成片响度归一: {final_ln}")
        audio_map = "[aoutln]"
        audio_input_args = ["-i", str(narration_wav), *original_audio_input]

    # 对于超长 volume 表达式（多段解说），使用 -filter_complex_script 避免命令行溢出
    if filter_complex is not None:
        if len(filter_complex.encode("utf-8")) > constants.FILTER_SCRIPT_THRESHOLD_BYTES:
            fc_script = Path(work_dir) / ".filter_complex.txt"
            fc_script.write_text(filter_complex, encoding="utf-8")
            lib.log(f"使用 filter_complex_script (表达式长度 {len(filter_complex.encode('utf-8'))} bytes)")
            filter_args = ["-filter_complex_script", str(fc_script)]
        else:
            filter_args = ["-filter_complex", filter_complex]
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_video),
        *audio_input_args,
        *bgm_input,
        *filter_args,
        # 0:v:0 (not 0:v): sources with attached cover art carry a second video stream.
        # -vf only ever applies to the first one, so mapping all of them makes ffmpeg
        # abort with "Could not write header (incorrect codec parameters ?)" and leave
        # an unreadable file — after the whole pipeline has already run.
        "-map", "0:v:0", "-map", audio_map,
    ]

    # Video filter chain: mask source subtitles first (drawbox), then burn our subtitles
    # on top. Either one forces a re-encode; with neither, the video stream is copied.
    crf = str(lib.CONFIG["output_crf"])  # env_int already clamps to >=0; keep 0 (lossless) intact
    preset = lib.CONFIG["output_preset"]
    max_h = lib.CONFIG["output_max_height"]
    vf_chain = []
    if mask_filter:
        vf_chain.append(mask_filter)
    vf_chain.extend(overlay_filters)
    if burn_subtitles:
        vf_chain.append(visual_render._subtitle_burn_filter(ass_path))
    # Downscale LAST so the mask + burned subtitles render at native resolution and are then
    # scaled down with the frame (crisp). The helper forces both dimensions even so an odd
    # OUTPUT_MAX_HEIGHT can't crash libx264; 'min(ih,H)' only ever shrinks the source.
    if max_h > 0:
        vf_chain.append(visual_render._output_downscale_filter(max_h))
    # yuv420p: 10-bit/4:2:2 sources re-encoded as-is play on desktop but fail on WeChat/
    # mobile/Safari; force 8-bit 4:2:0 so every recap is universally decodable. yuv420p also
    # needs EVEN width AND height, so normalize odd dims (4:2:2/4:4:4 permit them) before the
    # encode — otherwise libx264 aborts to a 0-byte file. The downscale helper already evens out.
    even = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    reencode = bool(vf_chain) or lib.CONFIG["force_video_reencode"]
    notes = []
    video_filter_script = None
    if vf_chain:
        if max_h <= 0:  # no downscale in the chain to force even dims
            vf_chain.append(even)
        video_filter = ",".join(vf_chain)
        if len(video_filter.encode("utf-8")) > constants.FILTER_SCRIPT_THRESHOLD_BYTES:
            video_filter_script = Path(work_dir) / ".video_filter.txt"
            video_filter_script.write_text(video_filter, encoding="utf-8")
            cmd += ["-filter_script:v:0", str(video_filter_script)]
            lib.log(
                "使用 video filter script "
                f"(表达式长度 {len(video_filter.encode('utf-8'))} bytes)"
            )
        else:
            cmd += ["-vf", video_filter]
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"]
        notes = ((["遮挡原字幕"] if mask_filter else [])
                 + ([f"视觉叠加×{len(overlay_filters)}"] if overlay_filters else [])
                 + (["压制解说字幕"] if burn_subtitles else [])
                 + ([f"缩放≤{max_h}p"] if max_h > 0 else []))
        lib.log(f"视频重编码: {' + '.join(notes)} (crf={crf}, preset={preset})")
    elif reencode:
        notes = ["force_video_reencode"]
        cmd += ["-vf", even, "-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-c:v", "copy"]

    # +faststart relocates the moov atom to the front so web/social players can start
    # before the full file downloads; valid (and beneficial) on the copy path too.
    if audio_mode == "adopted-packet-copy":
        # No -t/-shortest: either would discard valid AAC priming or tail packets.
        cmd += ["-c:a", "copy", "-movie_timescale",
                str(adopted_source_audio["sample_rate"]),
                "-movflags", "+faststart", str(output_path)]
    elif explicit_mix is not None:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-movie_timescale", "48000", "-movflags", "+faststart",
                str(output_path)]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                "-t", str(video_duration), str(output_path)]
    try:
        result = lib.run_cmd(cmd)
        if result.returncode != 0:
            raise RuntimeError(f"视频组装失败: {result.stderr}")
    finally:
        # 清理临时 filter 脚本（无论 ffmpeg 是否成功）
        if fc_script is not None:
            fc_script.unlink(missing_ok=True)
        if video_filter_script is not None:
            video_filter_script.unlink(missing_ok=True)

    if audio_mode == "adopted-packet-copy":
        adopted_audio = frozen_audio.verify_adopted_audio(
            input_video, output_path, audio_stream_index
        )
        try:
            pair_media.validate_aac_packet_interval(adopted_audio["output"])
        except ValueError:
            output_path.unlink(missing_ok=True)
            raise
    subtitle_track_binding.verify_rendered_picture(work_dir, output_path)
    audio_operations = {
        "narration": audio_mode == "narration",
        "source_mix": audio_mode == "source-mix",
        "bgm_mix": has_bgm and audio_mode != "adopted-packet-copy" and explicit_mix is None,
        "ducking": audio_mode == "narration" and explicit_mix is None,
        "loudness_normalization": (
            audio_mode != "adopted-packet-copy" and explicit_mix is None
            and lib.CONFIG["final_loudnorm"]
        ),
        "limiter": audio_mode != "adopted-packet-copy" and explicit_mix is None,
        "resample": audio_mode != "adopted-packet-copy",
        "tempo": audio_mode == "narration" and explicit_mix is None,
        "packet_copy": audio_mode == "adopted-packet-copy",
    }
    if explicit_mix is not None:
        audio_operations["explicit_audio_mix"] = True
    render_delivery = {
        "video_encode_passes": 1 if reencode else 0,
        "reencode_reason": notes,
        "audio_sample_rate": (
            adopted_audio["output"]["sample_rate"] if adopted_audio else 48000
        ),
        "final_compat_notes": (
            (["yuv420p"] if reencode else ["video_copy"])
            + (["aac_packet_copy", "faststart"] if adopted_audio else ["aac_48000", "faststart"])
        ),
    }
    loudness_mode = (
        "not_run" if audio_mode == "adopted-packet-copy" else
        "fixed_master_gain_no_loudnorm" if explicit_mix is not None else None
    )
    source_audio_status = "prepared_bed_adopted" if explicit_mix is not None else None
    render_output = strict_publish.publish_render(
        work_dir=work_dir, binding=binding, explicit_mix=explicit_mix,
        tts_segments=tts_segments, narration_wav=narration_wav,
        render_output=render_output, published_output=published_output,
        audio_mode=audio_mode, audio_operations=audio_operations,
        adopted_audio=adopted_audio, loudness_mode=loudness_mode,
        loudnorm_measurement=loudnorm_measurement, visual_qc=visual_qc,
        source_has_audio=source_has_audio, video_duration=video_duration,
        render_delivery=render_delivery, source_audio_status=source_audio_status,
    )
    lib.log(f"最终视频: {render_output} ({render_output.stat().st_size / 1024 / 1024:.1f}MB)")
    return render_output


def main():
    import argparse
    import shutil
    ap = argparse.ArgumentParser(
        description="video-assemble: mux narration audio over the video, duck the original, render subtitles.")
    ap.add_argument("video", help="source video (edited_source.mp4 in cut mode, else the original)")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--tts-meta", default=None, help="tts_meta.json (default: <work-dir>/tts_meta.json)")
    ap.add_argument("--narration-adoption", default=None,
                    help="strict narration_adoption v1 bound to an explicit --tts-meta")
    ap.add_argument("--audio-mix-adoption", default=None,
                    help="strict audio_mix_adoption v1 for adopted prepared bed and narration")
    ap.add_argument(
        "--audio-mode", choices=AUDIO_MODES,
        default="narration", help="audio path (default: narration)",
    )
    ap.add_argument(
        "--audio-stream-index", type=int, default=0,
        help="zero-based input audio stream ordinal for source/adopted modes",
    )
    ap.add_argument("--recap-stem", default=None, help="final recap filename stem (default: video stem)")
    ap.add_argument("--output-dir", default=None)
    ap.add_argument("--burn-subtitles", action=argparse.BooleanOptionalAction, default=None,
                    help="burn narration subtitles into the video (default on; --no-burn-subtitles to disable)")
    ap.add_argument("--subtitle-y-top", type=int, default=None,
                    help="inclusive top of a measured subtitle band in display-frame pixels")
    ap.add_argument("--subtitle-y-bot", type=int, default=None,
                    help="exclusive bottom of a measured subtitle band in display-frame pixels")
    ap.add_argument("--source-video", default=None,
                    help="original source video (cut mode) so timeline.json / 剪映 export reference the real clips")
    ap.add_argument("--export-jianying", action="store_true",
                    help="also export an OPTIONAL 剪映/JianYing draft from timeline.json after rendering")
    ap.add_argument("--jianying-out", default=None, help="parent dir for the 剪映 draft (default: work-dir)")
    bundle_group = ap.add_mutually_exclusive_group()
    bundle_group.add_argument("--jianying-bundle-media", dest="jianying_bundle_media", action="store_true",
                              help="copy media into the 剪映 draft folder (default on; portable/self-contained)")
    bundle_group.add_argument("--jianying-no-bundle-media", dest="jianying_bundle_media", action="store_false",
                              help="do NOT copy media into the draft — reference in place (only if 剪映 can read those paths; macOS 剪映 usually cannot)")
    ap.set_defaults(jianying_bundle_media=None)
    args = ap.parse_args()
    work_dir = Path(args.work_dir)
    if args.burn_subtitles is not None:
        lib.CONFIG["burn_subtitles"] = args.burn_subtitles
    if (args.subtitle_y_top is None) != (args.subtitle_y_bot is None):
        ap.error("--subtitle-y-top and --subtitle-y-bot must be provided together")
    if args.subtitle_y_top is not None:
        if args.subtitle_y_top < 0 or args.subtitle_y_bot <= args.subtitle_y_top:
            ap.error("subtitle Y coordinates must satisfy 0 <= top < bot")
        lib.CONFIG["subtitle_y_top"] = args.subtitle_y_top
        lib.CONFIG["subtitle_y_bot"] = args.subtitle_y_bot
        lib.CONFIG["mask_source_subtitles"] = True
        lib.CONFIG["source_subtitle_mask_policy"] = "opt_in"
        lib.CONFIG["source_subtitle_mask_policy_declared"] = True
        # A measured band is an explicit request to conceal the known source-caption
        # pixels.  The general 0.6 translucent look can leave white glyphs visible under
        # the generated subtitles; use an opaque mask unless the caller deliberately
        # chose a different opacity through the existing environment override.
        if "SUBTITLE_MASK_OPACITY" not in os.environ:
            lib.CONFIG["subtitle_mask_opacity"] = 1.0
    if args.source_video:
        if not os.path.exists(args.source_video):
            ap.error(f"--source-video does not exist: {args.source_video}")
        lib.CONFIG["source_video"] = args.source_video
        lib.CONFIG["source_video_explicit"] = True
    else:
        # SOURCE_VIDEO is an ambient env var in lib.CONFIG. Do not let a stale
        # shell value silently bind full-mode/direct timeline.json or JianYing
        # exports to an unrelated original; cut mode must pass --source-video.
        lib.CONFIG["source_video"] = ""
        lib.CONFIG["source_video_explicit"] = False
    if args.export_jianying:
        lib.CONFIG["export_jianying"] = True
    if args.jianying_bundle_media is not None:
        lib.CONFIG["jianying_bundle_media"] = args.jianying_bundle_media
    render_preflight._preflight_burn_subtitles()  # fail before the render if burn-in is on but ffmpeg lacks libass
    # Argument combinations are validated once, by assemble_video.
    tts_meta = Path(args.tts_meta) if args.tts_meta else None
    tts_segments = []
    if args.audio_mode == "narration":
        tts_meta = tts_meta or work_dir / "tts_meta.json"
        tts_segments = json.loads(tts_meta.read_text(encoding="utf-8"))["segments"]
    stem = args.recap_stem or Path(args.video).stem
    base = Path(args.output_dir) if args.output_dir else work_dir.parent
    final_output = assembly_contract._resolve_final_output(base, stem)
    if args.audio_mix_adoption is not None and final_output.exists():
        ap.error("explicit audio mix requires a new final delivery path")
    delivery_stage = None
    owned_alias = None
    output_path = work_dir / "output.mp4"
    try:
        assemble_video(
            args.video, tts_segments, work_dir, output_path,
            audio_mode=args.audio_mode, audio_stream_index=args.audio_stream_index,
            narration_adoption_path=args.narration_adoption, tts_meta_path=tts_meta,
            audio_mix_adoption_path=args.audio_mix_adoption,
        )
        assembly_qc = artifacts._load_work_json(work_dir, constants.ASSEMBLY_QC)
        if assembly_qc["blocking"]:
            codes = ", ".join(assembly_qc["blocking_codes"])
            raise SystemExit(
                f"组装 QC 阻断交付: {codes}；详见 {work_dir / constants.ASSEMBLY_QC}"
            )
        base.mkdir(parents=True, exist_ok=True)
        if args.audio_mix_adoption is not None:
            # Publish the delivery alias only when this process created it: stage a copy,
            # hard-link it into place (fails if the alias appeared meanwhile), remember the
            # inode, and roll back only an alias we own.
            delivery_stage = final_output.with_name(
                f".{final_output.name}.rendering-{os.getpid()}"
            )
            with output_path.open("rb") as source, delivery_stage.open("xb") as target:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
            try:
                os.link(delivery_stage, final_output)
            except FileExistsError as exc:
                raise RuntimeError("explicit audio mix delivery alias appeared during render") from exc
            stat = final_output.stat()
            owned_alias = (stat.st_dev, stat.st_ino)
            delivery_stage.unlink()
            delivery_stage = None
        else:
            shutil.copy2(str(output_path), str(final_output))
        manifest = assembly_contract._assembly_manifest_payload(
            args.video, tts_segments, work_dir, output_path,
            tts_meta_path=tts_meta,
            narration_input_binding=_current_narration_binding(work_dir, args.audio_mode),
            audio_mix_binding=_current_audio_mix_binding(work_dir, args.audio_mode),
            final_output=final_output,
            settings_payload=assembly_settings.assembly_settings_payload,
            audio_mode=args.audio_mode,
            audio_stream_index=args.audio_stream_index,
        )
        assembly_contract._write_assembly_manifest(work_dir, manifest)
    except BaseException:
        if delivery_stage is not None:
            delivery_stage.unlink(missing_ok=True)
        if owned_alias is not None and final_output.exists():
            stat = final_output.stat()
            if (stat.st_dev, stat.st_ino) == owned_alias:
                final_output.unlink()
        raise
    lib.log(f"组装完成: {final_output}")

    # OPTIONAL, decoupled: export a 剪映 draft from the timeline (lazy import; never
    # required by the core render path).
    if lib.CONFIG["export_jianying"]:
        from jianying.optional import maybe_export_jianying
        maybe_export_jianying(work_dir, args.jianying_out, stem)

    print(json.dumps({"status": "assembled", "output": str(final_output), "work_dir": str(work_dir)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
