"""Boundaries for the upstream GPT-SoVITS Fabric desktop protocol."""

from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess
from time import monotonic, sleep
from urllib.parse import quote, urlsplit

import httpx
import psutil
from websocket import WebSocketException, WebSocketTimeoutException, create_connection

from .voice_audio import read_voice_wav


FABRIC_PRODUCT = 'com.turingmirror.gpt-sovits-fabric'
FABRIC_VERSION = '1.0.1'
FABRIC_EXE = 'gpt-sovits-fabric.exe'
FABRIC_PROFILE_LIMIT = 256 * 1024
_HASH = re.compile(r'[0-9a-fA-F]{64}')
_COMMUNITY_FILES = 'https://api.turingmirror.com/v1/community/files'
_MODELSCOPE = 'https://www.modelscope.cn'
_MODEL_REPOSITORY = re.compile(r'/models/([A-Za-z0-9][\w.-]{0,127}/[A-Za-z0-9][\w.-]{0,127})/?', re.ASCII)
_IDENTITY = {'productId': FABRIC_PRODUCT, 'version': FABRIC_VERSION, 'protocol': 1}
_LANGUAGES = frozenset(('zh', 'en', 'ja', 'ko', 'yue', 'all_zh', 'all_ja', 'all_ko', 'all_yue', 'auto'))
_LANGUAGE_ALIASES = {'cn': 'zh', 'zh-cn': 'zh', 'zh-hans': 'zh', 'jp': 'ja', 'ja-jp': 'ja',
                     'en-us': 'en', 'kr': 'ko', 'ko-kr': 'ko'}
_LANGUAGE_NAMES = {'chinese': '中文', 'japanese': '日语', 'english': '英语', 'korean': '韩语', 'cantonese': '粤语'}
# These releases ship English names in installed metadata. Match the catalog's zh-CN names.
_ROLE_NAMES = {
    'ajitani hifumi': '阿慈谷日富美', 'hanaoka yuzu': '花冈柚子', 'komakaze rabu': '河驹风兰舞',
    'tachibana nozomi': '橘望', 'takanashi hoshino': '小鸟游星野',
}
_PARAMETERS = frozenset(('text_lang', 'speed', 'top_k', 'top_p', 'temperature', 'repetition_penalty',
                         'text_split_method', 'fragment_interval', 'seed', 'sample_steps'))
FABRIC_PREVIEW_LIMIT = 8 * 1024 * 1024


class FabricError(ValueError):
    pass


class FabricUnavailable(FabricError):
    pass


class MissingFabricRole(FabricError):
    pass


def _read_object(path, limit):
    try:
        with path.open('rb') as file:
            data = file.read(limit + 1)
        if len(data) <= limit:
            value = json.loads(data)
            if isinstance(value, dict):
                return value
    except (OSError, ValueError, RecursionError):
        pass
    return {}


@dataclass(frozen=True)
class FabricInstallation:
    install_id: str
    root: Path
    executable: Path
    state_path: Path


def registry_path():
    appdata = os.environ.get('APPDATA')
    return Path(appdata) / 'TuringMirror' if appdata else None


def find_installations(registry):
    if registry is None:
        return []
    installs = []
    for record_path in sorted((registry / 'installed').glob('*.json')):
        record = _read_object(record_path, 256 * 1024)
        install_id = record_path.stem
        manifest = record.get('manifest')
        if (record.get('installId') != install_id or record.get('productId') != FABRIC_PRODUCT or
                not isinstance(manifest, dict) or any(manifest.get(k) != v for k, v in _IDENTITY.items())):
            continue
        executable = record.get('executable')
        root = record.get('root')
        if not isinstance(executable, str) or not isinstance(root, str):
            continue
        executable, root = Path(executable), Path(root)
        try:
            if (executable.is_absolute() and root.is_absolute() and executable.name.casefold() == FABRIC_EXE
                    and executable.is_file() and root.is_dir()):
                installs.append(FabricInstallation(install_id, root.resolve(), executable.resolve(),
                                                   registry / 'apps' / record_path.name))
        except (OSError, ValueError):
            continue
    return installs


def selected_installation(profile, installs=None):
    if installs is None:
        installs = find_installations(registry_path())
    configured = profile.get('installation')
    if configured:
        return next((install for install in installs if str(install.root) == configured), None)
    return installs[0] if len(installs) == 1 else None


def installation_running(install):
    """Require positive shutdown evidence before changing the model directory."""
    try:
        for process in psutil.process_iter(['name', 'exe']):
            info = process.info
            executable = info['exe']
            if executable and os.path.basename(executable).casefold() == FABRIC_EXE:
                if Path(executable).resolve() == install.executable:
                    return True
            if not executable and (info['name'] or '').casefold() == FABRIC_EXE:
                return True
    except (OSError, psutil.Error):
        raise FabricError('无法确认 Fabric 已退出，请在原版软件中退出后重试') from None
    return False


class FabricConnection:
    def __init__(self, install, *, connect=create_connection, timeout=2):
        record = _read_object(install.state_path, 8192)
        endpoint, token = record.get('endpoint'), record.get('token')
        if (record.get('installId') != install.install_id or record.get('protocol') != 1 or
                not isinstance(endpoint, str) or not isinstance(token, str) or
                not re.fullmatch(r'[!-~]{1,512}', token)):
            raise FabricUnavailable('Fabric 本机接口尚未启动')
        match = re.fullmatch(r'ws://127\.0\.0\.1:([0-9]{1,5})/rpc', endpoint)
        if not match or not 0 < int(match[1]) < 65536:
            raise FabricError('Fabric 本机接口地址无效')
        try:
            self.socket = connect(endpoint, header={'Authorization': f'Bearer {token}'}, timeout=timeout,
                                  http_no_proxy=['127.0.0.1'], suppress_origin=True, redirect_limit=0)
        except (OSError, WebSocketException):
            raise FabricUnavailable('暂时无法连接 Fabric 本机接口') from None
        self.sequence = 0
        try:
            hello = self.call('app.hello')
            if (not isinstance(hello, dict) or hello.get('installId') != install.install_id or
                    any(hello.get(k) != v for k, v in _IDENTITY.items())):
                raise FabricError('Fabric 本机接口的身份或版本不匹配')
        except Exception:
            self.socket.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.socket.close()

    def call(self, method, params=None, *, on_event=None):
        self.sequence += 1
        message = {'jsonrpc': '2.0', 'id': self.sequence, 'method': method, 'params': params or {}}
        try:
            self.socket.send(json.dumps(message, ensure_ascii=False, allow_nan=False))
            while True:
                payload = json.loads(self.socket.recv())
                if not isinstance(payload, dict) or payload.get('jsonrpc') != '2.0':
                    raise FabricError('Fabric 返回了无效的本机接口响应')
                if payload.get('id') == self.sequence:
                    break
                event = payload.get('params')
                if on_event and payload.get('method') == 'ui.event' and isinstance(event, dict):
                    on_event(event)
        except (OSError, WebSocketException, json.JSONDecodeError, RecursionError):
            raise FabricUnavailable('Fabric 任务连接已中断，请刷新实际状态') from None
        error = payload.get('error')
        if isinstance(error, dict):
            data = error.get('data')
            detail = error.get('message')
            if isinstance(data, dict):
                if method == 'res.get' and data.get('code') == 'resource_missing':
                    raise MissingFabricRole('所选音色已不存在，请刷新本地音色')
                if isinstance(data.get('message'), str):
                    detail = data['message']
            raise FabricError(detail[:400] if isinstance(detail, str) and detail else 'Fabric 操作失败')
        if 'result' not in payload:
            raise FabricError('Fabric 未返回操作结果')
        return payload['result']

    def invoke(self, command, *, on_event=None, **args):
        return self.call('ui.invoke', {'command': command, 'args': args}, on_event=on_event)

    def watch(self, stop, on_event):
        self.socket.settimeout(0.5)
        while not stop.is_set():
            try:
                message = json.loads(self.socket.recv())
            except WebSocketTimeoutException:
                continue
            except (OSError, WebSocketException, ValueError, RecursionError):
                return
            if isinstance(message, dict) and message.get('method') == 'ui.event':
                event = message.get('params')
                if isinstance(event, dict):
                    on_event(event)


def connect_fabric(install, *, launch=False):
    try:
        return FabricConnection(install)
    except FabricUnavailable:
        if not launch:
            raise
    try:
        subprocess.Popen([str(install.executable), '--headless', '--product-root', str(install.root)],
                         cwd=install.root, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        raise FabricUnavailable('无法启动 GPT-SoVITS Fabric') from None
    deadline = monotonic() + 12
    while monotonic() < deadline:
        try:
            return FabricConnection(install, timeout=0.5)
        except FabricUnavailable:
            sleep(0.25)
    raise FabricUnavailable('Fabric 启动较慢，请刷新后重试')


def _text(value, limit=240):
    return value[:limit] if isinstance(value, str) else ''


def _localized(row, field):
    variants = row.get(f'{field}_i18n')
    chinese = _text(variants.get('zh-CN')) if isinstance(variants, dict) else ''
    return chinese or _text(row.get(field))


def role_display_name(role):
    name = _localized(role, 'name').strip() or _text(role.get('id'))
    base, _, suffix = name.rpartition(' (')
    language = _LANGUAGE_NAMES.get(suffix[:-1].casefold()) if base and suffix.endswith(')') else None
    if language:
        return f'{_ROLE_NAMES.get(base.casefold(), base)}（{language}）'
    return _ROLE_NAMES.get(name.casefold(), name)


def _https(value):
    value = _text(value, 2048)
    try:
        parsed = urlsplit(value)
        if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return ''


def catalog_entries(catalog, staged):
    """Keep the engine's download arguments on the backend, keyed by exact release."""
    entries = {}
    for section in ('voices', 'thirdparty_voices'):
        rows = catalog.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            identity = _text(row.get('id'))
            version = _text(row.get('community_version_id')) or _text(row.get('version'))
            size = row.get('size_bytes')
            sha = row.get('sha256')
            if not identity or not isinstance(sha, str) or not _HASH.fullmatch(sha):
                continue
            license_info = row.get('license')
            license_id = license_info.get('id') if isinstance(license_info, dict) else license_info
            license_url = license_info.get('url') if isinstance(license_info, dict) else None
            staged_id = _text(row.get('community_staging_id')) or identity
            public = {
                'id': identity, 'release': version, 'name': role_display_name(row),
                'series': _localized(row, 'series'), 'group': _localized(row, 'group'),
                'author': _text(row.get('author')), 'authorUrl': _https(row.get('author_url')),
                'family': _text(row.get('tag')), 'version': _text(row.get('version')),
                'date': _text(row.get('date'), 32), 'size': size if type(size) is int and size > 0 else 0,
                'license': _text(license_id), 'licenseUrl': _https(license_url),
                'sourceUrl': _https(row.get('source_url')), 'coverUrl': _https(row.get('cover_url')),
                'description': _text(row.get('description'), 2000),
                'staged': staged_id in staged, 'installed': row.get('installed') is True,
                'roleId': _text(row.get('installed_role_id')),
                'updateAvailable': row.get('update_available') is True,
            }
            entries[identity, version] = (public, row)
    return entries


def publisher_pack_url(entry):
    """Resolve a missing community source to identical bytes in the publisher repository."""
    file_id = entry.get('community_file_id')
    expected, size = _text(entry.get('sha256')).lower(), entry.get('size_bytes')
    if (entry.get('official') is not False or not isinstance(file_id, str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', file_id)
            or not _HASH.fullmatch(expected) or type(size) is not int or size <= 0):
        return None
    with httpx.Client(timeout=18, follow_redirects=False) as client:
        try:
            status = client.get(f'{_COMMUNITY_FILES}/{file_id}/download')
            if status.status_code != 422 or status.json().get('error', {}).get('code') != 'unavailable':
                return None
        except (httpx.HTTPError, ValueError, AttributeError):
            return None

        source = urlsplit(_https(entry.get('source_url')))
        repository = _MODEL_REPOSITORY.fullmatch(source.path)
        if (source.netloc not in ('modelscope.cn', 'www.modelscope.cn') or repository is None
                or repository[1].partition('/')[0].casefold() != _text(entry.get('author')).casefold()):
            raise FabricError('社区当前没有可用的文件来源，且目录中没有可核验的原作者仓库')
        name = repository[1]
        try:
            response = client.get(f'{_MODELSCOPE}/api/v1/models/{name}/repo/files',
                                  params={'Revision': 'master', 'Recursive': 'true'})
            response.raise_for_status()
            payload = response.json()
            files = payload.get('Data', {}).get('Files', []) if payload.get('Success') is True else []
        except (httpx.HTTPError, ValueError, AttributeError):
            raise FabricError('社区当前没有可用的文件来源，暂时无法读取原作者仓库') from None
        for item in files if isinstance(files, list) else ():
            if (not isinstance(item, dict) or item.get('Type') != 'blob'
                    or item.get('Size') != size or _text(item.get('Sha256')).lower() != expected):
                continue
            path = _text(item.get('Path'), 2048)
            if (path.lower().endswith('.zip') and '\\' not in path
                    and not {'', '.', '..'}.intersection(path.split('/'))):
                return f'{_MODELSCOPE}/models/{name}/resolve/master/{quote(path, safe="/")}'
    raise FabricError('社区当前没有可用的文件来源，原作者仓库也没有与该版本校验值和大小一致的角色包')


def _reference_language(value):
    code = _text(value).strip().lower()
    code = _LANGUAGE_ALIASES.get(code, code)
    return code if code in _LANGUAGES else ''


def role_references(role):
    lists = role.get('lists')
    if not isinstance(lists, list):
        return {}
    references = {}
    for index, listing in enumerate(lists):
        if not isinstance(listing, dict) or not isinstance(listing.get('rows'), list):
            continue
        for row in listing['rows']:
            if not isinstance(row, dict) or not row.get('exists') or row.get('ambiguous'):
                continue
            path, text, line = row.get('abs'), row.get('text'), row.get('line')
            if not isinstance(path, str) or not isinstance(text, str) or type(line) is not int:
                continue
            language = _reference_language(row.get('lang')) or _reference_language(listing.get('lang')) or 'auto'
            references[f'{index}:{line}'] = (path, text, language)
    return references


def installed_role(connection, role_id):
    if not isinstance(role_id, str) or not role_id or len(role_id) > 240:
        raise FabricError('请选择本地音色')
    resource = connection.call('res.get', {'id': role_id})
    role = resource.get('metadata') if isinstance(resource, dict) else None
    if (not isinstance(role, dict) or role.get('id') != role_id or resource.get('id') != role_id or
            resource.get('kind') != 'gsv.character'):
        raise FabricError('Fabric 返回了不匹配的本地音色，请刷新后重试')
    return role


def resolve_installed_role(connection, role_id, default_role_id):
    selected = role_id or default_role_id
    try:
        return installed_role(connection, selected)
    except MissingFabricRole:
        if not default_role_id or selected == default_role_id:
            raise
    return installed_role(connection, default_role_id)


def read_reference_audio(role, directory, reference):
    if not isinstance(reference, str) or reference not in (references := role_references(role)):
        raise FabricError('参考音频已变化，请刷新本地音色')
    path = Path(references[reference][0])
    if not path.is_absolute() or '\0' in str(path):
        raise FabricError('参考音频路径无效')
    try:
        path = path.resolve(strict=True)
        if not path.is_relative_to(directory) or not path.is_file():
            raise FabricError('参考音频须位于当前音色目录内')
        with path.open('rb') as source:
            data = source.read(FABRIC_PREVIEW_LIMIT + 1)
    except (OSError, RuntimeError):
        raise FabricError('无法读取参考音频，请刷新本地音色') from None
    if len(data) > FABRIC_PREVIEW_LIMIT:
        raise FabricError('参考音频文件过大，请选择另一段')
    suffix = path.suffix.lower()
    if suffix == '.wav' and data[:4] in (b'RIFF', b'RIFX', b'RF64') and data[8:12] == b'WAVE':
        return data, 'audio/wav'
    if suffix in ('.ogg', '.opus') and data.startswith(b'OggS'):
        return data, 'audio/ogg'
    if suffix == '.mp3' and (data.startswith(b'ID3') or len(data) >= 2 and data[0] == 0xff and data[1] & 0xe0 == 0xe0):
        return data, 'audio/mpeg'
    if suffix == '.flac' and data.startswith(b'fLaC'):
        return data, 'audio/flac'
    if suffix == '.m4a' and data[4:8] == b'ftyp':
        return data, 'audio/mp4'
    raise FabricError('参考音频格式无法播放')


@lru_cache(maxsize=512)
def _reference_seconds(probe, audio, size, mtime):
    try:
        result = subprocess.run([str(probe), '-v', 'error', '-protocol_whitelist', 'file,pipe',
                                 '-show_entries', 'format=duration', '-of', 'csv=p=0', str(audio)],
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=4, check=True)
        return float(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def choose_reference(root, role, requested=''):
    references = role_references(role)
    if not references or (requested and requested not in references):
        raise FabricError('该音色没有可用的参考音频，请在 Fabric 中补充参考资料')
    meta = _read_object(root / 'package_meta.json', 8192)
    data = root / ('UserData' if not (root / 'User_Data').is_dir() and (root / 'UserData').is_dir() else 'User_Data')
    variant = meta.get('variant')
    runtime = data / 'runtimes' / variant if meta.get('managed_runtime') and variant in ('nvidia', 'cpu') else root / 'Runtime'
    probes = (runtime / 'ffprobe.exe', root / 'ffprobe.exe')
    probe = next((candidate for candidate in probes if candidate.is_file()), None)
    if requested:
        choices = [requested]
    else:
        choices = sorted(references, key=lambda key: abs(len(references[key][1]) - 29))[:12]
    if not probe:
        return choices[0]
    for key in choices:
        audio = Path(references[key][0])
        try:
            stat = audio.stat()
            duration = _reference_seconds(probe, audio, stat.st_size, stat.st_mtime_ns)
        except OSError:
            continue
        if duration is not None and 3 <= duration <= 10:
            return key
    raise FabricError('参考音频需要 3 至 10 秒，请在本地音色中选择另一段或在 Fabric 中补充参考资料')


def speech_arguments(role, text, reference, backend, output):
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 1000:
        raise FabricError('请输入 1 至 1000 字的合成文本')
    if backend not in ('cuda', 'vulkan'):
        raise FabricError('请选择可用的合成引擎')
    references = role_references(role)
    if not reference:
        reference = next(iter(references), '')
    selected = references.get(reference)
    if selected is None or not Path(selected[0]).is_file():
        raise FabricError('该音色没有可用的参考音频，请在 Fabric 中补充参考资料')
    audio, transcript, language = selected
    params = role.get('params')
    parameters = {name: value for name, value in params.items() if name in _PARAMETERS} if isinstance(params, dict) else {}
    return {'roleId': role['id'], 'text': text.strip(), 'referenceAudio': audio,
            'referenceText': transcript, 'referenceLanguage': language, 'backend': backend,
            'parameters': parameters, 'format': 'wav', 'outputDirectory': str(output)}


def speech_artifact(result, output):
    artifacts = result.get('artifacts') if isinstance(result, dict) else None
    if not isinstance(artifacts, list):
        raise FabricError('Fabric 未返回合成音频')
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get('role') != 'speech' or artifact.get('format') != 'wav':
            continue
        file = artifact.get('path')
        if isinstance(file, str):
            # Rust canonicalizes Windows artifacts with an extended-length prefix.
            if os.name == 'nt' and file.startswith('\\\\?\\UNC\\'):
                file = '\\\\' + file[8:]
            elif os.name == 'nt' and file.startswith('\\\\?\\'):
                file = file[4:]
            candidate = Path(file)
            if not candidate.is_absolute():
                continue
            path = candidate.resolve()
            if path.is_relative_to(output.resolve()) and path.is_file():
                clip = read_voice_wav(path, max_seconds=58)
                return path, clip.duration
    raise FabricError('Fabric 未返回有效的合成音频')


def start_synthesis(connection, job_id, params):
    accepted = connection.call('cap.call', {'capability': 'gsv.synthesize', 'clientId': 'xiaomao',
                                           'requestId': job_id, 'arguments': params})
    native_id = accepted.get('jobId') if isinstance(accepted, dict) else None
    if not isinstance(native_id, str) or not native_id:
        raise FabricError('Fabric 未接受合成任务')
    return native_id
