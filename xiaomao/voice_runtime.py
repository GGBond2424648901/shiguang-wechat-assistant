"""Prepare spoken or selected song audio outside the WeChat input transaction."""

from dataclasses import dataclass
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock

from .desktop_voice import _route_available, load_voice_route
from .fabric_model_path import installed_role_directory
from .privacy import private_dir
from .voice_audio import VoiceAudio, make_hummed_melody, make_voice_probe, prepare_wechat_speech, read_voice_wav
from .voice_fabric import (
    FABRIC_PROFILE_LIMIT, FabricError, _read_object, choose_reference, connect_fabric,
    resolve_installed_role, selected_installation, speech_arguments, speech_artifact,
)
from .voice_policy import VoicePreferences
from .windows import wechat_voice_status
from .windows_voice_audio import list_audio_endpoints


@dataclass(frozen=True)
class PreparedVoice:
    audio: VoiceAudio
    text: str
    output_id: str
    input_id: str


class VoiceRuntime:
    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root / 'runtime' / 'voice'
        self.profile = self.directory / 'fabric.json'
        self.song = self.directory / 'song.wav'
        self._rules_path = self.directory.parent / 'config.json'
        self._rule_lock = RLock()
        self._rules_stamp = None
        self._rules = None

    def preferences(self, current):
        """Read saved session rules even while the worker is inside a recording."""
        from .config import Settings

        with self._rule_lock:
            try:
                stat = self._rules_path.stat()
                stamp = stat.st_ino, stat.st_mtime_ns, stat.st_size
                if self._rules is None or self._rules_stamp != stamp:
                    self._rules = Settings.load(self.root)
                    self._rules_stamp = stamp
            except (OSError, TypeError, ValueError):
                return VoicePreferences()
            name = current.group
            saved = self._rules
            if (name not in saved.allowed_groups or saved.conversation_scope(name) != current.memory_scope
                    or saved.is_conversation_paused(name)):
                return VoicePreferences()
            return saved.voice_preferences(name)

    def _route(self):
        if os.name != 'nt':
            return None
        route = load_voice_route(self.root)
        if not route:
            return None
        if not _route_available(route, list_audio_endpoints(), list_audio_endpoints(capture=True)):
            return None
        return route

    def can_prepare(self, role_id=None, kind='voice'):
        available = kind == 'sing' and self.song.is_file()
        if not available:
            profile = _read_object(self.profile, FABRIC_PROFILE_LIMIT)
            install = selected_installation(profile)
            available = install is not None and any(
                installed_role_directory(install.root, candidate) for candidate in (role_id, profile.get('roleId'))
            )
        return bool(available and self._route() and wechat_voice_status()['state']=='check_recording')

    def prepare(self, kind, role_id, text, job_id, check_active):
        check_active()
        route = self._route()
        if not route:
            raise ValueError('请选择已检测通过的微信语音设备')
        singing = kind == 'sing'
        if singing and self.song.is_file():
            return PreparedVoice(read_voice_wav(self.song, max_seconds=52), '[歌声]', route['outputId'], route['inputId'])
        profile = _read_object(self.profile, FABRIC_PROFILE_LIMIT)
        install = selected_installation(profile)
        default_role_id = profile.get('roleId')
        if not install or not (role_id or default_role_id):
            raise FabricError('请先选择 Fabric 音色')
        backend = 'vulkan' if profile.get('backend') == 'vulkan' else 'cuda'
        private_dir(self.directory)
        with TemporaryDirectory(prefix='reply-', dir=self.directory, ignore_cleanup_errors=True) as directory:
            output = Path(directory)
            with connect_fabric(install, launch=True) as connection:
                connection.socket.settimeout(100)
                if not connection.call('app.status').get('runtimeReady'):
                    raise FabricError('请先安装 Fabric 推理环境')
                role = resolve_installed_role(connection, role_id, default_role_id)
                selected = role['id']
                references = profile.get('references')
                requested_reference = references.get(selected, '') if isinstance(references, dict) else ''
                reference = choose_reference(install.root, role, requested_reference)
                args = speech_arguments(role, '嗯——，啦啦，嗯——，啦啦。' if singing else text,
                                        reference, backend, output)
                role_directory = role.get('dir')
                if not isinstance(role_directory, str) or not Path(role_directory).is_dir():
                    raise FabricError('本地音色目录不可用')
                check_active()
                # The native synthesis page uses this command without adding
                # reply text to Fabric's persistent API job history.
                preset = role.get('params')
                result = connection.invoke('synth_start', roleId=selected, roleDir=role_directory,
                                           version=role.get('version'), text=args['text'],
                                           refWav=args['referenceAudio'], refText=args['referenceText'],
                                           refLang=args['referenceLanguage'], streaming=False,
                                           params=(preset if isinstance(preset, dict) else {}) | args['parameters'],
                                           opts={'owner': job_id, 'job_id': job_id, 'output_dir': str(output),
                                                 'backend': backend, 'format': 'wav'})
                artifact = result.get('out') if isinstance(result, dict) else None
                path, _ = speech_artifact(
                    {'artifacts': [{'role': 'speech', 'format': 'wav', 'path': artifact}]}, output,
                )
                clip = read_voice_wav(path, max_seconds=52)
        check_active()
        audio = make_hummed_melody(clip) if singing else prepare_wechat_speech(clip)
        return PreparedVoice(audio,
                             '[原创哼唱]' if singing else args['text'], route['outputId'], route['inputId'])

    def current_route(self, prepared):
        route = self._route()
        return route == {'outputId': prepared.output_id, 'inputId': prepared.input_id}

    def prepare_test(self):
        route = self._route()
        if not route or wechat_voice_status()['state'] != 'check_recording':
            raise ValueError('请先保存通过回路测试的设备，并打开新版微信')
        return PreparedVoice(make_voice_probe(), '[语音测试]', route['outputId'], route['inputId'])
