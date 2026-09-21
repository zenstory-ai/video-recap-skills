"""Publish transaction for strict-adoption renders.

An active narration binding renders to a hidden candidate file. Its binding (and,
for an explicit mix, the audio mix binding) is written, QC gates the candidate,
the media file is published, and QC runs once more against the published path.
Any failure removes the candidate, the published file and the bindings written
by this render, so a strict output never exists without its consumed-input record.
"""

from pathlib import Path

import assembly_contract
import audio_mix_binding
import narration_binding


def current_narration_binding(work_dir, audio_mode):
    """Read the narration record only for the explicit narration render path."""
    if audio_mode != "narration":
        return None
    return narration_binding.binding_record(work_dir)


def current_audio_mix_binding(work_dir, audio_mode):
    if audio_mode != "narration":
        return None
    return audio_mix_binding.binding_record(work_dir)


def publish_render(*, work_dir, binding, explicit_mix, tts_segments, narration_wav,
                   render_output, published_output, audio_mode, audio_operations,
                   adopted_audio, loudness_mode, loudnorm_measurement, visual_qc,
                   source_has_audio, video_duration, render_delivery, source_audio_status):
    """Gate the rendered candidate on assembly QC and publish it with its bindings.

    Returns the final output path. Inactive-binding renders only write their
    binding and QC; strict renders write bindings, publish, and re-run QC, or roll
    everything back.
    """
    active = bool(binding and binding["active"])
    work_dir = Path(work_dir)
    binding_path = work_dir / narration_binding.FILENAME
    mix_binding_path = work_dir / audio_mix_binding.FILENAME
    binding_written = False
    mix_binding_written = False
    try:
        if active:
            report = narration_binding.finalize_binding(
                binding, tts_segments,
                (work_dir / "narration.wav" if explicit_mix is not None else narration_wav),
                published_output, rendered_output=render_output,
            )
            binding_written = True
            if not binding_path.is_file():
                raise RuntimeError("narration binding 未写入")
            narration_record = narration_binding.record_of(report, binding_path)
            mix_record = None
            if explicit_mix is not None:
                audio_mix_binding.finalize_binding(
                    explicit_mix, narration_record, render_output, published_output, work_dir
                )
                mix_binding_written = True
                mix_record = {"path": str(mix_binding_path.resolve()), "status": "FINALIZED"}
        elif binding:
            narration_binding.finalize_binding(
                binding, tts_segments, narration_wav, render_output
            )
            narration_record = current_narration_binding(work_dir, audio_mode)
            mix_record = None
        else:
            narration_record = current_narration_binding(work_dir, audio_mode)
            mix_record = None
        assembly_qc = assembly_contract._build_assembly_qc(
            tts_segments,
            video_duration,
            output_path=render_output,
            source_has_audio=source_has_audio,
            loudness_mode=loudness_mode,
            loudnorm_measurement=loudnorm_measurement,
            visual_qc=visual_qc,
            audio_mode=audio_mode,
            audio_operations=audio_operations,
            adopted_audio=adopted_audio,
            narration_input_binding=narration_record,
            audio_mix_binding=mix_record,
            source_audio_status=source_audio_status,
            render_delivery=render_delivery,
        )
        if active and assembly_qc["blocking"]:
            assembly_contract._write_assembly_qc(work_dir, assembly_qc)
            codes = ", ".join(assembly_qc["blocking_codes"])
            raise RuntimeError(f"身份约束渲染 QC 失败: {codes}")
        if active:
            render_output.rename(published_output)
            render_output = published_output
            current_binding = current_narration_binding(work_dir, audio_mode)
            if current_binding is None:
                raise RuntimeError("已发布 narration binding 未通过终态检查")
            current_mix_binding = current_audio_mix_binding(work_dir, audio_mode)
            if explicit_mix is not None and current_mix_binding is None:
                raise RuntimeError("已发布 audio mix binding 未通过终态检查")
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
            if binding_written:
                binding_path.unlink(missing_ok=True)
            if mix_binding_written:
                mix_binding_path.unlink(missing_ok=True)
        raise
    return render_output
