from dataclasses import replace, asdict
import pytest
from test_engine import Clock,Screen,IdentityVision,ReplyModel
from test_vision import binding
from xiaomao.config import Settings
from xiaomao.store import StateStore
from xiaomao.resident import Resident


def resident(tmp_path, clock, store=None):
    if not (tmp_path/'runtime'/'config.json').exists():
        cfg=Settings(tmp_path,group='甲',group_names=('甲','乙'),model_base_url='http://127.0.0.1:9999/v1',
            calibration=asdict(binding()),conversation_targets={
            '甲':{'account':'a','id':'one','kind':'private'},'乙':{'account':'a','id':'two','kind':'private'}})
        cfg.save()
    else: cfg=Settings.load(tmp_path)
    store=store or StateStore(cfg.runtime)
    return Resident(cfg,store,desktop_factory=lambda _:Screen(),vision=IdentityVision(),model_factory=lambda _:ReplyModel(),clock=clock),store


def test_rest_restores_observe_and_repeated_rest_preserves_prior(tmp_path):
    clock=Clock(); r,store=resident(tmp_path,clock)
    try:
        engine=r.engines[0]; engine.observe();scope=engine.settings.memory_scope
        info=r.rest_selected('甲',scope,1)
        assert engine.mode=='paused' and info['rest']['active']
        r.rest_selected('甲',scope,2)
        clock.advance(61);r.status();assert engine.mode=='paused'
        clock.advance(61);r.status();assert engine.mode=='observe'
        assert not r.cancel_rest_selected('甲',scope)['rest']['active']
    finally: store.close()


def test_rest_restart_and_manual_pause_never_auto_resume(tmp_path):
    clock=Clock();r,store=resident(tmp_path,clock)
    try:
        e=r.engines[0]; e.observe();scope=e.settings.memory_scope
        r.rest_selected('甲',scope,2)
        r2,_=resident(tmp_path,clock,store)
        assert r2.engines[0].mode=='paused'
        clock.advance(121);r2.status();assert r2.engines[0].mode=='observe'
        r2.rest_selected('甲',scope,1);r2.pause_selected('甲',scope)
        clock.advance(61);r2.status();assert r2.engines[0].mode=='paused'
    finally:store.close()


def test_rest_global_pause_clock_rollback_and_identity_are_safe(tmp_path):
    clock=Clock();r,store=resident(tmp_path,clock)
    try:
        e=r.engines[0];e.observe();scope=e.settings.memory_scope
        r.rest_selected('甲',scope,1);r.pause()
        clock.advance(61);r.status();assert e.mode=='paused'
        e.observe();r.rest_selected('甲',scope,1)
        clock.advance(-120);r.status();clock.advance(181);r.status()
        assert e.mode=='paused'
        with pytest.raises(ValueError):r.rest_selected('甲','another-account/one',1)
    finally:store.close()


def test_outbound_multiline_remains_exact_and_rest_cancels_queued_send(tmp_path):
    from test_window_routing import selected,window,screen_resident,ROOM,PEER
    cfg=selected(tmp_path,ROOM,PEER).with_window_binding(ROOM.name,window(ROOM,11)).with_window_binding(PEER.name,window(PEER,22))
    store=StateStore(cfg.runtime)
    try:
        r=screen_resident(cfg,store);r.enable();e=r.engines[1];e.message_source=object();scope=e.settings.memory_scope
        text='第一段\n第二段\t排版'
        assert r.queue_outbound(PEER.name,scope,text,[],'multiline-001')['queued']
        assert e.tasks[0].reply==text
        for bad in ('x\x00y','x\ry','x\x1by','x'*401):
            with pytest.raises(ValueError):r.queue_outbound(PEER.name,scope,bad,[],'multiline-bad')
        r.rest_selected(PEER.name,scope,1)
        assert e.mode=='paused' and e.tasks==[] and e.cancelled.is_set()
    finally:store.close()


def test_restore_auto_cannot_bypass_persisted_rest(tmp_path):
    clock=Clock();r,store=resident(tmp_path,clock)
    try:
        e=r.engines[0];e.enable();scope=e.settings.memory_scope
        r.rest_selected('甲',scope,2)
        r2,_=resident(tmp_path,clock,store);r2.restore_auto()
        assert r2.engines[0].mode=='paused'
        r2.cancel_rest_selected('甲',scope)
        assert r2.engines[0].mode=='auto'
    finally:store.close()


def test_stop_and_same_identity_window_reload_preserve_rest(tmp_path):
    clock=Clock();r,store=resident(tmp_path,clock)
    try:
        e=r.engines[0];e.observe();scope=e.settings.memory_scope
        r.rest_selected('甲',scope,2)
        r.reload_request=replace(r.settings,account_display_name='更新后的显示名')
        r._apply_window_reload()
        assert r.engines[0].mode=='paused' and r.status()['groups'][0]['rest']['active']
        r.stop()
        r2,_=resident(tmp_path,clock,store)
        assert r2.status()['groups'][0]['rest']['active'] and r2.engines[0].mode=='paused'
        clock.advance(121);r2.status();assert r2.engines[0].mode=='observe'
    finally:store.close()


def test_rest_expiry_checks_saved_scope_removal_before_resuming(tmp_path):
    clock=Clock();r,store=resident(tmp_path,clock)
    try:
        e=r.engines[0];e.enable();scope=e.settings.memory_scope
        r.rest_selected('甲',scope,1)
        saved=Settings.load(tmp_path)
        saved.with_saved_conversations(['乙']).save(expected=saved)
        clock.advance(61);r.status()
        assert e.mode=='paused' and not r.rests.info(e)['active']
    finally:store.close()


def test_cancel_rest_cannot_undo_newer_saved_manual_pause(tmp_path):
    clock=Clock();r,store=resident(tmp_path,clock)
    try:
        e=r.engines[0];e.enable();scope=e.settings.memory_scope
        r.rest_selected('甲',scope,1)
        saved=Settings.load(tmp_path);saved.with_conversation_pause('甲',True).save(expected=saved)
        r.cancel_rest_selected('甲',scope)
        assert e.mode=='paused' and not r.rests.info(e)['active']
    finally:store.close()


@pytest.mark.parametrize('expired',[False,True])
@pytest.mark.parametrize('action',['expiry','cancel'])
def test_recovered_unknown_send_fence_survives_rest_restart(tmp_path,expired,action):
    clock=Clock();r,store=resident(tmp_path,clock)
    try:
        engine=r.engines[0];engine.enable();scope=engine.settings.memory_scope
        r.rest_selected('甲',scope,1)
        identity=store.create('unresolved-send',clock());store.update(identity,'unknown')
        if expired:clock.advance(61)
        r2,_=resident(tmp_path,clock,store)
        assert r2.requested_mode=='paused'
        assert '未知' in r2.engines[0].reason
        if action=='cancel':r2.cancel_rest_selected('甲',scope)
        else:clock.advance(61);r2.status()
        assert r2.engines[0].mode=='paused' and store.counts().get('unknown')==1
        r2.news.close()
    finally:r.news.close();store.close()


def test_enabling_other_target_preserves_first_targets_temporary_rest(tmp_path):
    clock=Clock();r,store=resident(tmp_path,clock)
    try:
        first,second=r.engines;first.enable();second.enable()
        r.rest_selected('甲',first.settings.memory_scope,1)
        r.enable_selected('乙',second.settings.memory_scope)
        assert r.rests.info(first)['active'] and not first.settings.is_conversation_paused()
        clock.advance(61);r.status()
        assert first.mode=='auto' and second.mode=='auto'
    finally:r.news.close();store.close()
