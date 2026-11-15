"""Isolate per-test mutations to video-recap's module-level CONFIG."""

import copy
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lib  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_lib_config():
    snapshot = copy.deepcopy(lib.CONFIG)
    try:
        yield
    finally:
        lib.CONFIG.clear()
        lib.CONFIG.update(snapshot)
