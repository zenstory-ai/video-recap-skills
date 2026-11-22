import importlib.util
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[2]
COMMON_MODULES = [
    ("recap", ROOT / "skills" / "video-recap" / "scripts" / "_common.py"),
]


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(f"client_safety_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("name", "path"), COMMON_MODULES)
def test_api_error_sanitizer_redacts_media_data_urls_and_keys(name, path):
    common = _load_module(name, path)
    sanitizer = getattr(common, "sanitize_api_error", None) or getattr(common, "sanitize_error")
    test_tp_key = "tp-" + ("testkey" * 6)
    test_sk_key = "sk-" + ("secret" * 6)
    raw = (
        "server echoed data:audio/wav;base64,QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo= "
        f"for keys {test_tp_key} and {test_sk_key}"
    )

    safe = sanitizer(raw)

    assert "QUJDREV" not in safe
    assert test_tp_key not in safe
    assert test_sk_key not in safe
    assert "<redacted-data-url>" in safe
    assert "<redacted-key>" in safe
