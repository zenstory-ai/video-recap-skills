"""Independent local-adoption route checks: no synthesis and separate audio identity."""

import json
import hashlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'skills/video-recap/scripts'
sys.path.insert(0, str(SCRIPTS))
import recap_cli  # noqa: E402
import recap_runner  # noqa: E402
import recap_runtime  # noqa: E402
import recap_source  # noqa: E402
from test_audio_routing import _args, _finish_stubs  # noqa: E402


@pytest.fixture
def local_inputs(tmp_path):
    picture = tmp_path/'prebuilt.mp4'
    picture.write_bytes(b'prebuilt-picture-routing-fixture')
    paths = {}
    for key in ['tts_meta', 'narration_adoption', 'audio_mix_adoption']:
        paths[key] = tmp_path/f'{key}.json'
        paths[key].write_text(json.dumps({'fixture': key}))
    return picture, paths


def arguments(picture, paths, work):
    command = ['recap.py', str(picture), '--edit-mode', 'full', '--work-dir', str(work),
               '--no-burn-subtitles']
    for key, path in paths.items():
        command += ['--'+key.replace('_', '-'), str(path)]
    return command


def adopted_finish_stubs(monkeypatch, picture, paths, work, calls):
    fake_run = _finish_stubs(monkeypatch, work, calls)

    def identity(path):
        return {'path': str(path.resolve()),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def finish(skill, script, *args):
        result = fake_run(skill, script, *args)
        if script == 'assemble.py':
            final = work/'final.mp4'
            final.write_bytes(b'independent-routing-final-fixture')
            narration = {
                'artifact': 'narration_input_binding', 'schema_version': 1,
                'status': 'FINALIZED', 'identity_status': 'BOUND_TO_ADOPTION',
                'adoption': {**identity(paths['narration_adoption']),
                             'tts_meta': identity(paths['tts_meta'])},
                'final_output': identity(final),
            }
            narration_path = work/'narration_input_binding.json'
            narration_path.write_text(json.dumps(narration))
            mix = {
                'artifact': 'audio_mix_binding', 'schema_version': 1,
                'status': 'FINALIZED', 'adoption': identity(paths['audio_mix_adoption']),
                'picture': identity(picture),
                'narration_input_binding': identity(narration_path),
                'final_output': identity(final),
            }
            (work/'audio_mix_binding.json').write_text(json.dumps(mix))
        return result

    monkeypatch.setattr(recap_runner, '_run', finish)
    return finish


def test_local_adoption_ignores_ambient_synthesis_and_never_opens_authoring(
    local_inputs, tmp_path, monkeypatch,
):
    picture, paths = local_inputs
    work = tmp_path/'new-work'
    calls = []
    adopted_finish_stubs(monkeypatch, picture, paths, work, calls)
    monkeypatch.setenv('TTS_PROVIDER', 'invalid-must-not-be-selected')
    monkeypatch.setenv('VOICE_REF', '/missing/unrelated-reference.wav')
    monkeypatch.setenv('MIMO_TTS_VOICE', 'unused-ambient-voice')
    monkeypatch.setattr(sys, 'argv', arguments(picture, paths, work))
    recap_runner.main()
    assert [(skill, script) for skill, script, _ in calls] == [
        ('video-assemble', 'assemble.py'),
    ]
    forwarded = calls[0][2]
    for key, path in paths.items():
        flag = '--'+key.replace('_', '-')
        assert forwarded[forwarded.index(flag)+1] == str(path.resolve())
    assert not (work/'narration.json').exists()
    assert not (work/'clip_plan.json').exists()
    assert '--voice-ref' not in forwarded and '--tts-provider' not in forwarded


@pytest.mark.parametrize('changed', ['tts_meta', 'narration_adoption', 'audio_mix_adoption'])
def test_each_adoption_content_change_is_audio_only_identity(local_inputs, changed):
    _, paths = local_inputs
    args = _args(**{key: str(path) for key, path in paths.items()})
    before = recap_source.audio_binding(args)
    analysis = recap_runtime._analysis_settings(args)
    paths[changed].write_text(paths[changed].read_text()+'\n')
    assert recap_source.audio_binding(args) != before
    assert recap_runtime._analysis_settings(args) == analysis


def test_existing_adopted_workdir_cannot_trigger_any_stage(local_inputs, tmp_path, monkeypatch):
    picture, paths = local_inputs
    work = tmp_path/'already-used'
    work.mkdir()
    sentinel = work/'keep.txt'
    sentinel.write_text('preserve')
    calls = []
    _finish_stubs(monkeypatch, work, calls)
    monkeypatch.setattr(sys, 'argv', arguments(picture, paths, work))
    with pytest.raises((SystemExit, ValueError, RuntimeError)):
        recap_runner.main()
    assert calls == []
    assert sentinel.read_text() == 'preserve'


def test_adoption_cannot_change_between_run_manifest_and_assemble(
    local_inputs, tmp_path, monkeypatch,
):
    picture, paths = local_inputs
    work = tmp_path/'changing-work'
    calls = []
    fake_run = _finish_stubs(monkeypatch, work, calls)

    def changed_bundle(skill, script, *args):
        if script == 'assemble.py':
            # The child can see a different valid document than the one recorded
            # in the parent's run manifest. The parent must not report success.
            path = paths['audio_mix_adoption']
            path.write_text(path.read_text()+'\n')
        return fake_run(skill, script, *args)

    monkeypatch.setattr(recap_runner, '_run', changed_bundle)
    monkeypatch.setattr(sys, 'argv', arguments(picture, paths, work))
    with pytest.raises((SystemExit, ValueError, RuntimeError)):
        recap_runner.main()
    assert [script for _, script, _ in calls] == ['assemble.py']


@pytest.mark.parametrize('flag', ['--voice-ref', '--voice-r'])
def test_conflicting_voice_flag_is_rejected_in_full_and_abbreviated_spelling(
    local_inputs, tmp_path, flag,
):
    """An abbreviation must not slip a rejected option past the adoption guard."""
    picture, paths = local_inputs
    work = tmp_path/'new-work'
    argv = arguments(picture, paths, work)[1:] + [flag, str(picture)]
    with pytest.raises(SystemExit):
        parser, args = recap_cli.parse_args(argv)
        # Anything that reached voice_ref must also be visible to the guard.
        assert args.voice_ref is None or '--voice-ref' in args._explicit_options
        recap_source.validate_local_adoption(parser, args)


def test_parse_args_records_explicit_options_from_its_own_argv(monkeypatch):
    """Explicitness follows what argparse consumed, not whatever sys.argv happens to say."""
    monkeypatch.setattr(sys, 'argv', ['recap.py', '--voice-ref', '/unrelated.wav'])
    _parser, args = recap_cli.parse_args(
        ['video.mp4', '--audio-mode', 'source-mix', '--no-review-narration'],
    )
    assert args.audio_mode == 'source-mix'
    assert args.voice_ref is None
    assert args._explicit_options == frozenset({'--audio-mode', '--no-review-narration'})
