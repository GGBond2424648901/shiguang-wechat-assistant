"""Read-only, verified outbound receipt previews shared by both panels."""
import hashlib
import hmac
import json
import re


def _verified_receipts(directory,scope,salt):
    """Read existing confirmation evidence without opening a writable store."""
    import sqlite3
    from contextlib import closing
    path=directory/'state.sqlite3'
    if not path.is_file():return []
    scoped=hmac.new(salt,scope.encode(),hashlib.sha256).hexdigest()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.5)) as db:
        found=db.execute('''SELECT r.reply_digest,r.baseline,r.at FROM send_receipts r
            JOIN tasks t ON t.id=r.task_id
            WHERE r.group_digest=? AND t.state='verified' ORDER BY r.at DESC LIMIT 64''',(scoped,)).fetchall()
    return [(text,set(json.loads(baseline)),at) for text,baseline,at in found]


def _receipt_rows(path, digest, scope, peer, name, *, confirmations=(),salt=b''):
    """Read the v1 archive without creating directories, keys or ACLs."""
    import win32crypt
    from .memory import ConversationMemory
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
    rows={}
    for record in data['messages'][-ConversationMemory.MAX_MESSAGES:]:
        identity=record.get('message_id','')
        if (record.get('direction')!='out' or record.get('speaker_id')!='self'
                or record.get('kind') not in ('text','emoji','image')
                or not stable.fullmatch(identity)):
            continue
        at=record.get('created_at')
        if not isinstance(at,(float,int)) or not 0<at<4102444800:continue
        text=' '.join(record.get('text','').split())
        linked=(stable.fullmatch(record.get('reply_to_id',''))
                and re.fullmatch(r'p_[a-f0-9]{16}',record.get('reply_to_speaker','')))
        if not linked:
            # Proactive/local sends have no inbound source link. Admit them
            # only with the same scoped, timed, non-baseline proof accepted by
            # the real sender. A manual self-message alone is never evidence.
            from .domain import normalized
            hashed=hmac.new(salt,normalized(record.get('text','')).encode(),hashlib.sha256).hexdigest()
            hashed_id=hmac.new(salt,identity.encode(),hashlib.sha256).hexdigest()
            if not any(hashed==expected and -5<=at-sent<=120 and hashed_id not in baseline
                       for expected,baseline,sent in confirmations):continue
        if text:rows[identity]=(at,name,text[:55]+('…' if len(text)>55 else ''))
    return sorted(rows.values(),reverse=True)[:4]


def receipt_previews(cfg,watched,previous,cancelled=lambda:False):
    """Keep at most four previews per selected archive; only return the latest four."""
    primary=cfg.conversation_targets.get(cfg.group,{})
    if not primary:return [],'主要会话尚未绑定账号',{}
    targets=[(name,cfg.conversation_targets[name]) for name in cfg.allowed_groups
             if name in watched and cfg.conversation_targets.get(name,{}).get('account')==primary['account']]
    if not targets:return [],'',{}
    try:
        with (cfg.runtime/'fingerprint.key').open('rb') as file:salt=file.read(33)
        if len(salt)!=32:raise ValueError('salt size')
    except FileNotFoundError:return [],'',{}
    except (ValueError,OSError):return [],'本地回执暂不可读',{}
    rows=[];cache={};failed=False
    for name,target in targets:
        if cancelled():break
        scope=target['account']+'/'+target['id']
        digest=hmac.new(salt,scope.encode(),hashlib.sha256).hexdigest()
        path=cfg.runtime/('memory-'+digest+'.dpapi')
        try:
            info=path.stat()
            stamp=(name,info.st_ino,info.st_mtime_ns,info.st_size)
            if scope in previous and previous[scope][0]==stamp:
                found=previous[scope][1]
            else:
                confirmed=_verified_receipts(cfg.runtime,scope,salt)
                found=_receipt_rows(path,digest,scope,target['id'],name,confirmations=confirmed,salt=salt)
            rows.extend(found);cache[scope]=(stamp,found)
        except FileNotFoundError:continue
        except Exception:failed=True
    return sorted(rows,reverse=True)[:4],('部分会话的回执暂不可读' if failed else ''),cache
