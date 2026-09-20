"""Staged publish transaction for identity-bound (strict adoption) renders.

An active narration binding renders to a hidden candidate file. The binding
(and, for an explicit mix, the audio mix binding) is staged beside it, QC gates
the candidate, and only then are the media file and its bindings published
together. Any failure rolls back every published or staged piece, so a strict
output never exists without the evidence that binds it.
"""

from pathlib import Path

import assembly_contract
import audio_mix_binding
import narration_binding


def current_narration_binding(work_dir, audio_mode):
    """Read narration evidence only for the explicit narration render path."""
    if audio_mode != "narration":
        return None
    return narration_binding.binding_fingerprint(work_dir)


def current_audio_mix_binding(work_dir, audio_mode):
    if audio_mode != "narration":
        return None
    return audio_mix_binding.binding_fingerprint(work_dir)


def publish_render(*, work_dir, binding, explicit_mix, tts_segments, narration_wav,
                   render_output, published_output, audio_mode, audio_operations,
                   adopted_audio, loudness_mode, loudnorm_measurement, visual_qc,
                   source_has_audio, video_duration, render_delivery, source_audio_status):
    """Gate the rendered candidate on assembly QC and publish it with its bindings.

    Returns the final output path. Legacy (inactive binding) renders only write
    their binding and QC; strict renders publish file and bindings atomically or
    roll everything back.
    """
    active = bool(binding and binding["active"])
    staged_binding = None
    staged_binding_fingerprint = None
    binding_published = False
    staged_mix_binding = None
    staged_mix_fingerprint = None
    mix_binding_published = False
    try:
        if active:
            # Strict adoption: render to a hidden candidate, stage the binding, gate on QC,
            # then publish the file and the binding together or neither.
            narration_binding.assert_current(binding)
            report, staged_binding = narration_binding.stage_final_binding(
                binding, tts_segments, narration_wav, render_output, published_output
            )
            staged_binding_fingerprint = narration_binding.staged_binding_fingerprint(
                report, staged_binding, Path(work_dir) / narration_binding.FILENAME
            )
            if explicit_mix is not None:
                mix_report, staged_mix_binding = audio_mix_binding.stage_final_binding(
                    explicit_mix, staged_binding_fingerprint, render_output, published_output
                )
                staged_mix_fingerprint = audio_mix_binding.staged_binding_fingerprint(
                    mix_report, staged_mix_binding,
                    Path(work_dir) / audio_mix_binding.FILENAME,
                )
        elif binding:
            narration_binding.finalize_binding(
                binding, tts_segments, narration_wav, render_output
            )
        assembly_qc = assembly_contract._build_assembly_qc(
            tts_segments,
            video_duration,
            output_path=(published_output if active else render_output),
            source_has_audio=source_has_audio,
            loudness_mode=loudness_mode,
            loudnorm_measurement=loudnorm_measurement,
            visual_qc=visual_qc,
            audio_mode=audio_mode,
            audio_operations=audio_operations,
            adopted_audio=adopted_audio,
            narration_input_binding=(
                staged_binding_fingerprint if active
                else current_narration_binding(work_dir, audio_mode)
            ),
            audio_mix_binding=staged_mix_fingerprint,
            source_audio_status=source_audio_status,
            render_delivery=render_delivery,
        )
        if active and assembly_qc["blocking"]:
            assembly_contract._write_assembly_qc(work_dir, assembly_qc)
            codes = ", ".join(assembly_qc["blocking_codes"])
            raise RuntimeError(f"身份约束渲染 QC 失败: {codes}")
        if active:
            if explicit_mix is not None:
                audio_mix_binding.assert_current(explicit_mix)
            finalized_binding = narration_binding.finalize_binding(
                binding, tts_segments,
                (Path(work_dir) / "narration.wav" if explicit_mix is not None else narration_wav),
                published_output,
                staged_path=staged_binding,
            )
            staged_binding = None
            binding_path = Path(work_dir) / narration_binding.FILENAME
            binding_published = binding_path.is_file()
            if not isinstance(finalized_binding, dict):
                raise RuntimeError("active narration binding finalize 返回空结果")
            if not binding_path.is_file():
                raise RuntimeError("已发布 narration binding 未通过终态验证")
            prepublish_binding = narration_binding.staged_binding_fingerprint(
                report, binding_path, binding_path
            )
            if prepublish_binding["sha256"] != staged_binding_fingerprint["sha256"]:
                raise RuntimeError("已发布 narration binding 身份不一致")
            if explicit_mix is not None:
                finalized_mix = audio_mix_binding.finalize_binding(staged_mix_binding, work_dir)
                staged_mix_binding = None
                mix_binding_published = (
                    Path(work_dir) / audio_mix_binding.FILENAME
                ).is_file()
                if not isinstance(finalized_mix, dict):
                    raise RuntimeError("active audio mix binding finalize 返回空结果")
                published_mix_fingerprint = audio_mix_binding.staged_binding_fingerprint(
                    finalized_mix, Path(work_dir) / audio_mix_binding.FILENAME,
                    Path(work_dir) / audio_mix_binding.FILENAME,
                )
                if published_mix_fingerprint["sha256"] != staged_mix_fingerprint["sha256"]:
                    raise RuntimeError("已发布 audio mix binding 身份不一致")
                audio_mix_binding.assert_current(explicit_mix)
            render_output.rename(published_output)
            render_output = published_output
            current_binding = current_narration_binding(work_dir, audio_mode)
            if current_binding is None:
                raise RuntimeError("已发布 narration binding 未通过终态验证")
            current_mix_binding = current_audio_mix_binding(work_dir, audio_mode)
            if explicit_mix is not None and current_mix_binding is None:
                raise RuntimeError("已发布 audio mix binding 未通过终态验证")
            if explicit_mix is not None:
                audio_mix_binding.assert_current(explicit_mix)
            assembly_qc = assembly_contract._build_assembly_qc(
                tts_segments, video_duration, output_path=render_output,
                source_has_audio=source_has_audio,
                loudness_mode=loudness_mode, loudnorm_measurement=loudnorm_measurement,
                visual_qc=visual_qc, audio_mode=audio_mode,
                audio_operations=audio_operations, adopted_audio=adopted_audio,
                narration_input_binding=current_binding,
                audio_mix_binding=current_mix_binding,
                source_audio_status=source_audio_status,
                render_delivery=assembly_qc["delivery_qc"],
            )
            if assembly_qc["blocking"]:
                assembly_qc["output"] = {
                    "path": str(published_output), "exists": False, "bytes": 0,
                }
                assembly_contract._write_assembly_qc(work_dir, assembly_qc)
                codes = ", ".join(assembly_qc["blocking_codes"])
                raise RuntimeError(f"身份约束渲染终态 QC 失败: {codes}")
        assembly_contract._write_assembly_qc(work_dir, assembly_qc)
    except Exception:
        if active:
            render_output.unlink(missing_ok=True)
            published_output.unlink(missing_ok=True)
            if binding_published:
                (Path(work_dir) / narration_binding.FILENAME).unlink(missing_ok=True)
            if mix_binding_published:
                (Path(work_dir) / audio_mix_binding.FILENAME).unlink(missing_ok=True)
            if staged_binding is not None:
                Path(staged_binding).unlink(missing_ok=True)
            if staged_mix_binding is not None:
                Path(staged_mix_binding).unlink(missing_ok=True)
        raise
    return render_output
