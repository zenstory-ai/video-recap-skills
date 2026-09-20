"""Real CLI-to-CLI smoke: a source-only film needs no TTS client or credentials."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="requires local media tools")
@pytest.mark.parametrize("audio_mode", ["source-mix", "adopted-packet-copy"])
@pytest.mark.parametrize("picture_mode", ["full", "cut", "multi-cut"])
def test_source_really_renders_without_narration(tmp_path, audio_mode, picture_mode):
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "source.mp4"
    work = tmp_path / "work"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=320x180:r=24:d=2",
        "-f", "lavfi", "-i", "sine=f=400:r=48000:d=2",
        "-c:v", "libx264", "-threads", "2", "-c:a", "aac", "-shortest", str(source),
    ], check=True, capture_output=True)
    # Child process has no service credentials. No understanding/ASR/TTS may run.
    env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG") if k in os.environ}
    inputs = [source]
    if picture_mode == "multi-cut":
        source2 = tmp_path / "source2.mp4"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-vf", "negate", "-c:v", "libx264", "-threads", "2", "-c:a", "copy", str(source2),
        ], check=True, capture_output=True)
        inputs.append(source2)
    cli_args = [*map(str, inputs),
        "--work-dir", str(work), "--audio-mode", audio_mode, "--no-burn-subtitles",
    ]
    if picture_mode != "full":
        cli_args += ["--edit-mode", "cut"]
        work.mkdir()
        # This fixture supplies the phase-A identities and author plan for speech-free
        # synthetic sources. It does NOT claim understanding/creative model validation.
        seed = '''
import json,sys
from pathlib import Path
from recap_cli import parse_args
from recap_runtime import _write_run_manifest,_write_project_run_manifest,_build_multi_source_records
_,args=parse_args()
work=Path(args.work_dir)
videos=[Path(p).resolve() for p in args.video]
if len(videos)==1:
    _write_run_manifest(work,videos[0],args)
    clips=[{"start":0,"end":1},{"start":1,"end":2}]
else:
    sources=_build_multi_source_records(videos,args)
    _write_project_run_manifest(work,videos,args,sources)
    clips=[{"start":0,"end":1,"source_id":s["source_id"]} for s in sources]
(work/"clip_plan.json").write_text(json.dumps(clips))
'''
        subprocess.run([sys.executable, "-c", seed, *cli_args],
                       cwd=root / "skills/video-recap/scripts", env=env,
                       check=True, capture_output=True, text=True)
        env.update(SCENE_CUT_SNAP="0", SNAP_CLIP_LINE_END="0")
    result = subprocess.run([
        sys.executable, str(root / "skills/video-recap/scripts/recap.py"), *cli_args,
    ], env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "video-understanding/" not in result.stdout
    assert "video-voiceover/" not in result.stdout
    if picture_mode != "full":
        assert "video-cut/cut.py" in result.stdout
        assert (work / "edited_source.mp4").is_file()
    assert not (work / "narration.json").exists()
    assert not (work / "tts_meta.json").exists()
    manifest = json.loads((work / "assembly_manifest.json").read_text())
    assert manifest["audio_mode"] == audio_mode
    assert manifest["tts_segments"] == 0
    assert manifest["tts_meta"] is None
    assert manifest["qc_verdict"] == "PASS"
    output = Path(manifest["final_output"])
    assert output.is_file()
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(output), "-f", "null", "-"],
                   check=True, capture_output=True, timeout=60)
    ledger = json.loads((work / "preflight_qc.json").read_text())
    assert "not_applicable" in json.dumps(ledger)
