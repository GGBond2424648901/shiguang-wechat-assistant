"""Download and silently install the reviewed Fabric release."""

import hashlib
from pathlib import Path
import subprocess

import httpx

from .privacy import atomic_private_write, private_dir
from .voice_fabric import FABRIC_VERSION, FabricError


SETUP_NAME = f'GPT-SoVITS-Fabric-Setup-{FABRIC_VERSION}.exe'
SETUP_URL = f'https://github.com/Turing-Mirror/GPT-SoVITS-Fabric/releases/download/v{FABRIC_VERSION}/{SETUP_NAME}'
SETUP_SIZE = 26_217_157
SETUP_SHA256 = 'c44bbe50785f244cd35e4ae12086697326ddcaf7f7b1ac6b86797adc713055b0'


def _verified(data):
    return len(data) == SETUP_SIZE and hashlib.sha256(data).hexdigest() == SETUP_SHA256


def prepare_fabric_setup(directory, *, progress, check_active):
    cache = private_dir(directory)
    installer = Path(cache) / SETUP_NAME
    try:
        with installer.open('rb') as source:
            payload = source.read(SETUP_SIZE + 1)
    except FileNotFoundError:
        payload = b''
    if _verified(payload):
        return installer

    payload = bytearray()
    try:
        with httpx.stream('GET', SETUP_URL, timeout=35, follow_redirects=True) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(128 * 1024):
                check_active()
                if len(payload) + len(chunk) > SETUP_SIZE:
                    raise FabricError('Fabric 安装包大小与官方发布不符')
                payload.extend(chunk)
                progress(len(payload), SETUP_SIZE)
    except httpx.HTTPError:
        raise FabricError('暂时无法下载 Fabric 官方安装包，请重试') from None
    if not _verified(payload):
        raise FabricError('Fabric 安装包未通过官方 SHA-256 校验')
    check_active()
    atomic_private_write(installer, bytes(payload))
    return installer


def run_fabric_setup(installer, destination):
    # NSIS reads the complete trailing /D= value, including spaces, without quotes.
    command = f'{subprocess.list2cmdline([str(installer)])} /S /D={destination}'
    try:
        return subprocess.run(command, executable=str(installer), cwd=installer.parent,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              check=False).returncode
    except OSError:
        raise FabricError('无法启动 Fabric 的后台安装') from None
