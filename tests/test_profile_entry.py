import json
from pathlib import Path

import pytest

from xiaomao import paths


def test_installed_shortcut_reuses_registered_existing_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(paths.sys, 'frozen', True, raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    profile = tmp_path / 'old-source'
    (profile / 'runtime').mkdir(parents=True)
    (profile / 'runtime/config.json').write_text('{"saved_groups":["keep"]}', encoding='utf-8')
    pointer = tmp_path / 'local/ShiguangLauncher/profile.json'
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({'version': 1, 'root': str(profile)}), encoding='utf-8')
    assert paths.default_root() == profile.resolve()


def test_missing_registered_profile_does_not_silently_start_blank(tmp_path, monkeypatch):
    monkeypatch.setattr(paths.sys, 'frozen', True, raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    pointer = tmp_path / 'ShiguangLauncher/profile.json'
    pointer.parent.mkdir()
    pointer.write_text(json.dumps({'version': 1, 'root': str(tmp_path / 'gone')}), encoding='utf-8')
    with pytest.raises(ValueError, match='数据目录'):
        paths.default_root()


def test_profile_registration_preserves_all_existing_files(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    profile = tmp_path / 'old-source'
    (profile / 'runtime').mkdir(parents=True)
    (profile / 'runtime/config.json').write_text('{"saved_groups":["keep"]}', encoding='utf-8')
    (profile / 'runtime/deepseek.dpapi').write_bytes(b'encrypted-sample')
    before = {p: p.read_bytes() for p in profile.rglob('*') if p.is_file()}
    paths.remember_profile(profile)
    assert {p: p.read_bytes() for p in profile.rglob('*') if p.is_file()} == before
    monkeypatch.setattr(paths.sys, 'frozen', True, raising=False)
    assert paths.default_root() == profile.resolve()


def test_missing_new_panel_does_not_fall_back_to_old_ui(tmp_path, monkeypatch):
    from xiaomao import desktop_launch
    monkeypatch.setattr(desktop_launch.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(desktop_launch.sys, 'executable', str(tmp_path / 'Xiaomao.exe'))
    monkeypatch.setattr(desktop_launch, 'signal_panel', lambda _: False)
    with pytest.raises(ValueError, match='新版界面'):
        desktop_launch.launch_desktop(tmp_path)
    assert desktop_launch.launch_desktop(tmp_path, force_legacy=True) is False


def test_vbs_rejects_an_unversioned_installed_copy_and_uses_built_source(tmp_path):
    import os
    import shutil
    import subprocess
    if os.name != 'nt':
        pytest.skip('Windows script host')
    project = tmp_path / 'project'
    project.mkdir()
    script = project / 'start.vbs'
    shutil.copyfile(Path(__file__).resolve().parents[1] / '启动拾光微信聊天助手.vbs', script)
    local = tmp_path / 'local'
    old = local / 'Programs/XiaomaoAssistant'
    for rel in ['Xiaomao.exe', 'desktop/ShiguangDesktop.exe', 'desktop/resources/app/dist/index.html']:
        target = old / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'not-a-current-binary')
    env = {**os.environ, 'LOCALAPPDATA': str(local), 'SHIGUANG_ELECTRON': ''}
    result = subprocess.run(['cscript.exe', '//Nologo', str(script), '/check'], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and 'ERROR' in result.stdout
    for rel in ['.venv/Scripts/pythonw.exe', 'desktop/dist/index.html',
                'desktop/node_modules/electron/dist/electron.exe']:
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'source-check-only')
    version = json.loads((Path(__file__).resolve().parents[1] / 'desktop/package.json').read_text('utf-8'))['version']
    (project / 'desktop/dist/build-version.txt').write_text(version, encoding='ascii')
    result = subprocess.run(['cscript.exe', '//Nologo', str(script), '/check'], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert str(project / '.venv/Scripts/pythonw.exe') in result.stdout
    assert '--root' in result.stdout and '--legacy-ui' not in result.stdout

def test_windowed_startup_errors_remain_visible(monkeypatch):
    import ctypes
    from xiaomao import __main__ as entry
    calls=[]
    monkeypatch.setattr(ctypes.windll.user32, 'MessageBoxW', lambda *args: calls.append(args))
    with monkeypatch.context() as context:
        context.setattr(entry.sys, 'stderr', None)
        entry.report_error('gui', '已保存的数据目录暂不可用')
    assert len(calls)==1 and calls[0][1]=='已保存的数据目录暂不可用'

def test_existing_old_panel_cannot_be_reopened_as_new_version(tmp_path, monkeypatch):
    from xiaomao import desktop_launch
    from types import SimpleNamespace
    executable=tmp_path/'new/desktop/ShiguangDesktop.exe'
    old=SimpleNamespace(info={'exe':str(tmp_path/'old/desktop/ShiguangDesktop.exe'),'cmdline':['ShiguangDesktop.exe','--profile='+str(tmp_path)]})
    import psutil
    monkeypatch.setattr(psutil,'process_iter',lambda *args:[old])
    assert not desktop_launch.current_panel_running(tmp_path,executable,[])
    old.info['exe']=str(executable)
    assert desktop_launch.current_panel_running(tmp_path,executable,[])


def test_launcher_does_not_signal_old_panel(tmp_path, monkeypatch):
    from xiaomao import desktop_launch
    monkeypatch.setattr(desktop_launch.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(desktop_launch.sys, 'executable', str(tmp_path / 'Xiaomao.exe'))
    for rel in ['desktop/ShiguangDesktop.exe', 'desktop/resources/app/dist/index.html']:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(desktop_launch, '_panel_present', lambda _: True)
    monkeypatch.setattr(desktop_launch, 'current_panel_running', lambda *args: False)
    signals = []
    monkeypatch.setattr(desktop_launch, 'signal_panel', lambda _: signals.append(True))
    with pytest.raises(ValueError, match='完全退出'):
        desktop_launch.launch_desktop(tmp_path)
    assert signals == []
    monkeypatch.setattr(desktop_launch, 'current_panel_running', lambda *args: True)
    monkeypatch.setattr(desktop_launch, 'signal_panel', lambda _: True)
    assert desktop_launch.launch_desktop(tmp_path) is True
