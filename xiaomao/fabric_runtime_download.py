"""Resume an official Fabric runtime download before the native installer opens its archive."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from time import monotonic, sleep
from urllib.parse import urlsplit

import httpx

from .fabric_model_path import default_model_path
from .voice_fabric import FabricError


_REPOSITORY = 'https://cnb.cool/Turing-Mirror/GPT-SoVITS-Fabric-Releases/'
_INDEX = _REPOSITORY + '-/git/raw/main/index.json'
_RELEASES = _REPOSITORY + '-/releases/download/'
_CHUNK = 16 * 1024 * 1024


def _spec(index, variant):
    try:
        record, = index['runtimes'][variant]['parts']
        name, sha, size = record['name'], record['sha256'], record['size_bytes']
        url = next(url for url in record['urls'] if isinstance(url, str) and url.startswith(_RELEASES))
        if (variant not in ('nvidia', 'cpu') or not re.fullmatch(rf'runtime-{variant}-[0-9a-zA-Z_.-]+\.tar\.gz', name)
                or type(size) is not int or not _CHUNK <= size <= 16 * 1024**3
                or not re.fullmatch(r'[0-9a-f]{64}', sha) or urlsplit(url).path.rsplit('/', 1)[-1] != name):
            raise ValueError()
        return name, sha, size, url
    except (AttributeError, KeyError, StopIteration, TypeError, ValueError):
        raise FabricError('Fabric 运行环境清单格式无效') from None


def _fetch_chunk(client, url, directory, index, size, check_active):
    first = index * _CHUNK
    last = min(first + _CHUNK, size) - 1
    expected = last - first + 1
    target = directory / f'{index:05}.part'
    if target.is_file() and target.stat().st_size == expected:
        return expected
    for attempt in range(5):
        check_active()
        temporary = None
        try:
            with client.stream('GET', url, headers={'Range': f'bytes={first}-{last}'}) as response:
                response.raise_for_status()
                if (response.url.scheme != 'https' or response.url.host not in ('cnb.cool', 'asset.cnb.cool') or
                        response.status_code != 206 or response.headers.get('Content-Range') != f'bytes {first}-{last}/{size}'):
                    raise FabricError('Fabric 发布源未返回预期的文件区间')
                with NamedTemporaryFile(mode='wb', dir=directory, suffix='.download', delete=False) as output:
                    temporary = Path(output.name)
                    for data in response.iter_bytes(256 * 1024):
                        check_active()
                        if output.tell() + len(data) > expected:
                            raise FabricError('Fabric 发布源返回的文件区间过大')
                        output.write(data)
            if temporary.stat().st_size != expected:
                raise FabricError('Fabric 发布源返回的文件区间不完整')
            temporary.replace(target)
            return expected
        except (httpx.HTTPError, FabricError):
            if attempt == 4:
                raise FabricError('Fabric 运行环境网络下载中断') from None
            deadline = monotonic() + min(16, 2 ** (attempt + 1))
            while monotonic() < deadline:
                check_active()
                sleep(0.25)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)


def cache_official_runtime(root, variant, *, check_active, progress):
    runtime = default_model_path(root).parent / 'update_cache' / 'runtime'
    with httpx.Client(timeout=20, follow_redirects=True, headers={'Accept-Encoding': 'identity'}) as client:
        check_active()
        response = client.get(_INDEX)
        response.raise_for_status()
        name, sha, size, source = _spec(response.json(), variant)
        archive = runtime / name
        if archive.is_file() and archive.stat().st_size == size:
            # Fabric verifies an existing native cache before extraction.
            return
        runtime.mkdir(parents=True, exist_ok=True)
        segments = runtime / f'.shiguang-{sha[:16]}'
        segments.mkdir(exist_ok=True)
        count = (size + _CHUNK - 1) // _CHUNK
        check_active()
        with client.stream('GET', source, headers={'Range': 'bytes=0-65535'}) as sample:
            if sample.status_code == 206 and sample.url.scheme == 'https' and sample.url.host == 'asset.cnb.cool':
                source = str(sample.url)
        done = 0
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix='fabric-download') as executor:
            parts = [executor.submit(_fetch_chunk, client, source, segments, index, size, check_active)
                     for index in range(count)]
            try:
                for part in as_completed(parts):
                    done += part.result()
                    progress(done, size)
            except BaseException:
                for part in parts:
                    part.cancel()
                raise
        temporary = None
        try:
            digest = hashlib.sha256()
            with NamedTemporaryFile(mode='wb', dir=runtime, suffix='.verify', delete=False) as merged:
                temporary = Path(merged.name)
                for index in range(count):
                    check_active()
                    with (segments / f'{index:05}.part').open('rb') as part:
                        while block := part.read(1024 * 1024):
                            digest.update(block)
                            merged.write(block)
                if merged.tell() != size or digest.hexdigest() != sha:
                    for index in range(count):
                        (segments / f'{index:05}.part').unlink(missing_ok=True)
                    raise FabricError('Fabric 运行环境未通过官方 SHA-256 校验')
            check_active()
            temporary.replace(archive)
            for index in range(count):
                (segments / f'{index:05}.part').unlink(missing_ok=True)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
