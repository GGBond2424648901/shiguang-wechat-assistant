"""Account-contained, read-only media decoding; plaintext and keys stay in RAM.

The implementation below is original. Reverse-engineered format references:
https://github.com/yanyanbabyi/Wechat-Emoticon-Parser-fix (MIT, seed derivation)
https://github.com/fanyuantaier/wechatauto-replica/blob/main/wechatauto/media.py
(Apache-2.0, V2 layout); https://sarv.blog/posts/wxam/ (WXGF/HEVC framing).
No upstream exporter, process modification, or network download code is used.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import io
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import time
import warnings
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from PIL import Image, ImageOps


MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_SIDE = 1024
MAX_FRAMES = 32
V1 = b'\x07\x08V1\x08\x07'
V2 = b'\x07\x08V2\x08\x07'
MAGICS = (b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'GIF87a', b'GIF89a', b'RIFF', b'BM', b'II*\0', b'MM\0*')
FORMATS = {'PNG', 'JPEG', 'GIF', 'WEBP', 'BMP', 'TIFF'}
HEX = re.compile(r'[0-9a-fA-F]{32}')


def extract_cache_key(packed) -> str:
    """A message's bounded packed_info_data may contain one exact cache token."""
    if not isinstance(packed, bytes) or len(packed) > 65536:
        return ''
    tokens = {x.lower() for x in re.findall(rb'(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])', packed)}
    return tokens.pop().decode('ascii') if len(tokens) == 1 else ''


def _signature(data):
    return (data.startswith(MAGICS[:4] + MAGICS[5:])
            or data.startswith(b'RIFF') and data[8:12] == b'WEBP')


def _decrypt(data, key, mode, *, padded=True):
    cipher = Cipher(algorithms.AES(key), mode).decryptor()
    result = cipher.update(data) + cipher.finalize()
    if padded:
        unpad = PKCS7(128).unpadder()
        result = unpad.update(result) + unpad.finalize()
    return result


def _seed_valid(seed, account, data, kind):
    if not isinstance(seed, int) or not 0 <= seed < 2**32:
        return False
    try:
        if kind == 3 and data.startswith(V2) and len(data) >= 31:
            key = hashlib.md5((str(seed) + account).encode()).hexdigest()[:16].encode()
            plain = _decrypt(data[15:31], key, modes.ECB(), padded=False)
        elif kind == 47 and len(data) >= 16:
            key = hashlib.md5((str(seed) + account + 'EMOTICON').encode()).digest()
            plain = _decrypt(data[:16], key, modes.CBC(key), padded=False)
        else:
            return False
        return _signature(plain) or plain.startswith(b'wxgf')
    except ValueError:
        return False


def seed_from_chunks(chunks, account, data, kind, *, cancelled=lambda: False, deadline=None):
    """Verify bounded numeric seed candidates against this account's ciphertext."""
    seen = set()
    tail = b''
    for chunk in chunks:
        if cancelled() or deadline is not None and time.monotonic() >= deadline:
            return None
        for match in re.finditer(rb'(?<![0-9])[0-9]{5,10}(?![0-9])', tail + chunk):
            if cancelled() or deadline is not None and time.monotonic() >= deadline:
                return None
            value = int(match[0])
            if value in seen or value >= 2**32:
                continue
            seen.add(value)
            if len(seen) > 20000:
                return None
            if _seed_valid(value, account, data, kind):
                return value
        tail = chunk[-11:]
    return None


def _process_chunks(pid, deadline, cancelled):
    """Read only the explicitly bound Weixin PID, at most 64 MiB / 3 seconds."""
    if os.name != 'nt' or not isinstance(pid, int) or pid <= 0:
        return
    import ctypes
    import psutil
    from .dbcodec.windows_reader import kernel, MBI
    try:
        if psutil.Process(pid).name().casefold() != 'weixin.exe':
            return
    except psutil.Error:
        return
    handle = kernel.OpenProcess(0x410, False, pid)
    if not handle:
        return
    try:
        address = 0
        total = 0
        mbi = MBI()
        while address < 0x800000000000 and total < 64 * 1024 * 1024:
            if cancelled() or time.monotonic() >= deadline:
                return
            if not kernel.VirtualQueryEx(handle, address, ctypes.byref(mbi), ctypes.sizeof(mbi)):
                return
            base, size = mbi.BaseAddress or 0, mbi.RegionSize
            if base + size <= address:
                return
            address = base + size
            if mbi.State != 0x1000 or mbi.Protect & 0xff not in (2, 4, 8, 0x20, 0x40, 0x80) or mbi.Protect & 0x100:
                continue
            for offset in range(0, size, 2 * 1024 * 1024):
                if cancelled() or time.monotonic() >= deadline or total >= 64 * 1024 * 1024:
                    return
                length = min(size - offset, 2 * 1024 * 1024)
                buffer = ctypes.create_string_buffer(length)
                got = ctypes.c_size_t()
                kernel.ReadProcessMemory(handle, base + offset, buffer, length, ctypes.byref(got))
                total += length
                yield buffer.raw[:got.value]
    finally:
        kernel.CloseHandle(handle)


def _static_png(data):
    if not data or len(data) > MAX_INPUT_BYTES or not _signature(data):
        return None
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in FORMATS or source.width * source.height > MAX_PIXELS:
                return None
            for index in range(MAX_FRAMES):
                try:
                    source.seek(index)
                except EOFError:
                    return None
                if source.width * source.height > MAX_PIXELS:
                    return None
                frame = ImageOps.exif_transpose(source).convert('RGBA')
                if frame.getchannel('A').getbbox() is None:
                    continue
                frame.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
                # A clean image removes EXIF, comments, profiles and animation data.
                mode = 'RGB' if frame.getchannel('A').getextrema() == (255, 255) else 'RGBA'
                clean = Image.new(mode, frame.size)
                clean.paste(frame.convert(mode))
                for _ in range(8):
                    output = io.BytesIO()
                    clean.save(output, format='PNG')
                    if output.tell() <= MAX_OUTPUT_BYTES:
                        return output.getvalue()
                    clean = clean.resize((max(1, clean.width * 3 // 4), max(1, clean.height * 3 // 4)), Image.Resampling.LANCZOS)
    return None


def _wxgf_frame(data):
    """Decode one color frame in a hidden, time-limited, pipe-only process."""
    if len(data) < 12 or len(data) > MAX_INPUT_BYTES or not data.startswith(b'wxgf'):
        return None
    width, height = struct.unpack('>HH', data[7:11])
    if not width or not height or width * height > MAX_PIXELS:
        return None
    if not 11 <= data[4] < len(data):
        return None
    partitions = []
    # Each partition is preceded by its big-endian byte length. A VPS start
    # identifies an independently decodable stream; internal NALs are excluded.
    for match in re.finditer(rb'\x00\x00\x00?\x01\x40\x01', data[data[4]:]):
        start = data[4] + match.start()
        length = int.from_bytes(data[start - 4:start], 'big')
        if length >= 6 and start + length <= len(data):
            partitions.append((start, length))
            if len(partitions) == 4:
                break
    if not partitions:
        return None
    try:
        import imageio_ffmpeg
        # No filename, URL, shell, or argument comes from the XML. HEVC cannot
        # fetch external resources, and the protocol whitelist is pipe only.
        command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
                   '-max_alloc', '67108864', '-protocol_whitelist', 'pipe',
                   '-threads', '1', '-f', 'hevc', '-i', 'pipe:0', '-an',
                   '-vf', "scale=w='min(1024,iw)':h='min(1024,ih)':force_original_aspect_ratio=decrease",
                   '-frames:v', '1', '-threads', '1', '-c:v', 'png', '-f', 'image2pipe', 'pipe:1']
        deadline = time.monotonic() + 5
        mask = None
        for start, length in partitions:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            result = subprocess.run(command, input=data[start:start + length], stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=remaining,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            # Fixed single frame and scale bound stdout to one <=1024px raster.
            if result.returncode or not 0 < len(result.stdout) <= 5 * 1024 * 1024:
                continue
            with Image.open(io.BytesIO(result.stdout)) as frame:
                if frame.mode in ('L', 'I', 'I;16') and len(partitions) > 1:
                    mask = frame.convert('L')
                    continue
                if mask is not None:
                    # WXGF animation pairs grayscale alpha then color. Never
                    # mistake the all-white alpha plane for a useful picture.
                    if mask.size != frame.size or mask.getbbox() is None:
                        mask = None
                        continue
                    colored = frame.convert('RGBA')
                    colored.putalpha(mask)
                    output = io.BytesIO()
                    colored.save(output, format='PNG')
                    return _static_png(output.getvalue())
                return _static_png(result.stdout)
    except (ImportError, OSError, RuntimeError, subprocess.TimeoutExpired):
        pass
    return None


class LocalMediaResolver:
    """Resolve only exact message references; missing/invalid media returns None.

    Caller must bind account_root/process_id/account_id to the active account.
    cache_key comes from that message's packed_info_data, never a guessed filename.
    XML paths, CDN URLs and auth material are intentionally never followed.
    """

    def __init__(self, account_root, *, process_id=None, account_id='', cancelled=None, seed_provider=None):
        self.root = Path(account_root).absolute()
        self.process_id = process_id
        self.account_id = account_id
        self.cancelled = cancelled or (lambda: False)
        self._seed_provider = seed_provider
        self._seed = None
        self._next_probe = 0.0

    def _safe_path(self, path):
        path.relative_to(self.root)
        # Check every ancestor, including root itself and its parents. Reject
        # Windows junctions/reparse points as well as ordinary symbolic links.
        for current in (path, *path.parents):
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('unsafe-media-path')
        path.resolve(strict=True).relative_to(self.root.resolve(strict=True))
        return path

    def _read(self, path):
        self._safe_path(path)
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_INPUT_BYTES:
            return None
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_ino, opened.st_dev) != (before.st_ino, before.st_dev):
                return None
            data = stream.read(MAX_INPUT_BYTES + 1)
        self._safe_path(path)
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            return None
        return data if len(data) <= MAX_INPUT_BYTES else None

    def _get_seed(self, data, kind):
        if not self.account_id or self.cancelled():
            return None
        if self._seed is not None and _seed_valid(self._seed, self.account_id, data, kind):
            return self._seed
        if time.monotonic() < self._next_probe:
            return None
        self._next_probe = time.monotonic() + 30
        if self._seed_provider:
            candidate = self._seed_provider(data, kind)
        else:
            deadline = time.monotonic() + 3
            chunks = _process_chunks(self.process_id, deadline, self.cancelled)
            try:
                candidate = seed_from_chunks(chunks, self.account_id, data, kind, cancelled=self.cancelled, deadline=deadline)
            finally:
                chunks.close()
        if _seed_valid(candidate, self.account_id, data, kind):
            self._seed = candidate
            return candidate
        return None

    def _plain(self, data, kind):
        if _signature(data) or data.startswith(b'wxgf'):
            return data
        if data.startswith((V1, V2)):
            if len(data) < 31:
                return None
            aes_length, xor_length = struct.unpack('<II', data[6:14])
            padded_length = aes_length + 16 - aes_length % 16 if aes_length else 0
            if aes_length > MAX_INPUT_BYTES or 15 + padded_length + xor_length > len(data):
                return None
            if data.startswith(V2):
                seed = self._get_seed(data, 3)
                if seed is None:
                    return None
                key = hashlib.md5((str(seed) + self.account_id).encode()).hexdigest()[:16].encode()
                xor = seed & 255
            else:
                key = b'cfcd208495d565ef'
                xor = data[-2] ^ 255 if xor_length >= 2 and data[-1] ^ (data[-2] ^ 255) == 217 else None
                if xor_length and xor is None:
                    return None
            prefix = _decrypt(data[15:15 + padded_length], key, modes.ECB()) if padded_length else b''
            if len(prefix) != aes_length:
                return None
            tail_start = len(data) - xor_length
            return prefix + data[15 + padded_length:tail_start] + bytes(x ^ xor for x in data[tail_start:])
        for magic in MAGICS:
            if len(data) >= len(magic):
                xor = data[0] ^ magic[0]
                if all(a ^ xor == b for a, b in zip(data, magic)):
                    return data.translate(bytes(x ^ xor for x in range(256)))
        if kind == 47 and len(data) % 16 == 0:
            seed = self._get_seed(data, 47)
            if seed is not None:
                key = hashlib.md5((str(seed) + self.account_id + 'EMOTICON').encode()).digest()
                return _decrypt(data, key, modes.CBC(key))
        return None

    def _paths(self, node, kind, group, month, cache_key):
        digest = node.get('md5', '')
        if not HEX.fullmatch(digest):
            return
        digest = digest.lower()
        if kind == 3:
            keys = [cache_key.lower()] if HEX.fullmatch(cache_key) else []
            keys = list(dict.fromkeys([*keys, digest]))
            folder = self.root / 'msg/attach' / hashlib.md5(group.encode()).hexdigest() / month / 'Img'
            for key in keys:
                for variant in ('', '_h', '_t'):
                    for extension in ('.dat', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'):
                        yield folder / (key + variant + extension)
        else:
            # Cache reuse may refer to an older month; enumerate only these
            # bounded month directory names, then look up the exact digest.
            months = [month]
            cache = self.root / 'cache'
            try:
                self._safe_path(cache)
                with os.scandir(cache) as entries:
                    for index, item in enumerate(entries):
                        if index >= 120:
                            break
                        if re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])', item.name):
                            months.append(item.name)
            except (OSError, ValueError):
                pass
            for item in list(dict.fromkeys(months))[:25]:
                for suffix in ('', '.thumb', '.dat', '.gif', '.png', '.webp'):
                    yield cache / item / 'Emoticon' / digest[:2] / (digest + suffix)
            for folder in ('Persist', 'Thumb', 'PersistStore', 'ThumbStore'):
                for suffix in ('', '.thumb'):
                    yield self.root / 'business/emoticon' / folder / (digest + suffix)
                    yield self.root / 'business/emoticon' / folder / digest[:2] / (digest + suffix)

    def resolve(self, raw: str, kind: int, group_id: str, created_at: float, *, cache_key='') -> bytes | None:
        if kind not in (3, 47) or not isinstance(raw, str) or len(raw) > 65536:
            return None
        if not isinstance(group_id, str) or not 0 < len(group_id) <= 256 or self.cancelled():
            return None
        if '<!DOCTYPE' in raw.upper() or '<!ENTITY' in raw.upper():
            return None
        try:
            start = raw.find('<msg')
            if start < 0:
                return None
            root = ET.fromstring(raw[start:])
            nodes = root.findall('img' if kind == 3 else 'emoji')
            if kind==47 and not nodes:
                from .stickers import media_md5
                digest=media_md5(raw[start:])
                if digest:nodes=[ET.Element('emoji',{'md5':digest})]
            if root.tag != 'msg' or len(nodes) != 1:
                return None
            month = datetime.fromtimestamp(created_at).strftime('%Y-%m')
            for path in self._paths(nodes[0], kind, group_id, month, cache_key):
                if self.cancelled():
                    return None
                try:
                    data = self._read(path)
                    plain = self._plain(data, kind) if data else None
                    result = _wxgf_frame(plain) if plain and plain.startswith(b'wxgf') else _static_png(plain)
                    if result:
                        return result
                except (OSError, ValueError, SyntaxError, RuntimeError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                    continue
        except (ET.ParseError, ValueError, OSError, OverflowError, TypeError):
            return None
        return None
