from dataclasses import asdict, replace
import pytest


class Clock:
    value = 100.0
    def __call__(self): return self.value
    def advance(self, n): self.value += n


class Screen:
    def __init__(self):
        from xiaomao.domain import Frame, Message
        self.frame = Frame("示例会话", (Message("更早的消息", "朋友"),Message("已有消息", "朋友")))
        self.events = []
        self.after_type = None
        self.unknown = False
        self.generation = 100
        self.focus_changed = False
        self.hidden=False
        self.prepared=0
        self.prepare_callback=None
        self.background=False
    def capture(self):
        if self.hidden:
            from xiaomao.windows import DesktopTemporarilyHidden
            raise DesktopTemporarilyHidden('微信目标群需要保持前台可见')
        return self.frame
    def idle_for(self): return 60
    def input_generation(self): return self.generation
    def assert_input_focus(self):
        if self.focus_changed: raise ValueError('focus changed')
    def click_input(self,expected_generation=None):
        assert expected_generation is None or expected_generation==self.generation
        self.events.append("focus")
    def prepare_send(self,idle_seconds=10):
        self.prepared+=1
        if self.prepare_callback:self.prepare_callback()
        self.background=False
        return self.generation
    def is_foreground(self):return not self.background
    def prepare_baseline(self,idle_seconds=10):
        if self.background:self.prepare_send(idle_seconds);return False
        return True
    def select_target(self,idle_seconds=10):return False
    def validate_target(self,image):return None
    def go_to_latest(self,idle_seconds=10):return False
    def type_text(self, text):
        self.events.append("type")
        self.frame = replace(self.frame, draft=text, draft_empty=False)
        if self.after_type: self.after_type()
    def click_send(self):
        from xiaomao.domain import Message
        self.events.append("send")
        if not self.unknown:
            self.frame = replace(self.frame, messages=self.frame.messages+(Message(self.frame.draft, "自己", "out"),), draft="", draft_empty=True)


class IdentityVision:
    def read(self, image, calibration): return image


class ReplyModel:
    def __init__(self): self.callback = None
    def reply(self, messages):
        if self.callback: self.callback()
        return "喵，下午好。"


@pytest.fixture
def system(tmp_path):
    from xiaomao.config import Settings, Calibration
    from xiaomao.engine import Engine
    from xiaomao.store import StateStore
    cfg = Settings(tmp_path, model_base_url="http://127.0.0.1:11434/v1", model_name="test", natural_pacing=False, reply_mode='mentions')
    cfg.calibration = asdict(Calibration(1, 1, 800, 600, "示例会话(2)", {
        "header":[0,0,1,.1], "messages":[0,.1,1,.7], "input":[0,.8,.8,.95], "send":[.8,.95,1,1]
    }, True))
    clock, screen, model = Clock(), Screen(), ReplyModel()
    store = StateStore(tmp_path / 'runtime')
    engine = Engine(cfg, store, screen, IdentityVision(), model, clock=clock, settle_seconds=0)
    yield engine, screen, model, clock, store
    store.close()


def trigger(system, auto=True):
    from xiaomao.domain import Message
    engine, screen, model, clock, store = system
    if auto: engine.enable()
    engine.observe_once()
    screen.frame = replace(screen.frame, messages=screen.frame.messages+(Message("小猫下午好", "朋友"),))
    clock.advance(3)
    engine.observe_once()
    clock.advance(3)
    engine.observe_once()


def process(system):
    engine, _, _, clock, _ = system
    clock.advance(4)
    engine.observe_once()


@pytest.mark.parametrize('state',['verified','skipped','unknown'])
def test_only_verified_reply_releases_editor_transaction(system,state):
    from xiaomao.domain import Message
    engine,screen,_,_,_=system
    engine.enable();engine._record([Message('小猫你好',message_id='finish-q')])
    finished=[];screen.finish_input=lambda:finished.append(1)
    engine._retire(engine.tasks[0],state)
    assert finished==([1] if state=='verified' else [])


def test_database_receives_in_background_without_any_screenshot(system):
    from xiaomao.database import MessageBatch
    from xiaomao.domain import Message
    engine,screen,model,clock,store=system
    batches=iter([MessageBatch([Message('之前的问题')],[],True),MessageBatch([], [Message('普通新消息',message_id='id2',created_at=101)])])
    class Feed:
        def poll(self):return next(batches)
    engine.message_source=Feed();engine.settings.reply_mode='all'
    from xiaomao.policy import TriggerPolicy
    engine.policy=TriggerPolicy('小猫','','','all')
    screen.capture=lambda:(_ for _ in ()).throw(AssertionError('后台接收不得截图'))
    engine.observe_once();clock.advance(1);engine.observe_once()
    assert engine.mode=='observe'
    assert engine.pending()[0]['messages']==['普通新消息']
    assert [m.text for m in engine.context]==['之前的问题','普通新消息']


def test_database_read_failure_is_retryable_and_preserves_auto(system):
    from xiaomao.database import MessageBatch,DatabaseUnavailable
    engine,screen,model,clock,store=system
    class Feed:
        failed=False
        def poll(self):
            if not self.failed:self.failed=True;raise DatabaseUnavailable('消息日志正在更新')
            return MessageBatch([],[],True)
    engine.message_source=Feed();engine.enable();engine.observe_once()
    assert engine.mode=='auto' and not engine.cancelled.is_set() and engine.message_source.failed
    clock.advance(3);engine.observe_once()
    assert engine.mode=='auto' and engine.last_observed==clock()


@pytest.mark.parametrize('interruption',['picker','expiry','clock_rollback'])
def test_database_interruption_after_typing_pauses_and_preserves_draft(system,interruption):
    from xiaomao.database import MessageBatch
    from xiaomao.domain import Message
    from xiaomao.windows import DesktopTemporarilyHidden
    engine,screen,model,clock,store=system
    class Feed:
        batches=[MessageBatch([],[],True),MessageBatch([],[Message('小猫你好',message_id='new',created_at=101)])]
        def poll(self):return self.batches.pop(0) if self.batches else MessageBatch([],[])
    engine.message_source=Feed();engine.enable();engine.observe_once();clock.advance(1)
    def interrupt():
        if interruption=='picker':raise DesktopTemporarilyHidden('成员选择弹窗遮挡输入区')
        clock.advance(121 if interruption=='expiry' else -2)
    screen.after_type=interrupt
    engine.observe_once()
    assert engine.mode=='paused'
    assert screen.frame.draft=='喵，下午好。' and not screen.frame.draft_empty
    assert screen.events==['focus','type'] and not store.db.execute('SELECT * FROM attempts').fetchall()
    clock.advance(200);engine.observe_once()
    assert screen.events==['focus','type'] and not engine.tasks


def test_database_temporary_obstruction_before_typing_keeps_auto(system):
    from xiaomao.database import MessageBatch
    from xiaomao.domain import Message
    engine,screen,model,clock,store=system
    class Feed:
        batches=[MessageBatch([],[],True),MessageBatch([],[Message('小猫你好',message_id='new',created_at=101)])]
        def poll(self):return self.batches.pop(0) if self.batches else MessageBatch([],[])
    engine.message_source=Feed();engine.enable();engine.observe_once();clock.advance(1)
    screen.hidden=True;engine.observe_once()
    assert engine.mode=='auto' and screen.events==[] and engine.tasks[0].state=='generated'


@pytest.mark.parametrize('state',['staged','sending','unknown'])
def test_database_orphaned_input_is_not_expired_as_an_unsent_candidate(system,state):
    from xiaomao.database import MessageBatch
    from xiaomao.domain import Message
    engine,screen,model,clock,store=system
    class Feed:
        def poll(self):return MessageBatch([],[])
    engine.message_source=Feed();engine.enable()
    engine._record([Message('小猫你好',message_id='new',created_at=100)])
    task=engine.tasks[0];task.state=state;store.update(task.id,state)
    screen.frame=replace(screen.frame,draft='输入了一半',draft_empty=False)
    clock.advance(121);engine.observe_once()
    assert engine.mode=='paused' and not engine.tasks and screen.events==[]
    assert screen.frame.draft=='输入了一半'
    assert store.get(task.id)['reason']!='expired'
    assert store.get(task.id)['state']==('skipped' if state=='staged' else 'unknown')


def test_manual_pause_reason_survives_inflight_database_preparation(system):
    from xiaomao.database import MessageBatch
    from xiaomao.domain import Message
    from xiaomao.windows import DesktopTemporarilyHidden
    engine,screen,model,clock,store=system
    class Feed:
        batches=[MessageBatch([],[],True),MessageBatch([],[Message('小猫你好',message_id='new',created_at=101)])]
        def poll(self):return self.batches.pop(0) if self.batches else MessageBatch([],[])
    engine.message_source=Feed();engine.enable();engine.observe_once();clock.advance(1)
    def pause_during_capture():
        engine.pause('用户已暂停')
        raise DesktopTemporarilyHidden('等待窗口')
    screen.capture=pause_during_capture
    engine.observe_once()
    assert engine.mode=='paused' and engine.reason=='用户已暂停' and screen.events==[]


def test_database_resume_skips_messages_from_manual_pause(system):
    from xiaomao.database import MessageBatch
    from xiaomao.domain import Message
    engine,screen,model,clock,store=system
    class Feed:
        batch=MessageBatch([],[],True)
        def poll(self):return self.batch
    engine.message_source=Feed();engine.observe_once();engine.pause()
    old=Message('小猫这是暂停期间的消息',message_id='during_pause')
    engine.message_source.batch=MessageBatch([old],[old])
    engine.enable();engine.observe_once()
    assert screen.events==[] and not engine.pending()
    assert engine.context[-1].text==old.text


def test_database_resume_rejects_late_sync_from_pause_period(system):
    from xiaomao.database import MessageBatch
    from xiaomao.domain import Message
    engine,screen,model,clock,store=system
    class Feed:
        batch=MessageBatch([],[],True)
        def poll(self):return self.batch
    engine.message_source=Feed();engine.observe_once();engine.pause();clock.advance(20)
    engine.enable();engine.observe_once();clock.advance(1)
    engine.message_source.batch=MessageBatch([],[Message('小猫暂停期间发的',message_id='late',created_at=110)])
    engine.observe_once()
    assert not engine.pending() and screen.events==[]


def test_queued_context_adds_prior_reply_without_duplicating_question(system):
    from xiaomao.domain import Message
    engine,screen,model,clock,store=system
    engine.message_source=object()
    engine._record([Message('小猫问题一',message_id='q1'),Message('小猫问题二',message_id='q2')])
    engine._remember(Message('先回答第一题','','out',message_id='answer-q1',reply_to_id='q1'))
    captured=[]
    model.reply=lambda msgs:captured.append([m.text for m in msgs]) or '好的'
    engine.mode='auto';screen.idle_for=lambda:0
    engine._reply_and_send(engine.tasks[-1])
    assert captured[0].count('小猫问题二')==1
    assert captured[0][-2:]==['先回答第一题','小猫问题二']


def test_queued_context_enriches_same_receipt_instead_of_duplicating_answer(system):
    from xiaomao.domain import Message
    engine,screen,model,clock,store=system
    engine.message_source=object()
    outgoing=Message('回答甲',direction='out',message_id='out1',created_at=100)
    engine._record([outgoing,Message('小猫乙的追问',speaker_id='p_'+'b'*16,message_id='in2')])
    engine._remember(replace(outgoing,reply_to_speaker='p_'+'a'*16,reply_to_id='in1'))
    captured=[];model.reply=lambda msgs:captured.append(list(msgs)) or '好的'
    engine.mode='auto';screen.idle_for=lambda:0
    engine._reply_and_send(engine.tasks[-1])
    answers=[m for m in captured[0] if m.message_id=='out1']
    assert len(answers)==1 and answers[0].reply_to_speaker=='p_'+'a'*16


def test_exact_input_readback_can_replace_wrong_ocr(system):
    engine,screen,model,clock,store=system
    screen.read_input_text=lambda expected:screen.frame.draft
    class MisreadingVision:
        def read(self,image,calibration):return replace(image,draft='OCR错字') if image.draft else image
    engine.vision=MisreadingVision()
    trigger(system);process(system)
    assert store.counts()=={'verified':1} and screen.events.count('send')==1


def test_wrong_exact_input_never_falls_back_to_correct_ocr(system):
    engine,screen,model,clock,store=system
    screen.read_input_text=lambda expected:'输入实际丢字'
    trigger(system);process(system)
    assert engine.mode=='paused' and 'send' not in screen.events


def test_clipboard_failure_before_input_keeps_auto_and_reuses_generated_reply(system):
    from xiaomao.windows import DesktopInputNotStarted
    engine,screen,model,clock,store=system
    calls=[];model.callback=lambda:calls.append(1)
    original=screen.type_text
    def blocked(text):raise DesktopInputNotStarted('剪贴板暂不可用')
    screen.type_text=blocked
    trigger(system);process(system)
    assert engine.mode=='auto' and len(engine.tasks)==1
    assert engine.tasks[0].state=='generated' and engine.tasks[0].reply
    assert screen.frame.draft_empty and 'send' not in screen.events
    screen.type_text=original;clock.advance(3);process(system)
    assert store.counts()=={'verified':1} and screen.events.count('send')==1 and calls==[1]


def test_manual_pause_during_clipboard_preflight_is_never_undone(system):
    from xiaomao.windows import DesktopInputNotStarted
    engine,screen,model,clock,store=system
    def blocked(text):
        engine.pause('用户已暂停')
        raise DesktopInputNotStarted('剪贴板暂不可用')
    screen.type_text=blocked
    trigger(system);process(system)
    assert engine.mode=='paused' and not engine.tasks and 'send' not in screen.events


def test_database_still_receives_while_clipboard_is_waiting_for_retry(system):
    from xiaomao.windows import DesktopInputNotStarted
    from xiaomao.database import MessageBatch
    from xiaomao.domain import Message
    engine,screen,model,clock,store=system
    first=Message('小猫第一个问题',message_id='question1',created_at=101)
    second=Message('小猫第二个问题',message_id='question2',created_at=104)
    batches=iter([MessageBatch([],[],True),MessageBatch([],[first]),MessageBatch([],[second])])
    class Feed:
        def poll(self):return next(batches)
    engine.message_source=Feed()
    def blocked(text):raise DesktopInputNotStarted('剪贴板暂不可用')
    screen.type_text=blocked
    engine.enable();engine.observe_once();clock.advance(3);engine.observe_once()
    assert engine.mode=='auto' and engine.tasks[0].state=='generated'
    clock.advance(.5);engine.observe_once()
    assert engine.mode=='auto' and engine.last_observed==clock()
    assert any(m.message_id=='question2' for m in engine.context)
    assert 'send' not in screen.events and store.counts().get('unknown',0)==0


def test_database_self_message_and_same_id_never_create_extra_reply(system):
    from xiaomao.database import MessageBatch
    from xiaomao.domain import Message
    engine,screen,model,clock,store=system
    batches=iter([MessageBatch([],[],True),MessageBatch([], [Message('小猫好',message_id='new'),Message('自己','','out',message_id='self')]),MessageBatch([], [Message('小猫好',message_id='new')])])
    class Feed:
        def poll(self):return next(batches)
    engine.message_source=Feed()
    for _ in range(3):engine.observe_once();clock.advance(1)
    assert len(engine.pending())==1
    assert [m.text for m in engine.context][-1]=='自己'


@pytest.mark.parametrize('unreadable_after_send',[False,True])
@pytest.mark.parametrize('incoming_kind',[1,3,47,(57<<32)|49])
# The fixture's Name2Id row 2 identifies "friend"; the private peer must match.
@pytest.mark.parametrize('conversation_id',['g@chatroom','friend'])
def test_database_end_to_end_uses_outgoing_database_confirmation(system,tmp_path,unreadable_after_send,incoming_kind,conversation_id):
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    from test_database import database
    engine,screen,model,clock,store=system
    path=tmp_path/'message_0.db';database(path,[(1,1,99,1,2,'旧消息')])
    source=MemoryDatabase(path)
    engine.message_source=DatabaseFeed(lambda:[source],conversation_id,'wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=clock)
    if incoming_kind in (3,47):
        engine.settings.reply_mode='all';engine.policy.mode='all'
        engine.message_source.resolve_media=lambda message:b'\x89PNG\r\n\x1a\nstatic'
    engine.enable();engine.observe_once()
    clock.advance(1);database(path,[(2,2,101,2,2,'小猫你好')])
    if incoming_kind!=1:
        import sqlite3
        with sqlite3.connect(path) as conn:conn.execute(f'UPDATE [{engine.message_source.table}] SET local_type=? WHERE local_id=2',(incoming_kind,))
    if incoming_kind in (3,47):
        model.reply=lambda messages: '喵，下午好。' if messages[-1].image_data else pytest.fail('image not passed to model')
    if incoming_kind==(57<<32)|49:
        with sqlite3.connect(path) as conn:
            conn.execute(f'UPDATE [{engine.message_source.table}] SET message_content=? WHERE local_id=2',('<msg><appmsg><type>57</type><title>小猫你好</title><refermsg><content>旧问题</content></refermsg></appmsg></msg>',))
        model.reply=lambda messages:'喵，下午好。' if messages[-1].text=='小猫你好' else pytest.fail('quoted body not passed to model')
    def sent():
        screen.events.append('send')
        database(path,[(3,3,101,3,1,screen.frame.draft)])
        # The screen deliberately has no outgoing bubble; DB supplies confirmation.
        screen.frame=replace(screen.frame,draft='',draft_empty=True)
        if unreadable_after_send:
            def unavailable():raise AssertionError('动画变化或窗口失焦不能影响数据库回执')
            screen.capture=unavailable
    screen.click_send=sent
    engine.observe_once()
    assert store.counts()=={'verified':1} and screen.events.count('send')==1
    assert engine.context[-1].text=='喵，下午好。'
    assert engine.context[-1].reply_to_id==conversation_id+'/s/2'
    assert engine.context[-1].reply_to_speaker==engine.context[-2].speaker_id
    assert engine.context[-1].reply_to_speaker
    engine.observe_once()
    assert screen.events.count('send')==1
    source.close()


def test_delayed_database_receipt_waits_without_retyping_or_pausing(system,tmp_path):
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    from test_database import database
    engine,screen,model,clock,store=system
    path=tmp_path/'message_0.db';database(path,[(1,1,99,1,2,'旧消息')])
    source=MemoryDatabase(path)
    engine.message_source=DatabaseFeed(lambda:[source],'g@chatroom','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=clock)
    engine.enable();engine.observe_once();clock.advance(1)
    database(path,[(2,2,101,2,2,'小猫你好')])
    screen.click_send=lambda:screen.events.append('send')
    engine.observe_once()
    assert engine.mode=='auto' and engine.tasks[0].state=='awaiting_receipt'
    clock.advance(4);database(path,[(3,3,105,3,1,'喵，下午好。')])
    engine.observe_once()
    assert store.counts()=={'verified':1} and screen.events.count('send')==1
    source.close()


def test_database_prior_matching_outgoing_is_not_a_new_send_confirmation(system,tmp_path):
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    from test_database import database
    engine,screen,model,clock,store=system
    path=tmp_path/'message_0.db';database(path,[(1,1,99,1,1,'喵，下午好。')])
    source=MemoryDatabase(path)
    engine.message_source=DatabaseFeed(lambda:[source],'g@chatroom','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=clock)
    engine.enable();engine.observe_once();clock.advance(1)
    database(path,[(2,2,101,2,2,'小猫你好')])
    screen.click_send=lambda:None
    engine.observe_once()
    assert engine.tasks[0].state=='awaiting_receipt'
    clock.advance(31);engine.observe_once()
    assert engine.mode=='paused' and store.counts()=={'unknown':1}
    source.close()


def test_outgoing_arriving_during_generation_does_not_confirm_later_send(system,tmp_path):
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    from test_database import database
    engine,screen,model,clock,store=system
    path=tmp_path/'message_0.db';database(path,[(1,1,99,1,2,'旧')])
    source=MemoryDatabase(path)
    engine.message_source=DatabaseFeed(lambda:[source],'g@chatroom','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=clock)
    engine.enable();engine.observe_once();clock.advance(1)
    database(path,[(2,2,101,2,2,'小猫好')])
    model.callback=lambda:database(path,[(3,3,101,3,1,'喵，下午好。')])
    screen.click_send=lambda:setattr(screen,'frame',replace(screen.frame,draft='',draft_empty=True))
    engine.observe_once()
    clock.advance(31);engine.observe_once()
    assert store.counts()=={'unknown':1}
    source.close()


def test_media_unavailable_is_retried_without_pausing_or_losing_later_text(system):
    from xiaomao.domain import Message
    engine,screen,model,clock,store=system
    engine.settings.reply_mode='all';engine.policy.mode='all';engine.mode='auto'
    class Source:
        def resolve_media(self,message):return None
    engine.message_source=Source()
    engine._record([Message('[图片]',kind='image',message_id='image',media_raw='<msg/>'),Message('接着说',message_id='text')])
    assert len(engine.tasks)==2
    engine._reply_and_send(engine.tasks[0])
    assert engine.mode=='auto' and screen.events==[] and len(engine.tasks)==2
    assert engine.tasks[0].retry_at>clock()


def test_restart_keeps_earlier_context_beyond_24_and_merges_baseline_once(system):
    from xiaomao.domain import Message
    from xiaomao.engine import Engine
    engine,screen,model,clock,store=system
    for i in range(40):engine._remember(Message('记忆'+str(i),message_id=str(i),created_at=90+i))
    reopened=Engine(engine.settings,store,screen,engine.vision,model,clock=clock,settle_seconds=0)
    assert len(reopened.memory.messages)==40
    reopened._seed_context([Message('记忆'+str(i),message_id=str(i),created_at=90+i) for i in range(16,40)])
    reopened._record([Message('小猫刚才提到记忆0吗',message_id='followup')])
    assert len(reopened.memory.messages)==41
    assert reopened.tasks[0].context[0].text=='记忆0'


def test_legacy_context_migrates_once_without_repeating_baseline(system):
    from xiaomao.domain import Message
    from xiaomao.engine import Engine
    engine,screen,model,clock,store=system
    store.save_context(engine.settings.group,[Message('更早的重要约定'),Message('近期重叠消息')],clock())
    restored=Engine(engine.settings,store,screen,engine.vision,model,clock=clock)
    restored._seed_context([Message('近期重叠消息',message_id='id1'),Message('最新消息',message_id='id2')])
    assert [m.text for m in restored.memory.messages]==['更早的重要约定','近期重叠消息','最新消息']
    assert restored.memory.messages[1].message_id=='id1'


def test_observe_mode_produces_candidate_without_sending(system):
    trigger(system, auto=False)
    process(system)
    engine, screen, _, _, store = system
    assert screen.events == []
    assert store.counts() == {"candidate": 1}
    assert engine.pending()[0]["messages"] == ["小猫下午好"]


def test_visible_history_supplies_context_without_replying_to_it(system):
    from xiaomao.domain import Message
    engine,screen,model,clock,store=system
    screen.frame=replace(screen.frame,messages=(Message('这个工具是我自己开发的','甲'),Message('很可爱呀','','out')))
    engine.enable();engine.observe_once()
    assert engine.pending()==[] and screen.events==[]
    captured=[]
    model.reply=lambda messages:captured.append([m.text for m in messages]) or '是自己开发的喵。'
    screen.frame=replace(screen.frame,messages=screen.frame.messages+(Message('小猫，开发的吗','乙'),))
    clock.advance(3);engine.observe_once();clock.advance(3);engine.observe_once();process(system)
    assert captured[0]==['这个工具是我自己开发的','很可爱呀','小猫，开发的吗']
    assert list(engine.context)[-1].text=='是自己开发的喵。'
    from xiaomao.memory import ConversationMemory
    restored=ConversationMemory(engine.settings.runtime,engine.settings.group,clock)
    assert restored.messages[-1].text=='是自己开发的喵。'


def test_temporary_memory_save_failure_retains_backlog_and_flushes(system,monkeypatch):
    from xiaomao.domain import Message
    from xiaomao.memory import MemoryPersistenceError,ConversationMemory
    engine,screen,model,clock,store=system
    save=engine.memory._save
    def failing(*args):raise MemoryPersistenceError('test')
    monkeypatch.setattr(engine.memory,'_save',failing)
    engine._remember(Message('先记住周五取件',message_id='m1'))
    assert [m.text for m in engine._context_snapshot()]==['先记住周五取件']
    monkeypatch.setattr(engine.memory,'_save',save)
    engine._remember(Message('周五下午',message_id='m2'))
    restored=ConversationMemory(engine.settings.runtime,engine.settings.group,clock)
    assert [m.text for m in restored.messages]==['先记住周五取件','周五下午']
    assert not engine.memory_pending


def test_idle_compaction_survives_restart_and_never_enqueues_history(system):
    from xiaomao.domain import Message
    from xiaomao.database import MessageBatch
    from xiaomao.engine import Engine
    engine,screen,model,clock,store=system
    history=[Message('第一条事实' if i==0 else f'旧话{i}',message_id=f'm{i}') for i in range(97)]
    engine._seed_context(history)
    class Feed:
        def poll(self):return MessageBatch(history[-24:],[],True)
    calls=[]
    model.compact=lambda summary,older:calls.append(len(older)) or '保留第一条事实'
    engine.message_source=Feed();engine.enable();engine.observe_once()
    engine._compaction_thread.join(2)
    assert not engine._compaction_thread.is_alive()
    engine.observe_once()
    assert calls==[73] and not engine.tasks and screen.events==[]
    restored=Engine(engine.settings,store,screen,IdentityVision(),model,clock=clock)
    assert restored.memory.summary=='保留第一条事实'
    assert len(restored.memory.messages)==24


def test_resolved_image_remains_in_followup_context_without_xml(system):
    from xiaomao.domain import Message
    engine,screen,model,clock,store=system
    from xiaomao.policy import TriggerPolicy
    engine.settings.reply_mode='all';engine.policy=TriggerPolicy('小猫','',mode='all')
    png=b'\x89PNG\r\n\x1a\nstatic'
    references=[]
    class Feed:
        def resolve_media(self,message):references.append(message.media_raw);return png
    engine.message_source=Feed()
    engine._record([Message('[图片]',kind='image',message_id='img',media_raw='<msg>local-ref</msg>')])
    assert engine._prepare_media(engine.tasks[0])
    engine._record([Message('这张图是什么',message_id='followup')])
    context=engine.tasks[-1].context
    assert references and all(ref=='<msg>local-ref</msg>' for ref in references)
    assert context[0].image_data==png and not context[0].media_raw
    assert context[-1].text=='这张图是什么'


def test_readback_ignores_only_ocr_missing_literary_dash():
    from xiaomao.engine import readback_key
    assert readback_key('哪件事——是开发的吗？')==readback_key('哪件事是开发的吗？')
    for expected,actual in [('1.5','15'),('1—2','12'),('A-B','AB'),('可以','不可以')]:
        assert readback_key(expected)!=readback_key(actual)


def test_input_layout_can_settle_without_retyping_or_double_sending(system):
    engine,screen,_,_,store=system
    trigger(system)
    original=screen.capture;seen=[]
    def capture():
        frame=original()
        if screen.events==['focus','type'] and not seen:
            seen.append(1);return replace(frame,draft='喵，下午')
        return frame
    screen.capture=capture
    process(system)
    assert screen.events==['focus','type','send'] and store.counts()=={'verified':1}


def test_native_guard_failure_shows_actionable_reason_without_any_input(system):
    from xiaomao.windows import DesktopUnavailable
    engine,screen,_,_,_=system
    def unavailable():
        raise DesktopUnavailable('微信目标群需要保持前台可见')
    screen.capture=unavailable
    engine.observe_once()
    assert engine.mode=='paused'
    assert engine.reason=='微信目标群需要保持前台可见'
    assert screen.events==[]


def test_single_new_trigger_is_sent_and_client_readback_verified(system):
    trigger(system)
    process(system)
    engine, screen, _, _, store = system
    assert screen.events == ["focus", "type", "send"]
    assert store.counts() == {"verified": 1}
    assert engine.pending() == []


def test_manual_input_during_post_activation_ocr_defers_without_clicking(system):
    engine,screen,_,_,store=system
    trigger(system)
    original_capture=screen.capture
    def capture():
        frame=original_capture()
        if screen.prepared:screen.generation+=1
        return frame
    screen.capture=capture
    process(system)
    assert screen.events==[]
    assert engine.mode=='auto'
    assert store.counts()=={'generated':1}


def test_all_mode_queues_each_message_and_freezes_its_context(system):
    from xiaomao.domain import Message
    from xiaomao.policy import TriggerPolicy
    engine,screen,model,clock,store=system
    engine.settings.reply_mode='all'
    engine.policy=TriggerPolicy('小猫','',mode='all')
    engine.enable();engine.observe_once()
    received=[]
    def reply(messages):received.append([m.text for m in messages]);return '好呀喵'
    model.reply=reply
    first,second=Message('今晚吃什么？','朋友'),Message('周末去哪里？','朋友')
    screen.frame=replace(screen.frame,messages=screen.frame.messages+(first,second))
    clock.advance(3);engine.observe_once();clock.advance(3);engine.observe_once()
    assert screen.events.count('send')==1 and [t.messages for t in engine.tasks]==[[second]]
    assert received[0][-1]==first.text and second.text not in received[0]


def test_temporary_model_failure_retries_without_reenable_or_duplicate_send(system):
    from xiaomao.model import ModelError
    engine,screen,model,clock,store=system
    calls=[]
    def reply(messages):
        calls.append(1)
        if len(calls)==1:raise ModelError('临时网络错误',retryable=True)
        return '来啦喵'
    model.reply=reply
    trigger(system);process(system)
    assert engine.mode=='auto' and screen.events==[]
    clock.advance(2);engine.observe_once();assert len(calls)==1
    clock.advance(5);engine.observe_once()
    assert screen.events.count('send')==1 and store.counts()=={'verified':1}


def test_temporary_model_error_after_expiry_skips_task_without_pausing(system):
    from xiaomao.model import ModelError
    engine,screen,model,clock,store=system
    trigger(system)
    def reply(messages):clock.advance(121);raise ModelError('临时网络错误',retryable=True)
    model.reply=reply
    process(system)
    assert engine.mode=='auto' and screen.events==[] and store.counts()=={'skipped':1}


@pytest.mark.parametrize('phase', ['before_activation', 'during_activation', 'after_focus'])
def test_near_expiry_task_is_skipped_before_any_text_is_inserted(system, phase):
    from xiaomao.domain import Message
    engine, screen, model, clock, store = system
    engine.mode = 'auto'
    engine._record([Message('小猫，合成的排队问题。', message_id='queued')])
    task = engine.tasks[0]
    if phase == 'before_activation':
        clock.advance(116)
    elif phase == 'during_activation':
        screen.prepare_callback = lambda: clock.advance(116)
    else:
        capture = screen.capture
        advanced = []
        def slow_focus_capture():
            if screen.events == ['focus'] and not advanced:
                advanced.append(True)
                clock.advance(116)
            return capture()
        screen.capture = slow_focus_capture

    engine._reply_and_send(task)

    assert engine.mode == 'auto' and not engine.tasks
    assert screen.frame.draft_empty and 'type' not in screen.events and 'send' not in screen.events
    assert store.get(task.id)['reason'] == 'insufficient_input_time'
    assert not store.db.execute('SELECT * FROM attempts').fetchall()
    if phase == 'before_activation':
        assert screen.prepared == 0


def test_task_with_input_time_reserve_can_still_send_without_extra_delay(system):
    from xiaomao.domain import Message
    engine, screen, model, clock, store = system
    engine.mode = 'auto'
    engine._record([Message('小猫，还能及时回复。', message_id='queued')])
    clock.advance(115)
    engine._reply_and_send(engine.tasks[0])
    assert store.counts() == {'verified': 1} and screen.events.count('send') == 1


def test_duplicate_group_identity_is_checked_before_body_ocr(system):
    from xiaomao.windows import DesktopUnavailable
    engine,screen,_,_,_=system
    calls=[]
    def duplicate(image):raise DesktopUnavailable('最近列表有两个同名目标群')
    screen.validate_target=duplicate
    engine.vision.read=lambda *args:calls.append('body')
    engine.enable();engine.observe_once()
    assert engine.mode=='paused' and calls==[] and screen.events==[]


def test_database_verified_group_uses_full_header_when_sidebar_label_is_missing(system):
    from xiaomao.windows import DesktopTemporarilyHidden
    engine,screen,_,_,_=system
    class Source:
        def assert_identity(self):pass
    engine.message_source=Source()
    def unavailable(image):raise DesktopTemporarilyHidden('sidebar label missing')
    screen.validate_target=unavailable
    frame=engine._frame(observation=True)
    assert frame.safe and frame.group=='示例会话'
    assert screen.events==[]


def test_database_identity_error_still_blocks_a_matching_window_title(system):
    from xiaomao.database import DatabaseUnavailable
    engine,screen,_,_,_=system
    class Source:
        def assert_identity(self):raise DatabaseUnavailable('同名群的身份发生变化')
    engine.message_source=Source()
    with pytest.raises(DatabaseUnavailable):engine._frame(observation=True)
    assert screen.events==[]


def test_frame_reads_new_capture_geometry_instead_of_persisted_coordinates(system):
    from dataclasses import replace
    engine,screen,_,_,_=system
    updated=replace(engine.settings.binding,regions={**engine.settings.binding.regions,'header':[.2,0,.8,.1]})
    screen.calibration=updated
    def read(image,binding):
        if binding.rect('header')!=(160,0,640,60):
            return replace(image,safe=False,reason='cropped header')
        return image
    engine.vision.read=read
    assert engine._frame().safe


def test_database_uniqueness_does_not_allow_a_different_chat_header(system):
    engine,screen,_,_,_=system
    class Source:
        def assert_identity(self):pass
    engine.message_source=Source()
    screen.frame=replace(screen.frame,group='',safe=False,reason='群标题与已校准目标不一致')
    with pytest.raises(ValueError):engine._frame()
    assert screen.events==[]


def test_latest_recovery_rejects_normal_unknown_attempt_after_reenable(system):
    engine,screen,_,clock,store=system
    trigger(system)
    def uncertain_send():
        screen.events.append('send')
        screen.frame=replace(screen.frame,draft='',draft_empty=True)
    screen.click_send=uncertain_send
    process(system)
    assert store.counts()=={'unknown':1}
    engine.enable();engine.observe_once();engine.reply_latest()
    engine.observe_once();engine.observe_once()
    clock.advance(31);engine.observe_once()
    assert screen.events.count('send')==1
    assert store.counts()=={'unknown':1}


def test_pause_during_model_request_prevents_all_input(system):
    engine, screen, model, _, store = system
    model.callback = lambda: engine.pause("user")
    trigger(system)
    process(system)
    assert screen.events == []
    assert store.counts() == {"skipped": 1}


def test_pause_after_typing_leaves_draft_but_never_clicks_send(system):
    engine, screen, _, _, store = system
    screen.after_type = lambda: engine.pause("user")
    trigger(system)
    process(system)
    assert screen.events == ["focus", "type"]
    assert screen.frame.draft == "喵，下午好。"
    assert store.counts() == {"skipped": 1}


@pytest.mark.parametrize("change", [{"draft":"用户自己的草稿","draft_empty":False}, {"group":"另一个群"}, {"safe":False}, {"at_bottom":False}])
def test_invalid_target_or_existing_draft_prevents_all_input(system, change):
    trigger(system)
    engine, screen, _, _, store = system
    screen.frame = replace(screen.frame, **change)
    process(system)
    assert screen.events == []
    assert engine.status()["mode"] == ('auto' if change=={'at_bottom':False} else 'paused')


def test_locating_latest_messages_rebuilds_baseline_without_replaying_old_text(system):
    from xiaomao.domain import Message
    trigger(system)
    engine,screen,_,clock,store=system
    screen.frame=replace(screen.frame,at_bottom=False)
    calls=[]
    def latest(idle_seconds):
        calls.append(idle_seconds);screen.frame=replace(screen.frame,at_bottom=True);return True
    screen.go_to_latest=latest
    process(system)
    assert engine.mode=='auto' and calls==[10] and screen.events==[] and engine.pending()==[]
    engine.observe_once();assert screen.events==[]
    screen.frame=replace(screen.frame,messages=screen.frame.messages+(Message('小猫，这是定位之后的新消息','朋友'),))
    clock.advance(3);engine.observe_once();clock.advance(3);engine.observe_once();process(system)
    assert screen.events.count('send')==1 and store.counts()=={'skipped':1,'verified':1}


def test_initial_background_baseline_waits_for_visible_scroll_position(system):
    engine,screen,_,clock,store=system
    engine.enable();screen.background=True
    engine.observe_once()
    assert screen.prepared==1 and engine.last_observed is None and screen.events==[]
    engine.observe_once()
    assert engine.last_observed==clock() and engine.pending()==[] and store.counts()=={}


@pytest.mark.parametrize('at_bottom',[True,False])
def test_initial_existing_draft_does_not_activate_or_navigate(system,at_bottom):
    engine,screen,_,_,_=system
    engine.enable();screen.background=True
    screen.frame=replace(screen.frame,draft='真实草稿',draft_empty=False,at_bottom=at_bottom)
    calls=[]
    screen.go_to_latest=lambda *args:calls.append('navigate')
    engine.observe_once()
    assert engine.mode=='paused' and screen.prepared==0 and screen.events==[] and calls==[]


def test_unknown_send_is_quarantined_and_never_retried(system):
    trigger(system)
    engine, screen, _, clock, store = system
    screen.unknown = True
    process(system)
    for _ in range(3):
        clock.advance(40)
        engine.observe_once()
    assert screen.events.count("send") == 1
    assert store.counts() == {"unknown": 1}
    assert engine.status()["mode"] == "paused"


def test_expired_candidate_is_not_sent_late(system):
    trigger(system)
    engine, screen, _, clock, store = system
    clock.advance(121)
    engine.observe_once()
    assert screen.events == []
    assert store.counts() == {"skipped": 1}


def test_hourly_limit_is_checked_before_input(system):
    trigger(system)
    engine, screen, _, clock, store = system
    for offset in [40,80,120,160,200,240]:
        task = store.create(f"old-{offset}", clock()-offset)
        store.mark_sending(task, clock()-offset)
        store.update(task, "verified")
    process(system)
    assert screen.events == []
    assert engine.pending()


def test_enable_discards_observation_candidates_and_builds_new_baseline(system):
    trigger(system, auto=False)
    engine, screen, _, clock, store = system
    engine.enable()
    clock.advance(10)
    engine.observe_once()
    clock.advance(10)
    engine.observe_once()
    assert screen.events == []
    assert store.counts() == {"skipped": 1}


def test_model_context_ends_with_current_trigger_not_later_unaddressed_chatter(system):
    from xiaomao.domain import Message
    engine, screen, model, clock, store = system
    engine.enable()
    engine.observe_once()
    screen.frame = replace(screen.frame, messages=screen.frame.messages+(
        Message("小猫回答这个问题", "甲"), Message("这是其他人的普通闲聊", "乙")))
    clock.advance(3)
    engine.observe_once()
    clock.advance(3)
    engine.observe_once()
    model.reply = lambda messages: messages[-1].text
    process(system)
    assert screen.frame.messages[-1].text == "小猫回答这个问题"


def test_enabling_during_old_observation_discards_its_results(system,monkeypatch):
    import threading
    from xiaomao.domain import Message
    engine,screen,_,clock,store=system
    engine.observe_once()
    screen.frame=replace(screen.frame,messages=screen.frame.messages+(Message('小猫，这是开启前的旧消息','朋友'),))
    clock.advance(3);engine.observe_once();clock.advance(3)
    ready,release=threading.Event(),threading.Event()
    original=engine.tracker.update
    def blocked(messages):
        result=original(messages)
        ready.set();release.wait(3)
        return result
    monkeypatch.setattr(engine.tracker,'update',blocked)
    thread=threading.Thread(target=engine.observe_once);thread.start()
    assert ready.wait(2)
    engine.enable();release.set();thread.join(3)
    process(system)
    assert screen.events == []
    assert engine.pending() == []


def test_manual_input_with_changed_messages_requires_new_baseline(system):
    from xiaomao.domain import Message
    engine,screen,_,clock,_=system
    engine.enable();engine.observe_once()
    screen.generation+=1
    screen.frame=replace(screen.frame,messages=screen.frame.messages+(Message('小猫，这可能是滚动出来的历史','朋友'),))
    clock.advance(3);engine.observe_once()
    assert engine.status()['mode']=='paused'
    assert screen.events==[]


def test_same_window_focus_change_after_typing_cannot_send(system):
    trigger(system)
    engine,screen,_,_,_=system
    screen.after_type=lambda:setattr(screen,'focus_changed',True)
    process(system)
    assert 'send' not in screen.events
    assert engine.status()['mode']=='paused'


def test_focus_change_during_staged_ocr_is_detected_before_send_attempt(system,monkeypatch):
    trigger(system)
    engine,screen,_,_,store=system
    original=screen.capture
    def capture():
        if screen.events == ['focus','type']:
            screen.focus_changed=True
        return original()
    monkeypatch.setattr(screen,'capture',capture)
    process(system)
    assert 'send' not in screen.events
    assert store.counts()=={'skipped':1}


def test_brief_window_switch_waits_then_processes_new_message_without_reenable(system):
    from xiaomao.domain import Message
    engine,screen,_,clock,store=system
    engine.enable();engine.observe_once()
    screen.hidden=True;clock.advance(3);engine.observe_once()
    assert engine.mode=='auto' and '等待' in engine.reason
    assert screen.events==[]
    screen.frame=replace(screen.frame,messages=screen.frame.messages+(Message('小猫你好','朋友'),))
    screen.hidden=False;screen.generation+=1;clock.advance(10)
    engine.observe_once();clock.advance(3);engine.observe_once()
    process(system)
    assert screen.events==['focus','type','send']
    assert store.counts()=={'verified':1}


def test_long_window_absence_rebuilds_baseline_without_replaying_messages(system):
    from xiaomao.domain import Message
    engine,screen,_,clock,store=system
    engine.enable();engine.observe_once()
    screen.hidden=True;clock.advance(3);engine.observe_once()
    screen.frame=replace(screen.frame,messages=screen.frame.messages+(Message('小猫，这是离开期间的旧消息','朋友'),))
    clock.advance(121);screen.hidden=False;screen.generation+=1
    engine.observe_once();process(system)
    assert engine.mode=='auto'
    assert screen.events==[] and engine.pending()==[] and store.counts()=={}


def test_manual_pause_while_waiting_for_window_never_auto_resumes(system):
    engine,screen,_,clock,_=system
    engine.enable();engine.observe_once()
    screen.hidden=True;clock.advance(3);engine.observe_once()
    engine.pause();screen.hidden=False;clock.advance(10);engine.observe_once()
    assert engine.mode=='paused' and screen.events==[]


def test_window_hidden_after_typing_preserves_draft_and_requires_manual_resume(system):
    trigger(system)
    engine,screen,_,_,store=system
    screen.after_type=lambda:setattr(screen,'hidden',True)
    process(system)
    assert engine.mode=='paused'
    assert screen.events==['focus','type']
    assert screen.frame.draft=='喵，下午好。'
    assert store.counts()=={'skipped':1}


def test_returning_to_wrong_group_does_not_resume_after_window_wait(system):
    engine,screen,_,clock,_=system
    engine.enable();engine.observe_once()
    screen.hidden=True;clock.advance(3);engine.observe_once()
    screen.hidden=False;screen.frame=replace(screen.frame,group='另一个群')
    clock.advance(3);engine.observe_once()
    assert engine.mode=='paused' and screen.events==[]


def test_window_wait_does_not_bypass_upward_scroll_geometry_check(system):
    from xiaomao.domain import Message
    engine,screen,_,clock,_=system
    anchors=tuple(replace(m,box=(60,100+i*50,250,130+i*50)) for i,m in enumerate(screen.frame.messages))
    screen.frame=replace(screen.frame,messages=anchors)
    engine.enable();engine.observe_once()
    screen.hidden=True;clock.advance(3);engine.observe_once()
    moved=tuple(replace(m,box=(60,m.box[1]+30,250,m.box[3]+30)) for m in anchors)
    screen.frame=replace(screen.frame,messages=moved+(Message('小猫，滚动出的历史','朋友',box=(60,250,250,280)),))
    screen.hidden=False;screen.generation+=1;clock.advance(10);engine.observe_once()
    assert engine.mode=='paused' and screen.events==[]


def test_queued_candidate_expires_while_window_is_hidden(system):
    trigger(system)
    engine,screen,_,clock,store=system
    screen.hidden=True;clock.advance(3);engine.observe_once()
    clock.advance(121);screen.hidden=False
    engine.observe_once();process(system)
    assert engine.mode=='auto' and screen.events==[]
    assert store.counts()=={'skipped':1} and engine.pending()==[]


@pytest.mark.parametrize('hidden_times,returned_at', [((109,105),110),((109,230),115),((109,95),110)])
def test_clock_rollback_or_observed_expiry_stays_invalid_after_window_returns(system,hidden_times,returned_at):
    trigger(system)
    engine,screen,_,clock,store=system
    screen.hidden=True
    for moment in hidden_times:
        clock.value=moment;engine.observe_once()
    screen.hidden=False;clock.value=returned_at
    engine.observe_once();process(system)
    assert screen.events==[] and engine.pending()==[]
    assert store.counts()=={'skipped':1}


def test_resident_worker_prepares_wechat_itself_before_input(system):
    trigger(system);process(system)
    _,screen,_,_,store=system
    assert screen.prepared==1
    assert screen.events==['focus','type','send'] and store.counts()=={'verified':1}


def test_failed_window_activation_never_types_or_clicks_send(system):
    from xiaomao.windows import DesktopUnavailable
    trigger(system)
    engine,screen,_,_,_=system
    def unavailable():raise DesktopUnavailable('无法激活目标微信')
    screen.prepare_callback=unavailable
    process(system)
    assert screen.events==[] and engine.mode=='paused'


def test_other_app_typing_does_not_disable_background_receipt(system):
    from xiaomao.domain import Message
    engine,screen,_,clock,store=system
    engine.enable();engine.observe_once();screen.background=True;screen.generation+=1
    screen.frame=replace(screen.frame,messages=screen.frame.messages+(Message('小猫你好','朋友'),))
    clock.advance(3);engine.observe_once();clock.advance(3);engine.observe_once();process(system)
    assert engine.mode=='auto' and store.counts()=={'generated':1}
    assert screen.background and not screen.events
    screen.background=False
    process(system)
    assert engine.mode=='auto' and store.counts()=={'verified':1}


def test_local_latest_mention_recovery_requires_two_frames_and_is_not_repeated(system):
    from xiaomao.domain import Message
    engine,screen,_,clock,store=system
    screen.frame=replace(screen.frame,messages=screen.frame.messages+(Message('小猫，这是当前漏掉的点名','朋友'),))
    engine.enable();engine.observe_once()
    engine.reply_latest();engine.observe_once()
    assert screen.events==[]
    clock.advance(3);engine.observe_once();process(system)
    assert store.counts()=={'verified':1}
    # 再次恢复同一最后入站，即使模拟界面仍显示它，也不能重复发送。
    screen.frame=replace(screen.frame,messages=screen.frame.messages[:-1])
    engine.enable();engine.observe_once();engine.reply_latest();engine.observe_once()
    clock.advance(3);engine.observe_once();clock.advance(40);engine.observe_once()
    assert screen.events.count('send')==1


def test_startup_preference_resumes_only_a_ready_non_quarantined_worker(system):
    from xiaomao.engine import Engine
    engine,screen,model,clock,store=system
    engine.settings.auto_on_launch=True
    new=Engine(engine.settings,store,screen,IdentityVision(),model,clock=clock,settle_seconds=0)
    assert new.mode=='auto'
    task=store.create(store.fingerprint('uncertain'),clock());store.mark_sending(task,clock())
    quarantined=Engine(engine.settings,store,screen,IdentityVision(),model,clock=clock,settle_seconds=0)
    assert quarantined.mode=='paused'


def test_latest_recovery_request_expires_without_admitting_old_content(system):
    from xiaomao.domain import Message
    engine,screen,_,clock,store=system
    screen.frame=replace(screen.frame,messages=screen.frame.messages+(Message('小猫你好','朋友'),))
    engine.enable();engine.observe_once();engine.reply_latest()
    clock.advance(121);engine.observe_once();process(system)
    assert screen.events==[] and store.counts()=={}


def test_other_chat_header_waits_for_local_selection_without_model_or_input(system):
    engine,screen,_,clock,_=system
    engine.enable()
    screen.frame=replace(screen.frame,group='',safe=False,reason='群标题与已校准目标不一致')
    calls=[]
    screen.select_target=lambda idle_seconds:calls.append(idle_seconds) or True
    engine.observe_once()
    assert engine.mode=='auto' and calls==[10]
    assert screen.events==[] and engine.pending()==[]
def test_pointer_halo_never_authorizes_overwriting_a_real_draft(system):
    from dataclasses import replace
    from types import SimpleNamespace
    from xiaomao.windows import DesktopTemporarilyHidden
    engine,screen,model,clock,store=system
    engine.message_source=SimpleNamespace(assert_identity=lambda:None)
    screen.frame=replace(screen.frame,draft='',draft_empty=False)
    checked=[]
    screen.confirm_empty_editor=lambda image,vision:checked.append(image) or None
    with pytest.raises(DesktopTemporarilyHidden,match='草稿'):engine._frame(observation=True)
    assert checked==[screen.frame] and not screen.events
    screen.frame=replace(screen.frame,draft='真草稿')
    with pytest.raises(DesktopTemporarilyHidden,match='草稿'):engine._frame(observation=True)
    assert len(checked)==1 and not screen.events


@pytest.mark.parametrize('observation',[True,False])
def test_editor_corroboration_preflight_and_after_focus_keeps_visible_chat(system,observation):
    from types import SimpleNamespace
    from xiaomao.domain import Message
    engine,screen,model,clock,store=system
    engine.message_source=SimpleNamespace(assert_identity=lambda:None)
    visible=replace(screen.frame,draft='',draft_empty=False)
    screen.frame=visible
    checked=[]
    def confirm(image,vision):
        checked.append(image)
        return replace(image,draft_empty=True,messages=(Message('native history'),))
    screen.confirm_empty_editor=confirm
    frame=engine._frame(observation=observation)
    assert frame.draft_empty and frame.messages==visible.messages and checked==[visible]
    assert not screen.events
