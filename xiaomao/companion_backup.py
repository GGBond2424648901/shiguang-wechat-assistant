"""Only selected-conversation memory is exported. Never reads credentials."""
import json
import os
import math
import re
from pathlib import Path
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .companion import CompanionPreferences,local_day,fresh_state
from .companion_memory import bounded_facts
from .privacy import private_dir,atomic_private_write

MAX_BYTES=70*1024*1024
MAGIC=b'XLCOMPANION1\n'


def _valid_time(value):
    return type(value) in (int,float) and math.isfinite(value) and value>=0


def _validate_fact(f):
    fields={'source','speaker','quote','kind','at','source_text','state','resolution'}
    if (not isinstance(f,dict) or set(f)-fields
            or not all(isinstance(f.get(k),str) for k in ('source','speaker','quote','kind'))
            or len(f['quote'])>240 or len(f['source'])>64 or len(f['speaker'])>512
            or f['kind'] not in ('fact','preference','topic','feeling','interaction','thread')
            or not _valid_time(f.get('at'))):raise ValueError()
    if not isinstance(f.get('source_text',''),str) or len(f.get('source_text',''))>2000:raise ValueError()
    if f['kind'] in ('feeling','interaction','thread') and not re.fullmatch(r'p_[a-f0-9]{16}',f['speaker']):raise ValueError()
    if f['kind']!='thread':
        if 'state' in f or 'resolution' in f:raise ValueError()
        return
    if f.get('state') not in ('open','closed'):raise ValueError()
    if f['state']=='open':
        if 'resolution' in f:raise ValueError()
        return
    r=f.get('resolution')
    if (not isinstance(r,dict) or set(r)!={'source','quote','source_text','at'}
            or not all(isinstance(r.get(k),str) for k in ('source','quote','source_text'))
            or not re.fullmatch(r'm_[a-f0-9]{16}',r['source']) or r['source']==f['source']
            or not 3<=len(r['quote'])<=240 or len(r['source_text'])>2000 or r['quote'] not in r['source_text']
            or not _valid_time(r.get('at')) or r['at']<f['at']):raise ValueError()


def _payload(memory,notes):
    with memory._lock:
        return {'version':1,'scope':memory.group,'summary':memory.summary,
            'messages':[memory._encode_message(m) for m in memory.messages],
            'preferences':notes.data['preferences'],'facts':notes.data['facts'],'diary':notes.data['diary']}


def _key(password,salt):
    if not isinstance(password,str) or not 8<=len(password)<=1024:raise ValueError('备份口令须为8至1024个字符')
    return Scrypt(salt=salt,length=32,n=2**15,r=8,p=1).derive(password.encode())


def export_backup(path,password,memory,notes):
    data=json.dumps(_payload(memory,notes),ensure_ascii=False,separators=(',',':')).encode()
    if len(data)>MAX_BYTES-4096:raise ValueError('会话备份过大')
    salt,nonce=os.urandom(16),os.urandom(12)
    blob=MAGIC+salt+nonce+AESGCM(_key(password,salt)).encrypt(nonce,data,MAGIC+salt)
    atomic_private_write(Path(path),blob)


def _read(path,password,scope):
    try:
        path=Path(path)
        if path.stat().st_size>MAX_BYTES:raise ValueError()
        blob=path.read_bytes()
        if not blob.startswith(MAGIC) or len(blob)<len(MAGIC)+44:raise ValueError()
        n=len(MAGIC);salt,nonce=blob[n:n+16],blob[n+16:n+28]
        data=json.loads(AESGCM(_key(password,salt)).decrypt(nonce,blob[n+28:],MAGIC+salt))
        if data['version']!=1 or data['scope']!=scope:raise ValueError()
        from .memory import ConversationMemory
        if not isinstance(data['summary'],str) or len(data['summary'])>4000 or not isinstance(data['messages'],list) or len(data['messages'])>4096:raise ValueError()
        data['decoded']=[ConversationMemory._decode_message(m) for m in data['messages']]
        CompanionPreferences(**data['preferences'])
        if not isinstance(data['facts'],list) or len(data['facts'])>200 or not isinstance(data['diary'],list) or len(data['diary'])>30:raise ValueError()
        for f in data['facts']:
            _validate_fact(f)
        for d in data['diary']:
            if not isinstance(d.get('text'),str) or len(d['text'])>600 or not isinstance(d.get('day'),str) or len(d['day'])!=10 or not isinstance(d.get('at'),(int,float)) or not isinstance(d.get('sources'),list) or len(d['sources'])>20 or any(not isinstance(s,str) or len(s)>64 for s in d['sources']):raise ValueError()
        return data
    except Exception:raise ValueError('无法打开备份：口令、文件完整性或会话身份不匹配') from None


def preview_backup(path,password,scope):
    data=_read(path,password,scope)
    return {'messages':len(data['messages']),'facts':len(data['facts']),'diaries':len(data['diary']),
            'summary_chars':len(data['summary']),'scope_matched':True}


def restore_backup(path,password,memory,notes):
    data=_read(path,password,memory.group)
    # Disable durably BEFORE merging any imported material. If this first write
    # fails, neither memory nor the existing profile is touched on disk.
    notes.data['preferences']['enabled']=False
    notes.save()
    try:_merge_import(data,memory,notes)
    except Exception:
        raise ValueError('导入未全部完成，部分记忆可能已合并；原有较新内容保留，主动聊天已关闭，可重试同一备份') from None


def _merge_import(data,memory,notes):
    with memory._lock:
        # Existing versions win, including IDs already compacted out of raw history.
        incoming=[m for m in data['decoded'] if memory._identity(m) not in memory._seen]
        merged=sorted(incoming+memory.messages,key=lambda m:m.created_at)[-memory.MAX_MESSAGES:]
        seen=list(dict.fromkeys(memory._seen+[memory._identity(m) for m in merged]))[-memory.MAX_SEEN_IDS:]
        summary=memory.summary or data['summary']
        memory._save(merged,summary,seen)
        memory.messages,memory._seen,memory.summary=merged,seen,summary
    from dataclasses import asdict
    # Import into a populated profile must not overwrite its newer customization.
    defaults=asdict(CompanionPreferences())
    if notes.data['preferences']==defaults:notes.data['preferences']=data['preferences']
    notes.data['preferences']['enabled']=False
    merged={}
    for fact in data['facts']+notes.data['facts']:
        key=(fact['source'],'thread' if fact['kind']=='thread' else fact['quote'])
        old=merged.get(key)
        # A closure is monotonic: importing either an older open record or a
        # differently excerpted version cannot resurrect a completed topic.
        if old and old.get('state')=='closed':
            if fact.get('state')!='closed' or old['resolution']['at']>=fact['resolution']['at']:continue
        merged[key]=fact
    notes.data['facts']=bounded_facts(sorted(merged.values(),key=lambda f:f['at']))
    diaries={d['day']:d for d in data['diary']+notes.data['diary']}
    notes.data['diary']=sorted(diaries.values(),key=lambda d:d['day'])[-30:]
    notes.data['planner']=fresh_state()
    notes.save()


def daily_snapshot(memory,notes,now):
    day=local_day(now)
    if notes.data['snapshot_day']==day:return
    import win32crypt
    folder=private_dir(notes.directory/'companion-snapshots'/notes.digest)
    data=json.dumps(_payload(memory,notes),ensure_ascii=False,separators=(',',':')).encode()
    if len(data)>MAX_BYTES-4096:raise ValueError('快照过大')
    encrypted=win32crypt.CryptProtectData(data,'Xiaoling Memory Snapshot',notes.entropy,None,None,1)
    atomic_private_write(folder/(day+'.dpapi'),encrypted)
    for old in sorted(folder.glob('*.dpapi'))[:-7]:old.unlink()
    notes.data['snapshot_day']=day;notes.save()
