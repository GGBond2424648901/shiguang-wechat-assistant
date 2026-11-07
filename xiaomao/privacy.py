"""运行时文件权限；不涉及微信进程或系统安全设置。"""
import os
from pathlib import Path


def make_private(path: Path) -> None:
    if os.name == "nt":
        import win32api
        import win32con
        import win32security
        import ntsecuritycon
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            acl = win32security.ACL()
            flags = (win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE) if path.is_dir() else 0
            acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, flags, ntsecuritycon.FILE_ALL_ACCESS, sid)
            descriptor = win32security.SECURITY_DESCRIPTOR()
            descriptor.SetSecurityDescriptorDacl(True, acl, False)
            win32security.SetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION, descriptor)
        finally:
            token.Close()
    else:
        path.chmod(0o700 if path.is_dir() else 0o600)


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    make_private(path)
    return path


def secret_bytes(path: Path, size: int = 32, *, create=True) -> bytes:
    import secrets
    if create and not path.exists():
        with path.open("xb") as output:
            output.write(secrets.token_bytes(size))
        make_private(path)
    value = path.read_bytes()
    if len(value) != size:
        raise ValueError("运行时密钥文件不完整，请在停止助手后检查 runtime")
    return value


def atomic_private_write(path: Path,data: bytes):
    """Parallel readers/writers must never share a temporary pathname."""
    import tempfile
    descriptor,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'wb') as output:output.write(data)
        make_private(temporary)
        import time
        for attempt in range(8):
            try:
                os.replace(temporary,path);break
            except PermissionError:
                if attempt==7:raise
                time.sleep(.01*(attempt+1))
    finally:temporary.unlink(missing_ok=True)
