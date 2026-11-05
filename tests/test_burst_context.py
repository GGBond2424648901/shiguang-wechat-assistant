"""Dense inbound traffic must preserve causal context without changing sends."""
from dataclasses import replace
import pytest

from xiaomao.domain import Message
from test_engine import system


ALICE = 'p_' + 'a' * 16
BOB = 'p_' + 'b' * 16


def prepare_burst(system):
    engine, screen, model, clock, store = system
    engine.settings.reply_mode = engine.policy.mode = 'all'
    engine.message_source = object()
    engine.mode = 'auto'
    # Exercise the actual model context boundary without performing input.
    screen.idle_for = lambda: 0
    captured = []
    model.reply = lambda messages: captured.append(list(messages)) or '收到。'
    return engine, clock, captured


def test_same_second_receipt_is_present_for_next_queued_member(system):
    engine, clock, captured = prepare_burst(system)
    clock.advance(.75)
    engine._record([
        Message('我在成都，周末去哪里？', message_id='a1', speaker_id=ALICE, created_at=100),
        Message('我在北京，周末去哪里？', message_id='b1', speaker_id=BOB, created_at=100),
    ])
    engine._remember(Message('成都可以去公园。', direction='out', message_id='answer-a1',
                             created_at=100, speaker_id='self', reply_to_id='a1', reply_to_speaker=ALICE))

    engine._reply_and_send(engine.tasks[1])

    assert [m.message_id for m in captured[0]] == ['a1', 'answer-a1', 'b1']
    assert captured[0][-2].reply_to_speaker == ALICE
    assert captured[0][-1].speaker_id == BOB


def test_dense_later_arrivals_do_not_evict_verified_reply_from_queued_context(system):
    engine, clock, captured = prepare_burst(system)
    engine._record([
        Message('我的包裹周五到。', message_id='a1', speaker_id=ALICE),
        Message('那什么时候取？', message_id='a2', speaker_id=ALICE),
    ])
    engine._remember(Message('周五可以取件。', direction='out', message_id='answer-a1',
                             created_at=101, speaker_id='self', reply_to_id='a1', reply_to_speaker=ALICE))
    engine._record([Message(f'后来的其他话题{i}', message_id=f'later-{i}', speaker_id=BOB)
                    for i in range(30)])
    assert not any(m.message_id == 'answer-a1' for m in engine.context)

    engine._reply_and_send(engine.tasks[1])

    assert [m.message_id for m in captured[0]] == ['a1', 'answer-a1', 'a2']


def test_future_reply_without_source_in_frozen_snapshot_is_excluded(system):
    engine, clock, captured = prepare_burst(system)
    engine._record([Message('我想聊成都。', message_id='a1', speaker_id=ALICE)])
    engine._record([Message('我想聊北京。', message_id='b1', speaker_id=BOB)])
    engine._remember(Message('北京的话题。', direction='out', message_id='answer-b1',
                             created_at=101, speaker_id='self', reply_to_id='b1', reply_to_speaker=BOB))
    engine._remember(Message('后来手动发送的无关消息。', direction='out', message_id='manual', created_at=101))

    engine._reply_and_send(engine.tasks[0])

    assert [m.message_id for m in captured[0]] == ['a1']


def test_same_text_from_two_members_remains_distinct_and_replies_keep_their_source(system):
    engine, clock, captured = prepare_burst(system)
    engine._record([
        Message('明天呢？', message_id='a1', speaker_id=ALICE),
        Message('明天呢？', message_id='b1', speaker_id=BOB),
        Message('那后天呢？', message_id='a2', speaker_id=ALICE, reply_to_id='a1', reply_to_speaker=ALICE),
    ])
    engine._remember(Message('甲的明天。', direction='out', message_id='answer-a1',
                             created_at=101, reply_to_id='a1', reply_to_speaker=ALICE))
    engine._remember(Message('乙的明天。', direction='out', message_id='answer-b1',
                             created_at=101, reply_to_id='b1', reply_to_speaker=BOB))

    engine._reply_and_send(engine.tasks[2])

    assert [m.message_id for m in captured[0]] == ['a1', 'b1', 'answer-a1', 'answer-b1', 'a2']
    assert [m.reply_to_speaker for m in captured[0][2:4]] == [ALICE, BOB]
    assert captured[0][-1].reply_to_id == 'a1'


def test_queued_receipt_metadata_is_enriched_after_it_leaves_recent_window(system):
    engine, clock, captured = prepare_burst(system)
    outgoing = Message('甲的答复。', direction='out', message_id='answer-a1', created_at=100)
    engine._record([Message('甲的问题。', message_id='a1', speaker_id=ALICE), outgoing,
                    Message('乙的追问。', message_id='b1', speaker_id=BOB)])
    engine._remember(replace(outgoing, reply_to_id='a1', reply_to_speaker=ALICE))
    engine._record([Message(f'后来消息{i}', message_id=f'later-{i}', speaker_id=BOB)
                    for i in range(30)])

    engine._reply_and_send(engine.tasks[1])

    answers = [m for m in captured[0] if m.message_id == 'answer-a1']
    assert len(answers) == 1
    assert answers[0].reply_to_id == 'a1' and answers[0].reply_to_speaker == ALICE


def test_existing_mention_merge_keeps_each_source_message_once(system):
    engine, clock, captured = prepare_burst(system)
    engine.settings.reply_mode = engine.policy.mode = 'mentions'
    engine._record([
        Message('小猫，先看第一条。', message_id='a1', speaker_id=ALICE),
        Message('小猫，再看第二条。', message_id='a2', speaker_id=ALICE),
    ])
    assert len(engine.tasks) == 1

    engine._reply_and_send(engine.tasks[0])

    assert [m.message_id for m in captured[0]] == ['a1', 'a2']


@pytest.mark.parametrize('transient_faults',[False,True])
def test_eighteen_message_burst_sends_every_source_once_in_order_at_existing_rate(system, monkeypatch,transient_faults):
    import random
    from xiaomao.database import MessageBatch

    engine, screen, model, clock, store = system
    engine.settings.reply_mode = engine.policy.mode = 'all'
    engine.settings.natural_pacing = True
    engine.settings.min_interval = engine.settings.pacing_jitter = 4
    engine.settings.minute_limit = 10
    engine.settings.hourly_limit = 180
    monkeypatch.setattr(random, 'uniform', lambda low, high: (low + high) / 2)

    class Feed:
        def __init__(self):
            self.initial = True
            self.pending = []
            self.history = []
            self.busy = 0

        def poll(self):
            if self.busy:
                self.busy-=1
                raise OSError('WAL is being committed')
            initial, self.initial = self.initial, False
            new, self.pending = self.pending, []
            self.history.extend(new)
            return MessageBatch(self.history[-24:], new, initial)

    feed = engine.message_source = Feed()
    requests, sent_at = [], []

    def reply(messages):
        requests.append(list(messages))
        return '答复' + messages[-1].message_id

    def sent():
        screen.events.append('send')
        sent_at.append(clock())
        feed.pending.append(Message(screen.frame.draft, direction='out', message_id=f'answer-{len(sent_at)-1}',
                                    created_at=int(clock()), speaker_id='self'))
        screen.frame = replace(screen.frame, draft='', draft_empty=True)
        if transient_faults:feed.busy=2

    model.reply = reply
    screen.click_send = sent
    if transient_faults:
        from xiaomao.windows import DesktopInputNotStarted
        original_type=screen.type_text
        failures=set()
        def type_with_contention(text):
            if int(text.removeprefix('答复q'))%3==0 and text not in failures:
                failures.add(text)
                raise DesktopInputNotStarted('剪贴板暂被占用，尚未输入')
            original_type(text)
        screen.type_text=type_with_contention
    engine.enable()
    engine.observe_once()
    speakers=['p_'+f'{i+1:016x}' for i in range(6)] if transient_faults else [ALICE,BOB]
    burst = [Message(f'合成问题{i}', message_id=f'q{i}', created_at=101,
                     speaker_id=speakers[i%len(speakers)]) for i in range(18)]
    # The faulty case includes arrivals DURING queued sends, not only one
    # initial batch. Keep each arrival inside the unchanged 120s lifetime.
    initial_burst=burst[:6] if transient_faults else burst
    feed.pending = list(initial_burst)
    clock.advance(1)
    engine.observe_once()
    # Repeated database rows must not produce duplicate tasks or sends.
    feed.pending = list(initial_burst)
    for step in range(150 if transient_faults else 115):
        clock.advance(1)
        if transient_faults and step in (24,48):
            start=6 if step==24 else 12
            feed.pending.extend(replace(m,created_at=int(clock())) for m in burst[start:start+6])
        engine.observe_once()
        if not engine.tasks and (not transient_faults or step>=48):
            break

    assert store.counts() == {'verified': 18}
    assert screen.events.count('send') == len(requests) == 18
    assert [request[-1].message_id for request in requests] == [f'q{i}' for i in range(18)]
    assert engine.mode=='auto' and not engine.cancelled.is_set()
    assert all((later-earlier>=6 if transient_faults else later-earlier==6)
               for earlier,later in zip(sent_at,sent_at[1:]))
    for index, request in enumerate(requests):
        assert len({m.message_id for m in request}) == len(request)
        assert [m.message_id for m in request if m.direction == 'in'] == [f'q{i}' for i in range(index + 1)]
        replies = [m for m in request if m.direction == 'out']
        assert [m.reply_to_id for m in replies] == [f'q{i}' for i in range(index)]
        assert [m.reply_to_speaker for m in replies] == [speakers[i%len(speakers)] for i in range(index)]
