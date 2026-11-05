"""当前 Windows 用户范围的 DPAPI 凭据。密钥和顺序作为一个密文原子保存。"""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os

from .privacy import atomic_private_write, private_dir


class CredentialError(RuntimeError):
    pass


class CredentialStore:
    ENTROPY = b"xiaomao:deepseek:credential:v1"
    MAX_KEYS = 8

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / "deepseek.dpapi"

    @classmethod
    def _validated(cls, values):
        if isinstance(values, (str, bytes)) or not isinstance(values, (tuple, list)):
            raise CredentialError("请按行输入 DeepSeek 密钥")
        keys = []
        for value in values:
            if not isinstance(value, str):
                raise CredentialError("密钥格式无效")
            value = value.strip()
            if (not value.startswith("sk-") or not 12 <= len(value) <= 256 or
                    not value.isascii() or not value.isprintable() or any(c.isspace() for c in value)):
                raise CredentialError("密钥格式无效")
            if value not in keys:
                keys.append(value)
        if not 1 <= len(keys) <= cls.MAX_KEYS:
            raise CredentialError("请保存 1 至 8 个不同的 DeepSeek 密钥")
        return tuple(keys)

    def get_keys(self) -> tuple[str,...]:
        if os.name != "nt" or not self.path.exists():
            raise CredentialError("尚未设置 DeepSeek 加密凭据")
        import win32crypt
        try:
            _, plain = win32crypt.CryptUnprotectData(self.path.read_bytes(), self.ENTROPY, None, None, 1)
            text = plain.decode("utf-8")
            # A v1 file was a single DPAPI-protected string. Keep it readable
            # without side effects; the next edit writes the versioned format.
            if text.startswith('sk-'):
                return self._validated([text])
            data = json.loads(text)
            if data['version'] != 2:
                raise ValueError()
            return self._validated(data['keys'])
        except Exception:
            raise CredentialError("无法解密凭据，请在原 Windows 用户下重新设置") from None

    def get_key(self) -> str:
        return self.get_keys()[0]

    @contextmanager
    def _edit(self):
        if os.name != 'nt':
            raise CredentialError("凭据加密需要 Windows DPAPI")
        import win32event
        digest = hashlib.sha256(os.path.normcase(str(self.path.resolve())).encode()).hexdigest()
        mutex = win32event.CreateMutex(None, False, 'Local\\xiaomao-deepseek-'+digest)
        try:
            result = win32event.WaitForSingleObject(mutex, 3000)
            if result not in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED):
                raise CredentialError("密钥正在由另一个操作更新，请稍后重试")
            try:
                yield
            finally:
                win32event.ReleaseMutex(mutex)
        finally:
            mutex.Close()

    def _store(self, values):
        keys = self._validated(values)
        import win32crypt
        try:
            private_dir(self.directory)
            plain = json.dumps({'version':2,'keys':keys}, separators=(',',':')).encode()
            encrypted = win32crypt.CryptProtectData(plain, "Xiaomao DeepSeek", self.ENTROPY, None, None, 1)
            atomic_private_write(self.path, encrypted)
        except Exception:
            raise CredentialError("Windows 凭据加密或保存失败") from None

    def set_keys(self, values) -> None:
        """Explicit full replacement; also lets a user repair unreadable ciphertext."""
        with self._edit():
            self._store(values)

    def set_key(self, value: str) -> None:
        """Legacy primary-key setter. Leave any configured backups in place."""
        with self._edit():
            existing = self.get_keys() if self.path.exists() else ()
            self._store((value, *existing[1:]))

    def add_keys(self, values) -> None:
        additions = self._validated(values)
        with self._edit():
            existing = self.get_keys() if self.path.exists() else ()
            self._store((*existing, *additions))

    @property
    def count(self) -> int:
        return len(self.get_keys()) if self.path.exists() else 0

    @property
    def available(self) -> bool:
        try:
            return bool(self.count)
        except CredentialError:
            return False
