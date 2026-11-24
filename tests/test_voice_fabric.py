import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import wave

import pytest
import httpx

from xiaomao.voice_fabric import (
    FABRIC_PRODUCT, FABRIC_VERSION, FabricConnection, FabricError,
    FabricInstallation, catalog_entries, find_installations, role_references, speech_arguments, speech_artifact,
)
from xiaomao.fabric_model_path import choose_model_path, installed_role_directory
from xiaomao import fabric_runtime_download as runtime_download
from xiaomao import desktop_fabric, fabric_setup, voice_fabric
from xiaomao.desktop_service import DesktopService


@pytest.fixture
def registered(tmp_path):
    registry = tmp_path / 'TuringMirror'
    (registry / 'apps').mkdir(parents=True)
    (registry / 'installed').mkdir()
    root = tmp_path / 'Fabric 语音'
    root.mkdir()
    executable = root / 'gpt-sovits-fabric.exe'
    executable.touch()
    record = {
        'installId': 'demo', 'productId': FABRIC_PRODUCT, 'root': str(root),
        'executable': str(executable),
        'manifest': {'productId': FABRIC_PRODUCT, 'version': FABRIC_VERSION, 'protocol': 1},
    }
    (registry / 'installed' / 'demo.json').write_text(json.dumps(record), encoding='utf-8')
    state = registry / 'apps' / 'demo.json'
    state.write_text(json.dumps({'installId': 'demo', 'protocol': 1,
                                 'endpoint': 'ws://127.0.0.1:23456/rpc', 'token': 'fake-local-token'}),
                     encoding='utf-8')
    return registry, root, state


def test_discover_only_pinned_fabric_executable(registered):
    registry, root, state = registered
    record = json.loads((registry / 'installed' / 'demo.json').read_text(encoding='utf-8'))
    record.update(installId='other', executable=str(root / 'other.exe'))
    Path(record['executable']).touch()
    (registry / 'installed' / 'other.json').write_text(json.dumps(record), encoding='utf-8')
    assert find_installations(registry) == [FabricInstallation('demo', root, root / 'gpt-sovits-fabric.exe', state)]


def test_fabric_setup_uses_silent_install_and_nsis_destination(tmp_path, monkeypatch):
    installer = tmp_path / 'downloaded setup.exe'
    destination = tmp_path / 'GPT-SoVITS Fabric'

    def run(command, **options):
        assert command == f'"{installer}" /S /D={destination}'
        assert options['executable'] == str(installer)
        assert options['cwd'] == tmp_path
        assert options['creationflags'] == getattr(fabric_setup.subprocess, 'CREATE_NO_WINDOW', 0)
        return fabric_setup.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(fabric_setup.subprocess, 'run', run)
    assert fabric_setup.run_fabric_setup(installer, destination) == 0


@pytest.mark.parametrize('endpoint', [
    'ws://10.0.0.1:23456/rpc', 'ws://localhost:23456/rpc',
    'ws://127.0.0.1:23456/rpc?redirect=1', 'ws://bad@127.0.0.1:23456/rpc',
    'ws://127.0.0.1:99999/rpc', 'ws://127.0.0.1:23456/other',
])
def test_never_sends_token_to_unexpected_endpoints(registered, endpoint):
    registry, _, state = registered
    record = json.loads(state.read_text(encoding='utf-8'))
    state.write_text(json.dumps(dict(record, endpoint=endpoint)), encoding='utf-8')
    with pytest.raises(FabricError):
        FabricConnection(find_installations(registry)[0], connect=lambda *a, **kw: pytest.fail('connected'))


class TestSocket:
    __test__ = False

    def __init__(self, install_id='demo'):
        self.install_id = install_id
        self.requests = []
        self.messages = []
        self.closed = False

    def send(self, payload):
        request = json.loads(payload)
        self.requests.append(request)
        response = {'jsonrpc': '2.0', 'id': request['id']}
        if request['method'] == 'app.hello':
            response['result'] = {'installId': self.install_id, 'version': FABRIC_VERSION,
                                  'productId': FABRIC_PRODUCT, 'protocol': 1}
        else:
            self.messages.append({'jsonrpc': '2.0', 'method': 'ui.event', 'params': {
                'event': 'store-progress', 'payload': {'voice_id': 'demo', 'done': 10}}})
            response['result'] = {'staged': True}
        self.messages.append(response)

    def recv(self):
        return json.dumps(self.messages.pop(0))

    def close(self):
        self.closed = True


def test_authenticates_then_receives_store_events(registered):
    registry, _, _ = registered
    socket = TestSocket()
    events = []

    def connect(endpoint, **options):
        assert endpoint == 'ws://127.0.0.1:23456/rpc'
        assert options['header'] == {'Authorization': 'Bearer fake-local-token'}
        assert options['redirect_limit'] == 0 and options['suppress_origin']
        return socket

    with FabricConnection(find_installations(registry)[0], connect=connect) as connection:
        assert connection.call('ui.invoke', {'command': 'store_install'}, on_event=events.append) == {'staged': True}
    assert socket.closed
    assert [request['method'] for request in socket.requests] == ['app.hello', 'ui.invoke']
    assert events == [{'event': 'store-progress', 'payload': {'voice_id': 'demo', 'done': 10}}]


def test_rejects_different_installation_after_hello(registered):
    registry, _, _ = registered
    socket = TestSocket('wrong')
    with pytest.raises(FabricError):
        FabricConnection(find_installations(registry)[0], connect=lambda *a, **kw: socket)
    assert socket.closed


def test_catalog_preserves_attribution_version_and_upstream_staging():
    entry = {
        'id': 'asset', 'community_file_id': 'file', 'community_version_id': 'release',
        'community_staging_id': 'staged-release', 'name': '音色', 'author': '作者',
        'license': {'id': 'CC-BY-NC-4.0', 'url': 'https://example.test/license'},
        'tag': 'v5turbo', 'version': '1', 'sha256': '0' * 64, 'size_bytes': 42,
        'installed': True, 'installed_role_id': 'prior', 'update_available': True,
        'cover_url': 'javascript:alert(1)', 'official': False,
    }
    choices = catalog_entries({'thirdparty_voices': [entry, dict(entry, id='broken', sha256='bad')]},
                              {'staged-release': {'size_bytes': 42}})
    assert len(choices) == 1
    public, original = choices[('asset', 'release')]
    assert public['staged'] and public['installed'] and public['updateAvailable']
    assert public['license'] == 'CC-BY-NC-4.0' and public['author'] == '作者'
    assert public['roleId'] == 'prior' and not public['coverUrl']
    assert original == entry


@pytest.fixture
def local_voice_library(registered, tmp_path, monkeypatch):
    registry, root, _ = registered
    models = root / 'User_Data' / 'characters'
    roles = {}
    for role_id, name, language in (
        ('hifumi', 'Ajitani Hifumi (Chinese)', 'ZH'),
        ('nozomi-jp', 'Tachibana Nozomi (Japanese)', 'JP'),
        ('custom', '自定义音色', 'zh'),
    ):
        directory = models / role_id
        directory.mkdir(parents=True)
        (directory / 'config.json').write_text(json.dumps({'name': name}), encoding='utf-8')
        pcm = directory / 'reference.wav'
        with wave.open(str(pcm), 'wb') as sample:
            sample.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            sample.writeframes(b'\0\x01' * 8000)
        roles[role_id] = {'id': role_id, 'name': name, 'author': '作者', 'version': 'v5turbo',
                          'dir': str(directory), 'params': {}, 'cover': '', 'cover_exists': False,
                          'lists': [{'file': 'dataset/sample.list', 'lang': language, 'note': '', 'rows': [
                              {'line': 1, 'exists': True, 'ambiguous': False, 'abs': str(pcm),
                               'text': '参考台词', 'lang': language},
                          ]}]}

    class NativeLibrary(TestSocket):
        busy = False
        delete_error = False

        def send(self, payload):
            request = json.loads(payload)
            method, params = request['method'], request['params']
            if method == 'app.hello':
                return super().send(payload)
            self.requests.append(request)
            response = {'jsonrpc': '2.0', 'id': request['id']}
            if method == 'res.get':
                role_id = params['id']
                role = roles.get(role_id)
                if role:
                    response['result'] = {'id': role_id, 'kind': 'gsv.character', 'name': role['name'],
                                          'value': role_id, 'metadata': role, 'files': []}
                else:
                    response['error'] = {'code': -32000, 'message': role_id, 'data': {'code': 'resource_missing'}}
            elif method == 'res.list':
                response['result'] = {'dir': str(models), 'roles': list(roles.values())}
            elif method == 'app.status':
                response['result'] = {'runtimeReady': True, 'busy': self.busy}
            elif method == 'ui.invoke' and params['command'] == 'store_catalog':
                response['result'] = {'source': 'cache', 'thirdparty_voices': [
                    {'id': 'community-release', 'name': 'Ajitani Hifumi (Chinese)', 'sha256': '0' * 64,
                     'installed': 'hifumi' in roles, 'installed_role_id': 'hifumi' if 'hifumi' in roles else ''},
                ]}
            elif method == 'ui.invoke' and params['command'] == 'store_staged':
                response['result'] = {}
            elif method == 'ui.invoke' and params['command'] == 'role_delete':
                role_id = params['args']['id']
                if self.delete_error:
                    response['error'] = {'code': -32000, 'message': '音色文件正在使用',
                                         'data': {'code': 'command_failed'}}
                else:
                    target = (models / role_id).resolve(strict=True)
                    assert target.parent == models.resolve(strict=True)
                    shutil.rmtree(target)
                    del roles[role_id]
                    response['result'] = None
            else:
                pytest.fail(f'意外调用 Fabric 指令 {method}')
            self.messages.append(response)

        def settimeout(self, timeout):
            pass

    socket = NativeLibrary()
    monkeypatch.setattr(voice_fabric, 'registry_path', lambda: registry)
    monkeypatch.setattr(desktop_fabric, 'connect_fabric',
                        lambda install, **kwargs: FabricConnection(install, connect=lambda *a, **kw: socket))
    return DesktopService(tmp_path / 'panel'), models, roles, socket


def test_local_fabric_roles_use_catalog_chinese_names_and_language_codes(local_voice_library):
    panel, _, _, _ = local_voice_library
    roles = {role['id']: role for role in panel.call('fabric.roles', {})['roles']}
    assert roles['hifumi'] == {'id': 'hifumi', 'name': '阿慈谷日富美（中文）', 'author': '作者',
                              'version': 'v5turbo', 'languages': ['zh']}
    assert roles['nozomi-jp']['name'] == '橘望（日语）'
    assert roles['nozomi-jp']['languages'] == ['ja']
    assert roles['custom']['name'] == '自定义音色'
    assert panel.call('fabric.references', {'roleId': 'nozomi-jp'})[0]['language'] == 'ja'


def test_fabric_reference_playback_preserves_original_files(local_voice_library):
    panel, models, roles, _ = local_voice_library
    ogg = models / 'hifumi' / 'reference.ogg'
    ogg.write_bytes(b'OggS\x00\x02\x00original reference data')
    roles['hifumi']['lists'][0]['rows'].append({'line': 2, 'exists': True, 'abs': str(ogg),
                                               'text': '另一个参考', 'lang': 'ZH'})
    wav_id = panel.call('fabric.playReference', {'roleId': 'hifumi', 'reference': '0:1'})['id']
    ogg_id = panel.call('fabric.playReference', {'roleId': 'hifumi', 'reference': '0:2'})['id']
    for token, source, mime in ((wav_id, models / 'hifumi' / 'reference.wav', 'audio/wav'),
                                (ogg_id, ogg, 'audio/ogg')):
        actual = panel.call('fabric.previewMedia', {'id': token})
        assert actual['mime'] == mime
        assert base64.b64decode(actual['data']) == source.read_bytes()
    assert panel.call('fabric.status', {}) is None
    assert not panel.fabric._profile_data()


def test_fabric_reference_rejects_recordings_outside_its_role(local_voice_library):
    panel, models, roles, _ = local_voice_library
    roles['hifumi']['lists'][0]['rows'][0]['abs'] = str(models / 'custom' / 'reference.wav')
    with pytest.raises(FabricError, match='参考音频'):
        panel.call('fabric.playReference', {'roleId': 'hifumi', 'reference': '0:1'})


def test_read_only_fabric_can_play_references_and_cannot_uninstall(local_voice_library):
    panel, models, _, _ = local_voice_library
    panel.read_only = True
    request = {'roleId': 'hifumi', 'reference': '0:1'}
    selected = panel.call('fabric.playReference', request)
    assert panel.call('fabric.previewMedia', selected)['mime'] == 'audio/wav'
    with pytest.raises(ValueError, match='只读'):
        panel.call('fabric.uninstall', {'roleId': 'hifumi'})
    assert (models / 'hifumi' / 'reference.wav').is_file()


def test_fabric_uninstall_removes_only_selected_role_and_its_preferences(local_voice_library):
    panel, models, _, socket = local_voice_library
    panel.call('fabric.selectRole', {'roleId': 'hifumi'})
    panel.call('fabric.saveReference', {'roleId': 'hifumi', 'reference': '0:1'})
    panel.call('fabric.saveReference', {'roleId': 'custom', 'reference': '0:1'})
    sample = panel.call('fabric.playReference', {'roleId': 'hifumi', 'reference': '0:1'})['id']

    after = panel.call('fabric.uninstall', {'roleId': 'hifumi'})

    assert after['roleId'] == panel.call('fabric.roles', {})['roleId'] == ''
    assert {role['id'] for role in after['roles']} == {'custom', 'nozomi-jp'}
    assert not (models / 'hifumi').exists() and (models / 'custom' / 'reference.wav').is_file()
    assert panel.fabric._profile_data()['references'] == {'custom': '0:1'}
    assert [row['params'] for row in socket.requests if row['method'] == 'ui.invoke'] == [
        {'command': 'role_delete', 'args': {'id': 'hifumi'}},
    ]
    with pytest.raises(FabricError):
        panel.call('fabric.previewMedia', {'id': sample})


def test_fabric_failed_uninstall_preserves_the_default(local_voice_library):
    panel, models, _, socket = local_voice_library
    panel.call('fabric.selectRole', {'roleId': 'hifumi'})
    socket.delete_error = True
    with pytest.raises(FabricError, match='正在使用'):
        panel.call('fabric.uninstall', {'roleId': 'hifumi'})
    assert panel.call('fabric.roles', {})['roleId'] == 'hifumi'
    assert (models / 'hifumi' / 'reference.wav').is_file()


def test_catalog_load_cannot_restore_pre_uninstall_install_status(local_voice_library, monkeypatch):
    panel, _, _, _ = local_voice_library
    convert = desktop_fabric.catalog_entries

    def unpack_stale_catalog(catalog, staged):
        # An uninstall can complete while the read is parsing a captured catalog response.
        panel.call('fabric.uninstall', {'roleId': 'hifumi'})
        return convert(catalog, staged)

    with monkeypatch.context() as patch:
        patch.setattr(desktop_fabric, 'catalog_entries', unpack_stale_catalog)
        with pytest.raises(FabricError, match='音色库已变化'):
            panel.call('fabric.catalog', {})
    assert panel.call('fabric.catalog', {})['items'][0]['installed'] is False


def test_fabric_uninstall_requires_current_role_path_and_idle_engine(local_voice_library):
    panel, models, roles, socket = local_voice_library
    socket.busy = True
    with pytest.raises(FabricError, match='任务'):
        panel.call('fabric.uninstall', {'roleId': 'hifumi'})
    socket.busy = False
    roles['hifumi']['dir'] = str(models / 'custom')
    with pytest.raises(FabricError, match='目录'):
        panel.call('fabric.uninstall', {'roleId': 'hifumi'})
    assert all(row['method'] != 'ui.invoke' for row in socket.requests)
    assert (models / 'hifumi' / 'reference.wav').is_file()


def test_fabric_resolves_stale_session_voice_to_installed_default(local_voice_library):
    panel, _, _, _ = local_voice_library
    with panel.fabric._connection() as connection:
        assert voice_fabric.resolve_installed_role(connection, 'removed', 'custom')['id'] == 'custom'
        assert voice_fabric.resolve_installed_role(connection, 'hifumi', 'custom')['id'] == 'hifumi'
        with pytest.raises(FabricError, match='所选音色'):
            voice_fabric.resolve_installed_role(connection, 'removed', 'removed-too')


def test_community_download_uses_identical_publisher_package_when_official_source_is_missing(monkeypatch):
    row = {
        'id': 'asset', 'community_file_id': 'file', 'community_version_id': 'release',
        'official': False, 'author': 'Publisher', 'source_url': 'https://modelscope.cn/models/Publisher/voices',
        'sha256': 'c' * 64, 'size_bytes': 42,
    }
    archive = 'models/voice ja/role.zip'
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == 'api.turingmirror.com':
            assert request.url.path == '/v1/community/files/file/download'
            return httpx.Response(422, json={'error': {'code': 'unavailable'}})
        assert request.url.path == '/api/v1/models/Publisher/voices/repo/files'
        return httpx.Response(200, json={'Success': True, 'Data': {'Files': [
            {'Path': 'other.zip', 'Type': 'blob', 'Sha256': 'a' * 64, 'Size': 42},
            {'Path': archive, 'Type': 'blob', 'Sha256': row['sha256'].upper(), 'Size': 42},
        ]}})

    make_client = httpx.Client
    monkeypatch.setattr(voice_fabric.httpx, 'Client',
                        lambda **kwargs: make_client(transport=httpx.MockTransport(respond), **kwargs))
    assert voice_fabric.publisher_pack_url(row) == (
        'https://www.modelscope.cn/models/Publisher/voices/resolve/master/models/voice%20ja/role.zip'
    )
    assert [request.url.host for request in calls] == ['api.turingmirror.com', 'www.modelscope.cn']


@pytest.mark.parametrize(('http_status', 'published_hash'), [(403, 'c' * 64), (422, '9' * 64)])
def test_publisher_fallback_preserves_community_access_and_package_identity(monkeypatch, http_status, published_hash):
    row = {'id': 'asset', 'community_file_id': 'file', 'community_version_id': 'release',
           'official': False, 'author': 'Publisher', 'source_url': 'https://www.modelscope.cn/models/Publisher/voices',
           'sha256': 'c' * 64, 'size_bytes': 42}
    requested_hosts = []

    def respond(request):
        requested_hosts.append(request.url.host)
        if request.url.host == 'api.turingmirror.com':
            return httpx.Response(http_status, json={'error': {'code': 'unavailable'}})
        return httpx.Response(200, json={'Success': True, 'Data': {'Files': [
            {'Path': 'role.zip', 'Type': 'blob', 'Sha256': published_hash, 'Size': 42},
        ]}})

    make_client = httpx.Client
    monkeypatch.setattr(voice_fabric.httpx, 'Client',
                        lambda **kwargs: make_client(transport=httpx.MockTransport(respond), **kwargs))
    if http_status == 403:
        assert voice_fabric.publisher_pack_url(row) is None
        assert requested_hosts == ['api.turingmirror.com']
    else:
        with pytest.raises(FabricError, match='一致'):
            voice_fabric.publisher_pack_url(row)


def test_still_visible_community_card_can_start_after_bridge_restart(registered, tmp_path, monkeypatch):
    registry, _, _ = registered
    monkeypatch.setattr(voice_fabric, 'registry_path', lambda: registry)
    service = desktop_fabric.DesktopFabric(tmp_path / 'profile')
    row = {'id': 'asset', 'community_file_id': 'file', 'community_version_id': 'release',
           'name': '角色', 'sha256': 'c' * 64, 'official': False}

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *error): pass
        def invoke(self, name, **args):
            if name == 'store_catalog':
                return {'source': 'cache', 'thirdparty_voices': [row]}
            assert name == 'store_staged'
            return {}

    monkeypatch.setattr(service, '_connection', lambda *args, **kwargs: Connection())
    monkeypatch.setattr(service, '_start', lambda install, kind, name, command, event, params, **kwargs: params)
    assert service.download('asset', 'release') == {'entry': row}
    with pytest.raises(FabricError, match='版本'):
        service.download('asset', 'other-release')


def test_retry_preserves_versioned_native_staging_metadata(tmp_path, monkeypatch):
    service = desktop_fabric.DesktopFabric(tmp_path / 'profile')
    service._begin(None, 'download', '角色')
    url = 'https://www.modelscope.cn/models/Publisher/voices/resolve/master/role.zip'
    row = {
        'id': 'asset', 'community_file_id': 'file', 'community_version_id': 'release',
        'community_staging_id': 'stage', 'sha256': 'c' * 64, 'size_bytes': 42, 'official': False,
    }
    attempts = []

    class Connection:
        def invoke(self, command, *, entry):
            assert command == 'store_install'
            attempts.append(entry)
            if entry is row:
                raise FabricError('请刷新社区后再下载此版本。')
            return {'staged': True}

    monkeypatch.setattr(desktop_fabric, 'publisher_pack_url', lambda entry: url if entry is row else None)
    assert service._download_voice(Connection(), row) == {'staged': True}
    assert attempts == [row, {
        'id': 'asset', 'community_version_id': 'release', 'community_staging_id': 'stage',
        'sha256': 'c' * 64, 'size_bytes': 42, 'official': False, 'pack_url': url,
    }]
    assert row['community_file_id'] == 'file'


def test_speech_uses_verified_references_and_preserves_native_role_parameters(tmp_path):
    reference = tmp_path / '参考音频.ogg'
    reference.touch()
    role = {'id': 'character', 'params': {'speed': 0.9, 'top_p': 0.8, 'ui_only': True}, 'lists': [
        {'lang': 'zh', 'rows': [{'line': 1, 'exists': True, 'ambiguous': True, 'abs': str(reference), 'text': '歧义'},
                                {'line': 2, 'exists': True, 'abs': str(reference), 'text': '参考台词', 'lang': 'ZH'}]}
    ]}
    assert list(role_references(role)) == ['0:2']
    request = speech_arguments(role, ' 实际合成文本 ', '0:2', 'cuda', tmp_path)
    assert request['parameters'] == {'speed': 0.9, 'top_p': 0.8}
    assert request['referenceAudio'] == str(reference) and request['referenceLanguage'] == 'zh'
    assert request['referenceText'] == '参考台词' and request['text'] == '实际合成文本'


@pytest.mark.skipif(os.name != 'nt', reason='Windows directory junction')
def test_model_directory_switch_preserves_both_libraries(tmp_path):
    root = tmp_path / 'Fabric'
    native = root / 'User_Data' / 'characters'
    native.mkdir(parents=True)
    (native / '原本音色').mkdir()
    data = tmp_path / '外部模型目录'
    data.mkdir()
    external_role = data / '外部音色'
    external_role.mkdir()
    (external_role / 'config.json').write_text('{"name": "外部音色"}', encoding='utf-8')
    assert choose_model_path(root, str(data)) == data
    assert native.is_junction() and (native / '外部音色').is_dir()
    assert installed_role_directory(root, '外部音色') == external_role
    assert choose_model_path(root, '') == native
    assert not native.is_junction() and (native / '原本音色').is_dir()
    assert external_role.is_dir()
    assert installed_role_directory(root, '外部音色') is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows Fabric installation')
def test_unresponsive_running_fabric_cannot_rename_models_or_report_shutdown(registered, tmp_path, monkeypatch):
    registry, root, _ = registered
    monkeypatch.setattr(voice_fabric, 'registry_path', lambda: registry)
    service = desktop_fabric.DesktopFabric(tmp_path / 'profile')
    native = root / 'User_Data' / 'characters'
    native.mkdir(parents=True)
    (native / '已有音色').mkdir()
    custom = tmp_path / '选中的目录'
    custom.mkdir()

    def timeout(*args, **kwargs):
        raise voice_fabric.FabricUnavailable('接口暂时无响应')

    class LiveProcess:
        info = {'name': voice_fabric.FABRIC_EXE, 'exe': str(root / voice_fabric.FABRIC_EXE)}

    monkeypatch.setattr(service, '_connection', timeout)
    monkeypatch.setattr('psutil.process_iter', lambda attrs: [LiveProcess()])
    with pytest.raises(FabricError, match='退出'):
        service.model_directory(str(custom))
    with pytest.raises(FabricError, match='退出'):
        service.stop()
    assert not native.is_junction() and (native / '已有音色').is_dir()


@pytest.mark.skipif(os.name != 'nt', reason='Windows directory junction')
def test_switching_back_restores_default_voice_and_reference(registered, tmp_path, monkeypatch):
    registry, root, _ = registered
    monkeypatch.setattr(desktop_fabric, 'registry_path', lambda: registry)
    monkeypatch.setattr(voice_fabric, 'registry_path', lambda: registry)
    service = desktop_fabric.DesktopFabric(tmp_path / 'profile')
    def inactive(*args, **kwargs):
        raise voice_fabric.FabricUnavailable('stopped')
    monkeypatch.setattr(service, '_connection', inactive)
    service._save(roleId='first', references={'first':'0:3'}, backend='vulkan')
    custom = tmp_path / '模型目录'
    custom.mkdir()
    assert service.model_directory(str(custom))['roleId'] == ''
    service._save(roleId='second', references={'second':'0:2'})
    restored = service.model_directory('')
    assert restored['roleId'] == 'first' and restored['backend'] == 'vulkan'
    assert service._profile_data()['references'] == {'first':'0:3'}
    assert service.model_directory(str(custom))['roleId'] == 'second'
    assert service._profile_data()['references'] == {'second':'0:2'}


@pytest.mark.skipif(os.name != 'nt', reason='Windows canonical file paths')
def test_artifact_accepts_fabric_extended_path_and_rejects_other_folders(tmp_path):
    output = tmp_path / 'preview'
    output.mkdir()
    clip = output / '试听.wav'
    with wave.open(str(clip), 'wb') as wav:
        wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\0\x01' * 8000)
    result = {'artifacts': [{'role': 'speech', 'format': 'wav', 'path': '\\\\?\\' + str(clip)}]}
    assert speech_artifact(result, output) == (clip, 1.0)
    with pytest.raises(FabricError):
        speech_artifact(result, tmp_path / 'other')


def test_official_runtime_download_resumes_ranges_and_verifies_before_publish(tmp_path, monkeypatch):
    payload = b'fabric archive bytes'
    sha = hashlib.sha256(payload).hexdigest()
    name = 'runtime-cpu-1.0.tar.gz'
    source = runtime_download._RELEASES + 'GSV-runtime/' + name
    ranges = []

    def respond(request):
        if str(request.url) == runtime_download._INDEX:
            return httpx.Response(200, json={'runtimes': {'cpu': {'parts': [
                {'name': name, 'sha256': sha, 'size_bytes': len(payload), 'urls': [source]}]}}})
        first, last = (int(value) for value in request.headers['Range'][6:].split('-', 1))
        ranges.append((first, last))
        last = min(len(payload) - 1, last)
        return httpx.Response(206, content=payload[first:last + 1],
                              headers={'Content-Range': f'bytes {first}-{last}/{len(payload)}'})

    monkeypatch.setattr(runtime_download, '_CHUNK', 8)
    make_client = httpx.Client
    monkeypatch.setattr(runtime_download.httpx, 'Client', lambda **kw: make_client(transport=httpx.MockTransport(respond), **kw))
    cache = tmp_path / 'User_Data' / 'update_cache' / 'runtime'
    staged = cache / f'.shiguang-{sha[:16]}'
    staged.mkdir(parents=True)
    (staged / '00000.part').write_bytes(payload[:8])
    progress = []
    runtime_download.cache_official_runtime(tmp_path, 'cpu', check_active=lambda: None,
                                            progress=lambda done, total: progress.append((done, total)))
    assert (cache / name).read_bytes() == payload
    assert (0, 7) not in ranges and {(8, 15), (16, len(payload) - 1)} <= set(ranges)
    assert progress[-1] == (len(payload), len(payload)) and not list(staged.iterdir())
