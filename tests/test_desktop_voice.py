from contextlib import contextmanager
import json
from threading import Event
from time import monotonic, sleep

import pytest

from xiaomao import desktop_voice
from xiaomao.desktop_service import DesktopService
from xiaomao.windows import DesktopUnavailable, wechat_voice_status
from xiaomao.windows_voice_audio import AudioEndpoint


def wait_done(voice):
    until = monotonic() + 2
    job = voice.status()
    while job['state'] == 'running' and monotonic() < until:
        sleep(.01)
        job = voice.status()
    assert job['state'] != 'running'
    return job


def test_devices_only_suggest_an_unambiguous_vb_cable_pair(tmp_path, monkeypatch):
    output = AudioEndpoint('cable-out', 'CABLE Input (VB-Audio Virtual ')
    input_end = AudioEndpoint('cable-in', 'CABLE Output (VB-Audio Virtual ')
    other = AudioEndpoint('remote-foo', 'Remote Virtual Speaker')
    monkeypatch.setattr(desktop_voice, 'list_audio_endpoints',
                        lambda *, capture=False: (input_end,) if capture else (other, output))
    panel = DesktopService(tmp_path, read_only=True)
    devices = panel.call('voice.devices', {})
    assert devices['suggested'] == {'outputId': output.id, 'inputId': input_end.id}
    assert devices['output'] == [{'id': other.id, 'name': other.name}, {'id': output.id, 'name': output.name}]
    assert devices['input'] == [{'id': input_end.id, 'name': input_end.name}]
    assert devices['job'] is None
    with pytest.raises(ValueError, match='只读'):
        panel.call('voice.probe', {'outputId': output.id, 'inputId': input_end.id})
    with pytest.raises(ValueError, match='只读'):
        panel.call('voice.installCable', {})


def test_route_is_verified_in_background_with_selected_endpoints(tmp_path, monkeypatch):
    called = Event()
    resume = Event()

    def probe(output, capture, *, check_active):
        assert (output, capture) == ('chosen-output', 'chosen-input')
        called.set()
        assert resume.wait(1)
        check_active()
        return True

    monkeypatch.setattr(desktop_voice, 'probe_voice_route', probe)
    voice = desktop_voice.DesktopVoiceSetup(tmp_path)
    started = voice.probe('chosen-output', 'chosen-input')
    assert started['state'] == 'running' and started['cancelable']
    try:
        assert called.wait(1)
        with pytest.raises(ValueError, match='正在运行'):
            voice.probe('changed-output', 'chosen-input')
    finally:
        resume.set()
    finished = wait_done(voice)
    assert finished['id'] == started['id']
    assert finished['state'] == 'done'
    assert finished['route'] == {'outputId': 'chosen-output', 'inputId': 'chosen-input'}


def test_cancel_is_scoped_to_the_running_job(tmp_path, monkeypatch):
    resume = Event()

    def probe(*args, check_active):
        assert resume.wait(1)
        check_active()
        pytest.fail('cancelled job continued')

    monkeypatch.setattr(desktop_voice, 'probe_voice_route', probe)
    voice = desktop_voice.DesktopVoiceSetup(tmp_path)
    task = voice.probe('out', 'in')
    with pytest.raises(ValueError, match='任务已变化'):
        voice.cancel('stale')
    voice.cancel(task['id'])
    resume.set()
    assert wait_done(voice)['state'] == 'cancelled'


def test_cable_task_uses_vendor_installer_and_distinguishes_driver_detection(tmp_path, monkeypatch):
    started = Event()
    proceed = Event()
    staged = tmp_path / 'VBCABLE_Setup_x64.exe'

    @contextmanager
    def prepare(cache, progress, check_active):
        assert cache.is_relative_to(tmp_path)
        check_active()
        progress(5, 5)
        yield staged

    def install(path):
        assert path == staged
        started.set()
        assert proceed.wait(1)
        return 0

    monkeypatch.setattr(desktop_voice, 'prepare_installer', prepare)
    monkeypatch.setattr(desktop_voice, 'run_vendor_setup', install)
    monkeypatch.setattr(desktop_voice, 'list_audio_endpoints', lambda *, capture=False: ())
    voice = desktop_voice.DesktopVoiceSetup(tmp_path)
    task = voice.install_cable()
    try:
        assert started.wait(1)
        running = voice.status()
        assert running['downloaded'] == running['total'] == 5
        assert not running['cancelable']
        with pytest.raises(ValueError, match='安装程序'):
            voice.cancel(task['id'])
    finally:
        proceed.set()
    done = wait_done(voice)
    assert done['state'] == 'done'
    assert '待检测' in done['message'] and done['route'] is None


def test_reports_version_of_the_running_wechat_chat_window(monkeypatch):
    from xiaomao import windows

    monkeypatch.setattr(windows, 'list_calibration_windows',
                        lambda: [{'kind': 'main', 'pid': 11}, {'kind': 'detached', 'pid': 11}])
    checked = []

    def read_version(pid):
        checked.append(pid)
        return (4, 1, 7, 59)

    monkeypatch.setattr(windows, '_wechat_client_version', read_version)
    assert wechat_voice_status() == {'state': 'update', 'version': '4.1.7.59'}
    assert checked == [11]
    monkeypatch.setattr(windows, '_wechat_client_version', lambda pid: (4, 1, 19, 8))
    assert wechat_voice_status() == {'state': 'check_recording', 'version': '4.1.19.8'}


def test_no_eligible_or_ambiguous_wechat_does_not_claim_voice_available(monkeypatch):
    from xiaomao import windows

    monkeypatch.setattr(windows, 'list_calibration_windows', lambda: [])
    assert wechat_voice_status() == {'state': 'open_chat', 'version': ''}
    monkeypatch.setattr(windows, 'list_calibration_windows',
                        lambda: [{'kind': 'main', 'pid': 11}, {'kind': 'main', 'pid': 22}])
    assert wechat_voice_status() == {'state': 'unverified', 'version': ''}

    def locked():
        raise DesktopUnavailable('桌面已锁定')

    monkeypatch.setattr(windows, 'list_calibration_windows', locked)
    assert wechat_voice_status() == {'state': 'unverified', 'version': ''}


def test_voice_route_save_requires_matching_successful_probe_and_current_endpoints(tmp_path, monkeypatch):
    endpoints = [AudioEndpoint('out', 'loopback output')]
    monkeypatch.setattr(desktop_voice, 'list_audio_endpoints',
                        lambda *, capture=False: (AudioEndpoint('in', 'loopback input'),) if capture else tuple(endpoints))
    monkeypatch.setattr(desktop_voice, 'wechat_voice_status',
                        lambda: {'state': 'update', 'version': '4.1.7.59'})
    monkeypatch.setattr(desktop_voice, 'probe_voice_route', lambda *args, **kwargs: True)
    panel = DesktopService(tmp_path)
    probe = panel.call('voice.probe', {'outputId': 'out', 'inputId': 'in'})
    wait_done(panel.voice)
    with pytest.raises(ValueError, match='测试'):
        panel.call('voice.saveRoute', {'probeId': 'other', 'outputId': 'out', 'inputId': 'in'})
    with pytest.raises(ValueError, match='测试'):
        panel.call('voice.saveRoute', {'probeId': probe['id'], 'outputId': 'other', 'inputId': 'in'})
    endpoints.clear()
    with pytest.raises(ValueError, match='设备'):
        panel.call('voice.saveRoute', {'probeId': probe['id'], 'outputId': 'out', 'inputId': 'in'})
    endpoints.append(AudioEndpoint('out', 'loopback output'))
    saved = panel.call('voice.saveRoute', {'probeId': probe['id'], 'outputId': 'out', 'inputId': 'in'})
    assert saved['route'] == {'outputId': 'out', 'inputId': 'in'}
    recovered = DesktopService(tmp_path).call('voice.devices', {})
    assert recovered['configured'] == saved['route']
    assert recovered['configuredAvailable']
    assert recovered['wechat'] == {'state': 'update', 'version': '4.1.7.59'}
    endpoints.clear()
    after_disconnect = DesktopService(tmp_path).call('voice.devices', {})
    assert after_disconnect['configured'] == saved['route']
    assert not after_disconnect['configuredAvailable']


def test_unreadable_route_config_has_no_effect_on_device_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_voice, 'list_audio_endpoints', lambda *, capture=False: ())
    monkeypatch.setattr(desktop_voice, 'wechat_voice_status', lambda: {'state': 'open_chat', 'version': ''})
    route_file = tmp_path / 'runtime' / 'voice' / 'route.json'
    route_file.parent.mkdir(parents=True)
    route_file.write_text(json.dumps({'outputId': [], 'inputId': 'in'}), encoding='utf-8')
    assert DesktopService(tmp_path).call('voice.devices', {})['configured'] is None
