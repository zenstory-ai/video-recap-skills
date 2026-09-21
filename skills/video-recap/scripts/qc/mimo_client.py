"""Load this skill's MiMo client without depending on the ambient ``lib`` module."""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "video_recap_mimo_qc_lib", Path(__file__).parent.with_name("lib.py")
)
_LIB = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_LIB)

DEFAULT_CONFIG = _LIB.CONFIG
mimo_qc_api_call = _LIB.mimo_qc_api_call
