from concurrent.futures import Future
from types import SimpleNamespace
from dataclasses import asdict
import pytest
from xiaomao.domain import Message
from xiaomao.companion import CompanionPreferences
from test_companion import at,Predictable


def test_three_conversations_cancel_continue_and_stop_independently(tmp_path):
    from xiaomao.companion_runtime import CompanionRuntime
    from xiaomao.store import StateStore
    store=StateStore(tmp_path); clock=[at(10)]
    rooms=[]
    def run(room,when,expected,receipt='verified'):
        clock[0]=when
        room.tick([],epoch='live',eligible=True)
        assert room.executor.calls[-1][2][1].kind==expected
        room.executor.finish()
        candidate,body=room.tick([],epoch='live',eligible=True)
        assert body==room.settings.memory_scope+'/'+expected
        room.begin_send(candidate);room.finish(candidate,receipt)
    try:
        for name in ('摄影群','读书群','朋友私聊'):
            cfg=SimpleNamespace(runtime=tmp_path,memory_scope='account/'+name)
            model=SimpleNamespace(proactive=lambda ctx,c,p,n=cfg.memory_scope:n+'/'+c.kind)
            r=CompanionRuntime(cfg,store,model,lambda:clock[0],executor=Deferred())
            r.configure(CompanionPreferences(enabled=True,daily_max=3))
            r.planner.rng=Predictable();r.account_daily_max=9
            r.observe(Message('想继续聊聊',message_id='in',created_at=at(10)))
            r.replied('reply',at(10));rooms.append(r)
        for r in rooms:run(r,at(10,2),'followup')
        rooms[0].observe(Message('我来了',message_id='new',created_at=at(10,5)))
        assert not rooms[0].planner.state['checkin_at']
        run(rooms[1],at(10,10),'checkin','unknown')
        assert not rooms[1].planner.state['signoff_at']
        run(rooms[2],at(10,10),'checkin')
        run(rooms[2],at(10,52),'signoff')
        for r in rooms[1:]:
            assert r.planner.decide(at(17)) is None and r.planner.state['unanswered']
        assert store.db.execute("select count(*) from proactive").fetchone()[0]==6
    finally:
        for room in rooms:room.close()
        store.close()


class Deferred:
    def __init__(self):self.calls=[]
    def submit(self,fn,*args):
        f=Future();self.calls.append((f,fn,args));return f
    def shutdown(self,**kwargs):pass
    def finish(self):
        f,fn,args=self.calls[-1];f.set_result(fn(*args))


def build(tmp_path):
    from xiaomao.companion_runtime import CompanionRuntime
    from xiaomao.companion_store import CompanionStore
    from xiaomao.store import StateStore
    notes=CompanionStore(tmp_path,'account/group');notes.data['preferences']=asdict(CompanionPreferences(enabled=True));notes.save()
    now=[at(10)];queue=Deferred();store=StateStore(tmp_path)
    model=SimpleNamespace(proactive=lambda *args:'刚才那个思路，慢慢试就好呀。')
    cfg=SimpleNamespace(runtime=tmp_path,memory_scope='account/group')
    r=CompanionRuntime(cfg,store,model,lambda:now[0],executor=queue)
    r.planner.rng=Predictable()
    r.observe(Message('今天学Python',message_id='a',created_at=at(10)),False)
    r.replied('out',at(10));now[0]=at(10,2)
    return r,store,queue,now


def test_generation_is_nonblocking_and_new_input_cancels_result(tmp_path):
    r,s,q,now=build(tmp_path)
    try:
        assert r.tick([],epoch='one',eligible=True) is None
        assert len(q.calls)==1 and not q.calls[0][0].done()
        r.observe(Message('换一个问题',message_id='b',created_at=now[0]+1),False)
        q.finish()
        assert r.tick([],epoch='one',eligible=True) is None
        assert s.db.execute('SELECT count(*) FROM proactive').fetchone()[0]==0
    finally:r.close();s.close()


def test_requested_proactive_runs_after_delay_without_new_inbound(tmp_path):
    r,s,q,now=build(tmp_path)
    try:
        r.request_once('one')
        with pytest.raises(ValueError):r.request_once('one')
        with pytest.raises(ValueError):r.preview([],'one')
        assert r.tick([],epoch='one',eligible=True) is None
        assert not q.calls
        now[0]+=30
        r.tick([],epoch='one',eligible=True)
        assert len(q.calls)==1 and q.calls[0][2][1].kind=='requested'
        q.finish()
        candidate,reply=r.tick([],epoch='one',eligible=True)
        assert reply and candidate.kind=='requested'
        r.begin_send(candidate);r.finish(candidate,'verified')
        assert r.status()['once_state']=='已发送并确认'
        with pytest.raises(ValueError):r.request_once('one')
    finally:r.close();s.close()


def test_requested_generation_cannot_send_after_new_message(tmp_path):
    r,s,q,now=build(tmp_path)
    try:
        r.request_once('one');now[0]+=30;r.tick([],epoch='one',eligible=True)
        r.observe(Message('换一个话题',message_id='new',created_at=now[0]))
        q.finish()
        assert r.tick([],epoch='one',eligible=True) is None
        assert r.status()['once_state']=='已取消，未发送'
        assert s.db.execute('SELECT count(*) FROM proactive').fetchone()[0]==0
    finally:r.close();s.close()


def test_requested_chat_survives_a_brief_queue_wait_before_and_after_generation(tmp_path):
    r,s,q,now=build(tmp_path)
    try:
        r.request_once('one')
        now[0]+=30
        blocked=dict(epoch='one',eligible=False,waiting_reason='等待当前发送任务完成')
        assert r.tick([],**blocked) is None
        assert not q.calls and r.once is not None
        r.tick([],epoch='one',eligible=True)
        q.finish()
        assert r.tick([],**blocked) is None
        assert s.db.execute('SELECT count(*) FROM proactive').fetchone()[0]==0
        now[0]+=5
        candidate,_=r.tick([],epoch='one',eligible=True)
        assert candidate.kind=='requested' and len(q.calls)==1
    finally:r.close();s.close()


@pytest.mark.parametrize('change',['incoming','pause','epoch','expired','clock'])
def test_requested_proactive_invalidates_before_generation(tmp_path,change):
    r,s,q,now=build(tmp_path)
    try:
        r.request_once('one')
        if change=='incoming':r.observe(Message('新问题',message_id='new',created_at=now[0]+1))
        if change=='pause':r.suspend()
        if change=='clock':now[0]-=1;r.tick([],epoch='one',eligible=True)
        now[0]+=151 if change=='expired' else 31
        r.tick([],epoch='two' if change=='epoch' else 'one',eligible=change!='pause')
        assert not q.calls
        assert s.db.execute('SELECT count(*) FROM proactive').fetchone()[0]==0
        assert '取消' in r.status()['once_state']
    finally:r.close();s.close()


def test_requested_proactive_does_not_override_quiet_disabled_or_budget(tmp_path):
    from dataclasses import replace
    from xiaomao.companion import local_day
    r,s,q,now=build(tmp_path)
    try:
        original=r.planner.preferences
        for p in (replace(original,enabled=False),replace(original,quiet_start=0,quiet_end=1439)):
            r.planner.preferences=p
            with pytest.raises(ValueError):r.request_once('one')
        r.planner.preferences=original
        for i in range(2):assert s.reserve_proactive(str(i),'account/group',local_day(now[0]),now[0],2)
        r.request_once('one');now[0]+=30;r.tick([],epoch='one',eligible=True);q.finish()
        assert r.tick([],epoch='one',eligible=True) is None
        assert '额度' in r.status()['once_state']
    finally:r.close();s.close()


def test_pause_epoch_and_late_model_cannot_send(tmp_path):
    for mode in ('epoch','late','paused'):
        r,s,q,now=build(tmp_path/mode)
        try:
            r.tick([],epoch='one',eligible=True);q.finish()
            if mode=='late':now[0]+=121
            assert r.tick([],epoch='two' if mode=='epoch' else 'one',eligible=mode!='paused') is None
        finally:r.close();s.close()


def test_clock_rollback_while_future_pending_does_not_resurrect(tmp_path):
    r,s,q,now=build(tmp_path)
    try:
        r.tick([],epoch='one',eligible=True)
        now[0]=at(10,1);r.tick([],epoch='one',eligible=True)
        q.finish();now[0]=at(10,2)+5
        assert r.tick([],epoch='one',eligible=True) is None
        assert s.db.execute('SELECT count(*) FROM proactive').fetchone()[0]==0
    finally:r.close();s.close()


def test_original_evidence_survives_compaction_before_reflection(tmp_path):
    from xiaomao.memory import ConversationMemory
    r,s,q,now=build(tmp_path)
    memory=ConversationMemory(tmp_path,'account/group')
    captured=[]
    r.model.reflect=lambda messages:captured.extend(messages) or {'facts':[],'diary':'','sources':[]}
    try:
        for i in range(100):
            m=Message('最早的偏好' if i==0 else '其它消息'+str(i),message_id=str(i),created_at=at(10)+i)
            memory.remember(m);r.observe(m,baseline=True)
        assert memory.compact_if_needed(SimpleNamespace(compact=lambda *a:'已有摘要'))
        assert len(memory.messages)==24
        r.planner.preferences=CompanionPreferences(enabled=False)
        r.tick(memory.context(),epoch='one',eligible=True,memory=memory)
        q.finish();r.tick(memory.context(),epoch='one',eligible=True,memory=memory)
        assert any(m.message_id=='0' and m.text=='最早的偏好' for m in captured)
    finally:r.close();s.close()


def test_large_evidence_queue_uses_bounded_batches(tmp_path):
    from xiaomao.memory import ConversationMemory
    r,s,q,now=build(tmp_path);captured=[]
    r.model.reflect=lambda messages:captured.extend(messages) or {'facts':[]}
    try:
        for i in range(60):r.observe(Message('长'*2000,message_id=str(i),created_at=now[0]),baseline=True)
        r.planner.preferences=CompanionPreferences(enabled=False)
        r.tick([],epoch='one',eligible=True,memory=ConversationMemory(tmp_path,'account/group'))
        q.finish();r.tick([],epoch='one',eligible=True)
        assert 0<sum(len(m.text) for m in captured)<=14000
        assert len(r.notes.data['evidence_pending'])>0
    finally:r.close();s.close()


def test_baseline_backfills_context_for_older_evidence(tmp_path):
    from xiaomao.companion_memory import source_ref
    r,s,q,now=build(tmp_path)
    try:
        r.notes.data['facts']=[{'source':source_ref('old'),'quote':'喜欢咖啡','speaker':'p_'+'a'*16,'kind':'preference','at':100}]
        r.observe(Message('以前喜欢咖啡，现在不喜欢了',message_id='old',speaker_id='p_'+'a'*16,created_at=100),baseline=True)
        assert '现在不喜欢了' in r.notes.data['facts'][0]['source_text']
    finally:r.close();s.close()


def test_verified_and_unknown_sends_keep_unanswered_guard_after_restart(tmp_path):
    r,s,q,now=build(tmp_path)
    try:
        r.tick([],epoch='one',eligible=True);q.finish()
        candidate,reply=r.tick([],epoch='one',eligible=True)
        assert reply and r.valid(candidate)
        r.begin_send(candidate)
        r.finish(candidate,'unknown')
        r.close()
        from xiaomao.companion_runtime import CompanionRuntime
        again=CompanionRuntime(r.settings,s,r.model,lambda:now[0],executor=Deferred())
        assert again.planner.state['unanswered']
        assert not again.valid(candidate)
        again.close()
    finally:s.close()


# Use the existing guarded sender fixture; no live computer interaction.
from test_engine import system


def attach(engine,clock):
    from xiaomao.companion_runtime import CompanionRuntime
    from xiaomao.companion_store import CompanionStore
    from xiaomao.companion import Candidate
    clock.value=at(10)
    notes=CompanionStore(engine.settings.runtime,engine.settings.memory_scope)
    notes.data['preferences']=asdict(CompanionPreferences(enabled=True));notes.save()
    engine.companion=CompanionRuntime(engine.settings,engine.store,engine.model,clock,executor=Deferred())
    engine.mode='auto';engine.cancelled.clear()
    c=Candidate('real-pipeline','idle',clock(),clock()+120,engine.companion.planner.state['revision'],'2026-11-04')
    assert engine.store.reserve_proactive(c.id,engine.settings.memory_scope,c.day,clock(),2)
    from xiaomao.domain import ReplyTask
    task=ReplyTask(engine.store.create('candidate',clock()),clock(),clock(),' ',reply='下午的阳光好暖呀。',proactive=c)
    engine.tasks.append(task)
    return task


def test_proactive_uses_same_input_send_and_readback_pipeline(system):
    e,screen,model,clock,store=system
    task=attach(e,clock)
    e._reply_and_send(task)
    assert screen.events==['focus','type','send']
    assert store.get(task.id)['state']=='verified'
    assert e.companion.planner.state['unanswered']
    e.companion.close()


def test_delayed_proactive_has_a_fresh_send_window_with_short_task_expiry(system):
    from xiaomao.companion_runtime import CompanionRuntime
    from xiaomao.companion_store import CompanionStore
    e,screen,model,clock,store=system
    clock.value=at(10);e.settings.expiry_seconds=20
    e.settings.conversation_targets={e.settings.group:{'id':'chat@chatroom','account':'a','kind':'group'}}
    notes=CompanionStore(e.settings.runtime,e.settings.memory_scope)
    notes.data['preferences']=asdict(CompanionPreferences(enabled=True));notes.save()
    model.proactive=lambda *a:'最近有没有什么新鲜的小点子呀？'
    e.companion=CompanionRuntime(e.settings,store,model,clock,executor=Deferred())
    try:
        e.mode='auto';e.cancelled.clear()
        e.companion.observe(Message('做小工具',message_id='previous',created_at=clock()-3600),baseline=True)
        e.companion_once();clock.advance(60)
        e._companion_tick();e.companion.executor.finish();e._companion_tick()
        assert len(e.tasks)==1
        task=e.tasks[0]
        e._reply_and_send(task)
        assert screen.events==['focus','type','send']
        assert store.get(task.id)['state']=='verified'
    finally:e.companion.close()


def test_queued_routine_can_still_send_after_regular_reply_expiry(system):
    from xiaomao.companion_runtime import CompanionRuntime
    from test_routines import at as routine_time, preferences
    e,screen,model,clock,store=system
    clock.value=routine_time(9)
    e.settings.expiry_seconds=120
    e.settings.conversation_targets={e.settings.group:{'id':'chat@chatroom','account':'a','kind':'group'}}
    e.mode='auto';e.cancelled.clear()
    model.proactive=lambda *args:'早安呀。'
    e.companion=CompanionRuntime(e.settings,store,model,clock,executor=Deferred())
    try:
        e.companion.configure(preferences())
        e._companion_tick()
        e.companion.executor.finish()
        e._companion_tick()
        task=e.tasks[0]
        clock.advance(125)
        assert e._ready_for_input(task)
        clock.value=routine_time(9,5,second=1)
        assert not e._active(task)
        assert screen.events==[]
    finally:e.companion.close()


def test_new_inbound_after_typing_preserves_draft_and_never_clicks_send(system):
    e,screen,model,clock,store=system
    task=attach(e,clock)
    screen.after_type=lambda:e._record([Message('新消息',message_id='new',created_at=clock())])
    e._reply_and_send(task)
    assert screen.events==['focus','type'] and screen.frame.draft
    assert e.mode=='paused'
    e.companion.close()
