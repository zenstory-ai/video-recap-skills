#!/usr/bin/env python3
"""Execute locked source-picture decisions without touching audio or legacy snap rules."""

import argparse
from fractions import Fraction
import json
from pathlib import Path
import re
import subprocess

from shot_review import probe_frame_clock, sha256_file

ALGORITHM = "locked-cfr-rgb-pad-bt709-v1"
MAPPED_ALGORITHM = "explicit-frame-map-rgb-pad-bt709-v2"


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _fields(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f"unsupported {label} fields")


def _range(value, label):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{label} requires half-open [start,end]")
    a, b = (_integer(v, label) for v in value)
    if b <= a:
        raise ValueError(f"empty/reversed {label}")
    return a, b


def _fps(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9]\d*(/[1-9]\d*)?", value):
        raise ValueError("fps must be a positive rational string")
    rate = Fraction(value)
    if not 1 <= rate <= 120:
        raise ValueError("unsupported fps")
    return rate


def _sources(raw):
    if not isinstance(raw, dict) or not raw:
        raise ValueError("sources must be a nonempty id mapping")
    for sid, source in raw.items():
        if not isinstance(sid, str) or not sid:
            raise ValueError("invalid source id")
        _fields(source, ["path", "sha256"], "source")
        if not isinstance(source.get("path"), str) or not source["path"]:
            raise ValueError("source requires path")
        if not isinstance(source.get("sha256"), str) or not re.fullmatch(
            "[a-f0-9]{64}", source["sha256"]
        ):
            raise ValueError("source requires lowercase SHA256 identity")
    return raw


def validate_plan(raw, facts):
    """No boundary repairs, timing estimates, aspect stretching or implicit fill."""
    _fields(
        raw,
        [
            "artifact",
            "schema_version",
            "fps",
            "canvas",
            "tail_frames",
            "sources",
            "shots",
        ],
        "plan",
    )
    if (
        raw.get("artifact") != "picture_plan"
        or type(raw.get("schema_version")) is not int
        or raw["schema_version"] != 1
    ):
        raise ValueError("unsupported picture_plan schema")
    rate = _fps(raw.get("fps"))
    canvas = raw.get("canvas")
    if not isinstance(canvas, list) or len(canvas) != 2:
        raise ValueError("canvas must be [width,height]")
    cw, ch = [_integer(v, "canvas", 2) for v in canvas]
    if cw % 2 or ch % 2:
        raise ValueError("canvas must be even for yuv420p")
    tail = _integer(raw.get("tail_frames"), "explicit black tail_frames")
    sources = _sources(raw.get("sources"))
    if set(sources) != set(facts):
        raise ValueError("source facts incomplete")
    for sid, source in sources.items():
        if source["sha256"] != facts[sid]["sha256"]:
            raise ValueError("source identity mismatch")
        if Fraction(facts[sid]["fps"]) != rate:
            raise ValueError("source/output fps differ; retime unsupported")
    shots = raw.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("shots must be nonempty")
    seen = set()
    normalized = []
    cursor = 0
    for shot in shots:
        _fields(
            shot,
            [
                "id",
                "source_id",
                "source_frames",
                "output_frames",
                "crop",
                "window",
                "source_frame_by_output",
            ],
            "shot",
        )
        sid = shot.get("source_id")
        name = shot.get("id")
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError("invalid/duplicate shot id")
        seen.add(name)
        if not isinstance(sid, str) or sid not in sources:
            raise ValueError("unknown source_id")
        start, end = _range(shot.get("source_frames"), "source_frames")
        out_start, out_end = _range(shot.get("output_frames"), "output_frames")
        if end > facts[sid]["frame_count"]:
            raise ValueError("source frame out of range")
        count = out_end - out_start
        if out_start != cursor:
            raise ValueError("output gap/overlap is unsupported")
        frame_map = shot.get("source_frame_by_output", list(range(start, end)))
        if not isinstance(frame_map, list) or len(frame_map) != count:
            raise ValueError(
                "source_frame_by_output must provide one index per output frame; implicit retime is unsupported"
            )
        previous = start
        for value in frame_map:
            _integer(value, "source_frame_by_output")
            if not start <= value < end or value < previous:
                raise ValueError(
                    "source_frame_by_output must be in range and nondecreasing"
                )
            previous = value
        crop = shot.get("crop")
        _fields(crop, ["x", "y", "width", "height", "x_by_frame"], "crop")
        x, y, w, h = [
            _integer(crop.get(k), f"crop {k}", 2 if k in ("width", "height") else 0)
            for k in ("x", "y", "width", "height")
        ]
        xs = crop.get("x_by_frame", [x] * count)
        if not isinstance(xs, list) or len(xs) != count or xs[0] != x:
            raise ValueError(
                "x_by_frame must provide one sample per output frame and agree with x"
            )
        for value in xs:
            _integer(value, "x_by_frame")
            if value % 2 or value + w > facts[sid]["width"]:
                raise ValueError("crop x outside source or not chroma aligned")
        if y % 2 or w % 2 or h % 2 or y + h > facts[sid]["height"]:
            raise ValueError("crop outside source or not chroma aligned")
        window = shot.get("window")
        if not isinstance(window, list) or len(window) != 4:
            raise ValueError("window must be [x,y,width,height]")
        wx, wy, ww, wh = [
            _integer(v, "window", 2 if i > 1 else 0) for i, v in enumerate(window)
        ]
        if wx + ww > cw or wy + wh > ch or ww % 2 or wh % 2:
            raise ValueError("window outside canvas or odd size")
        aspect_error = Fraction(w * wh, h * ww) - 1
        if abs(aspect_error) > Fraction(1, 500):
            raise ValueError(
                "crop/window aspect mismatch beyond pixel-rounding tolerance"
            )
        normalized.append(
            {
                **shot,
                "source_frame_by_output": list(frame_map),
                "mapping_kind": "explicit"
                if "source_frame_by_output" in shot
                else "implicit_identity",
                "crop_x_by_frame": list(xs),
                "aspect_error": str(aspect_error),
            }
        )
        cursor = out_end
    return {
        **raw,
        "fps": str(rate),
        "shots": normalized,
        "picture_frames": cursor,
        "total_frames": cursor + tail,
        "audio": "NOT_PRODUCED",
        "algorithm": MAPPED_ALGORITHM
        if any(s["mapping_kind"] == "explicit" for s in normalized)
        else ALGORITHM,
        "tail": {
            "kind": "explicit_black_fill",
            "output_frames": [cursor, cursor + tail],
            "is_end_card": False,
        },
    }


def validate_source_color(stream):
    """This slice does not convert known non-BT709 transfer functions or primaries."""
    for key in ("color_space", "color_transfer", "color_primaries"):
        if stream.get(key) not in {None, "unknown", "unspecified", "bt709"}:
            raise ValueError(
                "source color unsupported: requires BT709 or unspecified SDR tags"
            )


def inspect_sources(raw_sources, fps):
    facts = {}
    clocks = {}
    for sid, source in _sources(raw_sources).items():
        path = Path(source["path"]).resolve()
        if not path.is_file() or sha256_file(path) != source["sha256"]:
            raise ValueError("source identity mismatch or missing file")
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,time_base,sample_aspect_ratio,color_space,color_transfer,color_primaries,color_range:stream_tags=rotate:stream_side_data=rotation",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        stream = json.loads(result.stdout)["streams"][0]
        if (
            stream.get("sample_aspect_ratio", "1:1") != "1:1"
            or int(stream.get("tags", {}).get("rotate", 0)) != 0
            or any(
                float(s.get("rotation", 0)) != 0
                for s in stream.get("side_data_list", [])
            )
        ):
            raise ValueError("non-square pixels or rotation unsupported")
        validate_source_color(stream)
        pts, end, origin = probe_frame_clock(path)
        if (
            origin != 0
            or end != Fraction(len(pts), fps)
            or any(t != Fraction(i, fps) for i, t in enumerate(pts))
        ):
            raise ValueError(
                "only zero-origin exact CFR sources matching output fps are supported"
            )
        if sha256_file(path) != source["sha256"]:
            raise ValueError("source identity changed while probing")
        facts[sid] = {
            "path": str(path),
            "sha256": source["sha256"],
            "width": stream["width"],
            "height": stream["height"],
            "fps": str(fps),
            "frame_count": len(pts),
            "time_base": stream["time_base"],
            "origin": str(origin),
            "duration": str(end),
            "color": {k: v for k, v in stream.items() if k.startswith("color_")},
        }
        clocks[sid] = pts
    return facts, clocks


def _write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def execute(plan_path, output_dir, *, plan_only=False, crf=18):
    """Create a new independent picture candidate; never overwrite an existing revision."""
    plan_path = Path(plan_path).resolve()
    directory = Path(output_dir).resolve()
    plan_hash = sha256_file(plan_path)
    raw = json.loads(plan_path.read_text())
    _integer(crf, "crf")
    if crf > 51:
        raise ValueError("crf must be <=51")
    directory.mkdir(parents=True, exist_ok=False)
    report = {
        "artifact": "locked_picture_run",
        "schema_version": 1,
        "status": "PREPARING",
        "plan": {"path": str(plan_path), "sha256": plan_hash},
        "audio": "NOT_PRODUCED",
        "normal_speed_review": "NOT_CHECKED",
        "algorithm": "PENDING_VALIDATION",
    }
    _write_json(directory / "picture_run.json", report)
    try:
        facts, clocks = inspect_sources(raw.get("sources"), _fps(raw.get("fps")))
        normalized = validate_plan(raw, facts)
        report["algorithm"] = normalized["algorithm"]
        _write_json(directory / "picture_plan.validated.json", normalized)

        def recheck():
            if sha256_file(plan_path) != plan_hash or any(
                sha256_file(f["path"]) != f["sha256"] for f in facts.values()
            ):
                raise ValueError("plan/source identity changed during execution")

        recheck()
        if plan_only:
            report.update(status="PLANNED", source_facts=facts)
        else:
            mapping = render_picture(normalized, facts, clocks, directory, crf=crf)
            recheck()
            candidate = directory / "picture.rendering.mp4"
            mapping.update(
                artifact="picture_map",
                schema_version=1,
                algorithm=normalized["algorithm"],
                plan_sha256=plan_hash,
                source_facts=facts,
                tail=normalized["tail"],
                audio="NOT_PRODUCED",
            )
            mapping["output"]["path"] = str(directory / "picture.mp4")
            _write_json(directory / "picture_map.json", mapping)
            candidate.rename(directory / "picture.mp4")
            report.update(status="PICTURE_RENDERED", output=mapping["output"])
        _write_json(directory / "picture_run.json", report)
        return report
    except Exception:
        report["status"] = "FAILED"
        _write_json(directory / "picture_run.json", report)
        raise


def render_picture(*args, **kwargs):
    from picture_render import render_picture as render

    return render(*args, **kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan")
    parser.add_argument(
        "--output-dir", required=True, help="new directory; existing paths refused"
    )
    parser.add_argument(
        "--plan-only", action="store_true", help="validate/bind, do not render"
    )
    parser.add_argument("--crf", type=int, default=18)
    args = parser.parse_args(argv)
    execute(args.plan, args.output_dir, plan_only=args.plan_only, crf=args.crf)


if __name__ == "__main__":
    main()
