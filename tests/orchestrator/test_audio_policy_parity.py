"""Audio policy must agree wherever it is actually consumed.

Previously every skill declared the full ~30-key audio policy and this file asserted all
five copies matched. That parity was circular: the keys only needed to agree because
lib.py had been copied, and most skills never read them. Now a skill declares a knob only
if its own code reads it, so parity is asserted over the real overlap — which is where a
divergence could actually change a rendered recap.
"""
import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIBS = {
    "assemble": ROOT / "skills/video-assemble/scripts/lib.py",
    "voiceover": ROOT / "skills/video-voiceover/scripts/lib.py",
    "script": ROOT / "skills/video-script/scripts/lib.py",
    "recap": ROOT / "skills/video-recap/scripts/lib.py",
    "understanding": ROOT / "skills/video-understanding/scripts/lib.py",
}

# Knobs that shape the rendered audio. A skill is held to these only if it declares them,
# i.e. only if its own code reads them — see test_audio_policy_keys_are_declared_where_read.
# Exactly the inputs narration_tempo_budget() reads. A skill that carries these must
# compute the same cap from them, because voiceover and assemble both enforce it.
TEMPO_KEYS = (
    "narration_speed",
    "narration_cumulative_tempo_max",
    "narration_cumulative_tempo_hard_max",
    "tts_segment_tempo_max",
)
MIX_KEYS = (
    "fade_ms",
    "ducking_mode",
    "ducking_threshold",
    "ducking_ratio",
    "ducking_attack",
    "ducking_release",
    "ducking_level_sc",
    "ducking_makeup",
    "ducking_narr_weight",
    "ducking_orig_volume",
    "zone_ducking_volume",
    # `zone_fade_seconds` used to sit here. It was declared in all five lib.py copies and
    # read by nothing in the entire repository — kept alive only by the old blanket parity
    # assertion, which proved the copies agreed without anyone consuming the value.
    "idle_orig_volume",
    "duck_fade_seconds",
    "bgm_volume",
    "bgm_ducking_volume",
    "speech_ducking_volume",
    "final_loudnorm",
    "target_lufs",
    "target_true_peak",
    "target_lra",
    "final_limiter_peak",
    "tts_segment_normalize",
    "tts_segment_target_rms_dbfs",
    "tts_segment_peak_limit",
)


def _load_lib(name, path):
    spec = importlib.util.spec_from_file_location(f"audio_policy_{name}_lib", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def libs():
    return {name: _load_lib(name, path) for name, path in LIBS.items()}


def _readable_source(path, *, include_lib=True):
    """Everything in a skill that could read CONFIG, excluding the CONFIG literal itself."""
    scripts = path.parent
    parts = [
        p.read_text(encoding="utf-8")
        for p in sorted(scripts.rglob("*.py"))
        if p.name != "lib.py" and "__pycache__" not in p.parts
    ]
    if include_lib:
        lib_source = path.read_text(encoding="utf-8")
        config_span = set()
        for node in ast.walk(ast.parse(lib_source)):
            if (
                isinstance(node, ast.Assign)
                and any(getattr(t, "id", "") == "CONFIG" for t in node.targets)
                and isinstance(node.value, ast.Dict)
            ):
                config_span = set(range(node.lineno, node.end_lineno + 1))
        parts.append(
            "\n".join(
                line
                for number, line in enumerate(lib_source.splitlines(), start=1)
                if number not in config_span
            )
        )
    return "\n".join(parts)


@pytest.mark.parametrize("key", TEMPO_KEYS + MIX_KEYS)
def test_audio_policy_value_agrees_across_every_skill_that_declares_it(libs, key):
    declarers = {name: lib for name, lib in libs.items() if key in lib.CONFIG}
    assert declarers, f"no skill declares {key!r} any more; drop it from this contract"
    values = {name: lib.CONFIG[key] for name, lib in declarers.items()}
    assert len(set(map(repr, values.values()))) == 1, f"{key} diverged: {values}"


def test_tempo_budget_helper_agrees_wherever_the_tempo_knobs_are_declared(libs):
    """narration_tempo_budget turns the tempo knobs into the cap voiceover and assemble both
    enforce. Any skill carrying those knobs must compute the same budget from them."""
    declarers = {
        name: lib
        for name, lib in libs.items()
        if all(key in lib.CONFIG for key in TEMPO_KEYS)
    }
    assert {"assemble", "voiceover"} <= set(declarers), (
        "assemble and voiceover both enforce the tempo budget and must declare its inputs"
    )
    for offset in (-0.05, 0.0, 0.05, 0.08):
        expected = declarers["assemble"].narration_tempo_budget(offset)
        for name, lib in declarers.items():
            assert hasattr(lib, "narration_tempo_budget"), name
            assert lib.narration_tempo_budget(offset) == expected, name


def test_visual_qc_delivery_boundary_fields_are_not_audio_policy_keys(libs):
    """Delivery transparency fields are rollup/QC facts, not shared audio policy knobs;
    this guards against leaking visual-delivery contract fields into CONFIG parity."""
    delivery_fact_keys = {
        "video_encode_passes",
        "reencode_reason",
        "audio_sample_rate",
        "final_compat_notes",
        "double_encode",
    }
    for name, lib in libs.items():
        assert not (delivery_fact_keys & set(lib.CONFIG)), name


def test_no_skill_declares_config_it_never_reads(libs):
    """The invariant that replaces blanket parity.

    A key declared where nothing reads it is dead weight at best and a lie at worst:
    CLIP_PADDING was declared in five CONFIGs, reported as active through
    clip_padding_source, and read by none of them — including the one skill that
    implements padding.
    """
    allowed_unread = {
        # Derived report of a knob this skill implements: FOREIGN_SOURCE_AUDIO selects the
        # ducking volumes below it, and this exposes which policy ended up in effect.
        "assemble": {"foreign_source_audio"},
    }
    offenders = {}
    for name, path in LIBS.items():
        readable = _readable_source(path)
        unread = {
            key
            for key in libs[name].CONFIG
            if f'"{key}"' not in readable and f"'{key}'" not in readable
        }
        unread -= allowed_unread.get(name, set())
        if unread:
            offenders[name] = sorted(unread)
    assert not offenders, (
        f"CONFIG keys declared but read by nothing in their own skill: {offenders}. "
        "Declare a knob in the skill that implements it, not in every copy of lib.py."
    )
