"""Read-only, authenticated local WeChat snapshots. Plaintext lives in RAM only."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import time
from types import MappingProxyType
import xml.etree.ElementTree as ET
import zstandard
from .dbcodec.crypto import decode_page, _checksum
from .domain import Message
from .member_names import read_member_names


class DatabaseUnavailable(RuntimeError):
    pass


def wechat_mention(source, self_id):
    """Return the native recipient check when this row carries an @ list."""
    limit = 64 * 1024
    if not isinstance(source, (bytes, str)) or len(source) > limit:
        return None
    try:
        if isinstance(source, bytes):
            if source.startswith(b'\x28\xb5\x2f\xfd'):
                with zstandard.ZstdDecompressor().stream_reader(source) as stream:
                    source = stream.read(limit + 1)
            source = source.decode('utf-8-sig')
        if len(source) > limit or '<!DOCTYPE' in source.upper() or '<!ENTITY' in source.upper():
            return None
        root = ET.fromstring(source.strip('\0\ufeff \t\r\n'))
    except (ET.ParseError, UnicodeError, zstandard.ZstdError):
        return None
    lists = root.findall('atuserlist') if root.tag == 'msgsource' else []
    if len(lists) != 1 or len(lists[0]):
        return None
    recipients = (lists[0].text or '').strip(' ,;\t\r\n')
    return self_id in re.split(r'[,;\s]+', recipients) if recipients else None


def quoted_reply_text(raw):
    """Only the newly written body of appmsg 57, never its quoted old text."""
    if len(raw)>2*1024*1024 or '<!DOCTYPE' in raw.upper() or '<!ENTITY' in raw.upper():return None
    try:
        root=ET.fromstring(raw)
        apps=[root] if root.tag=='appmsg' else root.findall('appmsg') if root.tag=='msg' else []
        if len(apps)!=1:return None
        app=apps[0]
        if app.findtext('type')!='57':return None
        titles=app.findall('title')
        if len(titles)!=1 or len(titles[0]):return None
        text=(titles[0].text or '').strip()
        return text if text else None
    except ET.ParseError:
        return None


def signature(path):
    try:
        st=path.stat()
        return st.st_size,st.st_mtime_ns,st.st_ino
    except FileNotFoundError:return None


def overlay_wal(base, wal, key, mac_key):
    """Apply authenticated pages only through the last complete commit."""
    if not wal:return base
    if len(wal)<32:raise DatabaseUnavailable('消息日志正在更新')
    magic,version,page_size=struct.unpack('>III',wal[:12])
    if magic not in (0x377F0682,0x377F0683) or version!=3007000 or page_size!=4096:
        raise DatabaseUnavailable('消息日志格式不兼容')
    endian='<' if magic==0x377F0682 else '>'
    checksum=_checksum(wal[:24],endian)
    if checksum!=struct.unpack('>II',wal[24:32]):raise DatabaseUnavailable('消息日志头校验失败')
    pages=[];committed=0;size=0
    for offset in range(32,len(wal),4120):
        frame=wal[offset:offset+4120]
        if len(frame)!=4120:raise DatabaseUnavailable('消息日志尚未写入完整')
        if frame[8:16]!=wal[16:24]:break
        pgno,db_size=struct.unpack('>II',frame[:8])
        if not 0<pgno<10000000:raise DatabaseUnavailable('消息日志页号异常')
        checksum=_checksum(frame[:8]+frame[24:],endian,*checksum)
        if checksum!=struct.unpack('>II',frame[16:24]):raise DatabaseUnavailable('消息日志校验失败，稍后重试')
        pages.append((pgno,frame[24:]))
        if db_size:committed=len(pages);size=db_size
    if not committed:return base
    # A corrupt length must not allocate unbounded memory.
    if size*4096>max(len(base)+len(wal),len(base)*2):raise DatabaseUnavailable('消息日志长度异常')
    output=bytearray(base[:size*4096])
    if len(output)<size*4096:output.extend(b'\0'*(size*4096-len(output)))
    for pgno,data in pages[:committed]:
        plain=decode_page(data,key,mac_key,pgno)
        if pgno<=size:output[(pgno-1)*4096:pgno*4096]=plain
    return output


class MemoryDatabase:
    def __init__(self,path,key=None):
        self.path=Path(path);self.key=key;self._conn=None;self._signature=None

    def connection(self):
        wal_path=Path(str(self.path)+'-wal')
        before=(signature(self.path),signature(wal_path))
        if before==self._signature and self._conn is not None:return self._conn
        if before[0] is None:raise DatabaseUnavailable('等待微信消息数据库')
        with self.path.open('rb') as f:header=f.read(16)
        conn=sqlite3.connect(':memory:')
        try:
            if header==b'SQLite format 3\0':
                source=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)
                try:source.backup(conn)
                finally:source.close()
            else:
                if self.key is None:raise DatabaseUnavailable('消息数据库凭据尚未就绪')
                raw=self.path.read_bytes()
                wal=wal_path.read_bytes() if before[1] else b''
                if before!=(signature(self.path),signature(wal_path)):
                    raise DatabaseUnavailable('消息数据库正在提交，自动重试')
                if len(raw)<4096 or len(raw)%4096:raise DatabaseUnavailable('消息数据库页不完整')
                mac=hashlib.pbkdf2_hmac('sha512',self.key,bytes(x^0x3a for x in raw[:16]),2,32)
                plain=bytearray()
                for off in range(0,len(raw),4096):plain.extend(decode_page(raw[off:off+4096],self.key,mac,off//4096+1))
                plain=overlay_wal(plain,wal,self.key,mac)
                # deserialize is an in-memory snapshot, with no adjacent WAL file.
                plain[18:20]=b'\1\1'
                conn.deserialize(plain)
            conn.execute('PRAGMA query_only=ON');conn.execute('PRAGMA trusted_schema=OFF')
            conn.execute('SELECT count(*) FROM sqlite_master').fetchone()
        except Exception:
            conn.close();raise
        if self._conn:self._conn.close()
        self._conn,self._signature=conn,before
        return conn

    def close(self):
        if self._conn:self._conn.close();self._conn=None


@dataclass
class MessageBatch:
    history:list
    new:list
    initial:bool=False


class DatabaseFeed:
    def __init__(self,sources,group_id,self_id,*,table=None,clock=time.time,member_names=None):
        self.sources=sources;self.group_id=group_id;self.self_id=self_id;self.clock=clock
        self.member_names=member_names
        self._speaker_tokens={}
        self.table=table or 'Msg_'+hashlib.md5(group_id.encode()).hexdigest()
        if not re.fullmatch(r'Msg_[a-fA-F0-9]{32}',self.table):raise ValueError('无效群标识')
        self.watermarks={};self.started=None;self.seen=set()
        self.uploading={}

    def poll(self):
        now=self.clock();initial=self.started is None
        members=self.member_names() if self.member_names else {}
        group=self.group_id.endswith('@chatroom')
        if not initial and now<self.started:
            self.watermarks.clear();self.seen.clear();self.uploading.clear();initial=True
        uploading={key:at for key,at in self.uploading.items() if -5<=now-at<=120}
        rows=[];watermarks=dict(self.watermarks)
        for source in self.sources():
            conn=source.connection()
            if not conn.execute('SELECT 1 FROM sqlite_master WHERE name=?',(self.table,)).fetchone():
                watermarks[str(source.path)]=0
                continue
            columns={r[1] for r in conn.execute(f'PRAGMA table_info([{self.table}])')}
            required={'local_id','server_id','local_type','create_time','sort_seq','real_sender_id','message_content'}
            if not required<=columns:raise DatabaseUnavailable('微信消息表结构不兼容')
            ids=dict(conn.execute('SELECT rowid,user_name FROM Name2Id'))
            maximum=conn.execute(f'SELECT max(local_id) FROM [{self.table}]').fetchone()[0] or 0
            previous=self.watermarks.get(str(source.path),None if initial else 0)
            if previous is not None and maximum<previous:
                uploading={key:at for key,at in uploading.items() if key[0]!=str(source.path)}
            limit=256 if previous is not None and maximum>=previous else 96
            result=conn.execute(f'SELECT * FROM [{self.table}] ORDER BY local_id DESC LIMIT ?',(limit,))
            names=[d[0] for d in result.description]
            for record in result:
                d=dict(zip(names,record));local=int(d['local_id']);at=int(d['create_time'] or 0)
                kind=int(d['local_type'])&0xffffffff
                if kind not in (1,3,47,49):continue
                sender=ids.get(d['real_sender_id'],'')
                if not isinstance(sender,str) or not sender:continue
                if not group and sender not in (self.self_id,self.group_id):continue
                sid=int(d['server_id'] or 0)
                fresh=(not initial and previous is not None and maximum>=previous and local>previous
                       and at>=int(self.started) and -5<=now-at<=120)
                upload_key=(str(source.path),local)
                # An outgoing GIF can first appear as an incomplete appmsg 8;
                # upload completion updates that SAME local row, not a new ID.
                if fresh and sender==self.self_id and (kind==47 or int(d['local_type'])>>32==8):
                    if len(uploading)<256:uploading[upload_key]=at
                raw=d['message_content']
                if isinstance(raw,bytes):
                    if d.get('WCDB_CT_message_content')==4 or raw.startswith(b'\x28\xb5\x2f\xfd'):
                        try:raw=zstandard.ZstdDecompressor().decompress(raw,max_output_size=2*1024*1024)
                        except zstandard.ZstdError:continue
                    raw=raw.decode('utf-8',errors='replace')
                if not isinstance(raw,str) or not raw.strip():continue
                # A group body may repeat the sender already proved by the
                # DB row. A private message has no such removable prefix.
                if group:raw=raw.removeprefix(sender+':\n')
                if kind==49:
                    from .stickers import media_md5
                    if media_md5(raw):kind=47
                    else:
                        raw=quoted_reply_text(raw)
                        if raw is None:continue
                        kind=1
                was_uploading=upload_key in uploading
                if was_uploading and (sender!=self.self_id or at!=uploading[upload_key] or not -5<=now-at<=120):
                    del uploading[upload_key];was_uploading=False
                if was_uploading:
                    from .stickers import media_md5
                    if sid<=0 or not media_md5(raw):continue
                    del uploading[upload_key]
                identity=f'{self.group_id}/s/{sid}' if sid>0 else f'{self.group_id}/{source.path.name}/{local}'
                cache_key=''
                if kind==3:
                    from .media import extract_cache_key
                    cache_key=extract_cache_key(d.get('packed_info_data',b''))
                display=members.get(sender) if sender!=self.self_id else None
                message=Message(raw if kind==1 else '[图片]' if kind==3 else '[表情静态帧]',
                    display.name if display else '','out' if sender==self.self_id else 'in','text' if kind==1 else 'image' if kind==3 else 'emoji',
                    message_id=identity,created_at=at,media_raw=raw if kind!=1 else '',media_key=cache_key,
                    sender_source=display.source if display else '',
                    sender_observed_at=now if display else 0,
                    speaker_id='self' if sender==self.self_id else self._speaker_token(sender),
                    mentioned_self=wechat_mention(d.get('source'),self.self_id)
                    if group and kind==1 and sender!=self.self_id else None)
                # Server timestamps can lead the local fractional second. Do
                # not consume that fresh ID as history and lose its receipt.
                is_new=(fresh or was_uploading) and identity not in self.seen
                rows.append(((at,int(d['sort_seq'] or local),local),message,is_new))
            watermarks[str(source.path)]=maximum
        rows.sort(key=lambda r:r[0])
        unique={}
        for order,message,new in rows:
            if message.message_id in unique:new=new or unique[message.message_id][1]
            unique[message.message_id]=(message,new)
        self.watermarks=watermarks
        self.uploading=uploading
        if initial:self.started=now
        self.seen.update(unique)
        if len(self.seen)>4096:self.seen=set(unique)
        return MessageBatch([m for m,new in unique.values()][-(96 if initial else 24):],[m for m,new in unique.values() if new],initial)

    def _speaker_token(self,sender):
        token=self._speaker_tokens.get(sender)
        if token:return token
        token='p_'+hashlib.sha256((self.self_id+'\0'+self.group_id+'\0'+sender).encode()).hexdigest()[:16]
        if len(self._speaker_tokens)<4096:self._speaker_tokens[sender]=token
        return token

    def member_profiles(self,known_ids):
        """Refresh known people independently of the recent message window.

        The raw directory may include unrelated contacts. Only already-known,
        conversation-scoped opaque IDs leave this reader; raw IDs stay local.
        """
        from .member_names import known_member,safe_name
        known={value for value in known_ids if known_member(value)}
        if not known or not self.member_names:return {}
        now=self.clock();profiles={}
        for uid,display in self.member_names().items():
            if not isinstance(uid,str) or uid==self.self_id:continue
            speaker=self._speaker_token(uid)
            if speaker in known and safe_name(display.name):
                profiles[speaker]={'name':display.name,'source':display.source,'observed_at':now}
        return profiles


class DatabaseHub:
    """One selected logged-in account; credentials imported encrypted from 拾光."""
    def __init__(self,settings,cancelled,*,process_id=None):
        self.settings=settings;self.cancelled=cancelled;self.account=None
        self.process_id=process_id
        self.keys={};self.snapshots={};self.last_identity_check=0;self.feeds={}
        self._contact_cache=None

    @property
    def pid(self):
        if self.process_id is not None:return self.process_id
        if not self.settings.binding:raise DatabaseUnavailable('等待绑定已登录的微信窗口')
        return self.settings.binding.pid

    def _ensure(self,force_identity=False):
        from .dbcodec.discovery import discover_accounts,active_account_ids
        from .dbcodec.windows_reader import find_keys,protect,unprotect
        from .dbcodec.config_scan import verify_enc_key
        from .privacy import private_dir,make_private
        pid=self.pid
        if self.account and not force_identity and time.monotonic()-self.last_identity_check<30:return
        candidates=discover_accounts()
        if not candidates:candidates=discover_accounts([Path('D:/xwechat_files')])
        active=active_account_ids(candidates,[{'pid':pid}])
        selected=[a for a in candidates if a['id'] in active]
        if len(selected)!=1:raise DatabaseUnavailable('等待绑定微信登录账号的消息数据库')
        account=selected[0]
        if self.account and self.account['id']!=account['id']:
            raise DatabaseUnavailable('微信登录账号改变，暂停读取原账号')
        root=Path(account['path'])/'db_storage'
        paths=[root/'contact/contact.db',*sorted((root/'message').glob('message_[0-9]*.db'))]
        if not paths or not paths[0].exists():raise DatabaseUnavailable('微信联系人数据库尚未就绪')
        # Always verify the live process, account and page authentication.
        # Only search for credentials when an actual encrypted page needs one.
        missing={}
        for path in paths:
            with path.open('rb') as f:page=f.read(4096)
            if page[:16]==b'SQLite format 3\0':continue
            key=self.keys.get(page[:16].hex())
            if not key or not verify_enc_key(bytes.fromhex(key),page):missing[path]=page
        if missing:
            cache=private_dir(self.settings.runtime)/'wechat-keys.dpapi'
            project2=self.settings.root.parent/'开发项目二/Reconstructed/client'
            def candidates():
                yield cache
                yield Path(os.environ.get('APPDATA',''))/'拾光/data/accounts'/account['id']/'keys.dpapi'
                yield from project2.glob('release*/win-unpacked/data/accounts/'+account['id']+'/keys.dpapi')
                yield from project2.glob('runtime/**/accounts/'+account['id']+'/keys.dpapi')
            def accept(keys):
                for path,page in list(missing.items()):
                    salt=page[:16].hex()
                    try:key=bytes.fromhex(keys[salt])
                    except (TypeError,KeyError,ValueError):continue
                    if verify_enc_key(key,page):
                        self.keys[salt]=key.hex()
                        del missing[path]
            cache_updated=False
            for keyfile in candidates():
                try:keys=json.loads(unprotect(keyfile.read_bytes()))
                except Exception:continue  # unreadable external cache; try the next source
                if not isinstance(keys,dict):continue
                count=len(missing)
                accept(keys)
                cache_updated |= keyfile!=cache and len(missing)<count
                if not missing:break
            if missing:
                accept(find_keys(list(missing),self.cancelled,lambda *a:None,timeout=45,processes=[{'pid':pid}]))
                if missing:raise DatabaseUnavailable('新消息分库暂时无法验证，稍后自动重试')
                cache_updated=True
            if cache_updated:
                from .privacy import atomic_private_write
                atomic_private_write(cache,protect(json.dumps(self.keys).encode()))
        self.account=account;self.last_identity_check=time.monotonic()

    def _snapshot(self,path):
        path=Path(path)
        with path.open('rb') as f:salt=f.read(16)
        encoded=self.keys.get(salt.hex())
        if salt!=b'SQLite format 3\0' and not encoded:
            self.last_identity_check=0
            raise DatabaseUnavailable('发现新的消息分库，正在更新本机读取凭据')
        key=bytes.fromhex(encoded) if encoded else None
        if path in self.snapshots and self.snapshots[path].key!=key:
            self.snapshots.pop(path).close()
        if path not in self.snapshots:self.snapshots[path]=MemoryDatabase(path,key)
        return self.snapshots[path]

    def feed(self,group,force_identity=False):
        self._ensure(force_identity)
        if group not in self.settings.allowed_groups:raise DatabaseUnavailable('群不在已选范围')
        root=Path(self.account['path'])/'db_storage'
        from .contacts import resolve_contact
        try:target=resolve_contact(self.directory(),self.settings,group)
        except ValueError as error:raise DatabaseUnavailable(str(error)) from None
        group_id=target.id
        # Keep the resolved ID pinned even for a legacy title-only target.
        # A newly confirmed account/ID receives its own independent cursor.
        key=(target.account,group_id) if group in self.settings.conversation_targets else ('',group)
        if key in self.feeds and self.feeds[key].group_id!=group_id:
            raise DatabaseUnavailable('同名群的身份发生变化，已阻止跨群回复')
        if key not in self.feeds:
            sources=lambda:[self._snapshot(path) for path in sorted((root/'message').glob('message_[0-9]*.db'))]
            self.feeds[key]=DatabaseFeed(sources,group_id,self.account['wxid'],
                member_names=lambda:self.member_names(group_id))
        return self.feeds[key]

    def member_names(self,conversation_id):
        """Local-only raw-ID lookup, refreshed with the contact/WAL snapshot."""
        self._ensure()
        conn,cache=self._contact_metadata()
        if conversation_id not in cache['members']:
            cache['members'][conversation_id]=MappingProxyType(read_member_names(conn,conversation_id))
        return cache['members'][conversation_id]

    def _contact_metadata(self):
        conn=self._snapshot(Path(self.account['path'])/'db_storage/contact/contact.db').connection()
        # MemoryDatabase replaces its RAM connection only after the DB/WAL
        # signature changes and the replacement snapshot authenticates.
        if self._contact_cache is None or self._contact_cache['connection'] is not conn:
            self._contact_cache={'connection':conn,'directory':None,'members':{}}
        return conn,self._contact_cache

    def directory(self):
        self._ensure()
        from .contacts import read_directory
        conn,cache=self._contact_metadata()
        if cache['directory'] is None:
            cache['directory']=read_directory(conn,self.account['wxid'],self.account['id'])
        return list(cache['directory'])

    def close(self):
        self._contact_cache=None
        for snapshot in self.snapshots.values():snapshot.close()


class LazyDatabaseFeed:
    def __init__(self,hub,group):self.hub=hub;self.group=group
    def poll(self):return self.hub.feed(self.group).poll()
    def member_profiles(self,known_ids):return self.hub.feed(self.group).member_profiles(known_ids)
    def assert_identity(self):self.hub.feed(self.group,force_identity=True)
    def resolve_media(self,message):
        feed=self.hub.feed(self.group)
        from .media import LocalMediaResolver
        if not hasattr(self.hub,'media'):
            cancelled=getattr(self.hub.cancelled,'is_set',self.hub.cancelled)
            self.hub.media=LocalMediaResolver(self.hub.account['path'],process_id=self.hub.pid,account_id=self.hub.account['wxid'],cancelled=cancelled)
        return self.hub.media.resolve(message.media_raw,3 if message.kind=='image' else 47,feed.group_id,message.created_at,cache_key=message.media_key)
