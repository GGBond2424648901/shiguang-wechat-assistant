import json
from pathlib import Path
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from xiaomao.voice_fabric import MissingFabricRole
from xiaomao.voice_runtime import VoiceRuntime


@pytest.fixture
def saved_runtime(tmp_path, monkeypatch):
    runtime = VoiceRuntime(tmp_path)
    runtime.directory.mkdir(parents=True)
    install = tmp_path / 'fabric'
    role = install / 'User_Data' / 'characters' / 'still-installed'
    role.mkdir(parents=True)
    (role / 'config.json').write_text('{}', encoding='utf-8')
    runtime.profile.write_text(json.dumps({
        'backend': 'vulkan', 'roleId': 'still-installed',
        'references': {'still-installed': '0:2', 'removed': '9:9'},
    }), encoding='utf-8')
    monkeypatch.setattr('xiaomao.voice_runtime.selected_installation', lambda profile: SimpleNamespace(root=install))
    monkeypatch.setattr(runtime, '_route', lambda: {'outputId': 'out', 'inputId': 'in'})
    monkeypatch.setattr('xiaomao.voice_runtime.wechat_voice_status', lambda: {'state': 'check_recording'})
    return runtime, role


def test_voice_availability_requires_an_installed_default_or_session_override(saved_runtime):
    runtime, role = saved_runtime

    assert runtime.can_prepare('removed')
    (role / 'config.json').unlink()
    assert not runtime.can_prepare('removed')


@pytest.mark.parametrize('kind', ('voice', 'sing'))
def test_runtime_prepares_pcm_with_the_fallback_roles_own_settings(saved_runtime, monkeypatch, kind):
    runtime, directory = saved_runtime
    references = [directory / f'reference-{i}.wav' for i in (1, 2)]
    for reference in references:
        reference.touch()
    role = {
        'id': 'still-installed', 'dir': str(directory), 'version': 'v5turbo', 'params': {'top_k': 12},
        'lists': [{'lang': 'zh', 'rows': [{'line': i, 'abs': str(path), 'text': f'参考音频{i}', 'exists': True}
                                         for i, path in enumerate(references, 1)]}],
    }
    rate = 48_000
    pcm = (1_300 * np.sin(np.arange(rate) * (2 * np.pi * 220 / rate))).astype('<i2')
    calls = []

    class Fabric:
        socket = SimpleNamespace(settimeout=lambda timeout: None)

        def __enter__(self):
            return self

        def __exit__(self, *error):
            pass

        def call(self, method, params=None):
            if method == 'app.status':
                return {'runtimeReady': True}
            assert method == 'res.get'
            if params['id'] == 'removed':
                raise MissingFabricRole('该音色已不存在')
            assert params['id'] == role['id']
            return {'id': role['id'], 'kind': 'gsv.character', 'metadata': role}

        def invoke(self, command, **args):
            calls.append((command, args))
            path = Path(args['opts']['output_dir']) / 'output.wav'
            with wave.open(str(path), 'wb') as wav:
                wav.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
                wav.writeframes(pcm.tobytes())
            return {'out': str(path)}

    monkeypatch.setattr('xiaomao.voice_runtime.connect_fabric', lambda install, launch: Fabric())
    result = runtime.prepare(kind, 'removed', '今天辛苦了。', 'job', lambda: None)

    (command, args), = calls
    assert command == 'synth_start'
    assert (args['roleId'], args['refWav'], args['params'], args['opts']['backend']) == (
        role['id'], str(references[1]), {'top_k': 12}, 'vulkan')
    assert (result.output_id, result.input_id, result.audio.sample_rate) == ('out', 'in', rate)
    if kind == 'voice':
        assert (result.audio.duration, result.text) == (1, '今天辛苦了。')
    else:
        assert 3 < result.audio.duration < 6 and result.text == '[原创哼唱]'
    assert abs(np.frombuffer(result.audio.pcm, dtype='<i2')).max() > 2_000
