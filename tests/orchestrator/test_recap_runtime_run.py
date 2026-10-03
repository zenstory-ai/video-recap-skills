import subprocess

import pytest

import recap_runtime


def test_run_forces_utf8_stdio_for_stage_scripts(monkeypatch):
    seen = {}

    def fake_run(cmd, env=None):
        seen["env"] = env
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(recap_runtime.subprocess, "run", fake_run)
    recap_runtime._run("video-script", "validate.py", "--help")

    assert seen["env"]["PYTHONIOENCODING"] == "utf-8"


def test_run_turns_a_failed_stage_into_system_exit(monkeypatch):
    monkeypatch.setattr(recap_runtime.subprocess, "run", lambda cmd, env=None: subprocess.CompletedProcess(cmd, 3))

    with pytest.raises(SystemExit, match="exit 3"):
        recap_runtime._run("video-script", "validate.py")
