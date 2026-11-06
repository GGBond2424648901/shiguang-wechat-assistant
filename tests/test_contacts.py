import sqlite3
from dataclasses import asdict,replace
import pytest


def contacts():
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE contact(username,nick_name,remark,alias,delete_flag,verify_flag,local_type)')
    conn.executemany('INSERT INTO contact VALUES(?,?,?,?,0,0,1)',[
        ('a','小明','朋友甲','alpha'),('b','小红(2)','','beta'),
        ('r@chatroom','读书群','',''),('same','重复','',''),('same@chatroom','重复','',''),
        ('self','我','','')])
    return conn


def test_directory_lists_personal_group_and_searches_alias_beyond_visible_rows():
    from xiaomao.contacts import read_directory,search_directory
    rows=read_directory(contacts(),'self','account')
    assert len(rows)==5 and {r.kind for r in rows}=={'private','group'}
    assert search_directory(rows,'alpha')[0].name=='朋友甲'
    assert search_directory(rows,'小明')[0].id=='a'
    assert search_directory(rows,'群',kind='group')[0].id=='r@chatroom'
    assert not any(r.selectable for r in rows if r.name=='重复')


def test_directory_hides_nonfriend_member_metadata_but_keeps_groups_and_disabled_services():
    from xiaomao.contacts import read_directory
    conn=contacts()
    conn.executemany('INSERT INTO contact VALUES(?,?,?,?,0,?,?)',[
        ('member-only','群成员','','',0,3),
        ('unknown-system','缓存条目','','',0,0),
        ('public-account','公众号','','',8,1),
        ('room@chatroom','收藏群','','',0,2)])
    rows=read_directory(conn,'self','account')
    assert not {'member-only','unknown-system'} & {r.id for r in rows}
    assert next(r for r in rows if r.id=='room@chatroom').kind=='group'
    service=next(r for r in rows if r.id=='public-account')
    assert service.kind=='service' and not service.selectable


def test_hidden_nonfriend_same_title_still_prevents_title_only_friend_impersonation():
    from xiaomao.contacts import read_directory
    conn=contacts()
    conn.execute('INSERT INTO contact VALUES(?,?,?,?,0,0,3)',('member-only','朋友甲','',''))
    rows=read_directory(conn,'self','account')
    assert not any(r.id=='member-only' for r in rows)
    assert not next(r for r in rows if r.id=='a').selectable


def test_binding_uses_exact_account_id_and_detects_rename_and_duplicate():
    from xiaomao.contacts import read_directory,resolve_contact
    from xiaomao.config import Settings
    rows=read_directory(contacts(),'self','account')
    cfg=Settings('.',group='朋友甲',conversation_targets={'朋友甲':{'id':'a','account':'account','kind':'private'}})
    assert resolve_contact(rows,cfg,'朋友甲').id=='a'
    for bad in ('other_account',''):
        with pytest.raises(ValueError):resolve_contact(read_directory(contacts(),'self',bad),cfg,'朋友甲')
    with pytest.raises(ValueError):resolve_contact([replace(r,name='改名') if r.id=='a' else r for r in rows],cfg,'朋友甲')
    with pytest.raises(ValueError):resolve_contact(rows,Settings('.',group='重复'),'重复')


def test_personal_selection_roundtrips_kind_and_identity_without_enabling_all(tmp_path):
    from xiaomao.config import Settings
    from xiaomao.contacts import read_directory
    from test_vision import binding
    target=next(r for r in read_directory(contacts(),'self','account') if r.id=='b')
    cfg=Settings(tmp_path,calibration=asdict(binding())).select_conversation(target)
    cfg.save();loaded=Settings.load(tmp_path)
    assert loaded.allowed_groups==('小红(2)',)
    assert loaded.for_group('小红(2)').binding.target_kind=='private'
    assert loaded.conversation_targets['小红(2)']['id']=='b'
    assert loaded.memory_scope!='小红(2)' and 'account' in loaded.memory_scope


def test_private_header_exact_name_does_not_accept_group_member_suffix():
    from test_vision import binding,screenshot,RecordedOCR
    from xiaomao.vision import Vision
    b=replace(binding(),header_label='小红(2)',target_group='小红(2)',target_kind='private')
    assert Vision(RecordedOCR(header='小红(2)')).read(screenshot(),b).safe
    assert not Vision(RecordedOCR(header='小红(2)(3)')).read(screenshot(),b).safe


def test_short_title_fallback_preserves_all_ink_including_distant_suffix():
    from PIL import Image,ImageDraw
    from xiaomao.vision import read_header,TextLine
    image=Image.new('RGB',(885,45),'#ededed');draw=ImageDraw.Draw(image)
    draw.rectangle((5,16,35,30),fill='black')
    draw.rectangle((600,16,620,30),fill='black')
    calls=[]
    def ocr(image):
        calls.append(image.size)
        if len(calls)==1:return []
        assert image.getpixel((645,30))==(0,0,0)
        return [TextLine('豆豆(3)',(30,30,646,45),.99)]
    assert read_header(ocr,image)[0].text=='豆豆(3)'
    assert calls==[(885,45),(676,75)]


def test_contact_switch_keeps_pause_and_exact_private_binding(tmp_path,monkeypatch):
    from test_vision import binding
    from xiaomao.config import Settings
    from xiaomao.contacts import read_directory
    from xiaomao.selection import switch_group
    rows=read_directory(contacts(),'self','account');target=next(r for r in rows if r.id=='b')
    monkeypatch.setattr('xiaomao.selection.load_directory',lambda *args:rows)
    cfg=Settings(tmp_path,calibration=asdict(binding()));cfg.save();events=[]
    class Client:
        def status(self):return {'running':True,'mode':'paused'}
        def stop(self):events.append('stop')
        def wait_stopped(self):events.append('wait')
        def start(self,initial_mode=None):
            assert initial_mode=='paused'
            loaded=Settings.load(tmp_path);events.append('start')
            assert loaded.binding.target_kind=='private' and loaded.conversation_targets[target.name]==target.binding
            return {'group':loaded.group,'group_names':list(loaded.allowed_groups),'reply_mode':loaded.reply_mode,'mode':initial_mode}
    switch_group(tmp_path,target.name,Client(),binding=cfg.binding,contact=target)
    assert events==['stop','wait','start']


def test_stale_directory_is_rejected_before_stopping_current_chat(tmp_path,monkeypatch):
    from test_vision import binding
    from xiaomao.config import Settings
    from xiaomao.contacts import read_directory
    from xiaomao.selection import switch_group
    rows=read_directory(contacts(),'self','account');target=next(r for r in rows if r.id=='b')
    monkeypatch.setattr('xiaomao.selection.load_directory',lambda *args:[])
    cfg=Settings(tmp_path,calibration=asdict(binding()));cfg.save()
    class Client:
        def stop(self):raise AssertionError('stale recipient must not disturb the live worker')
    with pytest.raises(ValueError):switch_group(tmp_path,target.name,Client(),binding=cfg.binding,contact=target)
    assert Settings.load(tmp_path).group==cfg.group


def test_bound_private_chat_never_imports_legacy_same_display_name_context(tmp_path):
    from xiaomao.config import Settings
    from xiaomao.domain import Message
    from xiaomao.store import StateStore
    from xiaomao.engine import Engine
    from test_engine import Screen,IdentityVision,ReplyModel
    cfg=Settings(tmp_path,group='朋友甲',conversation_targets={'朋友甲':{'id':'a','account':'account','kind':'private'}})
    store=StateStore(cfg.runtime)
    store.load_context=lambda *args:[Message('其他同名群的秘密')]
    try:
        engine=Engine(cfg,store,Screen(),IdentityVision(),ReplyModel(),clock=lambda:100)
        assert not engine.context and not engine.legacy_context
    finally:store.close()


def test_private_name_cannot_collide_with_another_group_member_count_header():
    from xiaomao.contacts import read_directory,resolve_contact
    from xiaomao.config import Settings
    conn=contacts();conn.execute("UPDATE contact SET nick_name='读书群(3)' WHERE username='b'")
    rows=read_directory(conn,'self','account')
    personal=next(r for r in rows if r.id=='b');group=next(r for r in rows if r.id=='r@chatroom')
    assert not personal.selectable and not group.selectable
    bound=Settings('.',group=personal.name,conversation_targets={personal.name:personal.binding})
    with pytest.raises(ValueError):resolve_contact(rows,bound,personal.name)


def test_self_header_cannot_impersonate_a_group_even_though_self_is_not_selectable():
    from xiaomao.contacts import read_directory
    conn=contacts();conn.execute("UPDATE contact SET nick_name='读书群(3)' WHERE username='self'")
    rows=read_directory(conn,'self','account')
    assert not next(r for r in rows if r.id=='r@chatroom').selectable


def test_removing_saved_contact_releases_binding_for_explicit_new_choice():
    from xiaomao.config import Settings
    from xiaomao.contacts import Contact
    old=Contact('old','account','朋友甲','private')
    cfg=Settings('.',group='保留群',group_names=('保留群','朋友甲'),conversation_targets={'朋友甲':old.binding})
    cfg=cfg.with_saved_conversations(('保留群',))
    assert '朋友甲' not in cfg.conversation_targets
    changed=cfg.select_conversation(replace(old,id='new'))
    assert changed.conversation_targets['朋友甲']['id']=='new'


def test_add_private_and_group_to_monitored_list_preserves_primary_and_pins_ids(tmp_path):
    from xiaomao.contacts import Contact
    from xiaomao.config import Settings
    from xiaomao.selection import bind_saved_conversations
    group=Contact('room@chatroom','account','读书群','group')
    peer=Contact('peer','account','好友','private')
    other=Contact('other','account','未选择','private')
    rows=[group,peer,other]
    cfg=Settings(tmp_path,group=group.name)
    names=[group.name,peer.name]
    selected=bind_saved_conversations(cfg,names,rows,contacts=[peer])
    selected.save()
    loaded=Settings.load(tmp_path)
    assert loaded.group == group.name and loaded.allowed_groups == tuple(names)
    assert loaded.conversation_targets == {group.name:group.binding, peer.name:peer.binding}
    assert loaded.for_group(peer.name).memory_scope == 'account/peer'
    assert loaded.for_group(group.name).memory_scope == 'account/room@chatroom'
    with pytest.raises(ValueError):bind_saved_conversations(cfg,names,rows)
    with pytest.raises(ValueError):bind_saved_conversations(loaded,names,[group,replace(peer,account='changed')])
    with pytest.raises(ValueError):bind_saved_conversations(loaded,names,rows,contacts=[replace(peer,id='changed')])


def test_first_binding_never_imports_unproven_display_name_history(tmp_path,monkeypatch):
    from test_vision import binding
    from xiaomao.config import Settings
    from xiaomao.contacts import Contact
    from xiaomao.domain import Message
    from xiaomao.memory import ConversationMemory
    from xiaomao.selection import switch_group
    contact=Contact('r@chatroom','current-account','读书群','group')
    cfg=Settings(tmp_path,group=contact.name,calibration=asdict(binding()))
    cfg.save()
    old=ConversationMemory(cfg.runtime,contact.name)
    old.remember(Message('另一个登录账号的群聊记录',message_id='old'))
    monkeypatch.setattr('xiaomao.selection.load_directory',lambda *args:[contact])
    class Client:
        def status(self):return {'running':False,'mode':'stopped'}
        def stop(self):pass
        def wait_stopped(self):pass
        def start(self,initial_mode=None):raise AssertionError('selection must keep a stopped worker stopped')
    switch_group(tmp_path,contact.name,Client(),binding=cfg.binding,contact=contact)
    loaded=Settings.load(tmp_path)
    assert not ConversationMemory(cfg.runtime,loaded.memory_scope).messages
    assert ConversationMemory(cfg.runtime,contact.name).messages[0].message_id == 'old'


def test_unbound_group_title_cannot_open_a_bound_private_archive(tmp_path):
    from xiaomao.config import Settings
    from xiaomao.domain import Message
    from xiaomao.memory import ConversationMemory
    cfg=Settings(tmp_path,group='好友',conversation_targets={'好友':{'account':'account','id':'peer','kind':'private'}})
    archive=ConversationMemory(cfg.runtime,cfg.memory_scope)
    archive.remember(Message('仅供好友阅读',message_id='q'))
    unbound=Settings(tmp_path,group='account/peer')
    assert not ConversationMemory(unbound.runtime,unbound.memory_scope).messages
