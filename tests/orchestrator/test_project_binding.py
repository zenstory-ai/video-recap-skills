import json
import shlex
import shutil
import sys
from argparse import Namespace
from pathlib import Path

import pytest

import resources.project_binding as project_binding
import recap_runner
import recap_timeline
from _helpers import DEFAULT_NARRATION, seed_full_work, stub_child_run
from recap_cli import parse_args

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "resource-library"
BINDING_ENV = ("VIDEO_RECAP_MATERIAL_LIBRARY_DIR", "SUBTITLE_FONT_NAME", "SUBTITLE_FONT_FILE", "SUBTITLE_FONT_SIZE", "SUBTITLE_OUTLINE",
               "SUBTITLE_PLAY_RES_X", "SUBTITLE_PLAY_RES_Y", "SUBTITLE_ALIGNMENT", "SUBTITLE_MARGIN_V",
               "BGM_PATH", "MIMO_TTS_VOICE", "VOICE_REF", "FISH_TTS_REFERENCE_ID", "INDEX_TTS_VOICE")


@pytest.fixture
def clean_env(monkeypatch):
    for key in BINDING_ENV:
        monkeypatch.setenv(key, "")


def _project(tmp_path, bindings, *, library=None):
    lib = library or tmp_path / "lib"
    if not lib.exists():
        shutil.copytree(EXAMPLE, lib)
    path = tmp_path / "project" / "recap_project.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"schema": "video-recap.project.v1", "name": "演示项目",
                                "library": "../lib", "bindings": bindings}, ensure_ascii=False),
                    encoding="utf-8")
    return path, lib


def _args(**overrides):
    values = {"tts_provider": "auto", "mimo_tts_voice": None, "voice_ref": None,
              "material_library_dir": None}
    values.update(overrides)
    return Namespace(**values)


def test_bound_templates_and_resources_resolve_to_stage_settings(tmp_path, clean_env):
    path, lib = _project(tmp_path, {"subtitle_style": "clean-white@v1", "voice": "narrator-demo",
                                    "bgm": "pulse-demo"})

    resolved = project_binding.resolve_project(path.parent, _args())

    assert resolved["env"] == {
        "SUBTITLE_PLAY_RES_X": "900", "SUBTITLE_PLAY_RES_Y": "1600",
        "SUBTITLE_FONT_SIZE": "52", "SUBTITLE_OUTLINE": "3", "SUBTITLE_MAX_CHARS": "15",
        "SUBTITLE_FONT_NAME": "Arial",
        "SUBTITLE_ALIGNMENT": "2", "SUBTITLE_MARGIN_V": "160",
        "BGM_PATH": str((lib / "resources/bgm/pulse-demo/pulse-demo.wav").resolve()),
    }
    assert resolved["arg_updates"] == {"tts_provider": "mimo-tts", "mimo_tts_voice": "冰糖"}
    assert [(t["role"], t["id"], t["version"]) for t in resolved["templates"]] == [
        ("subtitle_style", "clean-white", 1)]


def test_font_resource_supplies_family_and_file(tmp_path, clean_env):
    lib = tmp_path / "lib"
    shutil.copytree(EXAMPLE, lib)
    font_dir = lib / "resources/font/brand-sans"
    font_dir.mkdir(parents=True)
    (font_dir / "brand.ttf").write_bytes(b"font")
    (font_dir / "resource.json").write_text(json.dumps({
        "schema": "video-recap.resource.v1", "id": "brand-sans", "kind": "font", "title": "品牌黑体",
        "files": [{"role": "regular", "path": "brand.ttf"}], "font": {"family": "Brand Sans"},
        "license": {"status": "licensed"}}), encoding="utf-8")
    template = lib / "templates/subtitle_style/clean-white/v1/template.json"
    data = json.loads(template.read_text(encoding="utf-8"))
    data["params"]["font"] = {"resource": "brand-sans"}
    template.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    path, _ = _project(tmp_path, {"subtitle_style": "clean-white@v1"}, library=lib)

    env = project_binding.resolve_project(path, _args())["env"]

    assert env["SUBTITLE_FONT_NAME"] == "Brand Sans"
    assert env["SUBTITLE_FONT_FILE"] == str((font_dir / "brand.ttf").resolve())


@pytest.mark.parametrize(
    "bindings, args, env, match",
    [
        pytest.param({"packaging": "bottom-bar@v1"}, {}, {}, "draft", id="draft_template"),
        pytest.param({"production_reference": "demo-pacing@v1"}, {}, {}, "draft", id="draft_reference"),
        pytest.param({"subtitle_style": "clean-white@v9"}, {}, {}, "不存在", id="missing_template"),
        pytest.param({"voice": "pulse-demo"}, {}, {}, "voice 资源", id="wrong_resource_kind"),
        pytest.param({"subtitle_style": "clean-white@v1"}, {}, {"SUBTITLE_FONT_SIZE": "40"}, "SUBTITLE_FONT_SIZE", id="env_conflict"),
        pytest.param({"voice": "narrator-demo"}, {}, {"MIMO_TTS_VOICE": "别的"}, "MIMO_TTS_VOICE", id="voice_env_conflict"),
        pytest.param({"voice": "narrator-demo"}, {"mimo_tts_voice": "别的"}, {}, "--mimo-tts-voice", id="voice_arg_conflict"),
        pytest.param({"voice": "narrator-demo"}, {"tts_provider": "fish-audio"}, {}, "fish-audio", id="provider_conflict"),
        pytest.param({"colour": "x"}, {}, {}, "bindings.colour", id="unknown_binding"),
        pytest.param({"voice": "narrator-demo"}, {}, {"VIDEO_RECAP_MATERIAL_LIBRARY_DIR": "/elsewhere"}, "VIDEO_RECAP_MATERIAL_LIBRARY_DIR", id="material_library_env_conflict"),
        pytest.param({"bgm": "pulse-demo"}, {"audio_mode": "adopted-packet-copy"}, {}, "adopted-packet-copy", id="bgm_with_frozen_audio"),
    ],
)
def test_bindings_that_cannot_apply_stop_before_any_stage(tmp_path, clean_env, monkeypatch,
                                                            bindings, args, env, match):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    path, _ = _project(tmp_path, bindings)

    with pytest.raises(SystemExit, match=match):
        project_binding.resolve_project(path, _args(**args))


def test_template_on_another_canvas_is_rejected(tmp_path, clean_env):
    path, _ = _project(tmp_path, {"subtitle_style": "clean-white@v1"})
    resolved = project_binding.resolve_project(path, _args())

    project_binding.check_canvas(resolved, 900, 1600)
    with pytest.raises(SystemExit, match="900x1600"):
        project_binding.check_canvas(resolved, 1080, 1920)


def test_project_run_forwards_bound_settings_and_records_them(monkeypatch, tmp_path, clean_env):
    path, lib = _project(tmp_path, {"subtitle_style": "clean-white@v1", "voice": "narrator-demo",
                                    "bgm": "pulse-demo"})
    video, work = seed_full_work(tmp_path)
    seen = {}

    def assemble(cli):
        seen.update({k: recap_runner.os.environ[k] for k in ("BGM_PATH", "SUBTITLE_FONT_SIZE")})
        (work / "assembly_manifest.json").write_text(json.dumps({"final_output": "x.mp4"}), encoding="utf-8")

    calls = []
    monkeypatch.setattr(recap_runner, "_run", stub_child_run(work, None, calls, assemble=assemble))
    monkeypatch.setattr(recap_runner, "_probe_display_size_or_raise", lambda *a, **k: (900, 1600))
    monkeypatch.setattr(sys, "argv", ["recap_runner.py", str(video), "--work-dir", str(work),
                                      "--project", str(path)])

    recap_runner.main()

    voiceover = next(cli for skill, script, cli in calls if script == "voiceover.py")
    assert voiceover[voiceover.index("--mimo-voice") + 1] == "冰糖"
    assert seen == {"BGM_PATH": str((lib / "resources/bgm/pulse-demo/pulse-demo.wav").resolve()),
                    "SUBTITLE_FONT_SIZE": "52"}
    lock = json.loads((work / "resource_lock.json").read_text(encoding="utf-8"))
    assert lock["project"] == {"path": str(path.resolve()), "name": "演示项目"}
    assert [t["id"] for t in lock["templates"]] == ["clean-white"]
    assert lock["library"] == str(lib.resolve())


def test_resume_command_carries_the_project_not_the_values_it_bound(tmp_path, clean_env):
    path, _ = _project(tmp_path, {"voice": "narrator-demo"})
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    _, args = parse_args([str(video), "--project", str(path)])
    project_binding.apply_project(project_binding.resolve_project(args.project, args), args)

    command = recap_timeline._continuation_command(tmp_path / "work", args)

    assert "--project" in command
    assert "--mimo-tts-voice" not in command and "--material-library-dir" not in command


def test_shipped_example_project_resolves_against_the_example_library(clean_env):
    resolved = project_binding.resolve_project(ROOT / "examples" / "demo-project", _args())

    assert resolved["library"] == str(EXAMPLE.resolve())
    assert resolved["env"]["BGM_PATH"].endswith("pulse-demo.wav")


def test_bound_packaging_template_is_handed_to_assemble_and_retired_when_unbound(tmp_path, clean_env):
    lib = tmp_path / "lib"
    shutil.copytree(EXAMPLE, lib)
    template = lib / "templates/packaging/bottom-bar/v1/template.json"
    data = json.loads(template.read_text(encoding="utf-8"))
    data.update(status="adopted", adoption={"date": "2026-09-27", "by": "user", "statement": "就用这个",
                                            "scope": "演示画布"})
    template.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    path, _ = _project(tmp_path, {"packaging": "bottom-bar@v1"}, library=lib)
    work = tmp_path / "work"
    work.mkdir()

    project_binding.sync_packaging_layers(work, project_binding.resolve_project(path, _args()))

    plan = json.loads((work / "packaging_layers.json").read_text(encoding="utf-8"))
    assert plan["canvas"] == {"width": 900, "height": 1600}
    assert plan["layers"] == [{"name": "bottom-bar",
                               "path": str((lib / "resources/image/frame-demo/frame-demo.png").resolve()),
                               "rect": {"x": 0, "y": 0, "width": 900, "height": 1600}}]

    project_binding.sync_packaging_layers(work, None)
    assert not (work / "packaging_layers.json").exists()

    (work / "packaging_layers.json").write_text(json.dumps({"layers": []}), encoding="utf-8")
    project_binding.sync_packaging_layers(work, None)
    assert (work / "packaging_layers.json").exists()  # caller-authored plans are not ours to delete


def test_resume_command_names_the_project_by_absolute_path(monkeypatch, tmp_path, clean_env, capsys):
    path, _ = _project(tmp_path, {"voice": "narrator-demo"})
    video, work = seed_full_work(tmp_path, narration=None)

    def understand(cli):
        (work / "agent_narration_brief.md").write_text("# brief", encoding="utf-8")

    monkeypatch.setattr(recap_runner, "_run", stub_child_run(work, understand=understand))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["recap_runner.py", str(video), "--work-dir", str(work),
                                      "--project", "project"])

    recap_runner.main()

    # The typed directory is kept, made absolute, and still names the same project file.
    typed = (tmp_path / "project").resolve()
    assert f"--project {shlex.quote(str(typed))}" in capsys.readouterr().out
    assert project_binding.project_path(typed) == path.resolve()


@pytest.mark.parametrize(
    "bindings, break_library, match",
    [
        pytest.param({"bgm": "pulse-demo"},
                     lambda lib: (lib / "resources/bgm/pulse-demo/pulse-demo.wav").unlink(),
                     "bgm 资源 pulse-demo", id="bound_bgm_file_missing"),
        pytest.param({"packaging": "bottom-bar@v1"},
                     lambda lib: (lib / "resources/image/frame-demo/frame-demo.png").unlink(),
                     "引用的资源 frame-demo", id="template_image_missing"),
    ],
)
def test_bindings_to_invalid_library_records_are_refused(tmp_path, clean_env, bindings, break_library, match):
    path, lib = _project(tmp_path, bindings)
    template = lib / "templates/packaging/bottom-bar/v1/template.json"
    data = json.loads(template.read_text(encoding="utf-8"))
    data.update(status="adopted", adoption={"date": "2026-09-27", "by": "u", "statement": "s", "scope": "s"})
    template.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    break_library(lib)

    with pytest.raises(SystemExit, match=match):
        project_binding.resolve_project(path, _args())


def test_equivalent_ambient_settings_are_not_conflicts(tmp_path, clean_env, monkeypatch):
    path, lib = _project(tmp_path, {"subtitle_style": "clean-white@v1", "bgm": "pulse-demo"})
    monkeypatch.chdir(lib / "resources/bgm/pulse-demo")
    monkeypatch.setenv("BGM_PATH", "pulse-demo.wav")
    monkeypatch.setenv("SUBTITLE_FONT_SIZE", "52.0")

    project_binding.resolve_project(path, _args())


REFERENCE_DIR = "templates/production_reference/demo-pacing/v1"
FACT_KEYS = {"source_facts", "labels", "evidence", "entities", "statement", "from", "path"}


def _reference_project(tmp_path, *, canvas=None, subtitle=True):
    lib = tmp_path / "lib"
    shutil.copytree(EXAMPLE, lib)
    template = lib / REFERENCE_DIR / "template.json"
    data = json.loads(template.read_text(encoding="utf-8"))
    data.update(status="adopted", adoption={"date": "2026-10-02", "by": "user", "statement": "按这份节奏来",
                                            "scope": "演示"})
    if canvas:
        data["canvas"] = canvas
    template.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    bindings = {"production_reference": "demo-pacing@v1"}
    if subtitle:
        bindings["subtitle_style"] = "clean-white@v1"
    path, _ = _project(tmp_path, bindings, library=lib)
    export = json.loads((lib / REFERENCE_DIR / "production_reference.json").read_text(encoding="utf-8"))
    return path, export


def test_bound_reference_is_a_marked_copy_with_no_canvas_check_and_retires_when_unbound(tmp_path, clean_env):
    path, export = _reference_project(tmp_path, canvas={"width": 1280, "height": 720})
    resolved = project_binding.resolve_project(path, _args())
    work = tmp_path / "work"
    work.mkdir()

    project_binding.check_canvas(resolved, 900, 1600)  # the reference's own canvas is information only
    with pytest.raises(SystemExit, match="subtitle_style"):
        project_binding.check_canvas(resolved, 1280, 720)
    project_binding.sync_production_reference(work, resolved)

    copy = json.loads((work / "production_reference.json").read_text(encoding="utf-8"))
    assert copy == {**export, "template": {"id": "demo-pacing", "version": 1}, "written_by": "video-recap --project"}
    assert not FACT_KEYS & {k for node in (copy, *copy["methods"]) for k in node}
    assert project_binding.bound_reference_note(work).startswith("本轮带制作参考 demo-pacing@v1")

    project_binding.sync_production_reference(work, None)
    assert not (work / "production_reference.json").exists()
    assert project_binding.bound_reference_note(work) is None


def test_caller_reference_is_kept_unless_it_disagrees_with_the_binding(tmp_path, clean_env):
    path, export = _reference_project(tmp_path)
    resolved = project_binding.resolve_project(path, _args())
    work = tmp_path / "work"
    work.mkdir()
    own = work / "production_reference.json"

    own.write_text(json.dumps({**export, "methods": export["methods"][:1]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="demo-pacing@v1"):
        project_binding.sync_production_reference(work, resolved)
    project_binding.sync_production_reference(work, None)
    assert own.exists()  # a caller's own copy is not ours to delete
    assert project_binding.bound_reference_note(work) is None

    own.write_text(json.dumps(export), encoding="utf-8")  # the same export copied by hand is no conflict
    project_binding.sync_production_reference(work, resolved)
    assert json.loads(own.read_text(encoding="utf-8")) == export  # left as the caller wrote it
    assert project_binding.bound_reference_note(work) is None  # without the binding it is just a file
    assert project_binding.bound_reference_note(work, resolved).startswith("本轮带制作参考 demo-pacing@v1")
    project_binding.sync_production_reference(work, None)
    assert json.loads(own.read_text(encoding="utf-8")) == export  # still the caller's, so unbinding keeps it


def test_payload_written_by_cannot_unmark_the_bound_copy(tmp_path, clean_env):
    path, export = _reference_project(tmp_path)
    resolved = project_binding.resolve_project(path, _args())
    resolved["production_reference"]["payload"] = {**export, "written_by": "someone else"}
    work = tmp_path / "work"
    work.mkdir()

    project_binding.sync_production_reference(work, resolved)
    project_binding.sync_production_reference(work, resolved)  # a resume is no conflict with our own copy

    copy = json.loads((work / "production_reference.json").read_text(encoding="utf-8"))
    assert copy["written_by"] == "video-recap --project"
    project_binding.sync_production_reference(work, None)
    assert not (work / "production_reference.json").exists()


def test_reference_reaches_work_dir_before_the_first_pause_and_lands_in_the_lock(monkeypatch, tmp_path, clean_env,
                                                                                capsys):
    path, _ = _reference_project(tmp_path)
    video, work = seed_full_work(tmp_path, narration=None)

    def understand(cli):
        (work / "agent_narration_brief.md").write_text("# brief", encoding="utf-8")

    def assemble(cli):
        (work / "assembly_manifest.json").write_text(json.dumps({"final_output": "x.mp4"}), encoding="utf-8")

    monkeypatch.setattr(recap_runner, "_run", stub_child_run(work, understand=understand, assemble=assemble))
    monkeypatch.setattr(recap_runner, "_probe_display_size_or_raise", lambda *a, **k: (900, 1600))
    monkeypatch.setattr(sys, "argv", ["recap_runner.py", str(video), "--work-dir", str(work),
                                      "--project", str(path)])

    recap_runner.main()

    assert "本轮带制作参考 demo-pacing@v1" in capsys.readouterr().out
    assert json.loads((work / "production_reference.json").read_text(encoding="utf-8"))["template"] == {
        "id": "demo-pacing", "version": 1}

    (work / "narration.json").write_text(json.dumps(DEFAULT_NARRATION, ensure_ascii=False), encoding="utf-8")
    recap_runner.main()

    lock = json.loads((work / "resource_lock.json").read_text(encoding="utf-8"))
    assert [(t["role"], t["id"], t["version"]) for t in lock["templates"]] == [
        ("subtitle_style", "clean-white", 1), ("production_reference", "demo-pacing", 1)]


def test_reference_only_binding_does_not_probe_the_canvas(monkeypatch, tmp_path, clean_env):
    path, _ = _reference_project(tmp_path, subtitle=False)
    video, work = seed_full_work(tmp_path)

    def assemble(cli):
        (work / "assembly_manifest.json").write_text(json.dumps({"final_output": "x.mp4"}), encoding="utf-8")

    def probe(*a, **k):
        raise AssertionError("a reference carries no geometry; nothing to probe")

    monkeypatch.setattr(recap_runner, "_run", stub_child_run(work, assemble=assemble))
    monkeypatch.setattr(recap_runner, "_probe_display_size_or_raise", probe)
    monkeypatch.setattr(sys, "argv", ["recap_runner.py", str(video), "--work-dir", str(work),
                                      "--project", str(path)])

    recap_runner.main()

    assert (work / "assembly_manifest.json").exists()
