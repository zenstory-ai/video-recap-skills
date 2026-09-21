"""Shared scaffolding for tests that drive recap_runner with stubbed child scripts.

Importing this module also puts video-recap's scripts on sys.path (conftest.py does so
for the whole group), so test modules import recap modules directly.
"""

import json
import sys
from argparse import Namespace
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import materials  # noqa: E402
import recap_runtime  # noqa: E402
import recap_timeline  # noqa: E402

DEFAULT_NARRATION = [{"start": 0, "end": 1, "narration": "full。"}]
DEFAULT_CLIPS = [{"start": 10, "end": 12}]


def manifest_args(**overrides):
    """A parsed-args stand-in carrying every setting the run manifest records."""
    values = {
        "context": "",
        "scene_threshold": None,
        "style": "纪录片",
        "edit_mode": "full",
        "audio_mode": "narration",
        "audio_stream_index": 0,
        "target_duration": None,
        "skip_asr": False,
        "mimo_video_overview": False,
        "consolidate": True,
        "consolidate_asr": False,
        "review_narration": None,
        "require_narration_review": False,
        "allow_duration_drift": False,
        "mimo_qc": "off",
        "mimo_qc_refresh": False,
        "mimo_tts_voice": None,
        "tts_provider": "auto",
        "voice_ref": None,
        "allow_partial_tts": False,
        "preserve_approved_text": False,
        "burn_subtitles": None,
        "subtitle_y_top": None,
        "subtitle_y_bot": None,
        "output_dir": None,
        "export_jianying": False,
        "jianying_bundle_media": False,
        "jianying_no_bundle_media": False,
        "material_library_dir": None,
        "use_materials": False,
        "save_materials": False,
        "require_final_qc": False,
        "tts_meta": None,
        "narration_adoption": None,
        "audio_mix_adoption": None,
        "_explicit_options": frozenset(),
    }
    values.update(overrides)
    return Namespace(**values)


def write_assemble_output(work, final_output):
    """Leave behind what assemble.py writes on success."""
    (work / "output.mp4").write_bytes(b"mp4")
    (work / "assembly_manifest.json").write_text(
        json.dumps({"final_output": str(final_output)}), encoding="utf-8"
    )


def write_cut_output(work, clips=None, **qc_overrides):
    """Leave behind what cut.py writes on success."""
    clips = [] if clips is None else clips
    qc = {
        "status": "pass",
        "target_duration_status": "within",
        "total_duration": sum(float(clip.get("duration", 0)) for clip in clips),
        "clip_count": len(clips),
        "join_fade_ms": 20.0,
        "output_geometry": {"width": 1920, "height": 1080, "fps": 30},
        "output_geometry_reason": "used_sources",
        "warnings": [],
    }
    qc.update(qc_overrides)
    (work / "edited_source.mp4").write_bytes(b"edited")
    (work / "clip_plan_validated.json").write_text(
        json.dumps({"clips": clips, "qc": qc}), encoding="utf-8"
    )


def write_review_output(work):
    """Leave behind what review.py writes on success (review_runner writes both)."""
    review = {"verdict": "PASS", "summary": "", "findings": []}
    (work / "narration_review.json").write_text(json.dumps(review), encoding="utf-8")
    (work / "narration_review.md").write_text("# review\n", encoding="utf-8")


def write_voiceover_output(work):
    """Leave behind what voiceover.py writes on success."""
    (work / "tts_meta.json").write_text(json.dumps({"segments": []}), encoding="utf-8")


def stub_child_run(work, final_output=None, calls=None, **handlers):
    """Build a recap_runner._run replacement.

    Records ``(skill, script, argv)`` into ``calls``, writes what cut.py / review.py /
    voiceover.py / assemble.py leave behind, and lets a test override any script by its
    stem (``review=lambda cli: ...``); a handler's return value is passed back to the runner.
    """

    def fake_run(skill, script, *cli_args):
        cli = [str(arg) for arg in cli_args]
        if calls is not None:
            calls.append((skill, script, cli))
        handler = handlers.get(script[: -len(".py")])
        if handler is not None:
            return handler(cli)
        if script == "cut.py":
            write_cut_output(work)
        elif script == "review.py":
            write_review_output(work)
        elif script == "voiceover.py":
            write_voiceover_output(work)
        elif script == "assemble.py":
            write_assemble_output(work, final_output)
        return None

    return fake_run


def seed_full_work(tmp_path, narration=DEFAULT_NARRATION, **overrides):
    """A Phase-B work dir for one source: narration.json plus a matching run manifest."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    work.mkdir()
    if narration is not None:
        (work / "narration.json").write_text(
            json.dumps(narration, ensure_ascii=False), encoding="utf-8"
        )
    recap_runtime._write_run_manifest(work, video.resolve(), manifest_args(**overrides))
    return video, work


def seed_cut_work(
    tmp_path, clips=DEFAULT_CLIPS, narration=DEFAULT_NARRATION, rendered=True, **overrides
):
    """A single-source cut work dir with clip_plan.json and (optionally) a rendered ledger."""
    video, work = seed_full_work(tmp_path, narration, edit_mode="cut", **overrides)
    (work / "clip_plan.json").write_text(json.dumps(clips), encoding="utf-8")
    if rendered:
        recap_timeline._write_phase_ledger(
            work,
            clip_plan_identity=materials.file_identity(work / "clip_plan.json"),
            edited_source_rendered=True,
        )
    return video, work


def seed_multi_work(tmp_path, args, narration=None):
    """A two-source cut project with manifests and a clip plan on the first source."""
    videos = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for video in videos:
        video.write_bytes(f"{video.stem} source".encode())
    videos = [video.resolve() for video in videos]
    work = tmp_path / "project"
    work.mkdir()
    records = recap_runtime._build_multi_source_records(videos, args)
    recap_runtime._write_multi_source_manifest(work, records)
    recap_runtime._write_project_run_manifest(work, videos, args, records)
    (work / "clip_plan.json").write_text(
        json.dumps(
            {"clips": [{"source_id": records[0]["source_id"], "start": 0, "end": 1}]}
        ),
        encoding="utf-8",
    )
    if narration is not None:
        (work / "narration.json").write_text(
            json.dumps(narration, ensure_ascii=False), encoding="utf-8"
        )
    return videos, work, records


def multi_cut_clip(records, videos):
    """The validated clip cut.py reports for the first source of a multi-source project."""
    return {
        "source_id": records[0]["source_id"],
        "source_path": str(videos[0]),
        "source_start": 0,
        "source_end": 1,
        "output_start": 0,
        "output_end": 1,
        "duration": 1,
    }
