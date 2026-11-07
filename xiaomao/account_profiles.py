"""Encrypted, account-bound route profiles owned by the current Windows user."""
from pathlib import Path
from typing import Callable
import hashlib
import json
import unicodedata

from .privacy import atomic_private_write, private_dir


_ENTROPY = b"xiaomao:account-profile:v1"
_MAX_VALUES_BYTES = 1024 * 1024


def _protect(data: bytes) -> bytes:
    import win32crypt
    return win32crypt.CryptProtectData(data, "Xiaomao account profile", _ENTROPY, None, None, 1)


def _unprotect(data: bytes) -> bytes:
    import win32crypt
    return win32crypt.CryptUnprotectData(data, _ENTROPY, None, None, 1)[1]


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("账户配置包含重复字段")
        value[key] = item
    return value


class AccountProfiles:
    """Store arbitrary route dictionaries; callers own validation and locking."""

    def __init__(self, runtime: Path, *, protect: Callable[[bytes], bytes] | None = None,
                 unprotect: Callable[[bytes], bytes] | None = None):
        self.directory = Path(runtime) / "account-profiles"
        self._protect = _protect if protect is None else protect
        self._unprotect = _unprotect if unprotect is None else unprotect

    def _path(self, account: str) -> Path:
        if (not isinstance(account, str) or not account.strip() or len(account) > 256 or
                any(character in "/\\" or unicodedata.category(character) == "Cc" for character in account)):
            raise ValueError("账户标识无效")
        try:
            encoded = account.encode("utf-8")
        except UnicodeError:
            raise ValueError("账户标识无效") from None
        return self.directory / (hashlib.sha256(encoded).hexdigest() + ".dpapi")

    @staticmethod
    def _values(values: dict) -> dict:
        if not isinstance(values, dict):
            raise ValueError("账户配置必须是 JSON 字典")
        try:
            serialized = json.dumps(values, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(serialized) > _MAX_VALUES_BYTES:
                raise ValueError()
            return json.loads(serialized)
        except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
            raise ValueError("账户配置无效或超过 1 MiB") from None

    def save(self, account: str, values: dict) -> None:
        path = self._path(account)
        snapshot = self._values(values)
        envelope = {"version": 1, "account": account, "values": snapshot}
        plain = json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        try:
            encrypted = self._protect(plain)
            if not isinstance(encrypted, bytes) or not encrypted:
                raise ValueError()
            private_dir(self.directory)
            atomic_private_write(path, encrypted)
        except Exception:
            raise ValueError("账户配置加密或保存失败") from None

    def load(self, account: str) -> dict | None:
        path = self._path(account)
        try:
            encrypted = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise ValueError("无法读取账户配置") from None
        try:
            envelope = json.loads(self._unprotect(encrypted).decode("utf-8"), object_pairs_hook=_unique_object)
            if (not isinstance(envelope, dict) or set(envelope) != {"version", "account", "values"} or
                    type(envelope["version"]) is not int or envelope["version"] != 1 or
                    envelope["account"] != account):
                raise ValueError()
            return self._values(envelope["values"])
        except Exception:
            raise ValueError("账户配置损坏、账户不匹配或无法解密") from None
