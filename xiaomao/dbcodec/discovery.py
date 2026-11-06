"""Read-only account discovery. A directory is a candidate, not a verified login."""

import hashlib
import os
import re
from pathlib import Path
import psutil


def wechat_processes():
    result = []
    current_user = psutil.Process().username().casefold()
    for proc in psutil.process_iter(["pid", "name", "username", "memory_info"]):
        try:
            if (proc.info["name"] or "").lower() == "weixin.exe" and (
                proc.info["username"] or ""
            ).casefold() == current_user:
                result.append({"pid": proc.pid, "memory": proc.info["memory_info"].rss})
        except (psutil.Error, OSError):
            continue
    return sorted(result, key=lambda x: x["memory"], reverse=True)


def default_roots():
    roots = [Path.home() / "Documents/xwechat_files"]
    config = Path(os.environ.get("APPDATA", "")) / "Tencent/xwechat/config"
    if config.is_dir():
        for ini in config.glob("*.ini"):
            for encoding in ("utf-8-sig", "gbk", "utf-16"):
                try:
                    value = ini.read_text(encoding=encoding).strip().strip("\0")
                    if value and len(value) < 1024 and Path(value).is_dir():
                        roots.extend([Path(value) / "xwechat_files", Path(value)])
                    break
                except (UnicodeError, OSError, ValueError):
                    continue
    return roots


def describe_account(account):
    account = Path(account).resolve()
    dbs = account / "db_storage"
    if not dbs.is_dir():
        raise ValueError("请选择包含 db_storage 的微信账号目录")
    files = list(dbs.rglob("*.db"))
    if not files:
        raise ValueError("账号目录中没有数据库")
    encrypted = False
    for p in files:
        if p.stat().st_size >= 4096:
            with p.open("rb") as f:
                if f.read(16) != b"SQLite format 3\0":
                    encrypted = True
                    break
    wxid = re.sub(r"_[a-fA-F0-9]{4}$", "", account.name)
    return {
        "id": hashlib.sha256(str(account).casefold().encode()).hexdigest()[:20],
        "path": str(account),
        "wxid": wxid,
        "name": wxid,
        "status": "encrypted" if encrypted else "readable",
        "size": sum(p.stat().st_size for p in files),
        "databaseCount": len(files),
        "modified": max(p.stat().st_mtime for p in files),
    }


def discover_accounts(roots=None):
    accounts = {}
    for root in roots if roots is not None else default_roots():
        root = Path(root)
        if root.name == "db_storage":
            root = root.parent
        candidates = (
            [root]
            if (root / "db_storage").is_dir()
            else (list(root.iterdir()) if root.is_dir() else [])
        )
        for path in candidates:
            if not path.is_dir() or not (path / "db_storage").is_dir():
                continue
            try:
                item = describe_account(path)
                accounts[item["id"]] = item
            except (OSError, ValueError):
                continue
    return sorted(accounts.values(), key=lambda x: x["modified"], reverse=True)


def active_account_ids(accounts, processes):
    """Match message DB handles owned by current-user WeChat processes.

    Config/avatar handles and directory timestamps also exist for signed-out
    accounts, so they are deliberately not login evidence. Failure to enumerate
    handles means unknown; never fall back to the most recently modified folder.
    """
    from .account_probe import active_accounts
    return active_accounts(accounts,processes)
