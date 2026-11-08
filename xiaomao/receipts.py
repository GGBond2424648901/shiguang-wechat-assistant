"""Read-only, verified outbound receipt history shared by both panels."""
from collections import defaultdict
from contextlib import closing
from heapq import merge
import hashlib
import hmac
from itertools import islice
import json
import re
import sqlite3
from typing import NamedTuple

from .domain import normalized
from .memory import ConversationMemory


class Receipt(NamedTuple):
    at: float
    group: str
    text: str
    id: str
    kind: str
    trigger: str
    linked: bool


def _verified_receipts(directory,scope,salt):
    """Read existing confirmation evidence without opening a writable store."""
    path=directory/'state.sqlite3'
    if not path.is_file():return set(),{}
    scoped=hmac.new(salt,scope.encode(),hashlib.sha256).hexdigest()
    exact=set()
    by_text=defaultdict(list)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.5)) as db:
        columns={row[1] for row in db.execute('PRAGMA table_info(send_receipts)')}
        confirmed='r.confirmed_digest' if 'confirmed_digest' in columns else "''"
        sticker='r.sticker_md5' if 'sticker_md5' in columns else "''"
        found=db.execute(f'''SELECT r.reply_digest,r.baseline,r.at,{confirmed},{sticker}
            FROM send_receipts r JOIN tasks t ON t.id=r.task_id
            WHERE r.group_digest=? AND t.state='verified' ORDER BY r.at DESC LIMIT ?''',
            (scoped,ConversationMemory.MAX_MESSAGES))
        for text,baseline,at,identity,media in found:
            if identity:exact.add(identity)
            elif not media:by_text[text].append((set(json.loads(baseline)),at))
    return exact,by_text


def _receipt_rows(path, digest, scope, peer, name, *, confirmations,salt):
    """Read the v1 archive without creating directories, keys or ACLs."""
    import win32crypt
    with path.open('rb') as encrypted:
        blob=encrypted.read(ConversationMemory.MAX_FILE_BYTES+1)
    if len(blob)>ConversationMemory.MAX_FILE_BYTES:raise ValueError('archive size')
    _,plain=win32crypt.CryptUnprotectData(blob,b'xiaomao:group-memory:v1:'+digest.encode('ascii'),None,None,1)
    data=json.loads(plain)
    if data.get('version')!=1 or data.get('group')!=scope or not isinstance(data.get('messages'),list):
        raise ValueError('archive scope')
    # Only _match_receipt enriches outgoing messages with the source link,
    # after confirm_receipt has accepted the actual WeChat outbound row.
    stable=re.compile(re.escape(peer)+r'/(?:s/[1-9]\d*|message_\d+\.db/[1-9]\d*)')
    messages=data['messages'][-ConversationMemory.MAX_MESSAGES:]
    sources={(row.get('message_id'),row.get('speaker_id')):row.get('text','')
             for row in messages if row.get('direction')=='in'}
    exact,legacy=confirmations
    rows={}
    unlinked=defaultdict(list)
    for record in messages:
        identity=record.get('message_id','')
        if (record.get('direction')!='out' or record.get('speaker_id')!='self'
                or record.get('kind') not in ('text','emoji','image')
                or not stable.fullmatch(identity)):
            continue
        at=record.get('created_at')
        if not isinstance(at,(float,int)) or not 0<at<4102444800:continue
        text=record.get('text','').strip()
        source_id=record.get('reply_to_id','')
        source_speaker=record.get('reply_to_speaker','')
        linked=bool(stable.fullmatch(source_id) and re.fullmatch(r'p_[a-f0-9]{16}',source_speaker))
        if not text:continue
        key=hmac.new(salt,(scope+'/'+identity).encode(),hashlib.sha256).hexdigest()
        trigger=sources.get((source_id,source_speaker),'') if linked else ''
        receipt=Receipt(at,name,text,key,record['kind'],trigger,linked)
        if linked:
            rows[identity]=receipt
            continue
        hashed_id=hmac.new(salt,identity.encode(),hashlib.sha256).hexdigest()
        if hashed_id in exact:
            rows[identity]=receipt
        elif record['kind']=='text' and legacy:
            hashed_text=hmac.new(salt,normalized(text).encode(),hashlib.sha256).hexdigest()
            if hashed_text in legacy:unlinked[hashed_text].append((identity,hashed_id,receipt))

    # Old stores have no digest for the confirmed bubble. Use a legacy text
    # match only when the confirmation identifies a unique archived message.
    for hashed_text,candidates in unlinked.items():
        for baseline,sent in legacy[hashed_text]:
            matches=[(identity,row) for identity,hashed_id,row in candidates
                     if -5<=row.at-sent<=120 and hashed_id not in baseline]
            if len(matches)==1:
                identity,row=matches[0]
                rows[identity]=row
    return sorted(rows.values(),reverse=True)


def receipt_page(cfg,watched,previous,*,offset=0,limit=4,cancelled=lambda:False):
    """Page verified receipts from cached projections of the bounded archives."""
    primary=cfg.conversation_targets.get(cfg.group,{})
    if not primary:return [],0,'主要会话尚未绑定账号',{}
    account=primary['account']
    targets=[(name,cfg.conversation_targets[name]) for name in cfg.allowed_groups
             if cfg.conversation_targets.get(name,{}).get('account')==account]
    if not targets:return [],0,'',{}
    try:
        with (cfg.runtime/'fingerprint.key').open('rb') as file:salt=file.read(33)
        if len(salt)!=32:raise ValueError('salt size')
    except FileNotFoundError:return [],0,'',{}
    except (ValueError,OSError):return [],0,'本地回执暂不可读',{}
    active_scopes={account+'/'+target['id'] for _,target in targets}
    cache={scope:entry for scope,entry in previous.items() if scope in active_scopes}
    batches=[];failed=False
    for name,target in targets:
        if name not in watched:continue
        if cancelled():break
        scope=account+'/'+target['id']
        digest=hmac.new(salt,scope.encode(),hashlib.sha256).hexdigest()
        path=cfg.runtime/('memory-'+digest+'.dpapi')
        try:
            info=path.stat()
            stamp=(digest,name,info.st_ino,info.st_mtime_ns,info.st_size)
            if scope in cache and cache[scope][0]==stamp:
                found=cache[scope][1]
            else:
                confirmed=_verified_receipts(cfg.runtime,scope,salt)
                found=_receipt_rows(path,digest,scope,target['id'],name,confirmations=confirmed,salt=salt)
            batches.append(found)
            cache[scope]=(stamp,found)
        except FileNotFoundError:
            cache.pop(scope,None)
        except Exception:
            cache.pop(scope,None)
            failed=True
    rows=list(islice(merge(*batches,reverse=True),offset,offset+limit))
    return rows,sum(map(len,batches)),('部分会话的回执暂不可读' if failed else ''),cache


def receipt_previews(cfg,watched,previous,cancelled=lambda:False):
    """Keep the legacy panel's latest-four display and call contract."""
    rows,_,note,cache=receipt_page(cfg,watched,previous,cancelled=cancelled)
    previews=[]
    for row in rows:
        text=' '.join(row.text.split())
        previews.append((row.at,row.group,text[:55]+('…' if len(text)>55 else '')))
    return previews,note,cache
