"""The QC config surface, tested through behaviour rather than structure.

`safe_mimo_config` reads its inputs from a COPY of CONFIG (`dict(DEFAULT_CONFIG)` inside
`_effective_config`, then `source.get(...)`). Neither a `CONFIG.get("...")` grep nor
instrumenting the live CONFIG dict can see those reads, so a structural "is this key
declared" check cannot protect them — trimming video-recap's CONFIG cut the persisted QC
report from 15 provenance fields to 4 while every existing test stayed green. Absent keys
are skipped silently (`if key in source`), so the report just got quietly thinner.

These assert the observable contract instead: which model a QC request actually uses, and
which non-secret fields the report actually carries.

Note the asymmetry these tests pin down: MIMO_QC_MODEL is re-read from the environment on
every call, while MIMO_MODEL and MIMO_VIDEO_MODEL are captured into CONFIG at import time.
So the latter two are exercised by evaluating lib.py in a fresh module namespace with the
variable set, not by patching os.environ after the fact.
"""
import importlib.util

import pytest

from _helpers import SCRIPTS as RECAP_SCRIPTS
from mimo_qc_evidence import safe_mimo_config

MODEL_ENV_VARS = ("MIMO_QC_MODEL", "MIMO_MODEL", "MIMO_VIDEO_MODEL")

# Every non-secret field safe_mimo_config promises to carry into the persisted QC report.
REPORTED_PROVENANCE_FIELDS = (
    "api_provider",
    "mimo_api_url",
    "mimo_api_url_source",
    "mimo_video_api_url",
    "mimo_video_api_url_source",
    "mimo_qc_model",
    "mimo_qc_model_source",
    "mimo_model",
    "mimo_model_source",
    "mimo_video_model",
    "mimo_video_model_source",
    "mimo_disable_thinking",
    "mimo_disable_thinking_source",
    "mimo_media_resolution",
    "mimo_media_resolution_source",
)


def _config_with_env(monkeypatch, **env):
    """Evaluate video-recap's lib.py under a given environment, in its own namespace.

    Model/URL knobs are captured into CONFIG at import time, so this is the only way to
    exercise the env -> CONFIG -> QC-request chain. A separate module instance keeps the
    live CONFIG (which other tests in this process patch) untouched.
    """
    for name in MODEL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location(
        "_qc_config_probe_lib", RECAP_SCRIPTS / "lib.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CONFIG


@pytest.fixture(autouse=True)
def _no_ambient_model_env(monkeypatch):
    for name in MODEL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("env_var", "expected"),
    [
        ("MIMO_QC_MODEL", "qc-model"),
        ("MIMO_MODEL", "general-model"),
        ("MIMO_VIDEO_MODEL", "video-model"),
    ],
)
def test_every_model_env_var_reaches_the_qc_request(monkeypatch, env_var, expected):
    """All three routes must land on the QC request. This survived the CONFIG trim only
    because mimo_video_model happened to remain declared and absorbs MIMO_MODEL — the
    chain has no test of its own, so pin it before the next trim removes the wrong key."""
    config = _config_with_env(monkeypatch, **{env_var: expected})
    assert safe_mimo_config(config)["model"] == expected


def test_qc_model_precedence_is_qc_then_video_then_general(monkeypatch):
    general = _config_with_env(monkeypatch, MIMO_MODEL="general-model")
    assert safe_mimo_config(general)["model"] == "general-model"

    video = _config_with_env(
        monkeypatch, MIMO_MODEL="general-model", MIMO_VIDEO_MODEL="video-model"
    )
    assert safe_mimo_config(video)["model"] == "video-model"

    qc = _config_with_env(
        monkeypatch,
        MIMO_MODEL="general-model",
        MIMO_VIDEO_MODEL="video-model",
        MIMO_QC_MODEL="qc-model",
    )
    assert safe_mimo_config(qc)["model"] == "qc-model"


def test_qc_model_env_is_re_read_on_every_call(monkeypatch):
    """MIMO_QC_MODEL is deliberately read at call time so a long-running agent process can
    change it between stages — unlike the other two, which are import-time."""
    monkeypatch.setenv("MIMO_QC_MODEL", "late-binding-model")
    assert safe_mimo_config()["model"] == "late-binding-model"


def test_explicit_config_still_outranks_the_environment(monkeypatch):
    monkeypatch.setenv("MIMO_QC_MODEL", "from-env")
    assert safe_mimo_config({"mimo_qc_model": "from-caller"})["model"] == "from-caller"


def test_report_carries_every_promised_provenance_field():
    """These are dropped silently when absent (`if key in source`), so a missing CONFIG key
    shows up as a quietly thinner report rather than as an error."""
    safe = safe_mimo_config()
    missing = [field for field in REPORTED_PROVENANCE_FIELDS if field not in safe]
    assert not missing, f"QC report lost provenance fields: {missing}"


def test_report_never_carries_a_credential(monkeypatch):
    config = _config_with_env(monkeypatch, MIMO_API_KEY="tp-secret-value")
    safe = safe_mimo_config(config)

    assert safe["key_present"] is True
    assert "tp-secret-value" not in repr(safe)
    assert not [key for key in safe if key.endswith("api_key") or key == "api_key"]
