import json
from types import SimpleNamespace

import pytest
from test_bubble_delivery import delivery
from test_engine import system
from test_reply_delivery import completion
from test_stickers import library
from xiaomao.companion import Candidate,local_day
from xiaomao.stickers import ReplyPlan,StickerLibrary


def test_proactive_can_select_allowlisted_sticker_with_text_in_one_call(tmp_path):
    library(tmp_path);lib=StickerLibrary(tmp_path)
    model,requests=completion(tmp_path,json.dumps({'send':True,'text':'突然想起那个镜头了','sticker':'s_1'}))
    candidate=Candidate('p','idle',100,220,1,'2026-11-06')
    plan=model.proactive_plan([],candidate,choices=lib.items)
    assert plan==ReplyPlan(text='突然想起那个镜头了',sticker='s_1')
    assert len(requests)==1
    content=requests[0]['messages'][0]['content']
    assert 's_1' in content and '图片' in content


def test_proactive_cannot_invent_sticker_id_or_fake_sent_action(tmp_path):
    from xiaomao.model import ModelError
    model,_=completion(tmp_path,json.dumps({'send':True,'text':'','sticker':'../../not-allowed.gif'}))
    with pytest.raises(ModelError):
        model.proactive_plan([],Candidate('p','idle',100,220,1,'2026-11-06'),choices=[])


@pytest.mark.parametrize('outcome',['verified','unknown','skipped'])
def test_proactive_text_and_gif_share_candidate_but_require_individual_receipts(delivery,outcome):
    engine,feed,_,clock,store=delivery
    library(engine.settings.root);engine.stickers=StickerLibrary(engine.settings.root)
    engine.desktop.type_sticker=feed.stage_sticker
    engine.desktop.verify_sticker_input=lambda item:feed.sticker==item
    from xiaomao.companion_runtime import CompanionRuntime
    from xiaomao.companion import CompanionPreferences
    engine.settings.conversation_targets={engine.settings.group:{'id':'g@chatroom','account':'a','kind':'group'}}
    runtime=CompanionRuntime(engine.settings,store,engine.model,clock)
    engine.companion=runtime
    runtime.planner.preferences=CompanionPreferences(enabled=True,quiet_start=0,quiet_end=0)
    candidate=Candidate('p','requested',clock(),clock()+120,runtime.planner.state['revision'],local_day(clock()))
    assert store.reserve_proactive(candidate.id,engine.settings.memory_scope,candidate.day,clock(),2)
    replies=[(candidate,ReplyPlan(text='来，歇一下吧',sticker='s_1'))]
    runtime.tick=lambda *a,**kw:replies.pop() if replies else None
    try:
        engine._companion_tick()
        assert len(engine.tasks)==1 and engine.tasks[0].reply=='来，歇一下吧'
        engine._reply_and_send(engine.tasks[0]);clock.advance(10)
        assert store.db.execute('SELECT state FROM proactive').fetchone()[0]=='sending'
        assert runtime.planner.state['checkin_at']==0
        assert '部分' in runtime.once_state
        if outcome=='skipped':
            engine.pause()
            assert feed.sent==['来，歇一下吧']
            assert '部分' in runtime.once_state
        else:
            feed.ack=outcome!='unknown'
            engine._reply_and_send(engine.tasks[0])
            assert feed.sent==['来，歇一下吧','[表情]']
            if outcome=='unknown':
                clock.advance(31);engine._expire_receipt()
                assert '未知' in runtime.once_state and engine.mode=='paused'
        expected='partial' if outcome=='skipped' else outcome
        assert store.db.execute('SELECT state FROM proactive').fetchone()[0]==expected
        assert store.counts()==({'verified':2} if outcome=='verified' else {'verified':1,outcome:1})
        assert bool(runtime.planner.state['checkin_at']) is (outcome=='verified')
        if outcome=='skipped':
            assert not store.reserve_proactive('second',engine.settings.memory_scope,candidate.day,clock(),1)
            store.proactive_result(candidate.id,'verified')
            assert store.db.execute('SELECT state FROM proactive').fetchone()[0]=='partial'
    finally:runtime.close()


def test_unavailable_proactive_sticker_never_reinterprets_history_as_a_new_question(delivery):
    from xiaomao.companion_runtime import CompanionRuntime
    from xiaomao.companion import CompanionPreferences
    engine,feed,calls,clock,store=delivery
    library(engine.settings.root);engine.stickers=StickerLibrary(engine.settings.root)
    engine.desktop.type_sticker=feed.stage_sticker
    engine.desktop.verify_sticker_input=lambda item:True
    engine.desktop.sticker_preview_fits=lambda item:False
    engine.settings.conversation_targets={engine.settings.group:{'id':'g@chatroom','account':'a','kind':'group'}}
    runtime=CompanionRuntime(engine.settings,store,engine.model,clock);engine.companion=runtime
    runtime.planner.preferences=CompanionPreferences(enabled=True,quiet_start=0,quiet_end=0)
    candidate=Candidate('media','idle',clock(),clock()+120,runtime.planner.state['revision'],local_day(clock()))
    assert store.reserve_proactive(candidate.id,engine.settings.memory_scope,candidate.day,clock(),2)
    replies=[(candidate,ReplyPlan(sticker='s_1'))]
    runtime.tick=lambda *a,**kw:replies.pop() if replies else None
    try:
        engine._companion_tick()
        engine._reply_and_send(engine.tasks[0])
        clock.advance(10)
        if engine.tasks:engine._reply_and_send(engine.tasks[0])
        assert not calls and not feed.sent and not engine.tasks
        assert store.db.execute('SELECT state FROM proactive').fetchone()[0]=='skipped'
    finally:runtime.close()
