"""Optional 剪映 / JianYing (CapCut) draft exporter — decoupled, stdlib + ffprobe only.

Reads a backend-neutral `timeline.json` (see timeline.py) and writes a 剪映 draft
folder (`draft_content.json` + `draft_info.json` + `draft_meta_info.json`) that the
desktop app can open and the user can keep editing: video clips on the main track,
the narration and BGM as their own audio tracks, the recap lines as a subtitle
track, and the gap-fill ducking carried as native volume keyframes.

The public entrypoints stay small (`us`, `build_draft`,
`export_timeline_to_jianying`, CLI). Internally the exporter is split into schema/templates, a thin
normalized build context, material/segment builders, track layout metadata, and
a safe writer/bundler. This mirrors the useful schema boundaries from duo-video
while ffmpeg remains the canonical renderer and JianYing export remains an
optional sidecar.

Schema and the draft skeleton are informed by the open-source pyJianYingDraft
(© GuanYixuan, Apache-2.0) and capcut-mate (© Hommy, Apache-2.0). JSON protocol
templates pinned from duo-video are vendored under its MIT license; builders
are implemented locally and no upstream executable code, resource package,
adapter binary, or credential is included. See ACKNOWLEDGEMENTS / 致谢.
"""

import json
import os
import subprocess
import tempfile
import uuid

from jianying.builders import build_timeline_track as _build_timeline_track
from jianying.model import DraftBuildContext as _DraftBuildContext
from jianying.schema import draft_content_skeleton as _draft_content_skeleton
from jianying.schema import meta_info as _meta_info
from jianying.schema import us
from jianying.timeline_contract import normalize_timeline as _normalize_timeline
from jianying.writer import write_draft as _write_draft

__all__ = ["us", "build_draft", "export_timeline_to_jianying", "main"]


def _default_id():
    return str(uuid.uuid4()).upper()


def _probe_media(path):
    """Return (duration_us, width, height) via ffprobe.

    A probe failure raises: a draft that silently carries a zero or guessed media
    duration is worse than a failed optional export. Still images legitimately
    report no duration (0).
    """
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-of",
            "json",
            "-show_entries",
            "format=duration:stream=width,height,codec_type",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(f"ffprobe failed for JianYing media {path}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    duration = data["format"].get("duration")
    width = height = 0
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width, height = int(stream["width"]), int(stream["height"])
            break
    return (us(float(duration)) if duration is not None else 0), width, height


def build_draft(timeline, new_id=None, probe=None):
    """Build the 剪映 draft_content dict and companion meta from a timeline."""
    return _build_normalized_draft(_normalize_timeline(timeline), new_id, probe)


def _build_normalized_draft(timeline, new_id=None, probe=None):
    """`timeline` has already passed jianying.timeline_contract.normalize_timeline."""
    new_id = new_id or _default_id
    probe = probe or _probe_media
    ctx = _DraftBuildContext.from_timeline(timeline, new_id, probe)

    for timeline_track in timeline["tracks"]:
        _build_timeline_track(ctx, timeline_track)
    ctx.finalize_tracks()

    draft_id = new_id()
    content = _draft_content_skeleton(
        draft_id,
        ctx.width,
        ctx.height,
        ctx.fps,
        ctx.total_us,
        ctx.materials,
        ctx.tracks,
    )
    meta = _meta_info(draft_id, ctx.total_us)
    return content, meta, ctx.notes


def _generate_reversed_media(source_path, output_path):
    commands = [
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            source_path,
            "-vf",
            "reverse",
            "-af",
            "areverse",
            output_path,
        ],
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            source_path,
            "-vf",
            "reverse",
            "-an",
            output_path,
        ],
    ]
    errors = []
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0 and os.path.isfile(output_path):
            return
        errors.append(
            (result.stderr or result.stdout or "unknown ffmpeg error").strip()
        )
    raise RuntimeError(
        f"failed to reverse JianYing source {source_path}: {'; '.join(errors)}"
    )


def _prepare_reverse_sources(clips, temporary_dir):
    """Generate a reversed copy for each clip and record it as the clip's reverse_path."""
    generated = []
    for clip in clips:
        source_path = clip["source_path"]
        if not os.path.isfile(source_path):
            raise ValueError(f"reverse source does not exist: {source_path}")
        output_path = os.path.join(
            temporary_dir, f"reversed-{uuid.uuid4().hex}.mp4"
        )
        _generate_reversed_media(source_path, output_path)
        clip["reverse_path"] = output_path
        generated.append(source_path)
    return generated


def export_timeline_to_jianying(
    timeline, out_dir, draft_name="recap", new_id=None, probe=None, bundle_media=True
):
    """Write a 剪映 draft folder under out_dir/draft_name. Returns (folder, notes).

    Referenced media is bundled by default so the draft is self-contained and
    portable. Pass bundle_media=False only when external absolute paths are
    intentionally required.
    """
    # Validate once at the boundary; everything below trusts the normalized copy.
    timeline = _normalize_timeline(timeline)
    reverse_clips = [
        clip
        for track in timeline["tracks"]
        if track["kind"] == "video"
        for clip in track["clips"]
        if clip.get("reverse") and "reverse_path" not in clip
    ]
    if reverse_clips and not bundle_media:
        raise ValueError("automatic reverse generation requires media bundling")
    if not reverse_clips:
        content, meta, notes = _build_normalized_draft(timeline, new_id=new_id, probe=probe)
        return _write_draft(
            content,
            meta,
            notes,
            out_dir,
            draft_name,
            bundle_media_enabled=bundle_media,
        )

    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="jianying-reverse-", dir=out_dir
    ) as temporary_dir:
        generated = _prepare_reverse_sources(reverse_clips, temporary_dir)
        content, meta, notes = _build_normalized_draft(timeline, new_id=new_id, probe=probe)
        notes.extend(f"已生成倒放素材: {source}" for source in generated)
        return _write_draft(
            content,
            meta,
            notes,
            out_dir,
            draft_name,
            bundle_media_enabled=True,
        )


def main():
    import argparse

    ap = argparse.ArgumentParser(
        description="Export a timeline.json to a 剪映/JianYing draft folder."
    )
    ap.add_argument("timeline", help="path to timeline.json")
    ap.add_argument(
        "--out-dir", required=True, help="parent dir to create the draft folder in"
    )
    ap.add_argument("--name", default="recap", help="draft folder name")
    bundle_group = ap.add_mutually_exclusive_group()
    bundle_group.add_argument(
        "--bundle-media",
        dest="bundle_media",
        action="store_true",
        help="copy referenced media into the draft folder (default)",
    )
    bundle_group.add_argument(
        "--no-bundle-media",
        dest="bundle_media",
        action="store_false",
        help="keep external media paths instead of making a portable draft",
    )
    ap.set_defaults(bundle_media=True)
    args = ap.parse_args()
    with open(args.timeline, encoding="utf-8") as f:
        timeline = json.load(f)
    draft_dir, notes = export_timeline_to_jianying(
        timeline,
        args.out_dir,
        args.name,
        bundle_media=args.bundle_media,
    )
    for note in notes:
        print(f"  注意: {note}")
    print(
        json.dumps({"status": "exported", "draft_dir": draft_dir}, ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
