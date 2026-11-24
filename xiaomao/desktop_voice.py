"""Local audio setup and diagnostics for the Windows desktop panel."""

from dataclasses import asdict
from io import BytesIO
import json
import logging
import os
from pathlib import Path
from threading import Event, RLock, Thread
from uuid import uuid4
import wave

from .privacy import atomic_private_write, private_dir
from .voice_audio import read_voice_wav
from .voice_driver import PACKAGE_SIZE, prepare_installer, run_vendor_setup
from .windows import wechat_voice_status
from .windows_voice_audio import VoiceOutputError, list_audio_endpoints, probe_voice_route


class _Cancelled(RuntimeError):
    pass


def _vb_cable_pair(outputs, inputs):
    render = [device.id for device in outputs if 'cable input (vb-audio' in device.name.casefold()]
    capture = [device.id for device in inputs if 'cable output (vb-audio' in device.name.casefold()]
    return {'outputId': render[0], 'inputId': capture[0]} if len(render) == len(capture) == 1 else None


def _valid_route(route):
    return (isinstance(route, dict) and route.keys() == {'outputId', 'inputId'} and
            all(isinstance(value, str) and 0 < len(value) <= 256 and value.isprintable()
                for value in route.values()))


def _route_available(route, outputs, inputs):
    return (route['outputId'] in {device.id for device in outputs} and
            route['inputId'] in {device.id for device in inputs})


def load_voice_route(root):
    path = Path(root) / 'runtime' / 'voice' / 'route.json'
    try:
        with path.open('rb') as source:
            data = source.read(4097)
        if len(data) <= 4096:
            route = json.loads(data)
            if _valid_route(route):
                return route
    except (OSError, ValueError, RecursionError):
        pass
    return None


def read_voice_test_target(root):
    path = Path(root) / 'runtime' / 'voice' / 'test-target.json'
    try:
        with path.open('rb') as source:
            raw = source.read(4097)
        if len(raw) <= 4096:
            data = json.loads(raw)
            name = data.get('displayName') if isinstance(data, dict) else None
            if isinstance(name, str) and 0 < len(name.strip()) <= 120 and name.isprintable():
                return name.strip()
    except (OSError, ValueError, RecursionError):
        pass
    return ''


class DesktopVoiceSetup:
    def __init__(self, root):
        self.root = Path(root)
        self._cache = Path(root) / 'runtime' / 'voice' / 'driver-cache'
        self._route_file = self._cache.parent / 'route.json'
        self._lock = RLock()
        self._job = None
        self._cancel = Event()

    def devices(self):
        supported = os.name == 'nt'
        outputs = list_audio_endpoints() if supported else ()
        inputs = list_audio_endpoints(capture=True) if supported else ()
        configured = load_voice_route(self.root)
        return {
            'supported': supported, 'suggested': _vb_cable_pair(outputs, inputs),
            'configured': configured,
            'configuredAvailable': configured is not None and _route_available(configured, outputs, inputs),
            'wechat': wechat_voice_status() if supported else {'state': 'unverified', 'version': ''},
            'output': [asdict(device) for device in outputs], 'input': [asdict(device) for device in inputs],
            'job': self.status(),
            'song': (self._cache.parent / 'song.wav').is_file(),
            'testTarget': self.test_target(),
        }

    def test_target(self):
        return read_voice_test_target(self.root)

    def set_song(self, path):
        if not isinstance(path, str) or Path(path).suffix.casefold() != '.wav' or not Path(path).is_absolute():
            raise ValueError('请选择本机 WAV 歌声片段')
        clip = read_voice_wav(path, max_seconds=52)
        buffer = BytesIO()
        with wave.open(buffer, 'wb') as wav:
            wav.setnchannels(clip.channels)
            wav.setsampwidth(2)
            wav.setframerate(clip.sample_rate)
            wav.writeframes(clip.pcm)
        private_dir(self._cache.parent)
        atomic_private_write(self._cache.parent / 'song.wav', buffer.getvalue())
        return {'song': True}

    def _read_route(self):
        return load_voice_route(self.root)

    def status(self):
        with self._lock:
            return dict(self._job) if self._job else None

    def _start(self, kind, message, task, *, total=0):
        if os.name != 'nt':
            raise ValueError('微信语音设备仅支持 Windows')
        with self._lock:
            if self._job and self._job['state'] == 'running':
                raise ValueError('有一个语音设备任务正在运行')
            self._cancel = Event()
            self._job = {
                'id': uuid4().hex, 'kind': kind, 'state': 'running', 'message': message,
                'cancelable': True, 'downloaded': 0, 'total': total, 'route': None,
            }
            Thread(target=self._run, args=(task,), name='voice-device-setup', daemon=True).start()
            return self.status()

    def _run(self, task):
        state, route = 'done', None
        try:
            message, route = task()
        except _Cancelled:
            state, message = 'cancelled', '语音设备任务已取消'
        except (ValueError, VoiceOutputError) as error:
            state, message = 'failed', str(error)
        except Exception:
            logging.getLogger(__name__).exception('Voice device setup failed')
            state, message = 'failed', '语音设备操作未完成，请刷新设备后重试'
        with self._lock:
            if self._job['cancelable'] and self._cancel.is_set():
                state, message, route = 'cancelled', '语音设备任务已取消', None
            self._job.update(state=state, message=message, route=route, cancelable=False)

    def _check_active(self):
        if self._cancel.is_set():
            raise _Cancelled

    def cancel(self, id):
        with self._lock:
            if not self._job or self._job['id'] != id:
                raise ValueError('语音设备任务已变化，请刷新状态')
            if self._job['state'] == 'running':
                if not self._job['cancelable']:
                    raise ValueError('请在 VB-CABLE 官方安装程序中取消')
                self._cancel.set()
            return self.status()

    def close(self):
        with self._lock:
            if self._job and self._job['cancelable']:
                self._cancel.set()

    def probe(self, outputId, inputId):
        route = {'outputId': outputId, 'inputId': inputId}
        if not _valid_route(route):
            raise ValueError('请选择有效的语音输入和输出设备')

        def run():
            if not probe_voice_route(outputId, inputId, check_active=self._check_active):
                raise ValueError('录音端未收到完整测试音，请核对是否选择了同一组虚拟音频设备')
            return '已在输入设备收到完整的测试音', route

        return self._start('probe', '正在检测音频回路', run)

    def save_route(self, probeId, outputId, inputId):
        route = {'outputId': outputId, 'inputId': inputId}
        with self._lock:
            job = self._job
            if (not _valid_route(route) or not job or job['id'] != probeId or
                    job['kind'] != 'probe' or job['state'] != 'done' or job['route'] != route):
                raise ValueError('请先测试当前选择的音频回路')
            if not _route_available(route, list_audio_endpoints(), list_audio_endpoints(capture=True)):
                raise ValueError('部分语音设备已断开，请刷新设备')
            private_dir(self._route_file.parent)
            atomic_private_write(self._route_file, json.dumps(route, ensure_ascii=False).encode('utf-8'))
            return {'route': route}

    def install_cable(self):
        def on_download(current, total):
            with self._lock:
                self._job.update(downloaded=current, total=total)

        def run():
            if _vb_cable_pair(list_audio_endpoints(), list_audio_endpoints(capture=True)):
                return '已检测到 VB-CABLE，可直接测试音频回路', None
            with prepare_installer(self._cache, progress=on_download, check_active=self._check_active) as installer:
                with self._lock:
                    self._check_active()
                    self._job.update(cancelable=False, message='请在 VB-CABLE 官方窗口中完成安装')
                exit_code = run_vendor_setup(installer)
            if exit_code not in (0, 3010):
                raise ValueError(f'VB-CABLE 安装程序提前退出（{exit_code}）')
            if _vb_cable_pair(list_audio_endpoints(), list_audio_endpoints(capture=True)):
                return '已检测到 VB-CABLE，请测试音频回路', None
            return '安装程序已退出，音频设备待检测。按安装提示操作后刷新设备。', None

        return self._start('install', '正在获取 VB-CABLE 官方安装包', run, total=PACKAGE_SIZE)
