from dataclasses import replace
import pytest
from xiaomao.config import Settings
from xiaomao.domain import Message, ReplyTask
from xiaomao.resident import Resident
from xiaomao.store import StateStore
from test_reply_preferences import configured
from test_engine import Screen, IdentityVision, ReplyModel, Clock


@pytest.fixture
def live(tmp_path):
    cfg=configured(tmp_path);cfg.save()
    store=StateStore(cfg.runtime)
    r=Resident(cfg,store,desktop_factory=lambda _:Screen(),vision=IdentityVision(),
        model_factory=lambda _:ReplyModel(),clock=Clock(),settle_seconds=0)
    yield r
    for e in r.engines:
        if e.companion:e.companion.close()
    store.close()


def queue(r,e,text,state='generated'):
    key=r.store.create_unique(r.store.fingerprint(e.settings.group+text),r.clock())
    t=ReplyTask(key,r.clock(),r.clock(),'朋友',messages=[Message(text)],reply='已生成',state=state)
    e.tasks.append(t);r.store.update(key,'sending' if state=='awaiting_receipt' else state)
    return t


def test_rule_change_preserves_other_rooms_and_addressed_tasks(live):
    a,b,_=live.engines
    ordinary=queue(live,a,'今晚吃什么');addressed=queue(live,a,'小玲你好');other=queue(live,b,'聊聊')
    s=live.configure_reply_rules('甲群',a.settings.memory_scope,{'reply_mode':'mentions'})
    assert a.policy.mode=='mentions' and b.policy.mode=='natural'
    assert a.tasks==[addressed] and b.tasks==[other]
    assert live.store.counts()['skipped']==1
    assert s['groups'][0]['reply_mode']=='mentions'
    assert Settings.load(live.settings.root).for_group('甲群').reply_mode=='mentions'


@pytest.mark.parametrize('state',['staged','sending','awaiting_receipt','unknown'])
def test_active_delivery_finishes_before_its_rules_change(live,state):
    a,b,_=live.engines;t=queue(live,a,'今晚吃什么',state)
    s=live.configure_reply_rules('甲群',a.settings.memory_scope,{'reply_mode':'mentions'})
    assert a.policy.mode=='natural' and s['groups'][0]['rules_pending']
    live.configure_reply_rules('乙群',b.settings.memory_scope,{'reply_mode':'all'})
    assert b.policy.mode=='all' and a.tasks==[t]
    a.tasks.remove(t)
    live._apply_reply_rules()
    assert a.policy.mode=='mentions' and not live.status()['groups'][0]['rules_pending']


def test_stale_identity_and_failed_save_do_not_change_live_settings(live,monkeypatch):
    a=live.engines[0]
    with pytest.raises(ValueError):live.configure_reply_rules('甲群','other/account',{'reply_mode':'all'})
    def fail(self,**kw):raise OSError('disk unavailable')
    monkeypatch.setattr(Settings,'save',fail)
    with pytest.raises(OSError):live.configure_reply_rules('甲群',a.settings.memory_scope,{'reply_mode':'all'})
    assert a.policy.mode=='natural' and not live.settings.conversation_rules


def test_defaults_change_only_inherited_fields_and_reset_is_live(live):
    a,b,_=live.engines
    live.configure_reply_rules('甲群',a.settings.memory_scope,{'reply_mode':'mentions'})
    live.configure_reply_defaults({'reply_mode':'all','sticker_frequency':'expressive'})
    assert a.policy.mode=='mentions' and b.policy.mode=='all'
    assert all(e.settings.sticker_frequency=='expressive' for e in live.engines)
    live.configure_reply_rules('甲群',a.settings.memory_scope,{'reply_mode':None})
    assert a.policy.mode=='all'


def test_window_reload_cannot_bypass_queued_rule_application(live):
    a=live.engines[0];ordinary=queue(live,a,'今晚吃什么')
    live.cycle_lock.acquire()
    try:
        live.configure_reply_rules('甲群',a.settings.memory_scope,{'reply_mode':'mentions'})
        live.reload_windows()
    finally:live.cycle_lock.release()
    live._apply_window_reload();live._apply_reply_rules()
    assert a.policy.mode=='mentions' and ordinary not in a.tasks


def test_frequency_change_keeps_the_confirmed_text_turns_sticker_continuation(live):
    a=live.engines[0]
    live.configure_reply_defaults({'sticker_frequency':'expressive'})
    continuation=queue(live,a,'太开心了')
    continuation.continuation_of='confirmed-text-bubble'
    result=live.configure_reply_rules('甲群',a.settings.memory_scope,{'sticker_frequency':'requested'})
    assert a.settings.sticker_frequency=='expressive'
    assert result['groups'][0]['rules_pending'] and a.tasks==[continuation]
    assert live.store.sticker_ready(a.settings.memory_scope,live.clock(),a.settings.sticker_frequency)
    a.tasks.remove(continuation);live._apply_reply_rules()
    assert a.settings.sticker_frequency=='requested'


def test_default_mode_save_from_panel_keeps_worker_and_inflight_receipt(live):
    from xiaomao.gui import apply_saved_settings
    from xiaomao.client import ControlClient
    from xiaomao.control import ControlServer
    a,b,_=live.engines;receipt=queue(live,a,'小玲吃饭啦','awaiting_receipt')
    original=Settings.load(live.settings.root)
    with ControlServer(live.settings.root,live):
        result=apply_saved_settings(ControlClient(live.settings.root),original,replace(original,reply_mode='mentions'))
    assert not live.stop_event.is_set() and a.tasks==[receipt]
    assert a.policy.mode=='natural' and b.policy.mode=='mentions'
    assert result['groups'][0]['rules_pending']


def test_page_save_of_independent_rule_does_not_require_stopping_receipt(live):
    from xiaomao.gui import apply_saved_settings
    from xiaomao.client import ControlClient
    from xiaomao.control import ControlServer
    a,b,_=live.engines;receipt=queue(live,a,'小玲吃饭啦','awaiting_receipt')
    original=Settings.load(live.settings.root)
    updated=original.with_conversation_rules('乙群',{'reply_mode':'mentions','sticker_frequency':'requested'})
    with ControlServer(live.settings.root,live):
        result=apply_saved_settings(ControlClient(live.settings.root),original,updated)
    assert not live.stop_event.is_set() and a.tasks==[receipt]
    assert b.policy.mode=='mentions' and b.settings.sticker_frequency=='requested'
    restored=Settings.load(live.settings.root)
    reboot=Resident(restored,live.store,desktop_factory=lambda _:Screen(),vision=IdentityVision(),
        model_factory=lambda _:ReplyModel(),clock=Clock(),settle_seconds=0)
    try:
        assert reboot.engines[1].settings.reply_mode=='mentions'
        assert reboot.engines[0].settings.reply_mode=='natural'
        assert reboot.engines[1].settings.sticker_frequency=='requested'
    finally:
        for e in reboot.engines:
            if e.companion:e.companion.close()


def test_authenticated_rule_api_updates_recipient_and_rejects_extra_fields(live):
    import httpx
    from xiaomao.client import ControlClient
    from xiaomao.control import ControlServer
    with ControlServer(live.settings.root,live) as server:
        client=ControlClient(live.settings.root)
        result=client.configure_reply_rules('乙群',live.engines[1].settings.memory_scope,{'reply_mode':'mentions'})
        assert result['groups'][1]['reply_mode']=='mentions'
        with httpx.Client(trust_env=False) as http:
            headers={'Authorization':'Bearer '+server.token}
            response=http.post(server.url+'/configure_reply_defaults',headers=headers,
                json={'preferences':{'reply_mode':'all'},'group':'other'})
            assert response.status_code==400
        client.configure_reply_defaults({'sticker_frequency':'expressive'})
        assert live.engines[1].settings.sticker_frequency=='expressive'
