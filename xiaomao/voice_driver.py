"""Fetch the original VB-Audio cable installer with its publisher notice."""

from contextlib import contextmanager, nullcontext
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile
import zipfile

import httpx

from .privacy import atomic_private_write, private_dir


PACKAGE_NAME = 'VBCABLE_Driver_Pack46.zip'
PACKAGE_URL = f'https://download.vb-audio.com/Download_CABLE/{PACKAGE_NAME}'
PACKAGE_SIZE = 1_324_680
PACKAGE_SHA256 = 'bc44e8974191a5a2427d6c979e2dab08dbb09925f3f435dfa2f0ba3e45b6eb90'
INSTALLER_NAME = 'VBCABLE_Setup_x64.exe'


def _verified(payload):
    return len(payload) == PACKAGE_SIZE and hashlib.sha256(payload).hexdigest() == PACKAGE_SHA256


def _read_cache(path):
    try:
        with path.open('rb') as source:
            return source.read(PACKAGE_SIZE + 1)
    except FileNotFoundError:
        return b''


def _download(client, progress, check_active):
    connection = nullcontext(client) if client is not None else httpx.Client(timeout=25)
    payload = bytearray()
    try:
        with connection as session, session.stream('GET', PACKAGE_URL, follow_redirects=False) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                check_active()
                if len(payload) + len(chunk) > PACKAGE_SIZE:
                    raise ValueError('VB-CABLE 下载大小与已发布安装包不符')
                payload.extend(chunk)
                progress(len(payload), PACKAGE_SIZE)
    except httpx.HTTPError as error:
        raise ValueError('VB-CABLE 官方安装包下载失败，请重试') from error
    if not _verified(payload):
        raise ValueError('VB-CABLE 安装包校验失败，请重试')
    return bytes(payload)


def _extract_original(payload, destination):
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = {name.casefold() for name in archive.namelist()}
            required = {INSTALLER_NAME.casefold(), 'vbmmecable64_win10.inf', 'readme.txt'}
            if (len(names) != len(archive.infolist()) or not required <= names
                    or any(not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}', name)
                           for name in archive.namelist())):
                raise ValueError('VB-CABLE 安装包文件结构异常')
            archive.extractall(destination)
    except zipfile.BadZipFile as error:
        raise ValueError('VB-CABLE 安装包无法解压') from error


@contextmanager
def prepare_installer(cache_directory, *, client=None, progress=lambda current, total: None,
                      check_active=lambda: None):
    """Yield the unmodified vendor setup until its caller has finished running it."""
    cache = Path(cache_directory)
    archive = cache / PACKAGE_NAME
    payload = _read_cache(archive)
    from_cache = _verified(payload)
    check_active()
    if not from_cache:
        payload = _download(client, progress, check_active)
    # The setup window is independent. Keep its original files available even
    # if the panel process exits while the Windows installer is still open.
    temp = tempfile.TemporaryDirectory(prefix='shiguang-vb-cable-', delete=False)
    directory = Path(temp.name)
    try:
        _extract_original(payload, directory)
        check_active()
        if not from_cache:
            private_dir(cache)
            atomic_private_write(archive, payload)
        yield directory / INSTALLER_NAME
    finally:
        if directory.resolve().parent == Path(tempfile.gettempdir()).resolve():
            temp.cleanup()


def run_vendor_setup(installer):
    """Open the vendor's own install dialog and wait for its process to exit."""
    if os.name != 'nt':
        raise ValueError('VB-CABLE 安装仅支持 Windows')
    import pythoncom
    import pywintypes
    import win32con
    import win32event
    import win32process
    from win32com.shell import shell, shellcon

    pythoncom.CoInitialize()
    try:
        try:
            launched = shell.ShellExecuteEx(
                fMask=shellcon.SEE_MASK_NOCLOSEPROCESS | shellcon.SEE_MASK_FLAG_DDEWAIT,
                lpVerb='runas', lpFile=str(installer), lpDirectory=str(installer.parent),
                nShow=win32con.SW_SHOWNORMAL,
            )
        except pywintypes.error as error:
            if error.winerror == 1223:
                raise ValueError('已取消 VB-CABLE 的 Windows 安装授权') from None
            raise ValueError('无法打开 VB-CABLE 官方安装程序') from error
        process = launched.get('hProcess')
        if not process:
            raise ValueError('无法跟踪 VB-CABLE 官方安装程序')
        try:
            win32event.WaitForSingleObject(process, win32event.INFINITE)
            return win32process.GetExitCodeProcess(process)
        finally:
            process.Close()
    finally:
        pythoncom.CoUninitialize()
