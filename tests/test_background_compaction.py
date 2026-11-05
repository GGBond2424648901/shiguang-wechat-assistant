from dataclasses import replace
import threading

from test_engine import system
from xiaomao.database import MessageBatch
from xiaomao.domain import Message


def compacting_engine(system):
    engine, screen, model, clock, store = system
    class Feed:
        pending = []
        def poll(self):
            messages, self.pending = self.pending, []
            return MessageBatch([], messages)
    engine.message_source = Feed()
    engine.mode = 'auto'
    history = [Message(f'合成旧事实{i}', message_id=f'old-{i}') for i in range(97)]
    engine._seed_context(history)
    return engine, screen, model, clock, history


def finish_background(engine):
    thread = getattr(engine, '_compaction_thread', None)
    if thread:
        thread.join(2)
        assert not thread.is_alive()


def test_slow_compaction_does_not_block_receipt_and_keeps_newer_messages(system):
    engine, screen, model, clock, history = compacting_engine(system)
    entered, release, observed = threading.Event(), threading.Event(), threading.Event()
    def compact(previous, older):
        entered.set()
        assert release.wait(3)
        return '合成的历史摘要。'
    model.compact = compact
    observer = threading.Thread(target=lambda: (engine.observe_once(), observed.set()), daemon=True)
    observer.start()
    try:
        assert entered.wait(1)
        assert observed.wait(.3), '接收循环不能等待压缩网络请求'
        clock.advance(1)
        engine.message_source.pending = [Message('压缩期间新到的事实', message_id='new', created_at=101)]
        engine.observe_once()
        assert engine.last_observed == 101
        assert engine.memory.messages[-1].message_id == 'new'
    finally:
        release.set()
        observer.join(2)
        finish_background(engine)
    engine.observe_once()
    assert engine.memory.summary == '合成的历史摘要。'
    assert [m.message_id for m in engine.memory.messages] == [f'old-{i}' for i in range(73, 97)] + ['new']
    assert engine.context[-1].message_id == 'new'


def test_pause_resume_discards_old_compaction_result(system):
    engine, screen, model, clock, history = compacting_engine(system)
    entered, release = threading.Event(), threading.Event()
    def compact(previous, older):
        entered.set()
        assert release.wait(3)
        return '暂停前启动的旧摘要。'
    model.compact = compact
    observer = threading.Thread(target=engine.observe_once, daemon=True)
    observer.start()
    try:
        assert entered.wait(1)
        engine.pause()
        engine.enable()
    finally:
        release.set()
        observer.join(2)
        finish_background(engine)
    engine.observe_once()
    assert engine.memory.summary == ''
    assert engine.memory.messages == history
    assert screen.events == []


def test_stop_does_not_join_network_or_allow_late_write_over_restarted_memory(system):
    from xiaomao.memory import ConversationMemory
    engine, screen, model, clock, history = compacting_engine(system)
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    def compact(previous, older):
        entered.set()
        assert release.wait(3)
        return '不能写回停止后的文件。'
    model.compact = compact
    observer = threading.Thread(target=engine.observe_once, daemon=True)
    observer.start()
    try:
        assert entered.wait(1)
        stopper = threading.Thread(target=lambda: (engine.stop(), stopped.set()), daemon=True)
        stopper.start()
        assert stopped.wait(.3)
        restarted = ConversationMemory(engine.settings.runtime, engine.settings.memory_scope, clock)
        restarted.remember(Message('新实例写入的事实', message_id='restarted'))
    finally:
        release.set()
        observer.join(2)
        finish_background(engine)
    loaded = ConversationMemory(engine.settings.runtime, engine.settings.memory_scope, clock)
    assert loaded.summary == ''
    assert loaded.messages[-1].message_id == 'restarted'


def test_failed_background_compaction_backs_off_from_completion(system):
    engine, screen, model, clock, history = compacting_engine(system)
    calls = []
    def compact(previous, older):
        calls.append(clock())
        raise RuntimeError('synthetic failure')
    model.compact = compact
    engine.observe_once()
    finish_background(engine)
    clock.advance(40)
    engine.observe_once()  # consume completion, start the retry deadline here
    clock.advance(31)
    engine.observe_once()
    assert calls == [100]
    assert engine.memory.messages == history
    clock.advance(30)
    engine.observe_once()
    finish_background(engine)
    assert len(calls) == 2


def test_queued_requests_bound_frozen_context_without_repeated_compaction_calls(system):
    engine, screen, model, clock, store = system
    engine.settings.reply_mode = engine.policy.mode = 'all'
    engine.mode = 'auto'
    screen.idle_for = lambda: 0
    engine._seed_context([Message(f'历史话题{i}', message_id=f'old-{i}') for i in range(220)])
    engine._record([Message(f'排队话题{i}', message_id=f'q{i}') for i in range(18)])
    calls, requests = [], []
    model.compact = lambda previous, older: calls.append(1) or '摘要'
    model.reply = lambda messages: requests.append(list(messages)) or '回答。'
    for task in list(engine.tasks):
        engine._reply_and_send(task)
    assert calls == []
    assert len(requests) == 18 and all(len(messages) <= 96 for messages in requests)
    assert [messages[-1].message_id for messages in requests] == [f'q{i}' for i in range(18)]
    assert all(not any(m.message_id == f'q{i+1}' for m in messages) for i, messages in enumerate(requests[:-1]))
    assert len(engine.memory.messages) == 238


def test_bounded_context_keeps_current_member_and_explicit_reply_dependencies(system):
    engine, screen, model, clock, store = system
    alice, bob = 'p_'+'a'*16, 'p_'+'b'*16
    engine.settings.reply_mode = engine.policy.mode = 'all'
    engine.mode = 'auto'
    screen.idle_for = lambda: 0
    source = Message('我周五去取件。', message_id='source', speaker_id=alice)
    answer = Message('可以周五下午去。', direction='out', message_id='answer',
                     speaker_id='self', reply_to_id='source', reply_to_speaker=alice)
    engine._seed_context([source, answer] + [Message(f'乙的话题{i}', message_id=f'b{i}', speaker_id=bob) for i in range(150)])
    engine._record([Message('那上午呢？', message_id='target', speaker_id=alice, reply_to_id='source')])
    requests = []
    model.compact = lambda *args: '不能等待这次压缩。'
    model.reply = lambda messages: requests.append(list(messages)) or '上午也可以。'
    engine._reply_and_send(engine.tasks[0])
    ids = [m.message_id for m in requests[0]]
    assert len(ids) <= 96 and ids[-1] == 'target'
    assert 'source' in ids and 'answer' in ids
    assert next(m for m in requests[0] if m.message_id == 'answer').reply_to_speaker == alice


def test_background_compaction_pins_later_verified_reply_for_queued_target(system):
    engine, screen, model, clock, history = compacting_engine(system)
    engine.settings.reply_mode = engine.policy.mode = 'all'
    alice = 'p_'+'a'*16
    engine._record([Message('小猫问题一', message_id='q1', speaker_id=alice),
                    Message('小猫问题二', message_id='q2', speaker_id=alice)])
    task = engine.tasks[1]
    engine._remember(Message('第一题的已确认回复', direction='out', message_id='answer-q1',
                             reply_to_id='q1', reply_to_speaker=alice))
    engine._seed_context([Message(f'后来的其他话题{i}', message_id=f'later-{i}') for i in range(30)])
    screen.idle_for = lambda: 0
    requests = []
    model.reply = lambda messages: requests.append(list(messages)) or '回答。'
    model.compact = lambda *args: '较早历史已整理。'
    engine.observe_once()
    finish_background(engine)
    engine.observe_once()
    assert not any(m.message_id == 'answer-q1' for m in engine.memory.messages)
    engine._reply_and_send(task)
    assert 'answer-q1' in [m.message_id for m in requests[-1]]
    assert requests[-1][-1].message_id == 'q2'
