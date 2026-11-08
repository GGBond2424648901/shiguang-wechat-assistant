"""Launch the installed Electron panel from the existing VBS and shortcut."""

import os
from pathlib import Path
import subprocess
import sys

from .panel_entry import signal_panel, _panel_present, _identity
from .config import configuration_lock
from .privacy import atomic_private_write, private_dir


def current_panel_running(root, executable, target):
    """Only reuse the intended build, never a tray panel left by an old build."""
    import psutil
    def path_key(value):
        return str(Path(value).resolve()).casefold()
    expected_root, expected_executable = path_key(root), path_key(executable)
    for process in psutil.process_iter(['exe', 'cmdline']):
        try:
            info = process.info
            if not info.get('exe') or path_key(info['exe']) != expected_executable:
                continue
            args = info.get('cmdline') or []
            if not any(arg.startswith('--profile=') and path_key(arg[10:]) == expected_root for arg in args):
                continue
            if target and not any(path_key(arg) == path_key(target[0]) for arg in args[1:] if not arg.startswith('-')):
                continue
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
            continue
    return False


def launch_desktop(root, *, force_legacy=False):
    if force_legacy or os.name != 'nt':
        return False
    frozen = getattr(sys, 'frozen', False)
    if frozen:
        project = Path(sys.executable).parent
        desktop = project / 'desktop'
        executable = desktop / 'ShiguangDesktop.exe'
        entry = desktop / 'resources/app/dist/index.html'
        target = []
    else:
        project = Path(__file__).resolve().parent.parent
        desktop = project / 'desktop'
        configured = os.environ.get('SHIGUANG_ELECTRON')
        executable = Path(configured) if configured else desktop / 'node_modules/electron/dist/electron.exe'
        entry = desktop / 'dist/index.html'
        target = [str(desktop)]
    if not executable.is_file() or not entry.is_file():
        raise ValueError('新版界面文件不完整，请重新安装或构建 desktop；只有显式选择旧版入口才会打开旧界面')
    if _panel_present('Local\\Shiguang.Panel.' + _identity(root)):
        if not current_panel_running(root, executable, target):
            raise ValueError('另一位置或旧版的助手界面仍在运行，请在该界面选择“完全退出”后再打开新版；已保存的设置不会改变')
        if signal_panel(root):
            return True
    arguments = [str(executable.resolve()), *target, '--profile=' + str(Path(root).resolve()), '--writable', '--managed']
    from .launch import in_job, _launch_wmi
    if in_job():
        _launch_wmi(arguments, project)
        return True
    try:
        env = dict(os.environ)
        env.pop('ELECTRON_RUN_AS_NODE', None)
        subprocess.Popen(arguments, cwd=project, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, env=env,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    except PermissionError:
        _launch_wmi(arguments, project)
    return True


def snapshot_upgrade_configuration(root):
    """Save one local snapshot before this version can change any preferences."""
    root = Path(root)
    settings = root / 'runtime/config.json'
    sources = [settings, root / 'config/persona.md', root / 'config/persona_modules.json',
               root / 'config/persona_documents.json']
    if not any(path.is_file() for path in sources):
        return
    runtime = private_dir(root / 'runtime')
    with configuration_lock(runtime):
        folder = private_dir(runtime / 'upgrade-v030')
        if (folder / 'complete').exists():
            return
        for source in sources:
            if source.is_file():
                target = folder / source.name
                atomic_private_write(target, source.read_bytes())
        atomic_private_write(folder / 'complete', b'v0.3.0\n')
