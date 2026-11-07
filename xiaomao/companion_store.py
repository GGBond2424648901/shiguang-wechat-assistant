"""Conversation-scoped, current-user encrypted companion preferences and notes."""
from dataclasses import asdict
import hashlib
import hmac
import json
from pathlib import Path
from .companion import CompanionPreferences,fresh_state
from .privacy import private_dir,secret_bytes,atomic_private_write


class CompanionStore:
    MAX_BYTES=8*1024*1024

    def __init__(self,runtime,scope,*,read_only=False):
        self.directory=Path(runtime) if read_only else private_dir(Path(runtime));self.scope=scope
        salt=secret_bytes(self.directory/'fingerprint.key',create=not read_only)
        self.digest=hmac.new(salt,scope.encode(),hashlib.sha256).hexdigest()
        self.path=self.directory/('companion-'+self.digest+'.dpapi')
        self.entropy=b'xiaomao:companion:v1:'+self.digest.encode()
        self.data={'version':1,'scope':scope,'preferences':asdict(CompanionPreferences()),
                   'planner':fresh_state(),'facts':[],'diary':[],'reflection_day':'','snapshot_day':'','evidence_pending':[]}
        if self.path.exists():
            try:
                if self.path.stat().st_size>self.MAX_BYTES:raise ValueError()
                import win32crypt
                _,plain=win32crypt.CryptUnprotectData(self.path.read_bytes(),self.entropy,None,None,1)
                data=json.loads(plain)
                if data['version']!=1 or data['scope']!=scope or not isinstance(data['planner'],dict):raise ValueError()
                CompanionPreferences(**data['preferences'])
                self.data.update(data)
            except Exception:raise ValueError('陪伴记忆无法解密或格式无效，原文件保留') from None

    def save(self):
        import win32crypt
        CompanionPreferences(**self.data['preferences'])
        plain=json.dumps(self.data,ensure_ascii=False,separators=(',',':')).encode()
        if len(plain)>self.MAX_BYTES-4096:raise ValueError('陪伴记忆超过保存上限')
        encrypted=win32crypt.CryptProtectData(plain,'Xiaoling Companion',self.entropy,None,None,1)
        atomic_private_write(self.path,encrypted)
