from dataclasses import replace
import pytest

from xiaomao.domain import Message
from test_engine import system


def natural_engine(system):
    engine, screen, model, clock, store = system
    engine.settings.reply_mode = engine.policy.mode = 'natural'
    engine.mode = 'auto'
    return engine, screen, model, clock, store


@pytest.mark.parametrize('kind',['emoji','image'])
def test_same_member_followup_supersedes_an_untyped_reaction_but_keeps_the_picture(system,kind):
    engine,screen,model,clock,store=natural_engine(system)
    member='p_'+'a'*16
    pic=Message('[图片]',kind=kind,message_id='cat',speaker_id=member,created_at=clock())
    engine._record([pic])
    old=engine.tasks[0]
    model.participate=lambda *a,**kw:'白猫在挥手吗？'
    screen.idle_for=lambda:0
    engine._reply_and_send(old)
    assert old.state=='generated' and not screen.events
    engine._record([Message('你谁？',speaker_id=member,message_id='identity',created_at=clock())])
    assert store.get(old.id)['state']=='skipped' and len(engine.tasks)==1
    context=engine._reply_context(engine.tasks[0])
    assert [m.message_id for m in context if m.message_id in ('cat','identity')]==['cat','identity']
    assert not screen.events


@pytest.mark.parametrize('mode,who,state,continuation',[
    ('all','a','generated',False),('natural','b','generated',False),('natural','','generated',False),
    ('natural','a','staged',False),('natural','a','awaiting_receipt',False),('natural','a','generated',True)])
def test_picture_followup_respects_mode_member_and_inflight_replies(system,mode,who,state,continuation):
    engine,_,_,clock,_=natural_engine(system)
    engine.settings.reply_mode=engine.policy.mode=mode
    original='p_'+'a'*16 if who else ''
    engine._record([Message('[表情静态帧]',kind='emoji',message_id='cat',speaker_id=original,created_at=clock())])
    old=engine.tasks[0];old.state=state;old.reply='之前生成的回复'
    if continuation:old.continuation_of='previous-verified'
    engine._record([Message('你谁？',message_id='followup',speaker_id=('p_'+who*16) if who else '',created_at=clock())])
    assert engine.tasks==[old,engine.tasks[-1]] and old.reply=='之前生成的回复'


def test_natural_decline_keeps_message_for_later_addressed_reply(system):
    engine, screen, model, clock, store = natural_engine(system)
    decisions, replies = [], []
    model.participate = lambda messages, profile='': decisions.append(list(messages)) or None
    model.reply = lambda messages: replies.append(list(messages)) or '周五去取就好。'
    engine._record([Message('我的包裹周五到。', message_id='ordinary')])
    task_id = engine.tasks[0].id
    engine._reply_and_send(engine.tasks[0])

    assert store.get(task_id)['reason'] == 'participation_declined'
    assert store.counts() == {'skipped': 1}
    assert not engine.tasks and screen.events == [] and screen.prepared == 0
    assert [m.message_id for m in engine._context_snapshot()] == ['ordinary']

    engine._record([Message('小玲，什么时候去取？', message_id='addressed')])
    engine._reply_and_send(engine.tasks[0])

    assert len(decisions) == len(replies) == 1
    assert [m.message_id for m in replies[0]] == ['ordinary', 'addressed']
    assert screen.events.count('send') == 1
    assert store.counts() == {'skipped': 1, 'verified': 1}


@pytest.mark.parametrize('text', ['小玲你好', '@小玲', '大家都在，@小玲\u2005你怎么看？'])
def test_natural_addressed_message_bypasses_optional_decision(system, text):
    engine, screen, model, clock, store = natural_engine(system)
    decisions, replies = [], []
    model.participate = lambda messages, profile='': decisions.append(messages) or None
    model.reply = lambda messages: replies.append(messages) or '在呢。'
    engine._record([Message(text, message_id='addressed')])
    engine._reply_and_send(engine.tasks[0])
    assert decisions == [] and len(replies) == 1
    assert screen.events.count('send') == 1 and store.counts() == {'verified': 1}


def test_natural_decides_and_composes_in_one_call_then_reuses_reply(system):
    engine, screen, model, clock, store = natural_engine(system)
    calls = []
    model.participate = lambda messages, profile='': calls.append(messages) or '这个角度挺有意思。'
    model.reply = lambda messages: pytest.fail('自然参与不能再请求一次普通回复')
    engine._record([Message('刚看完一本好书。', message_id='ordinary')])
    task = engine.tasks[0]
    screen.idle_for = lambda: 0
    engine._reply_and_send(task)
    engine._reply_and_send(task)
    assert len(calls) == 1 and task.reply == '这个角度挺有意思。'
    screen.idle_for = lambda: 60
    engine._reply_and_send(task)
    assert len(calls) == 1 and screen.events.count('send') == 1


def test_similar_at_name_does_not_force_natural_participation(system):
    engine, screen, model, clock, store = natural_engine(system)
    calls = []
    model.participate = lambda messages, profile='': calls.append(messages) or None
    model.reply = lambda messages: pytest.fail('另一个名字不能触发强制回复')
    engine._record([Message('@小玲猫 你好', message_id='other-person')])
    engine._reply_and_send(engine.tasks[0])
    assert len(calls) == 1 and screen.events == []


def test_natural_mode_keeps_separate_messages_without_mentions_merge_wait(system):
    engine, screen, model, clock, store = natural_engine(system)
    engine.settings.merge_seconds = 30
    calls = []
    model.participate = lambda messages, profile='': calls.append(messages[-1].message_id) or '接这条。'
    engine.enable()
    engine.observe_once()
    screen.frame = replace(screen.frame, messages=screen.frame.messages + (
        Message('第一句普通话。', '朋友', message_id='one'),
        Message('第二句普通话。', '朋友', message_id='two')))
    clock.advance(3)
    engine.observe_once()
    clock.advance(3)
    engine.observe_once()
    assert calls == ['one']
    assert screen.events.count('send') == 1
    assert [task.messages[0].message_id for task in engine.tasks] == ['two']


def test_all_mode_still_replies_to_each_message_without_optional_decision(system):
    engine, screen, model, clock, store = system
    engine.settings.reply_mode = engine.policy.mode = 'all'
    engine.mode = 'auto'
    calls = []
    model.participate = lambda messages, profile='': pytest.fail('逐条模式不应做可选参与判断')
    model.reply = lambda messages: calls.append(messages[-1].message_id) or '普通回复。'
    engine._record([Message('普通一。', '朋友', message_id='one'), Message('普通二。', '朋友', message_id='two')])
    assert len(engine.tasks) == 2
    engine._reply_and_send(engine.tasks[0])
    clock.advance(35)
    engine._reply_and_send(engine.tasks[0])
    assert calls == ['one', 'two'] and store.counts() == {'verified': 2}


def start_observer(system, receive_mode):
    from xiaomao.database import MessageBatch
    engine, screen, model, clock, store = natural_engine(system)
    if receive_mode == 'database':
        class Feed:
            def poll(self):
                return MessageBatch([], [])
        engine.message_source = Feed()
    engine.enable()
    engine.observe_once()
    screen.idle_for = lambda: 0
    if receive_mode == 'database':
        screen.capture = lambda: pytest.fail('后台判断不能操作或截图输入窗口')
    return engine, screen, model, clock, store


@pytest.mark.parametrize('receive_mode', ['database', 'screen'])
@pytest.mark.parametrize('decision', [None, '我想接这句。'])
def test_natural_can_decide_once_while_rate_limited_and_user_is_active(system, receive_mode, decision):
    engine, screen, model, clock, store = start_observer(system, receive_mode)
    previous = store.create(store.fingerprint('previous'), clock())
    store.mark_sending(previous, clock())
    store.update(previous, 'verified')
    calls = []
    def participate(messages, profile=''):
        calls.append(messages[-1].message_id)
        return decision
    model.participate = participate
    engine._record([Message('普通话题。', message_id='ordinary')])
    engine.observe_once()
    engine.observe_once()

    assert calls == ['ordinary']
    assert screen.events == [] and screen.prepared == 0
    assert store.counts() == ({'verified': 1, 'skipped': 1} if decision is None else {'verified': 1, 'generated': 1})


@pytest.mark.parametrize('receive_mode', ['database', 'screen'])
@pytest.mark.parametrize('ordinary_state', ['candidate', 'generated'])
def test_natural_prioritizes_addressed_task_before_untyped_ordinary_work(system, receive_mode, ordinary_state):
    engine, screen, model, clock, store = start_observer(system, receive_mode)
    calls = []
    model.participate = lambda messages, profile='': pytest.fail('点名不能被普通候选挡住')
    model.reply = lambda messages: calls.append(messages[-1].message_id) or '针对你的问题来啦。'
    engine._record([Message('普通话题。', message_id='ordinary'), Message('大家说完了，@小玲 看这里', message_id='addressed')])
    ordinary = engine.tasks[0]
    if ordinary_state == 'generated':
        ordinary.state, ordinary.reply = 'generated', '已经生成但尚未输入。'
    if receive_mode == 'screen':
        # Screen generation normally waits for idle; block only actual input.
        screen.idle_for = lambda: 60
        screen.prepare_send = lambda *args: pytest.fail('测试只应生成点名回复')
        model.reply = lambda messages: calls.append(messages[-1].message_id) or setattr(screen, 'idle_for', lambda: 0) or '针对你的问题来啦。'
    engine.observe_once()
    assert calls == ['addressed'] and ordinary in engine.tasks
    assert ordinary.state == ordinary_state
    assert screen.events == []


def test_natural_priority_never_preempts_awaiting_receipt(system):
    engine, screen, model, clock, store = start_observer(system, 'database')
    engine.settings.min_interval = 3
    model.reply = lambda messages: pytest.fail('必须先核对已发送消息回执')
    model.participate = lambda messages, profile='': pytest.fail('必须先核对已发送消息回执')
    engine._record([Message('@小玲 你好', message_id='addressed'), Message('已经发送的问题。', message_id='pending-receipt')])
    pending = engine.tasks[1]
    pending.state, pending.sent_at = 'awaiting_receipt', clock()
    store.mark_sending(pending.id, clock())
    clock.advance(4)
    engine.observe_once()
    assert engine.mode == 'auto' and pending in engine.tasks and pending.state == 'awaiting_receipt'
    assert screen.events == []


def test_natural_full_queue_admits_at_by_replacing_only_unstarted_ordinary_candidate(system):
    engine, screen, model, clock, store = natural_engine(system)
    engine._record([Message(f'普通话题{i}', message_id=f'q{i}') for i in range(20)])
    generated, generating, replaceable = engine.tasks[:3]
    generated.state, generated.reply = 'generated', '已生成。'
    generating.model_attempts = 1
    engine._record([Message('@小玲 请回答', message_id='addressed')])

    assert len(engine.tasks) == 20
    assert engine.tasks[-1].messages[0].message_id == 'addressed'
    assert generated in engine.tasks and generating in engine.tasks
    assert store.get(replaceable.id)['reason'] == 'addressed_priority'
    assert any(m.message_id == 'q2' for m in engine._context_snapshot())


@pytest.mark.parametrize('mode', ['natural', 'all'])
def test_full_queue_never_replaces_generated_replies_to_admit_at(system, mode):
    engine, screen, model, clock, store = natural_engine(system)
    engine.settings.reply_mode = engine.policy.mode = mode
    engine._record([Message(f'普通话题{i}', message_id=f'q{i}') for i in range(20)])
    for task in engine.tasks:
        task.state, task.reply = 'generated', '已经生成。'
    original = list(engine.tasks)
    engine._record([Message('@小玲 请回答', message_id='addressed')])
    assert engine.tasks == original
    assert store.counts().get('skipped') == 1


def test_all_mode_keeps_fifo_even_when_later_message_is_addressed(system):
    engine, screen, model, clock, store = start_observer(system, 'database')
    engine.settings.reply_mode = engine.policy.mode = 'all'
    calls = []
    model.reply = lambda messages: calls.append(messages[-1].message_id) or '逐条回复。'
    engine._record([Message('先来的普通话。', message_id='ordinary'), Message('@小玲 后来的点名', message_id='addressed')])
    engine.observe_once()
    assert calls == ['ordinary']
