import threading

import pytest

from test_engine import system
from test_bubble_delivery import ALICE, BOB, delivery, question
from xiaomao.domain import Message


def test_window_preflight_failure_remains_visible_during_retry(delivery):
    from xiaomao.windows import DesktopTemporarilyHidden
    engine,feed,_,clock,_=delivery
    def blocked():raise DesktopTemporarilyHidden('窗口缩放后标题或发送按钮未能核实')
    engine.desktop.prepare_callback=blocked
    question(engine);engine.observe_once()
    assert '发送按钮未能核实' in engine.pending()[0]['progress']
    clock.advance(.1);engine.observe_once()
    assert '发送按钮未能核实' in engine.reason
    engine.desktop.prepare_callback=lambda:None
    clock.advance(4);engine.observe_once()
    assert feed.sent


def test_idle_pointer_is_parked_before_the_first_draft_capture_only(delivery):
    engine,feed,_,clock,_=delivery
    order=[];frame=engine._frame
    engine.desktop.park_editor_pointer=lambda idle:order.append('park')
    def read(*args,**kw):order.append('frame');return frame(*args,**kw)
    engine._frame=read
    question(engine);engine.observe_once()
    assert feed.sent and order[0]=='park' and order.count('park')==1


def test_idle_wait_keeps_only_the_current_unsent_text_and_explains_the_delay(delivery):
    engine,feed,_,clock,store=delivery
    engine.model.reply=lambda messages:'蓝色小鲸鱼'
    engine.desktop.idle_for=lambda:0
    question(engine,'所以你现在是什么动物')
    feed.history.append(engine.tasks[0].messages[-1])
    engine.observe_once()
    assert '空闲' in engine.pending()[0]['progress'] and not feed.sent
    for _ in range(4):
        clock.advance(engine.settings.expiry_seconds/3)
        engine.observe_once()
    assert len(engine.pending())==1
    engine.desktop.idle_for=lambda:30
    engine.observe_once()
    assert feed.sent==['蓝色小鲸鱼'] and store.counts()=={'verified':1}


@pytest.mark.parametrize('changed',['new_peer','self_message','end_of_grace','observation_gap'])
def test_idle_grace_does_not_revive_an_old_turn(delivery,changed):
    engine,feed,_,clock,store=delivery
    engine.model.reply=lambda messages:'只答当前问题'
    engine.desktop.idle_for=lambda:0
    question(engine)
    engine.observe_once()
    original=engine.tasks[0].id
    if changed=='observation_gap':clock.advance(engine.settings.expiry_seconds+1)
    else:
        for _ in range(4):
            clock.advance(engine.settings.expiry_seconds/3)
            engine.observe_once()
        assert engine.tasks
        if changed=='end_of_grace':clock.advance(engine.settings.expiry_seconds)
        else:
            direction='out' if changed=='self_message' else 'in'
            row=Message('新话题',speaker_id=BOB if direction=='in' else 'self',direction=direction,
                        message_id='new',created_at=clock())
            feed.pending.append(row);feed.history.append(row)
    engine.observe_once()
    assert store.get(original)['state']=='skipped' and not feed.sent


def test_missed_followup_uses_this_speakers_unsent_question(delivery):
    engine,_,_,clock,store=delivery
    question(engine,'你是什么动物','animal',ALICE)
    old=engine.tasks[0]
    engine._retire(old,'skipped','insufficient_input_time')
    question(engine,'你最爱什么颜色','color',BOB)
    engine._retire(engine.tasks[0],'skipped','expired')
    question(engine,'@小玲 为什么上一条不回复？','followup',ALICE)
    context=engine._reply_context(engine.tasks[0])
    assert [(m.message_id,m.delivery_status) for m in context if getattr(m,'delivery_status','')]==[('animal','not_sent')]
    assert context[-1].message_id=='followup'
    assert len(engine.tasks)==1 and store.db.execute('SELECT count(*) FROM attempts').fetchone()[0]==0


def test_grace_rechecks_live_history_after_prepare_and_before_first_edit(delivery):
    engine,feed,_,clock,store=delivery
    engine.model.reply=lambda messages:'只答当前题'
    question(engine)
    original=engine.tasks[0]
    feed.history.append(original.messages[-1])
    engine.desktop.idle_for=lambda:0
    engine.observe_once()
    for _ in range(4):
        clock.advance(engine.settings.expiry_seconds/3)
        engine.observe_once()
    assert original in engine.tasks
    def incoming_during_prepare():
        row=Message('已经解决了',message_id='finished',speaker_id=ALICE,created_at=clock())
        feed.pending.append(row);feed.history.append(row)
    engine.desktop.prepare_callback=incoming_during_prepare
    engine.desktop.idle_for=lambda:30
    engine.observe_once()
    assert store.get(original.id)['state']=='skipped'
    assert [t.messages[-1].message_id for t in engine.tasks]==['finished']
    assert not feed.sent and 'focus' not in engine.desktop.events


@pytest.mark.parametrize('permitted,idle,expect',[(False,60,False),(True,0,False),(True,60,True)])
def test_only_explicit_idle_wake_allows_a_verified_background_reply(delivery,permitted,idle,expect):
    engine,feed,_,clock,store=delivery
    engine.settings.idle_wake=permitted
    engine.desktop.background=True;engine.desktop.idle_for=lambda:idle
    engine.model.reply=lambda _:'单独窗口的答复'
    called=[]
    feed.assert_identity=lambda:called.append('identity')
    def wake(_):
        assert called==['identity']
        called.append('wake');engine.desktop.background=False
        return True
    engine.desktop.try_idle_wake=wake
    question(engine);engine.observe_once()
    assert ('wake' in called)==expect
    assert feed.sent==(['单独窗口的答复'] if expect else [])
    if permitted and not idle:
        assert engine.tasks[0].wait_reason=='desktop_busy'
        assert '键盘或鼠标' in engine.pending()[0]['progress']


def test_slow_database_receipt_returns_focus_before_another_chat_can_send(delivery):
    engine,feed,_,clock,store=delivery
    feed.ack=False
    engine.model.reply=lambda _:'晚饭吃了吗'
    front=['wechat']
    def release():
        assert feed.sent==['晚饭吃了吗']
        front[0]='original-editor'
    engine.desktop.release_send_focus=release
    question(engine);engine.observe_once()
    assert engine.tasks[0].state=='awaiting_receipt'
    assert store.counts()=={'sending':1}
    assert front[0]=='original-editor'
    # Background receipt polling never types or clicks again.
    engine.observe_once()
    assert feed.sent==['晚饭吃了吗']


def test_verified_hot_account_does_not_rescan_external_key_directories(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from xiaomao.config import Settings
    from xiaomao.database import DatabaseHub,DatabaseUnavailable
    from xiaomao.dbcodec import discovery
    base=tmp_path/'wx';p=base/'db_storage/contact/contact.db';p.parent.mkdir(parents=True)
    p.write_bytes(b'SQLite format 3\0'+bytes(4080))
    hub=DatabaseHub(Settings(tmp_path),threading.Event())
    hub.settings=SimpleNamespace(binding=SimpleNamespace(pid=9),runtime=tmp_path/'runtime',root=tmp_path)
    account={'id':'acct','path':str(base),'wxid':'me'}
    monkeypatch.setattr(discovery,'discover_accounts',lambda:[account])
    active=['acct'];monkeypatch.setattr(discovery,'active_account_ids',lambda *args:active)
    hub.account=account
    from pathlib import Path
    original=Path.glob
    def guarded_glob(self,pattern,*args,**kw):
        assert '**' not in pattern,'a hot identity check should not walk the external project'
        return original(self,pattern,*args,**kw)
    monkeypatch.setattr(Path,'glob',guarded_glob)
    hub._ensure(True)
    active.clear()
    with pytest.raises(DatabaseUnavailable):hub._ensure(True)


def test_followup_prompt_and_output_use_only_confirmed_delivery(tmp_path):
    from test_reply_delivery import completion
    from xiaomao.model import ModelError
    import json
    model,requests=completion(tmp_path,json.dumps({'send':True,'text':'上一条我回了呀，是你问我是什么动物那句','sticker':''}))
    previous=Message('你是什么动物',message_id='old',speaker_id=ALICE,delivery_status='not_sent')
    current=Message('@小玲 为什么上一条不回复？',message_id='new',speaker_id=ALICE)
    with pytest.raises(ModelError) as exc:
        model.reply_plan([previous,current],choices=[])
    assert exc.value.code=='delivery_claim'
    assert 'not_sent' in str(requests[0]['messages'])
@pytest.mark.parametrize('content',['你是什么动物？','@小玲 你是什么动物？'])
def test_live_question_waits_for_user_to_return_to_wechat_without_preparing_the_desktop(delivery,content):
    engine,feed,_,clock,store=delivery
    engine.model.reply=lambda messages:'蓝色小鲸鱼'
    engine.desktop.background=True
    question(engine,content)
    engine.observe_once()
    assert engine.desktop.background and engine.desktop.prepared==0 and not feed.sent
    assert '前台' in engine.pending()[0]['progress']
    clock.advance(4)
    engine.desktop.background=False
    engine.observe_once()
    assert feed.sent==['蓝色小鲸鱼'] and store.counts()=={'verified':1}
