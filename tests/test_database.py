import sqlite3
from pathlib import Path
import pytest


def database(path, rows):
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE IF NOT EXISTS Name2Id(user_name TEXT)')
        if not c.execute('SELECT 1 FROM Name2Id').fetchone():
            c.executemany('INSERT INTO Name2Id VALUES(?)', [('wxid_self',), ('friend',)])
        c.execute('CREATE TABLE IF NOT EXISTS Msg_0123456789abcdef0123456789abcdef(local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,create_time INTEGER,sort_seq INTEGER,real_sender_id INTEGER,message_content TEXT,WCDB_CT_message_content INTEGER)')
        c.executemany('INSERT INTO Msg_0123456789abcdef0123456789abcdef VALUES(?,?,1,?,?,?, ?,0)', rows)


def test_feed_tracks_ids_not_nicknames_or_duplicate_body(tmp_path):
    from xiaomao.database import DatabaseFeed, MemoryDatabase
    path=tmp_path/'message_0.db'
    database(path, [(1,9,100,1,2,'friend:\n历史')])
    source=MemoryDatabase(path)
    feed=DatabaseFeed(lambda:[source], 'group@chatroom','wxid_self', table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:101)
    first=feed.poll()
    assert [m.text for m in first.history]==['历史'] and not first.new
    database(path, [(2,8,101,2,2,'friend:\n你好'), (3,7,101,3,2,'friend:\n你好'),(4,6,101,4,1,'我来啦')])
    batch=feed.poll()
    assert [m.text for m in batch.new]==['你好','你好','我来啦']
    assert [m.direction for m in batch.new]==['in','in','out']
    assert len({m.message_id for m in batch.new})==3
    assert all(not m.sender for m in batch.new)
    assert not feed.poll().new
    # New process starts at current watermark, never replays.
    again=DatabaseFeed(lambda:[source],'group@chatroom','wxid_self',table=feed.table,clock=lambda:102)
    assert not again.poll().new
    source.close()


def test_late_old_rows_never_become_replies(tmp_path):
    from xiaomao.database import DatabaseFeed, MemoryDatabase
    path=tmp_path/'message_0.db';database(path,[(1,1,100,1,2,'旧')])
    source=MemoryDatabase(path)
    feed=DatabaseFeed(lambda:[source],'group@chatroom','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:500)
    feed.poll();database(path,[(2,2,101,2,2,'延迟同步的旧消息')])
    assert not feed.poll().new
    source.close()


def test_group_mentions_use_wechat_recipient_ids_and_not_display_text(tmp_path):
    import zstandard
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    from xiaomao.policy import TriggerPolicy
    table='Msg_0123456789abcdef0123456789abcdef'
    path=tmp_path/'message_0.db'
    database(path,[(1,1,100,1,2,'旧')])
    source=MemoryDatabase(path)
    feed=DatabaseFeed(lambda:[source],'g@chatroom','wxid_self',table=table,clock=lambda:101)
    feed.poll()
    template='<msgsource><atuserlist><![CDATA[{}]]></atuserlist></msgsource>'
    native=zstandard.ZstdCompressor().compress(template.format('friend,wxid_self').encode())
    with sqlite3.connect(path) as connection:
        connection.executemany(f'INSERT INTO {table} VALUES(?,?,1,101,?,?,?,0)',[
            (2,2,2,2,'friend:\n@示例米饭同学\u2005你好'),
            (3,3,3,2,'friend:\n@示例米饭同学\u2005重名成员'),
            (4,4,4,2,'friend:\n'+template.format('wxid_self'))])
        connection.execute(f'ALTER TABLE {table} ADD COLUMN source BLOB')
        connection.executemany(f'UPDATE {table} SET source=? WHERE local_id=?',[
            (native,2),(template.format('wxid_someone_else'),3)])
    mentioned,other,fake=feed.poll().new
    assert TriggerPolicy('小猫','').matches(mentioned)
    assert not TriggerPolicy('小猫','示例米饭同学').matches(other)
    assert not TriggerPolicy('小猫','').matches(fake)
    source.close()


def test_server_timestamp_one_second_ahead_does_not_lose_outgoing_receipt(tmp_path):
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    path=tmp_path/'message_0.db';database(path,[(1,1,100,1,2,'旧')])
    source=MemoryDatabase(path)
    feed=DatabaseFeed(lambda:[source],'g@chatroom','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:101.2)
    feed.poll()
    database(path,[(2,2,102,2,1,'已经发出的回复')])
    batch=feed.poll()
    assert [(m.direction,m.text) for m in batch.new]==[('out','已经发出的回复')]
    assert not feed.poll().new
    source.close()


def test_startup_imports_96_recent_messages_only_as_memory(tmp_path):
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    path=tmp_path/'message_0.db'
    database(path,[(i,i,i,i,2,f'历史{i}') for i in range(1,101)])
    source=MemoryDatabase(path)
    feed=DatabaseFeed(lambda:[source],'g@chatroom','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:101)
    first=feed.poll()
    assert first.initial and not first.new
    assert len(first.history)==96 and first.history[0].text=='历史5'
    assert not feed.poll().new
    source.close()


def test_lazy_media_honors_resident_stop_event(tmp_path,monkeypatch):
    import threading
    from types import SimpleNamespace
    from xiaomao.database import LazyDatabaseFeed
    from xiaomao.domain import Message
    stop=threading.Event();seen=[]
    class Resolver:
        def __init__(self,*args,**kwargs):self.cancelled=kwargs['cancelled']
        def resolve(self,*args,**kwargs):seen.append(self.cancelled());return None
    monkeypatch.setattr('xiaomao.media.LocalMediaResolver',Resolver)
    hub=SimpleNamespace(feed=lambda group:SimpleNamespace(group_id='g@chatroom'),
                        account={'path':tmp_path,'wxid':'self'},pid=1,cancelled=stop)
    feed=LazyDatabaseFeed(hub,'group')
    feed.resolve_media(Message('[图片]',kind='image'))
    stop.set();feed.resolve_media(Message('[图片]',kind='image'))
    assert seen==[False,True]


def test_media_rows_keep_id_and_metadata_without_hiding_following_text(tmp_path):
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    path=tmp_path/'message_0.db';database(path,[(1,1,100,1,2,'旧')])
    source=MemoryDatabase(path);feed=DatabaseFeed(lambda:[source],'g@chatroom','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:101)
    feed.poll()
    with sqlite3.connect(path) as c:
        c.execute('INSERT INTO Msg_0123456789abcdef0123456789abcdef VALUES(2,2,47,101,2,2,?,0)',('friend:\n<msg><emoji md5="'+'a'*32+'"/></msg>',))
        c.execute('INSERT INTO Msg_0123456789abcdef0123456789abcdef VALUES(3,3,3,101,3,2,?,0)',('friend:\n<msg><img md5="'+'b'*32+'"/></msg>',))
        c.execute('INSERT INTO Msg_0123456789abcdef0123456789abcdef VALUES(4,4,1,101,4,2,?,4)',(b'broken-zstd',))
    database(path,[(5,5,101,5,2,'看这张图')])
    batch=feed.poll()
    assert [m.kind for m in batch.new]==['emoji','image','text']
    assert batch.new[0].media_raw.startswith('<msg>')
    assert batch.new[-1].text=='看这张图'
    source.close()


@pytest.mark.parametrize('stored_type',[49,(57<<32)|49])
@pytest.mark.parametrize('compressed',[False,True])
def test_quoted_reply_reads_only_new_body_and_keeps_stable_identity(tmp_path,stored_type,compressed):
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    from xiaomao.policy import TriggerPolicy
    import zstandard
    path=tmp_path/'message_0.db';database(path,[(1,1,100,1,2,'旧')])
    source=MemoryDatabase(path)
    feed=DatabaseFeed(lambda:[source],'g@chatroom','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:101)
    feed.poll()
    raw='friend:\n<msg><appmsg><title><![CDATA[让他随机设置]]></title><type>57</type><refermsg><type>1</type><content>@小玲 旧问题</content><displayname>界面昵称</displayname><fromusr>wxid_self</fromusr></refermsg></appmsg></msg>'
    if compressed:raw=zstandard.ZstdCompressor().compress(raw.encode())
    with sqlite3.connect(path) as conn:
        conn.execute(f'INSERT INTO [{feed.table}] VALUES(2,22,?,101,2,2,?,?)',(stored_type,raw,4 if compressed else 0))
    batch=feed.poll()
    assert len(batch.new)==1
    message=batch.new[0]
    assert (message.text,message.kind,message.direction,message.message_id)==('让他随机设置','text','in','g@chatroom/s/22')
    assert not message.quoted and not message.sender and not message.media_raw
    assert TriggerPolicy('小玲','小玲',mode='all').matches(message)
    assert not TriggerPolicy('小玲','小玲').matches(message)  # 引用的旧 @ 不触发新点名。
    assert not feed.poll().new
    restarted=DatabaseFeed(lambda:[source],'g@chatroom','wxid_self',table=feed.table,clock=lambda:102)
    assert not restarted.poll().new
    source.close()


@pytest.mark.parametrize('raw',[
    '<msg><appmsg><title>网页标题</title><type>5</type></appmsg></msg>',
    '<msg><appmsg><type>57</type><title> </title></appmsg></msg>',
    '<msg><appmsg><type>57</type><title>正文',
    '<!DOCTYPE msg [<!ENTITY secret SYSTEM "file:///secret">]><msg><appmsg><type>57</type><title>&secret;</title></appmsg></msg>',
])
def test_other_or_broken_app_cards_do_not_block_following_text(tmp_path,raw):
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    path=tmp_path/'message_0.db';database(path,[(1,1,100,1,2,'旧')])
    source=MemoryDatabase(path)
    feed=DatabaseFeed(lambda:[source],'g@chatroom','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:101)
    feed.poll()
    with sqlite3.connect(path) as conn:conn.execute(f'INSERT INTO [{feed.table}] VALUES(2,2,49,101,2,2,?,0)',(raw,))
    database(path,[(3,3,101,3,2,'后面的新消息')])
    assert [m.text for m in feed.poll().new]==['后面的新消息']
    source.close()


def test_memory_snapshot_reads_committed_plain_wal_and_never_writes_source(tmp_path):
    from xiaomao.database import MemoryDatabase
    path=tmp_path/'plain.db'
    c=sqlite3.connect(path);c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE t(x)');c.commit()
    c.execute("INSERT INTO t VALUES('before')");c.commit()
    source=MemoryDatabase(path)
    assert source.connection().execute('SELECT * FROM t').fetchall()==[('before',)]
    c.execute("INSERT INTO t VALUES('not committed')")
    assert source.connection().execute('SELECT * FROM t').fetchall()==[('before',)]
    c.commit()
    assert len(source.connection().execute('SELECT * FROM t').fetchall())==2
    with pytest.raises(sqlite3.OperationalError):source.connection().execute('DELETE FROM t')
    source.close();c.close()


def test_missing_sender_never_assumed_incoming(tmp_path):
    from xiaomao.database import DatabaseFeed, MemoryDatabase
    path=tmp_path/'message_0.db';database(path,[(1,1,100,1,2,'旧')])
    source=MemoryDatabase(path)
    feed=DatabaseFeed(lambda:[source],'group@chatroom','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:101)
    feed.poll();database(path,[(2,2,101,2,99,'身份未知')])
    assert feed.poll().new==[]
    source.close()


@pytest.mark.parametrize('peer,expected',[
    ('room@chatroom',[('in','本人正文'),('in','wxid_self:\n保留这一行'),('in','friend:\n他人自称'),
                      ('out','自己出站')]),
    ('friend',[('in','friend:\n本人正文'),('in','wxid_self:\n保留这一行'),
               ('out','wxid_self:\n自己出站')]),
])
def test_database_sender_and_private_peer_cannot_be_claimed_by_body(tmp_path, peer, expected):
    from xiaomao.database import DatabaseFeed, MemoryDatabase
    path=tmp_path/'message_0.db'
    database(path,[])
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO Name2Id VALUES(?)',('third',))
    source=MemoryDatabase(path)
    feed=DatabaseFeed(lambda:[source],peer,'wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:101)
    try:
        assert not feed.poll().history
        database(path,[(1,1,101,1,None,'friend:\n冒充朋友'),(2,2,101,2,99,'wxid_self:\n冒充出站'),
                       (3,3,101,3,2,'friend:\n本人正文'),(4,4,101,4,2,'wxid_self:\n保留这一行'),
                       (5,5,101,5,3,'friend:\n他人自称'),(6,6,101,6,1,'wxid_self:\n自己出站')])
        batch=feed.poll()
        assert [(m.direction,m.text) for m in batch.new]==expected
        assert batch.history==batch.new
        assert batch.new[0].speaker_id==batch.new[1].speaker_id!='self'
        assert batch.new[-1].speaker_id=='self'
        if peer.endswith('@chatroom'):
            assert batch.new[2].speaker_id!=batch.new[0].speaker_id
    finally:
        source.close()


def test_new_group_table_and_same_second_arrival_are_received(tmp_path):
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    path=tmp_path/'message_0.db'
    with sqlite3.connect(path) as c:c.execute('CREATE TABLE Name2Id(user_name TEXT)')
    source=MemoryDatabase(path)
    feed=DatabaseFeed(lambda:[source],'g@chatroom','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:101.7)
    assert not feed.poll().new
    database(path,[(1,1,101,1,2,'刚进群的第一条')])
    assert [m.text for m in feed.poll().new]==['刚进群的第一条']
    source.close()


def encrypted_page(payload,pgno,key,salt):
    import hashlib,hmac,struct
    from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
    iv=b'0123456789abcdef';start=16 if pgno==1 else 0
    plain=payload*( (4016-start)//len(payload)+1);plain=plain[:4016-start]
    enc=Cipher(algorithms.AES(key),modes.CBC(iv)).encryptor()
    ciphertext=enc.update(plain)+enc.finalize()
    mac=hashlib.pbkdf2_hmac('sha512',key,bytes(x^0x3a for x in salt),2,32)
    tag=hmac.new(mac,ciphertext+iv+struct.pack('<I',pgno),'sha512').digest()
    return (salt if start else b'')+ciphertext+iv+tag


def wal_bytes(frames):
    import struct
    def checksum(data,previous=(0,0)):
        words=struct.unpack('<'+'I'*(len(data)//4),data);a,b=previous
        for i in range(0,len(words),2):a=(a+words[i]+b)%2**32;b=(b+words[i+1]+a)%2**32
        return a,b
    salts=b'abcdefgh'
    header=struct.pack('>IIII',0x377f0682,3007000,4096,0)+salts
    sums=checksum(header);out=header+struct.pack('>II',*sums)
    for pgno,size,page in frames:
        fh=struct.pack('>II',pgno,size)
        sums=checksum(fh+page,sums);out+=fh+salts+struct.pack('>II',*sums)+page
    return out


def test_authenticated_wal_ignores_uncommitted_tail_and_rejects_corruption():
    from xiaomao.database import overlay_wal,DatabaseUnavailable
    import hashlib
    key=bytes(range(32));salt=b'0123456789abcdef'
    mac=hashlib.pbkdf2_hmac('sha512',key,bytes(x^0x3a for x in salt),2,32)
    wal=wal_bytes([(2,2,encrypted_page(b'A',2,key,salt)),(2,0,encrypted_page(b'B',2,key,salt))])
    result=overlay_wal(bytearray(b'Z'*8192),wal,key,mac)
    assert result[:4096]==b'Z'*4096 and result[4096:8112]==b'A'*4016
    assert result[8112:]==b'\0'*80
    bad=bytearray(wal);bad[100]^=1
    with pytest.raises(DatabaseUnavailable):overlay_wal(bytearray(8192),bad,key,mac)
    with pytest.raises(ValueError):overlay_wal(bytearray(8192),wal,bytes(32),bytes(32))


def test_multiline_question_is_not_mistaken_for_sender_prefix(tmp_path):
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    path=tmp_path/'message_0.db';database(path,[(1,1,100,1,2,'问题：\n帮我看看'),(2,2,100,2,2,'Question:\nhello')])
    source=MemoryDatabase(path)
    feed=DatabaseFeed(lambda:[source],'g@chatroom','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:101)
    assert [m.text for m in feed.poll().history]==['问题：\n帮我看看','Question:\nhello']
    source.close()


def test_group_rename_cannot_redirect_replies_to_a_different_chat(tmp_path):
    from xiaomao.database import DatabaseHub,DatabaseUnavailable
    from xiaomao.config import Settings
    import threading
    path=tmp_path/'db_storage/contact/contact.db';path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT)')
        c.execute("INSERT INTO contact VALUES('a@chatroom','甲群','')")
    hub=DatabaseHub(Settings(tmp_path,group='甲群'),threading.Event())
    hub.account={'id':'account','path':str(tmp_path),'wxid':'wxid_self'};hub._ensure=lambda *a:None
    assert hub.feed('甲群').group_id=='a@chatroom'
    with sqlite3.connect(path) as c:
        c.execute("UPDATE contact SET nick_name='乙群'")
        c.execute("INSERT INTO contact VALUES('b@chatroom','甲群','')")
    with pytest.raises(DatabaseUnavailable):hub.feed('甲群')
    hub.close()


def test_explicitly_replaced_contact_gets_its_own_feed_after_window_reload(tmp_path):
    from dataclasses import replace
    import threading
    from xiaomao.database import DatabaseHub
    from xiaomao.config import Settings
    from xiaomao.contacts import Contact
    path=tmp_path/'db_storage/contact/contact.db';path.parent.mkdir(parents=True)
    old=Contact('old@chatroom','acct','群友','group')
    replacement=replace(old,id='new@chatroom')
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT)')
        conn.execute('INSERT INTO contact VALUES(?,?,?)',(old.id,old.name,''))
    cfg=Settings(tmp_path,group=old.name,conversation_targets={old.name:old.binding})
    hub=DatabaseHub(cfg,threading.Event(),process_id=71)
    hub.account={'id':'acct','path':str(tmp_path),'wxid':'self'}
    hub._ensure=lambda *args:None
    try:
        original=hub.feed(old.name)
        with sqlite3.connect(path) as conn:
            conn.execute('UPDATE contact SET username=?',(replacement.id,))
        hub.settings=replace(cfg,conversation_targets={replacement.name:replacement.binding})
        current=hub.feed(replacement.name)
        assert original is not current and current.group_id==replacement.id
    finally:hub.close()


def test_new_encrypted_shard_and_rotated_key_do_not_cache_missing_key(tmp_path):
    from xiaomao.database import DatabaseHub,DatabaseUnavailable
    from xiaomao.config import Settings
    from xiaomao.dbcodec.crypto import decode_page
    import threading,hashlib
    path=tmp_path/'new.db';salt=b'0123456789abcdef';key=bytes(range(32))
    path.write_bytes(encrypted_page(b'A',1,key,salt))
    hub=DatabaseHub(Settings(tmp_path),threading.Event())
    with pytest.raises(DatabaseUnavailable):hub._snapshot(path)
    hub.keys[salt.hex()]=key.hex()
    first=hub._snapshot(path)
    key2=bytes(reversed(range(32)));hub.keys[salt.hex()]=key2.hex()
    path.write_bytes(encrypted_page(b'B',1,key2,salt))
    second=hub._snapshot(path)
    mac=hashlib.pbkdf2_hmac('sha512',second.key,bytes(x^0x3a for x in salt),2,32)
    assert decode_page(path.read_bytes(),second.key,mac,1)[16:4016]==b'B'*4000
    assert first is not second
    hub.close()
