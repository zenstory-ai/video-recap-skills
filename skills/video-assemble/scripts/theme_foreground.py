"""Validate and produce finite-role RGBA title/caption/note/marker foregrounds."""

from __future__ import annotations

import base64
import contextlib
from fractions import Fraction
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import tempfile

from subtitle_track_binding import (
    _frame_clock_hash,
    _picture_clock,
    bound_subtitle_entries,
    current_bindings,
    prepare_subtitle_track,
)


PATTERN = "frame_%06d.png"
SHA256 = re.compile(r"[a-f0-9]{64}")
COLOR = re.compile(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?")
FIT_POLICIES = {"fixed", "cjk_count_v1"}
BROWSER_ENV_VAR = "THEME_FOREGROUND_BROWSER"
BROWSER_COMMANDS = ("google-chrome", "google-chrome-stable", "chromium",
                    "chromium-browser", "chrome")
BROWSER_APP_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
)


def resolve_browser_executable(explicit=None):
    """Locate a Chrome/Chromium capture binary without assuming one machine layout."""
    if explicit:
        return str(explicit)
    declared = os.environ.get(BROWSER_ENV_VAR, "").strip()
    if declared:
        return declared
    for command in BROWSER_COMMANDS:
        found = shutil.which(command)
        if found:
            return found
    for candidate in BROWSER_APP_PATHS:
        if Path(candidate).is_file():
            return candidate
    raise ValueError(
        "No Chrome/Chromium capture browser found. Pass an explicit executable path, "
        f"set {BROWSER_ENV_VAR}, or install one of {list(BROWSER_COMMANDS)}; "
        f"also tried {list(BROWSER_APP_PATHS)}"
    )


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _save(path, value):
    temporary = Path(str(path) + ".writing")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _fields(value, names, label="object"):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError(f"{label} expected exactly these fields: {names}")


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _number(value, label, minimum=None):
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be a finite number")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    return value


def _text(value, label, *, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _fraction(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a canonical rational string")
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"Invalid {label}") from exc
    if result <= 0 or value != f"{result.numerator}/{result.denominator}":
        raise ValueError(f"{label} must be a positive canonical N/D rational")
    return result


def _local_file(value, label):
    _fields(value, ["path", "sha256"], label)
    raw = _text(value["path"], f"{label}.path")
    if "://" in raw or "?" in raw or "#" in raw:
        raise ValueError(f"{label} requires a plain local path without URL/query syntax")
    if not isinstance(value["sha256"], str) or not SHA256.fullmatch(value["sha256"]):
        raise ValueError(f"{label}.sha256 requires lowercase SHA256")
    path = Path(raw).expanduser().resolve()
    if not path.is_file() or _sha256(path) != value["sha256"]:
        raise ValueError(f"{label} identity mismatch or file missing")
    return {"path": str(path), "sha256": value["sha256"]}


def _color(value, label):
    if not isinstance(value, str) or not COLOR.fullmatch(value):
        raise ValueError(f"{label} must be an exact #RRGGBB or #RRGGBBAA color")
    return value


def _anchor(value, label):
    _fields(value, ["horizontal", "vertical", "x", "y", "width"], label)
    if value["horizontal"] not in {"left", "center", "right"}:
        raise ValueError(f"{label}.horizontal unsupported")
    if value["vertical"] not in {"top", "bottom"}:
        raise ValueError(f"{label}.vertical unsupported")
    for key in ("x", "y"):
        _number(value[key], f"{label}.{key}")
    _number(value["width"], f"{label}.width", 1)
    return value


def _style(value, label, font_weights):
    names = ["font_id", "font_weight", "preferred_size", "minimum_size", "line_height",
             "letter_spacing", "color", "skew_x", "shadow", "stroke", "wrap", "max_lines",
             "text_align"]
    _fields(value, names, label)
    font_id = _text(value["font_id"], f"{label}.font_id")
    weight = _integer(value["font_weight"], f"{label}.font_weight", 1)
    if font_id not in font_weights or font_weights[font_id] != weight:
        raise ValueError(f"{label} references a missing font at a different weight")
    preferred = _number(value["preferred_size"], f"{label}.preferred_size", 1)
    minimum = _number(value["minimum_size"], f"{label}.minimum_size", 1)
    if minimum > preferred:
        raise ValueError(f"{label} minimum_size exceeds preferred_size")
    _fields(value["line_height"], ["kind", "value"], f"{label}.line_height")
    if value["line_height"]["kind"] not in {"ratio", "px"}:
        raise ValueError(f"{label}.line_height kind unsupported")
    _number(value["line_height"]["value"], f"{label}.line_height.value", 0.01)
    _number(value["letter_spacing"], f"{label}.letter_spacing")
    _color(value["color"], f"{label}.color")
    _number(value["skew_x"], f"{label}.skew_x")
    for optional, fields in (("shadow", ["offset_x", "offset_y", "blur", "color"]),
                             ("stroke", ["width", "color"])):
        item = value[optional]
        if item is not None:
            _fields(item, fields, f"{label}.{optional}")
            for key in fields[:-1]:
                _number(item[key], f"{label}.{optional}.{key}", 0 if key in {"blur", "width"} else None)
            _color(item["color"], f"{label}.{optional}.color")
    if value["wrap"] not in {"nowrap", "pre_wrap_anywhere"}:
        raise ValueError(f"{label}.wrap unsupported")
    _integer(value["max_lines"], f"{label}.max_lines", 1)
    if value["text_align"] not in {"left", "center", "right"}:
        raise ValueError(f"{label}.text_align unsupported")
    return value


def _fit(value, label):
    _fields(value, ["policy", "version", "width", "count_subtract", "addend"], label)
    if value["policy"] not in FIT_POLICIES or value["version"] != 1:
        raise ValueError(f"{label} requires a supported versioned fit policy")
    _number(value["width"], f"{label}.width", 1)
    _number(value["count_subtract"], f"{label}.count_subtract", 0)
    _number(value["addend"], f"{label}.addend", 0)
    return value


def fit_size(text, fit, preferred, minimum):
    """Apply the finite v1 fit formula; this is character-count policy, not ink measurement."""
    if fit["policy"] == "fixed":
        return float(preferred)
    if fit["policy"] != "cjk_count_v1" or fit["version"] != 1:
        raise ValueError("Unsupported fit policy")
    count = max((len(line) for line in text.split("\n")), default=0)
    if count == 0:
        raise ValueError("Cannot fit empty text")
    candidate = (fit["width"] - fit["count_subtract"] * count) / (count + fit["addend"])
    return max(float(minimum), min(float(preferred), float(candidate)))


def shared_title_size(primary, secondary, fit, preferred, minimum):
    return min(fit_size(primary, fit, preferred, minimum),
               fit_size(secondary, fit, preferred, minimum))


def _rgba_bytes(path, width, height):
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
         "-show_entries", "stream=codec_name,width,height,pix_fmt", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    streams = json.loads(probe.stdout).get("streams", []) if not probe.returncode else []
    required = {"codec_name": "png", "width": width, "height": height, "pix_fmt": "rgba"}
    if len(streams) != 1 or any(streams[0].get(key) != value for key, value in required.items()):
        raise ValueError("Image must be an exact-size PNG with actual RGBA pixel format")
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, timeout=60,
    )
    expected = width * height * 4
    if result.returncode or result.stderr.strip() or len(result.stdout) != expected:
        raise ValueError("Underlay must be a decodable exact-size RGBA canvas")
    return result.stdout


def _validate_underlay(path, canvas, picture):
    pixels = _rgba_bytes(path, canvas["width"], canvas["height"])
    left, top = picture["left"], picture["top"]
    right, bottom = left + picture["width"], top + picture["height"]
    if left < 0 or top < 0 or right > canvas["width"] or bottom > canvas["height"]:
        raise ValueError("Picture window lies outside canvas")
    inside = []
    for y in range(canvas["height"]):
        for x in range(canvas["width"]):
            alpha = pixels[(y * canvas["width"] + x) * 4 + 3]
            if left <= x < right and top <= y < bottom:
                inside.append(alpha)
    if any(inside):
        raise ValueError("Every underlay picture-window pixel must be transparent")


def _load_profile(reference):
    asset = _local_file(reference, "profile")
    document = json.loads(Path(asset["path"]).read_text(encoding="utf-8"))
    _fields(document, ["artifact", "schema_version", "canvas", "picture", "underlay", "fonts", "roles"], "profile")
    if document["artifact"] != "theme_foreground_profile" or document["schema_version"] != 1:
        raise ValueError("Unsupported theme_foreground_profile schema")
    _fields(document["canvas"], ["width", "height"], "canvas")
    canvas = {key: _integer(document["canvas"][key], f"canvas.{key}", 1) for key in ("width", "height")}
    _fields(document["picture"], ["left", "top", "width", "height"], "picture")
    picture = {key: _integer(document["picture"][key], f"picture.{key}", 0 if key in {"left", "top"} else 1)
               for key in ("left", "top", "width", "height")}
    underlay = _local_file(document["underlay"], "underlay")
    if not isinstance(document["fonts"], list) or not document["fonts"]:
        raise ValueError("fonts must be a non-empty list")
    fonts, weights = [], {}
    for index, item in enumerate(document["fonts"]):
        _fields(item, ["id", "weight", "path", "sha256", "platform_families", "postscript_names"], f"fonts[{index}]")
        font_id = _text(item["id"], f"fonts[{index}].id")
        if font_id in weights:
            raise ValueError("Duplicate font id")
        weight = _integer(item["weight"], f"fonts[{index}].weight", 1)
        allowlists = {}
        for key in ("platform_families", "postscript_names"):
            if not isinstance(item[key], list) or not item[key]:
                raise ValueError(f"fonts[{index}].{key} must be a non-empty list")
            values = [_text(value, f"fonts[{index}].{key}") for value in item[key]]
            if len(values) != len(set(values)):
                raise ValueError(f"fonts[{index}].{key} contains duplicates")
            allowlists[key] = values
        file = _local_file({"path": item["path"], "sha256": item["sha256"]}, f"fonts[{index}]")
        fonts.append({"id": font_id, "weight": weight, **allowlists, **file})
        weights[font_id] = weight
    _fields(document["roles"], ["title", "caption", "note", "marker"], "roles")
    roles = {}
    for name in ("caption", "note", "marker"):
        role = document["roles"][name]
        _fields(role, ["anchor", "style", "fit"], f"roles.{name}")
        roles[name] = {"anchor": _anchor(role["anchor"], f"roles.{name}.anchor"),
                       "style": _style(role["style"], f"roles.{name}.style", weights),
                       "fit": _fit(role["fit"], f"roles.{name}.fit")}
    title = document["roles"]["title"]
    _fields(title, ["anchor", "style", "fit", "shared_fit", "gap", "colors"], "roles.title")
    if title["shared_fit"] is not True:
        raise ValueError("Title pair requires shared_fit true")
    _fields(title["gap"], ["minimum", "visible", "size_subtract"], "roles.title.gap")
    for key in ("minimum", "visible", "size_subtract"):
        _number(title["gap"][key], f"roles.title.gap.{key}", 0)
    if not isinstance(title["colors"], list) or len(title["colors"]) != 2:
        raise ValueError("Title requires exactly two colors")
    roles["title"] = {
        "anchor": _anchor(title["anchor"], "roles.title.anchor"),
        "style": _style(title["style"], "roles.title.style", weights),
        "fit": _fit(title["fit"], "roles.title.fit"), "shared_fit": True,
        "gap": title["gap"], "colors": [_color(item, "roles.title.colors") for item in title["colors"]],
    }
    _validate_underlay(underlay["path"], canvas, picture)
    return {"reference": asset, "canvas": canvas, "picture": picture, "underlay": underlay,
            "fonts": fonts, "roles": roles}


def _captions(subtitle, base, video):
    if subtitle.get("kind") == "none":
        _fields(subtitle, ["kind"], "subtitle")
        return {"kind": "none"}, []
    _fields(subtitle, ["kind", "track", "validation"], "subtitle")
    if subtitle["kind"] != "bound":
        raise ValueError("subtitle.kind must be bound or none")
    track = _local_file(subtitle["track"], "subtitle.track")
    validation = _local_file(subtitle["validation"], "subtitle.validation")
    raw_validation = json.loads(Path(validation["path"]).read_text(encoding="utf-8"))
    binding = raw_validation.get("binding", {})
    selected = binding.get("identities", {}).get("audio", {}).get("selected_stream")
    if type(selected) is not int or Path(binding.get("input_video", "")).resolve() != Path(base["path"]):
        raise ValueError("Subtitle validation is not bound to the selected base")
    captured_stdout = io.StringIO()
    with contextlib.redirect_stdout(captured_stdout):
        current = current_bindings(base["path"], selected, edit_plan_path=binding.get("edit_plan"))
    if current != binding.get("identities"):
        raise ValueError("Subtitle validation base/audio identity is stale")
    with contextlib.redirect_stdout(captured_stdout):
        frame_pts, duration = _picture_clock(base["path"])
    if _frame_clock_hash(frame_pts, duration) != binding.get("frame_clock_sha256"):
        raise ValueError("Subtitle validation frame-clock identity is stale")
    if len(frame_pts) != video["total_frames"] or duration != Fraction(video["total_frames"], 1) / video["fps"]:
        raise ValueError("Declared frame clock differs from selected base")
    with tempfile.TemporaryDirectory(prefix="theme-subtitle-") as temporary:
        isolated = Path(temporary)
        shutil.copyfile(track["path"], isolated / "subtitle_track.json")
        captured_stdout = io.StringIO()
        with contextlib.redirect_stdout(captured_stdout):
            regenerated = prepare_subtitle_track(
                base["path"], isolated, duration, audio_mode="adopted-packet-copy",
                selected_audio_stream=selected, edit_plan_path=binding.get("edit_plan"),
            )
        if regenerated != raw_validation:
            raise ValueError("Supplied subtitle validation differs from canonical track projection")
        entries = bound_subtitle_entries(isolated, float(duration))
    captions = []
    for index, entry in enumerate(entries):
        projection = entry.get("frame_projection", {})
        start = projection.get("start", {}).get("frame_index")
        end = projection.get("end", {}).get("frame_index")
        _integer(start, f"caption[{index}].start_frame")
        _integer(end, f"caption[{index}].end_frame", 1)
        if not start < end <= video["body_end_frame"]:
            raise ValueError("Caption visibility must be non-empty and inside body")
        captions.append({"id": f"bound-caption-{index:04d}", "start_frame": start,
                         "end_frame": end, "text": _text(entry.get("text"), "caption.text")})
    if any(a["end_frame"] > b["start_frame"] for a, b in zip(captions, captions[1:])):
        raise ValueError("Bound captions may not overlap")
    projection = {"kind": "bound", "track": track, "validation": validation,
                  "binding": binding, "entries": captions}
    return projection, captions


def _validate_base_clock(base, video):
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
         "-show_entries", "stream=width,height", "-of", "json", base["path"]],
        capture_output=True, text=True, timeout=60,
    )
    streams = json.loads(probe.stdout).get("streams", []) if not probe.returncode else []
    if len(streams) != 1 or (streams[0].get("width"), streams[0].get("height")) != (video["width"], video["height"]):
        raise ValueError("Declared canvas differs from selected base picture")
    captured_stdout = io.StringIO()
    with contextlib.redirect_stdout(captured_stdout):
        frame_pts, duration = _picture_clock(base["path"])
    expected_duration = Fraction(video["total_frames"], 1) / video["fps"]
    if len(frame_pts) != video["total_frames"] or duration != expected_duration:
        raise ValueError("Declared total frames/fps differs from selected base frame clock")
    return {"frame_count": len(frame_pts), "duration": str(duration),
            "frame_clock_sha256": _frame_clock_hash(frame_pts, duration)}


def validate_plan_document(document):
    _fields(document, ["artifact", "schema_version", "base", "video", "subtitle", "profile", "content"], "plan")
    if document["artifact"] != "theme_foreground_plan" or document["schema_version"] != 1:
        raise ValueError("Unsupported theme_foreground_plan schema")
    base = _local_file(document["base"], "base")
    _fields(document["video"], ["width", "height", "fps", "total_frames", "body_end_frame"], "video")
    video = {"width": _integer(document["video"]["width"], "video.width", 1),
             "height": _integer(document["video"]["height"], "video.height", 1),
             "fps": _fraction(document["video"]["fps"], "video.fps"),
             "total_frames": _integer(document["video"]["total_frames"], "video.total_frames", 1),
             "body_end_frame": _integer(document["video"]["body_end_frame"], "video.body_end_frame", 1)}
    if video["body_end_frame"] > video["total_frames"]:
        raise ValueError("body_end_frame exceeds total_frames")
    base_clock = _validate_base_clock(base, video)
    profile = _load_profile(document["profile"])
    if (video["width"], video["height"]) != (profile["canvas"]["width"], profile["canvas"]["height"]):
        raise ValueError("Profile canvas differs from video canvas")
    _fields(document["content"], ["title", "notes", "markers"], "content")
    _fields(document["content"]["title"], ["primary", "secondary"], "content.title")
    title = {key: _text(document["content"]["title"][key], f"title.{key}") for key in ("primary", "secondary")}
    projection, captions = _captions(document["subtitle"], base, video)
    identifiers, notes, markers = set(), [], []
    for role, result in (("notes", notes), ("markers", markers)):
        source = document["content"][role]
        if not isinstance(source, list):
            raise ValueError(f"content.{role} must be a list")
        for index, item in enumerate(source):
            names = ["id", "start_frame", "end_frame", "lines", "position"] if role == "notes" \
                else ["id", "start_frame", "end_frame", "text", "visible"]
            _fields(item, names, f"{role}[{index}]")
            item_id = _text(item["id"], f"{role}[{index}].id")
            if item_id in identifiers:
                raise ValueError("Duplicate content id")
            identifiers.add(item_id)
            start = _integer(item["start_frame"], f"{role}.start_frame")
            end = _integer(item["end_frame"], f"{role}.end_frame", 1)
            if not start < end <= video["body_end_frame"]:
                raise ValueError(f"{role} interval must be non-empty and inside body")
            if role == "notes":
                if not isinstance(item["lines"], list) or not 1 <= len(item["lines"]) <= profile["roles"]["note"]["style"]["max_lines"]:
                    raise ValueError("Note lines violate max_lines")
                _fields(item["position"], ["left", "top"], "note.position")
                result.append({"id": item_id, "start_frame": start, "end_frame": end,
                               "lines": [_text(line, "note line") for line in item["lines"]],
                               "position": {key: _number(item["position"][key], f"note.position.{key}") for key in ("left", "top")}})
            else:
                if type(item["visible"]) is not bool:
                    raise ValueError("marker.visible must be boolean")
                result.append({"id": item_id, "start_frame": start, "end_frame": end,
                               "text": _text(item["text"], "marker.text"), "visible": item["visible"]})
    markers_sorted = sorted(markers, key=lambda item: (item["start_frame"], item["end_frame"]))
    if any(a["end_frame"] > b["start_frame"] for a, b in zip(markers_sorted, markers_sorted[1:])):
        raise ValueError("Markers may not overlap, including hidden markers")
    return {"base": base, "base_clock": base_clock, "video": video, "profile": profile, "caption_projection": projection,
            "content": {"title": title, "captions": captions, "notes": notes, "markers": markers}}


def build_schedule(validated):
    body = validated["video"]["body_end_frame"]
    timed = validated["content"]["captions"] + validated["content"]["notes"] + validated["content"]["markers"]
    boundaries = sorted({0, body, *(item[key] for item in timed for key in ("start_frame", "end_frame"))})
    schedule = []
    for start, end in zip(boundaries, boundaries[1:]):
        state = {"title": validated["content"]["title"]}
        for role in ("captions", "notes", "markers"):
            state[role] = [item for item in validated["content"][role]
                           if item["start_frame"] <= start < item["end_frame"]]
        key = hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")).encode()).hexdigest()
        if schedule and schedule[-1]["state_key"] == key:
            schedule[-1]["end_frame"] = end
        else:
            schedule.append({"start_frame": start, "end_frame": end, "state_key": key, "state": state})
    return schedule


def ordered_sequence_digest(directory, count):
    directory = Path(directory)
    expected = [directory / (PATTERN % index) for index in range(count)]
    if sorted(item.name for item in directory.iterdir()) != [item.name for item in expected]:
        raise ValueError("Sequence requires exact contiguous zero-based files")
    manifest = [{"frame": index, "sha256": _sha256(path.resolve())} for index, path in enumerate(expected)]
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _snapshot(validated):
    profile = validated["profile"]
    return {
        "base": validated["base"], "base_clock": validated["base_clock"],
        "video": {**validated["video"], "fps": str(validated["video"]["fps"])},
        "profile": profile["reference"], "underlay": profile["underlay"], "fonts": profile["fonts"],
        "caption_projection": validated["caption_projection"], "content": validated["content"],
    }


def _resource_identities():
    paths = [Path(__file__).resolve(), Path(__file__).with_name("theme_foreground_capture.mjs"),
             Path(__file__).with_name("theme_foreground_dom.html")]
    return [{"path": str(path), "sha256": _sha256(path)} for path in paths]


def _assert_current(plan_path, plan_sha, validated, resources):
    if _sha256(plan_path) != plan_sha:
        raise ValueError("Plan changed during production")
    snapshot = _snapshot(validated)
    for item in [snapshot["base"], snapshot["profile"], snapshot["underlay"], *snapshot["fonts"]]:
        if _sha256(item["path"]) != item["sha256"]:
            raise ValueError("A bound input changed during production")
    projection = snapshot["caption_projection"]
    if projection["kind"] == "bound":
        for key in ("track", "validation"):
            if _sha256(projection[key]["path"]) != projection[key]["sha256"]:
                raise ValueError("A subtitle input changed during production")
    for resource in resources:
        if _sha256(resource["path"]) != resource["sha256"]:
            raise ValueError("Trusted renderer resource changed during production")


def _capture_request(validated, state):
    profile = validated["profile"]
    underlay = "data:image/png;base64," + base64.b64encode(Path(profile["underlay"]["path"]).read_bytes()).decode()
    fonts = []
    for index, font in enumerate(profile["fonts"]):
        fonts.append({"id": font["id"], "weight": font["weight"], "family": f"ThemeFont{index}",
                      "platform_families": font["platform_families"],
                      "postscript_names": font["postscript_names"],
                      "source": "data:font/ttf;base64," + base64.b64encode(Path(font["path"]).read_bytes()).decode()})
    roles = json.loads(json.dumps(profile["roles"]))
    title_style = roles["title"]["style"]
    roles["title"]["size"] = shared_title_size(state["title"]["primary"], state["title"]["secondary"],
                                                  roles["title"]["fit"], title_style["preferred_size"],
                                                  title_style["minimum_size"])
    for role, plural in (("caption", "captions"), ("note", "notes"), ("marker", "markers")):
        style, fit = roles[role]["style"], roles[role]["fit"]
        texts = []
        for item in state[plural]:
            texts.extend(item.get("lines", [item.get("text", "")]))
        roles[role]["size"] = min([fit_size(text, fit, style["preferred_size"], style["minimum_size"])
                                   for text in texts], default=float(style["preferred_size"]))
    return {"canvas": profile["canvas"], "underlay": underlay, "fonts": fonts,
            "roles": roles, "state": state}


def _capture(node, browser, request, destination):
    resource = Path(__file__).with_name("theme_foreground_capture.mjs")
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as stream:
        json.dump(request, stream, ensure_ascii=False)
        request_path = Path(stream.name)
    try:
        process = subprocess.Popen([node, str(resource), str(request_path), str(destination), browser],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=6)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            raise RuntimeError(f"Trusted browser capture timed out: {stderr.strip()}")
    finally:
        request_path.unlink(missing_ok=True)
    if process.returncode:
        raise RuntimeError(f"Trusted browser capture failed: {stderr.strip()}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Trusted browser capture returned invalid evidence") from exc


def _runtime(executable, label):
    raw = shutil.which(executable) if os.sep not in executable else executable
    if not raw or not Path(raw).is_file():
        raise ValueError(f"{label} executable is missing")
    if "://" in raw or "?" in raw or "#" in raw:
        raise ValueError(f"{label} must be a plain local executable path")
    return str(Path(raw).resolve())


def run_producer(plan_path, output_dir, *, plan_only=False, node_executable="node",
                 browser_executable=None):
    plan_path = Path(plan_path).resolve()
    plan_sha = _sha256(plan_path)
    document = json.loads(plan_path.read_text(encoding="utf-8"))
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    run_path = directory / "theme_foreground_run.json"
    report = {"artifact": "theme_foreground_run", "schema_version": 1, "status": "PREPARING"}
    _save(run_path, report)
    try:
        validated = validate_plan_document(document)
        resources = _resource_identities()
        node = _runtime(node_executable, "Node")
        browser = _runtime(resolve_browser_executable(browser_executable), "Browser")
        schedule = build_schedule(validated)
        report.update(plan={"path": str(plan_path), "sha256": plan_sha},
                      inputs=_snapshot(validated), schedule=schedule,
                      runtime={"node_executable": node, "browser_executable": browser,
                               "resources": resources})
        _assert_current(plan_path, plan_sha, validated, resources)
        if plan_only:
            report.update(status="PLANNED", render="NOT_RENDERED", producer_receipt="NOT_ISSUED")
            _save(run_path, report)
            return report
        input_snapshot = _snapshot(validated)
        _save(directory / "input_snapshot.json", input_snapshot)
        _save(directory / "caption_projection.json", validated["caption_projection"])
        _save(directory / "state_schedule.json", schedule)
        frames = directory / "frames"
        states = directory / "states"
        frames.mkdir()
        states.mkdir()
        evidence = []
        rendered = {}
        for item in schedule:
            _assert_current(plan_path, plan_sha, validated, resources)
            destination = states / f"state_{item['state_key']}.png"
            if item["state_key"] not in rendered:
                observation = _capture(node, browser, _capture_request(validated, item["state"]), destination)
                _assert_current(plan_path, plan_sha, validated, resources)
                _rgba_bytes(destination, validated["video"]["width"], validated["video"]["height"])
                rendered[item["state_key"]] = destination
                evidence.append({"state_key": item["state_key"], "png_sha256": _sha256(destination),
                                 "layout": observation})
        _assert_current(plan_path, plan_sha, validated, resources)
        for item in schedule:
            source = rendered[item["state_key"]]
            for frame in range(item["start_frame"], item["end_frame"]):
                os.link(source, frames / (PATTERN % frame))
        digest = ordered_sequence_digest(frames, validated["video"]["body_end_frame"])
        _assert_current(plan_path, plan_sha, validated, resources)
        node_version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        browser_version = subprocess.run([browser, "--version"], capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        receipt = {
            "artifact": "theme_foreground_producer_receipt", "schema_version": 1,
            "status": "FOREGROUND_RENDERED", "plan": {"path": str(plan_path), "sha256": plan_sha},
            "inputs": input_snapshot,
            "snapshots": {
                "input": {"path": str(directory / "input_snapshot.json"),
                          "sha256": _sha256(directory / "input_snapshot.json")},
                "caption_projection": {"path": str(directory / "caption_projection.json"),
                                       "sha256": _sha256(directory / "caption_projection.json")},
                "state_schedule": {"path": str(directory / "state_schedule.json"),
                                   "sha256": _sha256(directory / "state_schedule.json")},
            },
            "caption_projection": validated["caption_projection"],
            "state_ranges": schedule, "layout_and_font_evidence": evidence,
            "runtime": {"node": node_version, "browser": browser_version,
                        "node_executable": node, "browser_executable": browser,
                        "resources": resources},
            "foreground": {"directory": str(frames), "pattern": PATTERN, "start_frame": 0,
                           "end_frame": validated["video"]["body_end_frame"], "ordered_sha256": digest},
            "claims": {"direct_listening": "NOT_CHECKED", "acoustic_alignment": "NOT_CHECKED",
                       "normal_speed_review": "NOT_CHECKED", "creative_approval": False,
                       "release_approved": False},
        }
        _assert_current(plan_path, plan_sha, validated, resources)
        _save(directory / "producer_receipt.json", receipt)
        report.update(status="FOREGROUND_RENDERED", producer_receipt=str(directory / "producer_receipt.json"),
                      foreground=receipt["foreground"])
        _save(run_path, report)
        return receipt
    except Exception as exc:
        (directory / "producer_receipt.json").unlink(missing_ok=True)
        report.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        report.pop("foreground", None)
        _save(run_path, report)
        raise
