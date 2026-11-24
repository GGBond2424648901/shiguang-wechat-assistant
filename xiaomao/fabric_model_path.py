"""Keep Fabric's native character layout while choosing a model volume on Windows."""

from pathlib import Path
import struct
from uuid import uuid4

from .voice_fabric import FabricError

_OPEN_REPARSE_POINT = 0x00200000
_SET_REPARSE_POINT = 0x000900A4
_MOUNT_POINT_TAG = 0xA0000003


def default_model_path(root):
    current, legacy = root / 'User_Data', root / 'UserData'
    return (legacy if not current.is_dir() and legacy.is_dir() else current) / 'characters'


def installed_role_directory(root, role_id):
    if (not isinstance(role_id, str) or not 0 < len(role_id) <= 240 or role_id.endswith(('.', ' ')) or
            any(character in role_id for character in '<>:"/\\|?*\0')):
        return None
    try:
        directory = default_model_path(root).resolve(strict=True) / role_id
        config = directory / 'config.json'
        resolved = directory.resolve(strict=True)
        if (resolved == directory and resolved.name == role_id and config.resolve(strict=True) == config and
                config.is_file()):
            return directory
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def _mount_point(link, target):
    import pywintypes
    import win32con
    import win32file

    substitute = ('\\??\\' + str(target)).encode('utf-16-le')
    display = str(target).encode('utf-16-le')
    names = substitute + b'\0\0' + display + b'\0\0'
    payload = struct.pack('<I6H', _MOUNT_POINT_TAG, len(names) + 8, 0, 0, len(substitute),
                          len(substitute) + 2, len(display)) + names
    link.mkdir()
    try:
        handle = win32file.CreateFile(str(link), win32con.GENERIC_WRITE, 0, None, win32con.OPEN_EXISTING,
                                      win32con.FILE_FLAG_BACKUP_SEMANTICS | _OPEN_REPARSE_POINT, None)
        try:
            win32file.DeviceIoControl(handle, _SET_REPARSE_POINT, payload, 0, None)
        finally:
            handle.Close()
    except pywintypes.error as error:
        link.rmdir()
        raise OSError(error.winerror, error.strerror) from None


def choose_model_path(root, destination, *, mount=_mount_point):
    """Switch libraries without overwriting the contents of either directory."""
    source = default_model_path(root)
    backup = source.with_name('characters.shiguang-default')
    if not isinstance(destination, str) or any(char in destination for char in '\0\n\r'):
        raise FabricError('请选择有效的模型目录')
    target = None
    if destination:
        candidate = Path(destination)
        if not candidate.is_absolute():
            raise FabricError('模型目录须填写本机绝对路径')
        if candidate == source:
            destination = ''
        else:
            target = candidate.resolve()
            if (not target.is_dir() or str(target).startswith('\\\\') or
                    any(target.is_relative_to(path) or path.is_relative_to(target) for path in (source, backup))):
                raise FabricError('请选择现有的本机文件夹，且与原模型目录相互独立')

    source.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        raise FabricError('当前 Fabric 模型目录是外部符号链接，请先核对安装目录')
    is_junction = source.is_junction()
    if not source.exists() and not is_junction and backup.is_dir():
        backup.rename(source)
    if is_junction and target and source.resolve() == target:
        return target
    if not is_junction and not target:
        source.mkdir(exist_ok=True)
        return source

    suffix = uuid4().hex
    staged = source.with_name(f'.shiguang-characters-{suffix}')
    prior = source.with_name(f'.shiguang-prior-{suffix}') if is_junction else backup
    moved = False
    try:
        if target:
            mount(staged, target)
        if source.exists() or is_junction:
            if prior.exists() or prior.is_junction():
                raise FabricError('检测到原有模型备份，请先核对模型目录')
            source.rename(prior)
            moved = True
        if target:
            staged.rename(source)
        elif backup.is_dir():
            backup.rename(source)
        else:
            source.mkdir()
    except (OSError, ValueError) as error:
        if moved and not source.exists() and not source.is_junction():
            prior.rename(source)
        if staged.is_junction():
            staged.rmdir()
        if isinstance(error, FabricError):
            raise
        raise FabricError('无法切换模型目录，请检查磁盘类型和文件夹权限') from None
    if is_junction:
        prior.rmdir()
    return target or source
