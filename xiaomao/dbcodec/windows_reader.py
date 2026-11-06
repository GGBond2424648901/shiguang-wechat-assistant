"""Current-user, read-only Weixin memory access; no injection or restarts."""

import ctypes
from ctypes import wintypes as wt
import re
import time
from . import config_scan
from .crypto import check_cancel
from .discovery import wechat_processes

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel.OpenProcess.restype = wt.HANDLE
kernel.CloseHandle.argtypes = [wt.HANDLE]
kernel.CloseHandle.restype = wt.BOOL
kernel.ReadProcessMemory.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel.ReadProcessMemory.restype = wt.BOOL
kernel.VirtualQueryEx.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
]
kernel.VirtualQueryEx.restype = ctypes.c_size_t


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("PartitionId", wt.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


class Blob(ctypes.Structure):
    _fields_ = [("size", wt.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data, decrypt=False):
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    raw = ctypes.create_string_buffer(data)
    src = Blob(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte)))
    dst = Blob()
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.POINTER(Blob),
    ]
    fn.restype = wt.BOOL
    if not fn(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(dst.data)


def protect(data):
    return _dpapi(data)


def unprotect(data):
    return _dpapi(data, True)


def find_keys(files, cancel, progress, timeout=150, processes=None):
    db_files = []
    salts = {}
    for path in files:
        with path.open("rb") as f:
            page = f.read(4096)
        if page[:16] == b"SQLite format 3\0":
            continue
        if len(page) != 4096:
            raise ValueError("数据库页不完整")
        salt = page[:16].hex()
        rel = path.name
        db_files.append((rel, str(path), path.stat().st_size, salt, page))
        salts.setdefault(salt, []).append(rel)
    if not db_files:
        return {}
    processes = wechat_processes() if processes is None else processes
    if not processes:
        raise RuntimeError("请先启动并登录电脑版微信，再点击重新读取")
    deadline = time.monotonic() + timeout
    keys = {}
    remaining = set(salts)
    accessible = False
    for process in processes:
        check_cancel(cancel)
        handle = kernel.OpenProcess(0x0010 | 0x0400, False, process["pid"])
        if not handle:
            continue
        accessible = True
        try:
            regions = []
            address = 0
            mbi = MBI()
            while address < 0x0000800000000000:
                check_cancel(cancel)
                if not kernel.VirtualQueryEx(
                    handle, address, ctypes.byref(mbi), ctypes.sizeof(mbi)
                ):
                    break
                base = mbi.BaseAddress or 0
                size = mbi.RegionSize
                if (
                    mbi.State == 0x1000
                    and mbi.Protect & 0xFF in (2, 4, 8, 0x20, 0x40, 0x80)
                    and not mbi.Protect & 0x100
                ):
                    regions.append((base, size))
                if base + size <= address:
                    break
                address = base + size
            reads = 0

            def read(addr, size):
                nonlocal reads
                check_cancel(cancel)
                if time.monotonic() > deadline:
                    raise TimeoutError("读取超时；请确认当前登录的账号与选定账号一致")
                buf = ctypes.create_string_buffer(size)
                got = ctypes.c_size_t()
                kernel.ReadProcessMemory(handle, addr, buf, size, ctypes.byref(got))
                reads += 1
                if reads % 100 == 0:
                    progress("正在读取已登录微信的本地数据库信息", None)
                return buf.raw[: got.value]

            config_scan._scan_windows_v411_config_cipher(
                process["pid"],
                regions,
                read,
                read,
                db_files,
                salts,
                keys,
                remaining,
                lambda *a: None,
            )
            if remaining:
                pattern = re.compile(rb"[xX]'([0-9a-fA-F]{64,192})'")
                for _, data in config_scan._iter_windows_region_chunks(
                    regions, read, overlap=200
                ):
                    for match in pattern.finditer(data):
                        run = match[1].decode().lower()
                        config_scan._verify_windows_direct_key_candidate(
                            run[:64],
                            run[64:96] or None,
                            db_files,
                            salts,
                            keys,
                            remaining,
                        )
                    if not remaining:
                        break
        finally:
            kernel.CloseHandle(handle)
        if not remaining:
            break
    if remaining:
        if not accessible:
            raise PermissionError(
                "无法读取微信进程：请让拾光微信聊天助手与微信在相同 Windows 用户及权限下运行"
            )
        raise RuntimeError(
            f"仍有 {len(remaining)} 个数据库无法读取。请确认选中了当前登录账号；此微信版本也可能尚不兼容。"
        )
    return keys
