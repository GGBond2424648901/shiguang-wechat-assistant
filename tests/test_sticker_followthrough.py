"""Regression cases from a real multi-turn acceptance conversation."""
from dataclasses import replace
import json

import httpx
import pytest

from test_engine import system
from test_sticker_engine import setup_sticker
from test_stickers import library
from xiaomao.domain import Message
from xiaomao.stickers import ReplyPlan, StickerLibrary, turn_sticker_request


REQUEST = '给我发两张适合现在轻松聊天氛围的表情包，风格要不一样。不要重复之前发过的，发完再告诉我你为什么挑这两张。'


@pytest.mark.parametrize('constraint', ['不要重复之前发过的', '别重复发', '不要再发相同的', '不要发之前那张'])
def test_novelty_constraint_does_not_cancel_media_order(constraint):
    request = turn_sticker_request([Message('发两张表情包，' + constraint)])
    assert request.explicit and not request.suppressed and request.count == 2


@pytest.mark.parametrize('ending', ['不要发了', '别再发表情', '算了，不用发了', '不要重复发，也别发表情了'])
def test_actual_cancellation_still_wins(ending):
    request = turn_sticker_request([Message('发两张表情包，' + ending)])
    assert not request.explicit and request.suppressed


def test_explain_after_requires_current_media_order_and_is_not_carried_to_new_order():
    member = 'p_' + 'a' * 16
    first = Message(REQUEST, speaker_id=member, message_id='one', created_at=100)
    request = turn_sticker_request([first])
    assert request.explicit and request.explain_after and request.varied
    second = Message('再来一张', speaker_id=member, message_id='two', created_at=120)
    request = turn_sticker_request([second], history=[first])
    assert request.explicit and not request.explain_after and not request.varied
    assert not turn_sticker_request([replace(first, quoted=True)]).explicit
    assert not turn_sticker_request([Message('为什么挑这两张')]).explicit


def test_model_keeps_requested_explanation_and_selects_different_styles(tmp_path):
    from xiaomao.model import ChatModel, ModelError
    from xiaomao.config import Settings
    library(tmp_path)
    choices = StickerLibrary(tmp_path).items
    answer = {'send': True, 'text': '一张陪你乐，另一张负责吐槽', 'sticker': ['s_0', 's_1']}
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(answer)}, 'finish_reason': 'stop'}]})
    model = ChatModel(Settings(tmp_path, model_base_url='http://localhost'), httpx.Client(transport=httpx.MockTransport(handle)))
    plan = model.reply_plan([Message(REQUEST)], choices=choices)
    assert plan.text == answer['text'] and plan.extra_stickers == ('s_1',)
    assert '全部图片收到发送回执后' in str(calls[-1])
    answer['sticker'] = ['s_0', 's_2']
    # Emotional/visual variety can exist within the same character category.
    assert model.reply_plan([Message(REQUEST)], choices=choices).extra_stickers == ('s_2',)
    answer['sticker'] = ['s_0', 's_1']
    answer['text'] = ''
    with pytest.raises(ModelError):
        model.reply_plan([Message(REQUEST)], choices=choices)
    answer['text'] = '我发了两张'
    assert not model.reply_plan([Message('发两张表情包')], choices=choices).text
    answer['sticker'] = ['s_0']
    with pytest.raises(ModelError):
        model.reply_plan([Message(REQUEST)], choices=choices[:1])


def test_novelty_qualifier_in_same_batch_keeps_explanation_and_variety():
    member = 'p_' + 'a' * 16
    first = Message('发两张不同风格的表情包，发完再告诉我为什么挑这两张', speaker_id=member, message_id='one')
    second = Message('不要重复之前发过的', speaker_id=member, message_id='two')
    request = turn_sticker_request([first, second])
    assert request.explicit and request.explain_after and request.varied and request.avoid_repeats


def test_incomplete_prepared_batch_never_schedules_full_explanation(system):
    engine, _, model, _, _ = system
    task = setup_sticker(system)
    task.messages = [replace(task.messages[0], text=REQUEST)]
    model.reply_plan = lambda *a, **kw: ReplyPlan('两张都选好啦', 's_0')
    engine._reply_and_send(task)
    assert not task.remaining_text and not task.sticker and task.sticker_failed


def test_diverse_candidates_reserve_choices_from_other_enabled_categories(tmp_path):
    entries = library(tmp_path, 30)
    path = tmp_path / 'assets/sticker_library/catalog.json'
    for entry in entries:
        entry['character'] = '菲比'
    entries[-1]['character'] = '鲸鱼娘'
    path.write_text(json.dumps({'items': entries}), encoding='utf-8')
    lib = StickerLibrary(tmp_path)
    request = turn_sticker_request([Message(REQUEST)])
    choices = lib.candidates([Message(REQUEST)], request=request)
    assert len(choices) <= 24 and len({s.character for s in choices}) == 2


def test_explicit_no_repeat_excludes_older_sends_in_this_conversation_only(tmp_path):
    from xiaomao.store import StateStore
    with_store = StateStore(tmp_path / 'runtime')
    try:
        for group, identity in [('one', 's_0'), ('other', 's_1')]:
            task = with_store.create(group, 100)
            with_store.mark_sending(task, 100, group=group, reply='[表情]', sticker_id=identity, sticker_md5='a'*32)
        assert not with_store.recent_stickers('one', 10000)
        assert with_store.recent_stickers('one', 10000, all_history=True) == ['s_0']
        with_store.prune(100 + 8*86400)
        with_store.close()
        with_store = StateStore(tmp_path / 'runtime')
        assert with_store.recent_stickers('one', 100 + 8*86400, all_history=True) == ['s_0']
        assert with_store.recent_stickers('other', 100 + 8*86400, all_history=True) == ['s_1']
    finally:
        with_store.close()


def prepare_order(system, *, ack=True):
    from xiaomao.database import MessageBatch
    engine, screen, model, clock, store = system
    task = setup_sticker(system)
    task.messages = [replace(task.messages[0], text=REQUEST)]
    model.reply_plan = lambda *a, **kw: ReplyPlan('一张陪你乐，另一张负责吐槽', 's_0', ('s_1',))
    sent = []
    staged = [None]
    original_type, original_send = screen.type_sticker, screen.click_send
    def type_sticker(prepared):
        staged[0] = prepared
        original_type(prepared)
    screen.type_sticker = type_sticker
    def send():
        text = screen.frame.draft
        media = staged[0]
        original_send()
        sent.append(Message(text, direction='out', kind='emoji' if media else 'text',
            message_id=f'out{len(sent)+1}', created_at=clock(),
            media_raw=f'<msg><emoji md5="{media.md5}"/></msg>' if media else ''))
        staged[0] = None
    screen.click_send = send
    engine.message_source = type('Feed', (), {'poll': lambda self: MessageBatch(sent, sent if ack else [])})()
    return task, sent


def test_requested_explanation_is_sent_only_after_both_media_receipts(system):
    engine, screen, model, clock, store = system
    task, sent = prepare_order(system)
    engine._reply_and_send(task)
    assert [m.kind for m in sent] == ['emoji']
    assert len(engine.tasks) == 1 and engine.tasks[0].sticker
    engine._reply_and_send(engine.tasks[0])
    assert len(sent) == 1  # unchanged per-message pacing
    clock.advance(engine.settings.min_interval + 1)
    engine._reply_and_send(engine.tasks[0])
    assert [m.kind for m in sent] == ['emoji', 'emoji']
    assert len(engine.tasks) == 1 and not engine.tasks[0].sticker
    clock.advance(engine.settings.min_interval + 1)
    engine._reply_and_send(engine.tasks[0])
    assert [m.kind for m in sent] == ['emoji', 'emoji', 'text']
    assert sent[-1].text == '一张陪你乐，另一张负责吐槽'
    assert store.counts() == {'verified': 3} and not engine.tasks
    assert all(m.reply_to_id == 'in1' for m in engine.context if m.direction == 'out')


@pytest.mark.parametrize('failure', ['unknown', 'pause', 'second_missing'])
def test_unfinished_media_batch_does_not_claim_completion_with_explanation(system, failure):
    engine, screen, model, clock, store = system
    task, sent = prepare_order(system, ack=failure != 'unknown')
    engine._reply_and_send(task)
    if failure == 'unknown':
        assert task.state == 'awaiting_receipt'
        engine.pause()
    elif failure == 'pause':
        engine.pause()
    else:
        second = engine.tasks[0]
        second.sticker.path.unlink()
        clock.advance(engine.settings.min_interval + 1)
        engine._reply_and_send(second)
    assert all(m.kind == 'emoji' for m in sent) and not engine.tasks
