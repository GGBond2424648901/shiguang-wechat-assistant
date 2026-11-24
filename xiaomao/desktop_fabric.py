"""Fabric's community download, native resource setup and installed audio models."""

import base64
import json
import logging
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event, RLock, Thread
from time import monotonic, sleep
from uuid import uuid4

import httpx

from .fabric_model_path import choose_model_path, default_model_path, installed_role_directory
from .fabric_runtime_download import cache_official_runtime
from .fabric_setup import SETUP_SIZE, prepare_fabric_setup, run_fabric_setup
from .privacy import atomic_private_write, private_dir
from .voice_fabric import (
    FABRIC_PREVIEW_LIMIT, FABRIC_PROFILE_LIMIT, FABRIC_VERSION, FabricError, FabricUnavailable, _read_object,
    catalog_entries, choose_reference, connect_fabric, find_installations, installation_running, installed_role,
    publisher_pack_url, read_reference_audio, registry_path, role_display_name, role_references, selected_installation,
    speech_arguments, speech_artifact, start_synthesis,
)


class DesktopFabric:
    def __init__(self, root):
        self._profile = Path(root) / 'runtime' / 'voice' / 'fabric.json'
        self._lock = RLock()
        self._entries = {}
        self._catalog_install = None
        self._catalog_revision = 0
        self._job = None
        self._job_install = None
        self._temp = None
        self._preview_path = None
        self._reference_media = {}
        self._installer_cancel = Event()
        self._runtime_cancel = Event()
        app_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(root) / 'tools'
        self._default_install = app_dir / 'GPT-SoVITS Fabric'

    def _profile_data(self):
        return _read_object(self._profile, FABRIC_PROFILE_LIMIT)

    def _installation(self):
        return selected_installation(self._profile_data())

    def _require_installation(self):
        install = self._installation()
        if install is None:
            raise FabricError(f'请选择 GPT-SoVITS Fabric {FABRIC_VERSION} 的安装目录')
        return install

    def _save(self, **updates):
        profile = self._profile_data() | updates
        private_dir(self._profile.parent)
        atomic_private_write(self._profile, json.dumps(profile, ensure_ascii=False).encode('utf-8'))

    def _switch_library(self, previous, current, **updates):
        profile = self._profile_data()
        libraries = profile.get('libraries')
        libraries = dict(libraries) if isinstance(libraries, dict) else {}
        preferences = {
            'roleId': profile.get('roleId', ''),
            'references': profile.get('references', {}),
            'backend': profile.get('backend', 'cuda'),
        }
        if previous:
            libraries[os.path.normcase(str(previous))] = preferences
        saved = libraries.get(os.path.normcase(str(current)), {})
        if not isinstance(saved, dict):
            saved = {}
        self._save(libraries=libraries, roleId=saved.get('roleId', ''), references=saved.get('references', {}),
                   backend=saved.get('backend', 'cuda'), **updates)

    def setup(self):
        installed = find_installations(registry_path())
        profile = self._profile_data()
        install = selected_installation(profile, installed)
        default_path = default_model_path(install.root) if install else None
        model_path = str(default_path.resolve()) if default_path else ''
        return {'supported': os.name == 'nt', 'version': FABRIC_VERSION,
                'installations': [str(item.root) for item in installed],
                'installation': str(install.root) if install else '', 'modelPath': model_path,
                'defaultModelPath': str(default_path or ''), 'defaultInstallPath': str(self._default_install),
                'roleId': profile.get('roleId', ''), 'backend': 'vulkan' if profile.get('backend')=='vulkan' else 'cuda'}

    def select_installation(self, path):
        selected = next((item for item in find_installations(registry_path()) if str(item.root) == path), None)
        if not isinstance(path, str) or selected is None:
            raise FabricError('请选择已登记的 Fabric 安装目录')
        with self._lock:
            self._ensure_idle()
            previous = self._installation()
            old_library = default_model_path(previous.root).resolve() if previous else None
            self._switch_library(old_library, default_model_path(selected.root).resolve(), installation=path)
            self._invalidate_catalog()
            self._reference_media.clear()
        return self.setup()

    def _connection(self, install=None, *, launch=False, timeout=20):
        connection = connect_fabric(install or self._require_installation(), launch=launch)
        connection.socket.settimeout(timeout)
        return connection

    def engine(self):
        setup = self.setup()
        if not setup['installation']:
            return setup
        try:
            with self._connection(timeout=45) as connection:
                engine = connection.invoke('engine_status')
                runtime = connection.invoke('provision_status')
                assets = connection.invoke('assets_status')
                extras = connection.invoke('extra_list')
                return {**setup, 'engine': engine, 'runtime': runtime, 'assets': assets,
                        'extraAssets': [row for row in extras.get('items', []) if row.get('group') == 'synth'],
                        'extraBusy': bool(extras.get('busy'))}
        except FabricUnavailable:
            return setup

    def start(self):
        with self._connection(launch=True):
            return self.setup()

    def stop(self):
        with self._lock:
            self._ensure_idle()
            install = self._require_installation()
            try:
                connection = self._connection(install)
            except FabricUnavailable:
                if installation_running(install):
                    raise FabricError('Fabric 接口暂无响应，请在原版软件中退出后重试') from None
                return {'stopped': True}
            with connection:
                if connection.call('app.status').get('busy') or connection.invoke('provision_status').get('busy'):
                    raise FabricError('Fabric 正在处理任务，请等当前任务结束')
                try:
                    connection.call('app.quit')
                except FabricUnavailable:
                    pass
            deadline = monotonic() + 4
            while monotonic() < deadline:
                if not installation_running(install):
                    return {'stopped': True}
                sleep(0.2)
            raise FabricError('Fabric 尚未退出，请在原版软件中退出后重试')

    def model_directory(self, path):
        if os.name != 'nt':
            raise FabricError('自定义 Fabric 模型目录仅支持 Windows')
        with self._lock:
            self._ensure_idle()
            install = self._require_installation()
            try:
                with self._connection(install):
                    raise FabricError('请先退出 Fabric，再切换模型目录')
            except FabricUnavailable:
                if installation_running(install):
                    raise FabricError('请先退出 Fabric，再切换模型目录') from None
            native = default_model_path(install.root)
            previous = native.resolve()
            selected = choose_model_path(install.root, path)
            try:
                self._switch_library(previous, selected)
            except OSError:
                choose_model_path(install.root, '' if previous == native else str(previous))
                raise FabricError('未能保存音色设置，已恢复原模型目录') from None
            self._invalidate_catalog()
            self._reference_media.clear()
            return self.setup()

    def install_engine(self):
        if os.name != 'nt':
            raise FabricError('Fabric 安装仅支持 Windows')
        with self._lock:
            self._ensure_idle()
            if self._installation():
                raise FabricError('已连接对应版本的 Fabric 安装')
            self._installer_cancel.clear()
            self._begin(None, 'setup', f'GPT-SoVITS Fabric {FABRIC_VERSION}')
            self._job.update(phase='download', total=SETUP_SIZE, cancelable=True)
            Thread(target=self._work_setup, name='fabric-install', daemon=True).start()
            return self.status()

    def _work_setup(self):
        def progress(done, total):
            with self._lock:
                self._job.update(done=done, total=total)

        def check_active():
            if self._installer_cancel.is_set():
                raise FabricError('已取消下载安装包')

        state, message = 'done', 'Fabric 已安装'
        try:
            installer = prepare_fabric_setup(self._profile.parent / 'fabric-setup', progress=progress,
                                             check_active=check_active)
            with self._lock:
                check_active()
                self._job.update(cancelable=False, phase='installer',
                                 message='正在后台安装语音引擎')
            code = run_fabric_setup(installer, self._default_install)
            if code != 0:
                raise FabricError(f'Fabric 官方安装程序已退出（{code}）')
            installation = self._default_install.resolve()
            if not any(item.root == installation for item in find_installations(registry_path())):
                raise FabricError('语音引擎安装后未登记本机接口，请重新安装')
        except FabricError as error:
            state = 'cancelled' if self._installer_cancel.is_set() else 'failed'
            message = str(error)
        except Exception:
            logging.getLogger(__name__).exception('Fabric setup failed')
            state, message = 'failed', 'Fabric 安装未完成，请刷新后重试'
        self._finish(state, message)

    def catalog(self, refresh=False):
        if type(refresh) is not bool:
            raise FabricError('目录刷新参数无效')
        with self._lock:
            install = self._require_installation()
            revision = self._catalog_revision
        with self._connection(install, timeout=45) as connection:
            data = connection.invoke('store_catalog', preferRemote=refresh)
            if not refresh and isinstance(data, dict) and data.get('source') == 'empty':
                data = connection.invoke('store_catalog', preferRemote=True)
            staged = connection.invoke('store_staged')
        if not isinstance(data, dict) or not isinstance(staged, dict):
            raise FabricError('Fabric 社区目录数据无效')
        entries = catalog_entries(data, staged)
        with self._lock:
            if self._installation() != install or self._catalog_revision != revision:
                raise FabricError('Fabric 音色库已变化，请重新加载社区音色')
            self._entries, self._catalog_install = entries, install
        return {'items': [entry for entry, _ in entries.values()], 'source': data.get('source', ''),
                'warning': (data.get('fetch_error') or '')[:300]}

    def _invalidate_catalog(self):
        self._entries, self._catalog_install = {}, None
        self._catalog_revision += 1

    def roles(self):
        with self._connection() as connection:
            return self._installed_roles(connection)

    def _installed_roles(self, connection):
        result = connection.call('res.list')
        if not isinstance(result, dict) or not isinstance(result.get('roles'), list):
            raise FabricError('Fabric 音色库数据无效')
        roles = [{key: role.get(key, '') for key in ('id', 'version', 'author')} |
                 {'name': role_display_name(role),
                  'languages': sorted({language for _, _, language in role_references(role).values()})}
                 for role in result['roles'] if isinstance(role, dict) and isinstance(role.get('id'), str)]
        selected = self._profile_data().get('roleId', '')
        directory = result.get('dir')
        return {'roles': roles, 'roleId': selected if any(role['id'] == selected for role in roles) else '',
                'modelPath': str(Path(directory).resolve()) if isinstance(directory, str) and directory else ''}

    def _role(self, install, role_id):
        with self._connection(install) as connection:
            return installed_role(connection, role_id)

    @staticmethod
    def _role_directory(install, role):
        directory = installed_role_directory(install.root, role['id'])
        reported = role.get('dir')
        if directory and isinstance(reported, str):
            path = Path(reported)
            try:
                if path.is_absolute() and path.resolve(strict=True) == directory:
                    return directory
            except (OSError, RuntimeError, ValueError):
                pass
        raise FabricError('本地音色目录已变化，请刷新后重试')

    def references(self, roleId):
        install = self._require_installation()
        role = self._role(install, roleId)
        chosen = self._profile_data().get('references')
        selected = chosen.get(roleId) if isinstance(chosen, dict) else None
        try:
            recommended = selected or choose_reference(install.root, role)
        except FabricError:
            recommended = ''
        return [{'id': key, 'text': text[:160], 'language': language, 'preferred': key == selected}
                | {'recommended': key == recommended} for key, (_, text, language) in role_references(role).items()]

    def play_reference(self, roleId, reference):
        with self._lock:
            install = self._require_installation()
            role = self._role(install, roleId)
            directory = self._role_directory(install, role)
            data, mime = read_reference_audio(role, directory, reference)
            clip = uuid4().hex
            if len(self._reference_media) >= 4:
                del self._reference_media[next(iter(self._reference_media))]
            self._reference_media[clip] = (roleId, data, mime)
            return {'id': clip}

    def save_reference(self, roleId, reference):
        install = self._require_installation()
        role = self._role(install, roleId)
        if not isinstance(reference, str) or reference not in role_references(role):
            raise FabricError('参考音频已变化，请刷新本地音色')
        choose_reference(install.root, role, reference)
        with self._lock:
            if self._installation() != install:
                raise FabricError('Fabric 安装目录已变化，请刷新本地音色')
            chosen = self._profile_data().get('references')
            self._save(references=(chosen if isinstance(chosen, dict) else {}) | {roleId: reference})
        return {'reference': reference}

    def save_backend(self, backend):
        if backend not in ('cuda', 'vulkan'):
            raise FabricError('请选择可用的合成引擎')
        if backend == 'vulkan':
            with self._connection() as connection:
                if not connection.invoke('assets_status').get('gsv_cpp_ready'):
                    raise FabricError('请先安装 Vulkan 推理资源')
        with self._lock:
            self._save(backend=backend)
        return self.setup()

    def select_role(self, roleId):
        install = self._require_installation()
        if (not isinstance(roleId, str) or not roleId or
                not any(isinstance(role, dict) and role.get('id') == roleId for role in self.roles()['roles'])):
            raise FabricError('所选音色已不存在，请刷新本地音色')
        with self._lock:
            if self._installation() != install:
                raise FabricError('Fabric 安装目录已变化，请重新选择音色')
            self._save(roleId=roleId)
        return {'roleId': roleId}

    def uninstall(self, roleId):
        with self._lock:
            self._ensure_idle()
            install = self._require_installation()
            with self._connection(install) as connection:
                role = installed_role(connection, roleId)
                directory = self._role_directory(install, role)
                status = connection.call('app.status')
                if not isinstance(status, dict) or type(status.get('busy')) is not bool:
                    raise FabricError('无法确认 Fabric 任务状态，请刷新后重试')
                if status['busy']:
                    raise FabricError('Fabric 正在处理合成任务，请等当前任务结束')
                connection.invoke('role_delete', id=roleId)
                if directory.exists():
                    raise FabricError('Fabric 尚未移除该音色，请刷新后重试')
                self._forget_role(roleId)
                return self._installed_roles(connection)

    def _forget_role(self, role_id):
        self._invalidate_catalog()
        self._reference_media = {key: clip for key, clip in self._reference_media.items() if clip[0] != role_id}
        if self._job and self._job.get('roleId') == role_id:
            self._job, self._preview_path = None, None
            if self._temp:
                self._temp.cleanup()
                self._temp = None
        profile = self._profile_data()
        updates = {}
        if profile.get('roleId') == role_id:
            updates['roleId'] = ''
        references = profile.get('references')
        if isinstance(references, dict) and role_id in references:
            updates['references'] = {key: value for key, value in references.items() if key != role_id}
        if updates:
            try:
                self._save(**updates)
            except OSError:
                raise FabricError('音色已卸载，未能保存音色设置，请刷新本地音色') from None

    def status(self):
        with self._lock:
            return dict(self._job) if self._job else None

    def _ensure_idle(self):
        if self._job and self._job['state'] == 'running':
            raise FabricError('已有一个 Fabric 下载或安装任务正在运行')

    def _entry(self, id, release):
        if not isinstance(id, str) or not isinstance(release, str):
            raise FabricError('社区音色参数无效')
        install = self._require_installation()

        def lookup():
            with self._lock:
                return self._entries.get((id, release)) if self._catalog_install == install else None

        entry = lookup()
        if not entry:
            self.catalog()
            entry = lookup()
        if not entry:
            raise FabricError('社区音色版本已变化，请刷新目录后重试')
        return install, entry[0], entry[1]

    def download(self, id, release):
        install, public, entry = self._entry(id, release)
        if public['staged'] or public['installed'] and not public['updateAvailable']:
            raise FabricError('该音色版本已经下载')
        return self._start(install, 'download', public['name'], 'store_install', 'store-progress',
                           {'entry': entry}, cancel=('store_cancel', {'voiceId': id}), voice_id=id)

    def install(self, id, release):
        install, public, entry = self._entry(id, release)
        if not public['staged']:
            raise FabricError('请先下载此版本的社区音色')
        return self._start(install, 'install', public['name'], 'store_install_staged', 'store-progress',
                           {'entry': entry}, voice_id=id)

    def reveal(self, id, release):
        install, public, entry = self._entry(id, release)
        if not public['staged']:
            raise FabricError('请先下载此版本的社区音色')
        with self._connection(install) as connection:
            connection.invoke('store_reveal_staged', voiceId=entry.get('community_staging_id') or id)
        return {'opened': True}

    def provision(self, variant):
        if variant not in ('cpu', 'nvidia'):
            raise FabricError('请选择 CPU 或 NVIDIA 推理环境')
        return self._start(self._require_installation(), 'runtime', 'NVIDIA CUDA' if variant == 'nvidia' else 'CPU',
                           'provision_start', 'provision-progress', {'variant': variant, 'force': False},
                           cancel=('provision_cancel', {}))

    def assets(self, kind):
        if kind == 'base':
            return self._start(self._require_installation(), 'assets', '基础模型', 'assets_ensure_engine_core',
                               'provision-progress', {})
        if kind == 'vulkan':
            return self._start(self._require_installation(), 'assets', 'Vulkan 推理资源', 'assets_ensure_gsv_cpp',
                               'provision-progress', {}, cancel=('assets_cancel_gsv_cpp', {}))
        raise FabricError('资源类型无效')

    def extra_assets(self, key):
        if key not in ('gsv-v3', 'gsv-v4', 'gsv-v5'):
            raise FabricError('请选择 Fabric 合成底模')
        names = {'gsv-v3': 'V3 合成底模', 'gsv-v4': 'V4 合成底模', 'gsv-v5': 'V5 / V5 Turbo 合成底模'}
        return self._start(self._require_installation(), 'assets', names[key], 'extra_download', 'extra-progress',
                           {'key': key}, cancel=('extra_cancel', {}))

    def preview(self, roleId, text, reference='', backend='cuda'):
        if not isinstance(reference, str):
            raise FabricError('请选择有效的参考音频')
        install = self._require_installation()
        role = self._role(install, roleId)
        reference = choose_reference(install.root, role, reference)
        with self._lock:
            self._ensure_idle()
            if self._installation() != install:
                raise FabricError('Fabric 安装目录已变化，请刷新本地音色')
            private_dir(self._profile.parent)
            temp = TemporaryDirectory(prefix='fabric-preview-', dir=self._profile.parent, ignore_cleanup_errors=True)
            try:
                params = speech_arguments(role, text, reference, backend, Path(temp.name))
            except Exception:
                temp.cleanup()
                raise
            if self._temp:
                self._temp.cleanup()
            self._temp, self._preview_path = temp, None
            job_id = self._begin(install, 'preview', role_display_name(role))
            self._job['roleId'] = roleId
            Thread(target=self._work_preview, args=(install, job_id, params, Path(temp.name)),
                   name='fabric-preview', daemon=True).start()
            return self.status()

    def _work_preview(self, install, job_id, params, output):
        state, message = 'failed', 'Fabric 合成未完成'
        try:
            with self._connection(install) as connection:
                native_id = start_synthesis(connection, job_id, params)
                with self._lock:
                    self._cancel_command = ('job.cancel', {'jobId': native_id})
                    self._job.update(phase='synthesis', message='正在合成试听', cancelable=True)
                deadline = monotonic() + 600
                while monotonic() < deadline:
                    result = connection.call('job.get', {'jobId': native_id})
                    if not isinstance(result, dict):
                        raise FabricError('Fabric 合成任务状态无效')
                    status = result.get('status')
                    if status == 'done':
                        clip, duration = speech_artifact(result.get('result'), output)
                        with self._lock:
                            self._preview_path = clip
                            self._job.update(duration=duration, audio=f'sg-media://voice/{job_id}')
                        state, message = 'done', '试听已生成'
                        break
                    if status == 'canceled':
                        state, message = 'cancelled', '试听已取消'
                        break
                    if status == 'failed':
                        failure = result.get('error')
                        detail = failure.get('message') if isinstance(failure, dict) else ''
                        raise FabricError(detail[:400] if isinstance(detail, str) and detail else 'Fabric 合成失败')
                    if status not in ('queued', 'running'):
                        raise FabricError('Fabric 合成任务状态无效')
                    sleep(0.4)
                else:
                    state, message = 'unknown', 'Fabric 合成仍在处理中，可在原版界面查看任务'
        except FabricUnavailable as error:
            state, message = 'unknown', str(error)
        except ValueError as error:
            message = str(error)
        except Exception:
            logging.getLogger(__name__).exception('Fabric preview failed')
        self._finish(state, message)

    def preview_media(self, id):
        if not isinstance(id, str):
            raise FabricError('试听已过期，请重新播放')
        with self._lock:
            if id in self._reference_media:
                _, data, mime = self._reference_media[id]
            else:
                if (not self._preview_path or not self._job or self._job['id'] != id or
                        self._job['state'] != 'done'):
                    raise FabricError('试听已过期，请重新播放')
                with self._preview_path.open('rb') as source:
                    data = source.read(FABRIC_PREVIEW_LIMIT + 1)
                if len(data) > FABRIC_PREVIEW_LIMIT:
                    raise FabricError('试听文件过大，请缩短合成文本')
                mime = 'audio/wav'
        return {'data': base64.b64encode(data).decode('ascii'), 'mime': mime}

    def _begin(self, install, kind, name, cancel=None):
        job_id = uuid4().hex
        self._job = {'id': job_id, 'kind': kind, 'state': 'running', 'name': name,
                     'phase': 'starting', 'done': 0, 'total': 0, 'message': '', 'cancelable': False}
        self._job_install = install
        self._cancel_command = cancel
        self._cancel_requested = False
        self._runtime_cancel.clear()
        return job_id

    def _finish(self, state, message):
        with self._lock:
            self._job.update(state=state, message=message, cancelable=False)

    def _start(self, install, kind, name, command, event, params, *, cancel=None, voice_id=None):
        with self._lock:
            self._ensure_idle()
            if self._installation() != install:
                raise FabricError('Fabric 安装目录已变化，请刷新后重试')
            self._begin(install, kind, name, cancel)
            Thread(target=self._work, args=(install, command, event, params, voice_id, cancel),
                   name='fabric-resource', daemon=True).start()
            return self.status()

    def _work(self, install, command, event, params, voice_id, cancel):
        def progress(data):
            item = data.get('payload')
            if data.get('event') != event or not isinstance(item, dict):
                return
            if voice_id and item.get('voice_id') != voice_id:
                return
            if event == 'extra-progress' and item.get('key') != params['key']:
                return
            phase = item.get('phase')
            phase_name = phase.partition(':')[0] if isinstance(phase, str) else ''
            if command == 'provision_start' and phase_name in ('engine-core', 'gsv-cpp'):
                return
            if command == 'assets_ensure_engine_core' and phase_name != 'engine-core':
                return
            if command == 'assets_ensure_gsv_cpp' and phase_name != 'gsv-cpp':
                return
            with self._lock:
                self._job.update(phase=phase_name, done=item.get('done', 0), total=item.get('total', 0),
                                 message=item.get('message', ''),
                                 cancelable=bool(cancel) and phase_name in ('download', 'gsv-cpp', 'retry', 'connecting', 'run')
                                 and not self._cancel_requested)

        state, message = 'done', 'Fabric 操作已完成'
        try:
            with self._connection(install, launch=True, timeout=3600) as connection:
                if command == 'provision_start':
                    self._cache_runtime(install, connection, params['variant'])
                # Fabric serializes responses and event delivery on each socket.
                with self._connection(install) as listener:
                    listener.call('ui.listen', {'event': event})
                    stop = Event()
                    watcher = Thread(target=listener.watch, args=(stop, progress), name='fabric-progress', daemon=True)
                    watcher.start()
                    try:
                        if command == 'store_install':
                            result = self._download_voice(connection, params['entry'])
                        else:
                            result = connection.invoke(command, **params)
                    finally:
                        stop.set()
                        watcher.join(timeout=1)
                if isinstance(result, dict) and result.get('staged'):
                    message = '音色已下载，请确认安装'
        except InterruptedError:
            state, message = 'cancelled', '推理环境下载已取消，可继续下载'
        except FabricUnavailable as error:
            state, message = 'unknown', str(error)
        except FabricError as error:
            state, message = 'failed', str(error)
            if self._cancel_requested and ('cancel' in message.casefold() or '取消' in message):
                state = 'cancelled'
        except Exception:
            logging.getLogger(__name__).exception('Fabric resource operation failed')
            state, message = 'failed', 'Fabric 操作失败，请刷新后重试'
        with self._lock:
            if state == 'done' and command in ('store_install', 'store_install_staged'):
                self._invalidate_catalog()
            self._finish(state, message)

    def _download_voice(self, connection, entry):
        try:
            return connection.invoke('store_install', entry=entry)
        except FabricUnavailable:
            raise
        except FabricError:
            with self._lock:
                if self._job['phase'] != 'starting' or self._cancel_requested:
                    raise
                self._job.update(phase='checking-source', message='正在核对原作者发布源')
            url = publisher_pack_url(entry)
            if not url:
                raise
            # Keep the catalog identity and native staging/hash checks while using the verified URL.
            direct = {key: value for key, value in entry.items() if key != 'community_file_id'}
            return connection.invoke('store_install', entry={**direct, 'pack_url': url})

    def _cache_runtime(self, install, connection, variant):
        status = connection.invoke('provision_status')
        if status.get('busy'):
            raise FabricError('Fabric 已有推理环境任务正在运行')
        if any(item.get('id') == variant and item.get('installed') for item in status.get('variants', [])):
            return

        def check_active():
            if self._runtime_cancel.is_set():
                raise InterruptedError()

        def progress(done, total):
            with self._lock:
                self._job.update(done=done, total=total)

        with self._lock:
            self._job.update(phase='runtime-download', message='正在从 Fabric 官方发布源获取推理环境',
                             cancelable=True)
        try:
            cache_official_runtime(install.root, variant, check_active=check_active, progress=progress)
        except (FabricError, OSError, httpx.HTTPError):
            if self._runtime_cancel.is_set():
                raise InterruptedError() from None
        finally:
            with self._lock:
                check_active()
                self._job.update(phase='starting', message='正在交给 Fabric 原版安装', cancelable=False)

    def cancel(self, id):
        with self._lock:
            if not self._job or self._job['id'] != id or not self._job['cancelable'] or self._cancel_requested:
                raise FabricError('目前没有可取消的 Fabric 任务')
            if self._job['kind'] == 'setup':
                self._installer_cancel.set()
                self._job['cancelable'] = False
                return self.status()
            if self._job['phase'] == 'runtime-download':
                self._runtime_cancel.set()
                self._job['cancelable'] = False
                return self.status()
            if not self._cancel_command:
                raise FabricError('目前没有可取消的 Fabric 任务')
            install = self._job_install
            command, args = self._cancel_command
            self._cancel_requested = True
            self._job['cancelable'] = False
        try:
            with self._connection(install) as connection:
                if command == 'job.cancel':
                    connection.call(command, args)
                else:
                    connection.invoke(command, **args)
        except FabricError:
            with self._lock:
                if self._job['id'] == id and self._job['state'] == 'running':
                    self._cancel_requested = False
                    self._job['cancelable'] = True
            raise
        return self.status()
