"""The same native window switches pages without losing unsaved settings or showing stale queue data."""
from types import SimpleNamespace
import tkinter as tk
import pytest
from xiaomao.config import Settings


def test_new_preference_defaults_on_and_roundtrips_for_existing_settings(tmp_path):
    cfg=Settings(tmp_path)
    assert cfg.split_replies is True
    cfg.split_replies=False;cfg.save()
    assert Settings.load(tmp_path).split_replies is False
    with pytest.raises(ValueError):Settings(tmp_path,split_replies='false')


def test_companion_picker_applies_commands_only_to_selected_conversation(native_root,tmp_path):
    from xiaomao.companion_ui import CompanionPage
    cfg=Settings(tmp_path,group='摄影群',group_names=('摄影群','小夏'),
                 conversation_targets={'摄影群':dict(id='g@chatroom',kind='group',account='a'),
                                       '小夏':dict(id='peer',kind='private',account='a')})
    cfg.save()
    status={'group':cfg.group,'group_names':list(cfg.allowed_groups),
            'groups':[{'name':n,'mode':'auto','pending_count':0,'companion':{'reason':n+'状态'}} for n in cfg.allowed_groups]}
    calls=[]
    client=SimpleNamespace(status=lambda:status,companion_configure=lambda g,s,p:calls.append((g,s,p['gentle_followups'])) or status,
                           companion_preview=lambda **kw:calls.append(kw) or status)
    p=SimpleNamespace(settings=cfg,directory=tmp_path,client=client,view=SimpleNamespace(status=status),
                      work=lambda action,success,*a,**kw:success(action()),show_status=lambda s:None)
    page=CompanionPage(native_root,p)
    try:
        page.on_show()
        page.scope_choice.set('小夏');page._choose_scope()
        page.enabled.set(True);page.save();page.preview()
        assert p.settings.group=='摄影群' and page.cfg.group=='小夏'
        assert calls==[('小夏','a/peer',True),{'group':'小夏','scope':'a/peer'}]
    finally:page.on_hide();page.destroy()


def test_workspace_button_preserves_native_state_and_customized_filter_color(native_root):
    from xiaomao.workspace_ui import button,PEACH
    from tkinter import ttk
    box=tk.Frame(native_root)
    calls=[]
    try:
        control=button(box,'刷新',lambda:calls.append('refresh'),compact=True,icon_name='refresh')
        control.grid(row=0,column=0)
        assert isinstance(control,ttk.Button) and control.winfo_manager()=='grid'
        control.configure(bg=PEACH,padx=7,pady=4,font=('Microsoft YaHei UI',11))
        assert control.cget('bg')==PEACH
        control.configure(state='disabled');control.invoke();assert not calls
        control.configure(state='normal',text='再次刷新');control.invoke()
        assert calls==['refresh'] and control.cget('text')=='再次刷新'
        assert control.cget('state')=='normal' and control.cget('takefocus')
    finally:box.destroy()


def test_native_mouse_and_keyboard_button_activation_and_disabled_guard(native_root):
    from xiaomao.workspace_ui import button
    window=tk.Toplevel(native_root);window.geometry('260x90+3400+2000')
    activated=[]
    try:
        control=button(window,'刷新',lambda:activated.append(1),icon_name='refresh')
        control.pack(padx=12,pady=10);window.update()
        control.focus_force();window.update()
        control.event_generate('<Enter>');window.update()
        assert control.instate(('focus','active'))
        control.event_generate('<ButtonPress-1>',x=8,y=8);window.update()
        assert control.instate(('pressed',))
        control.event_generate('<ButtonRelease-1>',x=8,y=8);window.update()
        assert activated==[1] and not control.instate(('pressed',))
        control.event_generate('<Return>');window.update()
        assert activated==[1,1]
        control.configure(state='disabled');control.event_generate('<Return>');window.update()
        assert activated==[1,1]
    finally:window.destroy()


def test_native_pages_keep_their_controls_and_show_real_activity(tmp_path,native_root):
    from xiaomao.persona import save_persona
    save_persona(tmp_path,'大肥鱼，聊天时用自己的话。')
    from xiaomao.theme import GlassView
    root=tk.Toplevel(native_root);root.geometry('930x620+3400+2000')
    try:
        events=[]
        p=SimpleNamespace(root=root,directory=tmp_path,settings=Settings(tmp_path),connection_error=None)
        for key in ('mode','reason','notice','details','target_group','reply_scope','display_name','model_name','secret','secret_state'):
            setattr(p,key,tk.StringVar(master=root,value=''))
        p.target_group.set(p.settings.group)
        for key in ('at_unique','auto_on_launch','split_replies','owner_tone'):
            setattr(p,key,tk.BooleanVar(master=root,value=True))
        for key in ('choose_contact','edit_groups','edit_persona','edit_companion','save_settings','test_model','calibrate','stop_and_exit','switch_selected_group'):
            setattr(p,key,lambda k=key:events.append(k))
        p.control=lambda op:events.append(op)
        p.client=SimpleNamespace(pending=lambda:{'tasks':[]},reply_latest=lambda:None)
        p.show_pending=lambda data:events.append(('queue',len(data['tasks'])))
        p.set_preview=lambda text:None
        p.show_status=lambda data:None
        p.work=lambda call,callback,*args,**kw:callback(call())
        p.switch_busy=False
        view=p.view=GlassView(p);view.render()
        view.pack(fill='both',expand=True);root.update()
        view.status={'mode':'auto','pending_count':2,'counts':{'verified':11,'unknown':1},'reason':'后台正在接收'}
        view.update_text()
        assert view.metrics['pending'].get()=='2' and view.metrics['verified'].get()=='11'
        assert '1' in view.unknown_note.get()
        assert view.actions['enable'].cget('state')=='disabled'
        view.actions['enable'].invoke()
        assert 'enable' not in events
        view.status['mode']='observe';view.update_text()
        assert view.actions['enable'].cget('state')=='normal'
        view.select('settings')
        field=view.pages['settings'].key_entries[0];field.insert(0,'unsaved input')
        view.select('assistant')
        assert not field.winfo_ismapped()
        view.actions['choose'].invoke();assert events[-1]=='choose_contact'
        view.select('persona');persona=view.pages['persona'];persona.select_category('custom')
        assert persona.editor.get('1.0','end-1c')=='大肥鱼，聊天时用自己的话。'
        view.select('companion');assert view.pages['companion'].active
        view.select('pending');assert ('queue',0) in events and view.page=='assistant'
        assert not view.pages['companion'].active
        view.select('settings');assert field.get()=='unsaved input'
        assert view.pages['settings'].winfo_manager()=='grid'
        view.nav['settings'].focus_force();root.update()
        view.nav['settings'].event_generate('<Alt-KeyPress-3>');root.update()
        assert view.page=='persona'
        persona.editor.insert('end-1c','保持自然。');root.update()
        persona.editor.focus_force();root.update()
        persona.editor.event_generate('<Control-s>');root.update()
        from xiaomao.persona import load_persona
        assert '保持自然。' in load_persona(tmp_path)
        view.nav['persona'].focus_force();root.update()
        view.nav['persona'].event_generate('<Down>');root.update()
        assert view.page=='companion'
        view.nav['companion'].event_generate('<End>');root.update()
        assert view.page=='settings' and field.get()=='unsaved input'
        from xiaomao.domain import Message
        from xiaomao.memory import ConversationMemory
        p.settings=Settings(tmp_path,group='新的私聊',conversation_targets={'新的私聊':{'account':'a','id':'p1','kind':'private'}})
        memory=ConversationMemory(p.settings.runtime,p.settings.memory_scope)
        memory.remember(Message('真实测试回执',direction='out',speaker_id='self',message_id='p1/s/9',
                                reply_to_id='p1/s/8',reply_to_speaker='p_'+'b'*16,created_at=123456))
        view.status['group_names']=['新的私聊'];view.select('assistant')
        root.update()
        assert view.recent_text.winfo_height()>=30 and p.preview.winfo_height()>=30
        recent=view.recent_text.get('1.0','end-1c')
        assert '新的私聊' in recent and '真实测试回执' in recent and 'p1/s/' not in recent
    finally:root.destroy()


def test_private_owner_tone_requires_exact_bound_peer_and_account(tmp_path):
    from dataclasses import replace
    def peer(account,identity,kind='private'):
        return {'account':account,'id':identity,'kind':kind}
    a=peer('account-a','wx-a');b=peer('account-a','wx-b')
    cfg=Settings(tmp_path,group='私聊',conversation_targets={'私聊':a})
    assert cfg.private_tone_key and not cfg.owner_confirmed
    cfg.set_owner_tone(True);cfg.save()
    assert Settings.load(tmp_path).owner_confirmed
    for other in (b,peer('account-b','wx-a'),peer('account-a','room@chatroom','group')):
        other_cfg=replace(cfg,conversation_targets={'私聊':other})
        assert not other_cfg.owner_confirmed
        other_cfg.set_owner_tone(False);assert other_cfg.owner_private_token==cfg.owner_private_token
    assert not replace(cfg,receive_mode='screen').owner_confirmed
    cfg.set_owner_tone(False);assert not cfg.owner_confirmed


def test_editing_persona_does_not_interrupt_pending_conversations(tmp_path,native_root):
    from xiaomao.persona_ui import PersonaPage
    from xiaomao.persona import load_persona,save_persona
    root=tk.Frame(native_root)
    try:
        save_persona(tmp_path,'原来的草稿')
        p=SimpleNamespace(directory=tmp_path,notice=tk.StringVar(root),work=lambda action,done,failed,**kw:done(action()))
        editor=PersonaPage(root,p);editor.select_category('custom')
        editor.editor.delete('1.0','end');editor.editor.insert('1.0','大肥鱼，聊天时用自己的话。')
        editor.save()
        assert load_persona(tmp_path)=='大肥鱼，聊天时用自己的话。' and '保存' in p.notice.get()
    finally:root.destroy()


def test_failed_switch_after_committing_new_target_cannot_inherit_owner_checkbox(tmp_path):
    from xiaomao.gui import Panel
    from dataclasses import replace
    cfg=Settings(tmp_path,group='甲',conversation_targets={name:{'account':'a','id':name,'kind':'private'} for name in ('甲','乙')})
    cfg.set_owner_tone(True)
    target=replace(cfg,group='乙');target.save()
    class Value:
        def __init__(self,x):self.x=x
        def set(self,x):self.x=x
        def get(self):return self.x
    p=Panel.__new__(Panel)
    p.directory=tmp_path;p.settings=cfg;p.switch_busy=False;p.target_group=Value('乙')
    p.notice=Value('');p.owner_tone=Value(True);p.at_unique=Value(False)
    p.view=SimpleNamespace(set_switch_busy=lambda value:None,render=lambda:None,refresh_pending=lambda:None,page='assistant')
    p.work=lambda act,done,failed:failed('无法启动')
    p._switch(False)
    assert p.settings.group=='乙' and p.target_group.get()=='乙'
    assert p.owner_tone.get() is False


def test_queue_labels_distinguish_members_without_exposing_protocol_ids(tmp_path):
    from xiaomao.gui import Panel
    captured=[]
    p=Panel.__new__(Panel);p.settings=Settings(tmp_path)
    p.preview=SimpleNamespace(configure=lambda **kw:None,delete=lambda *a:None,insert=lambda _,s:captured.append(s))
    ids=['p_'+'a'*16,'p_'+'b'*16,'p_'+'a'*16]
    p.show_pending({'tasks':[dict(sender=id,messages=[str(i)],reply='') for i,id in enumerate(ids)]})
    assert not any(id in captured[0] for id in ids)
    assert captured[0].count('成员 1')==2 and captured[0].count('成员 2')==1


def test_memory_summary_uses_the_same_member_labels_as_facts():
    from xiaomao.ui_labels import memory_text,member_label
    people={};a='p_'+'a'*16;b='p_'+'b'*16
    assert memory_text(f'{a}在说{b}的约定，引用 m_{"c"*16}。',people)=='成员 1在说成员 2的约定，引用 聊天记录。'
    assert member_label(b,people,'')=='成员 2'


def test_poll_refresh_keeps_queue_visible_and_shares_one_pending_read(tmp_path):
    from xiaomao.theme import GlassView
    events=[];requests=[]
    p=SimpleNamespace(settings=Settings(tmp_path),notice=SimpleNamespace(set=lambda v:None),set_preview=events.append,show_pending=lambda data:events.append('result'),
                      client=SimpleNamespace(pending=lambda:{}),work=lambda call,ok,fail,**kw:requests.append(ok))
    view=SimpleNamespace(panel=p,page='assistant',status={},_receipt_cache={},_show_recent=lambda *args:None,
                         _decorate_queue=lambda:None,queue_refresh=SimpleNamespace(configure=lambda **kw:None))
    view._current_scope=GlassView._current_scope.__get__(view)
    GlassView.refresh_pending(view);requests.pop()(({},([], '', {})))
    GlassView.refresh_pending(view,background=True)
    GlassView.refresh_pending(view,background=True)
    assert events==['正在读取当前队列…','result'] and len(requests)==1


def test_first_live_poll_fetches_empty_queue_even_if_start_already_returned_status():
    from xiaomao.gui import Panel
    state={'group':'甲','mode':'auto','pending_count':0}
    requests=[]
    panel=SimpleNamespace(closing=False,poll_busy=False,poll=lambda:None,client=SimpleNamespace(status=lambda:state),
                          root=SimpleNamespace(after=lambda *args:None),show_status=lambda _:None)
    panel.view=SimpleNamespace(page='assistant',status=state,_queue_scope=None,
        refresh_pending=lambda **kwargs:requests.append('read'),_current_scope=lambda:('account',('甲',)))
    panel.work=lambda fn,ok,*args,**kwargs:ok(fn())
    Panel.poll(panel)
    assert requests==['read']


def test_queue_callbacks_discard_old_recipient_and_other_pages(tmp_path):
    from dataclasses import replace
    from xiaomao.theme import GlassView
    cfg=Settings(tmp_path,group='保持主会话',group_names=('保持主会话','朋友'))
    requests=[];shown=[]
    p=SimpleNamespace(settings=cfg,set_preview=lambda _:None,show_pending=shown.append,
                      client=SimpleNamespace(pending=lambda:{}),work=lambda call,ok,fail,**kw:requests.append(ok))
    view=SimpleNamespace(panel=p,page='assistant',status={},_receipt_cache={},_show_recent=lambda *args:None,
                         _decorate_queue=lambda:None,queue_refresh=SimpleNamespace(configure=lambda **kw:None))
    view._current_scope=GlassView._current_scope.__get__(view)
    GlassView.refresh_pending(view)
    p.settings=replace(cfg,conversation_targets={'朋友':{'id':'new-peer','account':'new-account','kind':'private'}})
    GlassView.refresh_pending(view,background=True)
    requests[0](({'tasks':['旧账号']},([], '', {})));requests[1](({'tasks':['新账号']},([], '', {})))
    GlassView.refresh_pending(view);view.page='settings'
    requests[2](({'tasks':['隐藏后的队列']},([], '', {})))
    assert shown==[{'tasks':['新账号']}]


def test_click_scope_reads_only_its_own_queue_and_receipts(tmp_path,native_root):
    from xiaomao.theme import GlassView
    from xiaomao.gui import Panel
    cfg=Settings(tmp_path,group='甲群',group_names=('甲群','朋友'),conversation_targets={
        '甲群':{'account':'a','id':'a@chatroom','kind':'group'},
        '朋友':{'account':'a','id':'p','kind':'private'}})
    window=tk.Toplevel(native_root);window.geometry('1200x800+3400+2000')
    try:
        panel=Panel.__new__(Panel);panel.root=window;panel.settings=cfg;panel.directory=tmp_path
        panel.reason=tk.StringVar(window);panel.notice=tk.StringVar(window);panel.switch_busy=False
        def pending():
            return {'tasks':[dict(group=g,sender='',messages=[g+'的问题'],reply='',state='generated') for g in cfg.allowed_groups]}
        panel.client=SimpleNamespace(pending=pending)
        panel.work=lambda fn,ok,*args,**kw:ok(fn())
        view=panel.view=GlassView(panel);view.pack(fill='both',expand=True)
        view.status={'mode':'auto','group_names':list(cfg.allowed_groups)}
        view.update_text();window.update()
        second=view.watch_table.get_children()[1]
        view.watch_table.selection_set(second)
        window.update()
        text=panel.preview.get('1.0','end')
        assert '朋友的问题' in text and '甲群的问题' not in text
        assert panel.settings.group=='甲群' and cfg.allowed_groups==('甲群','朋友')
        assert view.actions['recover'].instate(('disabled',))
        view.all_queues.invoke()
        assert all(g+'的问题' in panel.preview.get('1.0','end') for g in cfg.allowed_groups)
    finally:window.destroy()


def test_receipt_preview_reads_only_current_account_watched_bound_peers(tmp_path):
    from xiaomao.domain import Message
    from xiaomao.memory import ConversationMemory
    from xiaomao.theme import receipt_previews
    member='p_'+'a'*16
    targets={n:{'account':account,'id':peer,'kind':'private'} for n,account,peer in
             [('主','a','p1'),('朋友','a','p2'),('非监听','a','p3'),('旧账号','b','p4')]}
    cfg=Settings(tmp_path,group='主',group_names=tuple(targets),conversation_targets=targets)
    def write(peer,account,n,body,source):
        record=Message(body,direction='out',message_id=f'{peer}/s/{n}',reply_to_id=source,
                       reply_to_speaker=member,speaker_id='self',created_at=1000+n)
        ConversationMemory(cfg.runtime,account+'/'+peer).remember(record)
    for i in range(1,6):write('p1','a',i,'已确认 '+str(i),'p1/s/200')
    write('p1','a',10,'手动发送','');write('p1','a',11,'另一会话的来源','p2/s/123')
    write('p2','a',9,'另一个已选会话','p2/s/200')
    write('p3','a',30,'不在监听范围','p3/s/200');write('p4','b',40,'另一个账号','p4/s/200')
    selected=['主','朋友','旧账号']
    rows,note,cache=receipt_previews(cfg,selected,{})
    assert [row[2] for row in rows]==['另一个已选会话','已确认 5','已确认 4','已确认 3']
    assert {scope for scope in cache}=={'a/p1','a/p2'} and not note
    path=ConversationMemory(cfg.runtime,'a/p2').path
    path.write_bytes(b'broken')
    rows,note,_=receipt_previews(cfg,selected,cache)
    assert len(rows)==4 and '暂不可读' in note
    assert path.read_bytes()==b'broken'


def test_read_only_preview_of_new_configuration_creates_no_runtime_files(tmp_path):
    from xiaomao.theme import receipt_previews
    cfg=Settings(tmp_path,group='主',conversation_targets={'主':{'account':'a','id':'p1','kind':'private'}})
    rows,note,cache=receipt_previews(cfg,['主'],{})
    assert not rows and not cache and not cfg.runtime.exists()


def test_proactive_receipt_preview_uses_verified_scoped_receipt_and_excludes_old_or_unconfirmed_self_text(tmp_path):
    from xiaomao.domain import Message
    from xiaomao.memory import ConversationMemory
    from xiaomao.store import StateStore
    from xiaomao.theme import receipt_previews
    cfg=Settings(tmp_path,group='朋友',conversation_targets={'朋友':{'account':'a','id':'p1','kind':'private'}})
    scope='a/p1';memory=ConversationMemory(cfg.runtime,scope)
    old=Message('早呀',direction='out',speaker_id='self',message_id='p1/s/1',created_at=999)
    actual=Message('早呀',direction='out',speaker_id='self',message_id='p1/s/2',created_at=1001)
    unconfirmed=Message('别把这条当成功',direction='out',speaker_id='self',message_id='p1/s/3',created_at=1003)
    manual=Message('手动发送',direction='out',speaker_id='self',message_id='p1/s/4',created_at=1004)
    for message in (old,actual,unconfirmed,manual):memory.remember(message)
    store=StateStore(cfg.runtime)
    try:
        task=store.create('first',1000)
        store.mark_sending(task,1000,group=scope,reply=actual.text,baseline_ids=[old.message_id])
        assert store.confirm_receipt(task,scope,actual)
        pending=store.create('second',1002)
        store.mark_sending(pending,1002,group=scope,reply=unconfirmed.text)
        other=store.create('other',1003)
        store.mark_sending(other,1003,group='b/p1',reply=manual.text)
        assert store.confirm_receipt(other,'b/p1',manual)
        rows,note,_=receipt_previews(cfg,['朋友'],{})
        assert rows==[(1001,'朋友','早呀')] and not note
    finally:store.close()
