"""Shared strict-input helpers for the explicit-input assemble modules.

Every helper here fails closed with ``ValueError`` on malformed input and never
guesses: a path must be a local file, a JSON document must parse, a rational must
be canonical ``N/D``. ``run_logged`` and ``probe_json`` wrap the external tools so
each caller logs the same evidence.
"""

from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess


def require_fields(value, required, label):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError(f"{label} requires exactly fields {required}")


def without_digests(value, label):
    """Drop the ``sha256`` / ``*_sha256`` keys older caller JSON declared; they are ignored."""
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return {key: item for key, item in value.items()
            if key != "sha256" and not key.endswith("_sha256")}


def require_integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def require_number(value, label, minimum, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) \
            or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be finite in [{minimum},{maximum}]")
    return float(value)


def require_local_path(path, label):
    if not isinstance(path, (str, Path)) or not str(path) or "://" in str(path):
        raise ValueError(f"{label} requires a local path")
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise ValueError(f"{label} is missing: {resolved}")
    return resolved


def require_declared_path(value, label):
    """Resolve a caller ``{"path": ...}`` declaration to an existing local file."""
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object with a path")
    return require_local_path(value.get("path"), label)


def read_json_bytes(path, label):
    """Return (resolved_path, raw_bytes, parsed) for one local JSON document."""
    resolved = require_local_path(path, label)
    raw = resolved.read_bytes()
    try:
        return resolved, raw, json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc


def write_json_atomic(path, value):
    path = Path(path)
    temporary = path.with_suffix(".writing.json")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def canonical_fraction(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a canonical rational string")
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if result <= 0 or value != f"{result.numerator}/{result.denominator}":
        raise ValueError(f"{label} must be a positive canonical N/D rational")
    return result


def probe_json(path, *ffprobe_args):
    result = subprocess.run(
        ["ffprobe", "-v", "error", *ffprobe_args, "-of", "json", str(path)],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode or result.stderr.strip():
        raise ValueError(f"media probe failed for {path}: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid media probe JSON for {path}") from exc


def run_logged(command, directory, label, *, prefix="", timeout=3600):
    """Run one FFmpeg command, keeping ``<prefix><label>.command.json`` and ``.log``."""
    directory = Path(directory)
    name = f"{prefix}{label}"
    write_json_atomic(directory / f"{name}.command.json", command)
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    (directory / f"{name}.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"{name} FFmpeg failed; see {name}.log")
    return result
