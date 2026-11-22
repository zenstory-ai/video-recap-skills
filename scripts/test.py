#!/usr/bin/env python3
"""Cross-platform test runner: one isolated pytest process per test group.

`video-recap` and `video-reference` each ship their own `lib.py` (with no shared
package), so a single `pytest tests/` run would import both skills into one
process and collide on `lib`. Run one group per subprocess instead.

Works on macOS, Linux, and Windows (the bash equivalent is scripts/test.sh).

Usage: python scripts/test.py            # run every skill group
       python scripts/test.py script     # run one or more named groups
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

GROUPS = [
    "understanding", "cut", "voiceover", "assemble", "script", "orchestrator", "reference",
]

_SUBTITLES_FILTER_RE = re.compile(r"(?:^|\s)subtitles(?:\s|$)", re.MULTILINE)


def _ffmpeg_required() -> bool:
    raw = os.environ.get("RECAP_REQUIRE_FFMPEG", "").strip().lower()
    return raw not in {"", "0", "false", "no"}


def _require_ffmpeg_if_requested() -> bool:
    """Fail closed when RECAP_REQUIRE_FFMPEG is set and ffmpeg/ffprobe/libass is missing."""
    if not _ffmpeg_required():
        return True
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        print(
            f"RECAP_REQUIRE_FFMPEG is set, but required media tools are missing from PATH: {', '.join(missing)}.",
            file=sys.stderr,
        )
        return False
    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if probe.returncode != 0 or not _SUBTITLES_FILTER_RE.search(probe.stdout):
        print(
            "RECAP_REQUIRE_FFMPEG is set, but ffmpeg does not expose the libass `subtitles` filter.",
            file=sys.stderr,
        )
        return False
    return True


def _require_pytest():
    """Fail with the actual problem instead of reporting every group as a test failure.

    Without this, a missing pytest prints "No module named pytest" once per group and then
    "FAILED groups: understanding, cut, ..." — indistinguishable from real failures.
    """
    import importlib.util

    if importlib.util.find_spec("pytest") is not None:
        return True
    print(
        f"pytest is not installed for this interpreter ({sys.executable}).\n"
        f"  Install it:  {sys.executable} -m pip install pytest\n"
        "  Or run the suite with an interpreter that has it (PYTHON=... scripts/test.sh).",
        file=sys.stderr,
    )
    return False


def main(argv):
    if not _require_pytest():
        return 2
    if not _require_ffmpeg_if_requested():
        return 2
    root = Path(__file__).resolve().parent.parent
    groups = argv or GROUPS
    failed = []
    for group in groups:
        print(f"== {group} ==", flush=True)
        result = subprocess.run(
            # -rs prints skip reasons so a silently-skipped real-render test (e.g. ffmpeg
            # missing) is visible in CI output instead of a bare "s".
            [sys.executable, "-m", "pytest", str(root / "tests" / group), "-q", "-rs"],
            cwd=str(root),
        )
        if result.returncode != 0:
            failed.append(group)
    if failed:
        print(f"FAILED groups: {', '.join(failed)}")
        return 1
    print("All skill groups passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
