"""SQLCipher 4 authenticated snapshot decoding. Never modifies the source."""

import hashlib
import hmac
import os
import struct
import uuid
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PAGE = 4096


def check_cancel(cancel):
    if cancel and cancel.is_set():
        raise InterruptedError("任务已取消")


def decode_page(data, key, mac_key, pgno):
    start = 16 if pgno == 1 else 0
    if len(data) != PAGE:
        raise ValueError("数据库页不完整")
    digest = hmac.new(
        mac_key, data[start:4032] + struct.pack("<I", pgno), "sha512"
    ).digest()
    if not hmac.compare_digest(digest, data[4032:]):
        raise ValueError(f"数据库第 {pgno} 页认证失败；密钥不匹配或数据已损坏")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(data[4016:4032])).decryptor()
    plain = decryptor.update(data[start:4016]) + decryptor.finalize()
    return (b"SQLite format 3\0" if pgno == 1 else b"") + plain + b"\0" * 80


def _checksum(data, endian, s1=0, s2=0):
    if len(data) % 8:
        raise ValueError("WAL 校验长度错误")
    for a, b in struct.iter_unpack(endian + "II", data):
        s1 = (s1 + a + s2) & 0xFFFFFFFF
        s2 = (s2 + b + s1) & 0xFFFFFFFF
    return s1, s2


def apply_wal(wal_path, output, key, mac_key, cancel=None):
    """Validate checksum chain and apply only frames through last commit."""
    if not wal_path.exists() or wal_path.stat().st_size == 0:
        return 0
    with wal_path.open("rb") as f:
        header = f.read(32)
        if len(header) != 32:
            raise ValueError("WAL 头不完整")
        magic, version, page_size = struct.unpack(">III", header[:12])
        if (
            magic not in (0x377F0682, 0x377F0683)
            or page_size != PAGE
            or version != 3007000
        ):
            raise ValueError("不支持的 WAL 格式")
        endian = "<" if magic == 0x377F0682 else ">"
        checksum = _checksum(header[:24], endian)
        if checksum != struct.unpack(">II", header[24:]):
            raise ValueError("WAL 头校验失败")
        frames = []
        last_commit = 0
        db_size = 0
        while True:
            check_cancel(cancel)
            offset = f.tell()
            fh = f.read(24)
            if not fh:
                break
            data = f.read(PAGE)
            if len(fh) != 24 or len(data) != PAGE:
                raise ValueError("WAL 帧不完整，请重试读取")
            if fh[8:16] != header[16:24]:
                break  # old WAL allocation after reset
            pgno, size = struct.unpack(">II", fh[:8])
            if not 0 < pgno < 10000000:
                raise ValueError("WAL 页号无效")
            checksum = _checksum(fh[:8] + data, endian, *checksum)
            if checksum != struct.unpack(">II", fh[16:]):
                raise ValueError("WAL 帧校验失败，请重试读取")
            frames.append((offset + 24, pgno))
            if size:
                last_commit = len(frames)
                db_size = size
        with output.open("r+b") as out:
            for offset, pgno in frames[:last_commit]:
                check_cancel(cancel)
                f.seek(offset)
                plain = decode_page(f.read(PAGE), key, mac_key, pgno)
                if pgno <= db_size:
                    out.seek((pgno - 1) * PAGE)
                    out.write(plain)
            if last_commit:
                out.truncate(db_size * PAGE)
    return last_commit


def decrypt_database(source, target, key, cancel=None, progress=None):
    source, target = Path(source), Path(target)
    if len(key) != 32:
        raise ValueError("数据库密钥长度无效")
    size = source.stat().st_size
    if size < PAGE or size % PAGE:
        raise ValueError("数据库长度不完整")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
    try:
        with source.open("rb") as f, temp.open("xb") as out:
            salt = f.read(16)
            f.seek(0)
            mac_key = hashlib.pbkdf2_hmac(
                "sha512", key, bytes(x ^ 0x3A for x in salt), 2, 32
            )
            for pgno in range(1, size // PAGE + 1):
                check_cancel(cancel)
                out.write(decode_page(f.read(PAGE), key, mac_key, pgno))
                if progress and pgno % 1024 == 0:
                    progress(pgno * PAGE, size)
        apply_wal(Path(str(source) + "-wal"), temp, key, mac_key, cancel)
        check_cancel(cancel)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
