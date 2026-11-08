"""Keep installed application files separate from the user's settings and data."""
import os
import json
from pathlib import Path
import sys


def _local_data():
    local = os.environ.get('LOCALAPPDATA')
    return Path(local) if local else Path.home() / 'AppData' / 'Local'


def remember_profile(root):
    """An explicit launcher opt-in; records a path, never copies or resets data."""
    from .privacy import atomic_private_write, private_dir
    root = Path(root).resolve()
    if not (root / 'runtime/config.json').is_file():
        raise ValueError('原数据目录没有配置文件，未更换安装版的数据目录')
    folder = private_dir(_local_data() / 'ShiguangLauncher')
    atomic_private_write(folder / 'profile.json', json.dumps(
        {'version': 1, 'root': str(root)}, ensure_ascii=False).encode('utf-8'))


def default_root() -> Path:
    source = Path(__file__).resolve().parent.parent
    if not getattr(sys, "frozen", False) and (source / "pyproject.toml").is_file():
        return source
    pointer = _local_data() / 'ShiguangLauncher/profile.json'
    if pointer.exists():
        try:
            data = json.loads(pointer.read_text('utf-8'))
            root = Path(data['root'])
            if data.get('version') != 1 or not root.is_absolute() or not (root / 'runtime/config.json').is_file():
                raise ValueError()
            return root.resolve()
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError('已保存的数据目录暂不可用，请恢复原目录或从原 VBS 启动；不会创建空白配置') from None
    return _local_data() / 'XiaomaoAssistant'
