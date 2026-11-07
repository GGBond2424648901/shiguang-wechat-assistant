"""Launch the installed Electron panel from the existing VBS and shortcut."""

import os
from pathlib import Path
import subprocess
import sys

from .panel_entry import signal_panel
from .config import configuration_lock
from .privacy import atomic_private_write, private_dir


def launch_desktop(root, *, force_legacy=False):
    if force_legacy or os.name != 'nt':
        return False
    if signal_panel(root):
        return True
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
        return False
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
