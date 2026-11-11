from xiaomao.domain import ReplyTask
from test_engine import system
from dataclasses import replace
import pytest


def test_news_guard_is_checked_before_any_desktop_input(system):
    engine, screen, _, clock, store = system
    engine.enable()
    task = ReplyTask(store.create('news-guard', clock()), clock(), clock(), '', reply='资讯摘要', state='generated', operator_plan=True)
    task.send_guard = lambda: False
    engine.tasks.append(task)
    assert engine._active(task) is False
    assert task not in engine.tasks
    assert store.get(task.id)['state'] == 'skipped'
    assert not screen.events


def test_news_cancel_after_staging_keeps_the_draft_and_does_not_click_send(system):
    engine, screen, _, clock, store = system
    engine.enable()
    task = ReplyTask(store.create('news-stage-guard', clock()), clock(), clock(), '', reply='资讯摘要', state='staged', operator_plan=True)
    task.send_guard = lambda: False
    engine.tasks.append(task)
    assert engine._active(task) is False
    assert engine.mode == 'paused' and not screen.events
    assert '草稿' in engine.reason


def test_news_guard_does_not_interrupt_receipt_confirmation_after_send(system):
    engine, _, _, clock, store = system
    engine.enable()
    task = ReplyTask(store.create('news-sent-guard', clock()), clock(), clock(), '', reply='资讯摘要', state='sending', operator_plan=True)
    task.send_guard = lambda: False
    engine.tasks.append(task)
    assert engine._active(task) is True


@pytest.mark.parametrize('mode', ['all', 'natural', 'mentions'])
def test_ordinary_chat_takes_priority_over_an_unstaged_news_part(system, mode):
    engine, _, _, clock, store = system
    engine.settings = replace(engine.settings, reply_mode=mode)
    engine.enable()
    news = ReplyTask(store.create('news-priority', clock()), clock(), clock(), '', state='generated', operator_plan=True)
    news.send_guard = lambda: True
    ordinary = ReplyTask(store.create('ordinary-priority', clock()), clock(), clock(), '')
    engine.tasks.extend([news, ordinary])
    assert engine._next_task() is ordinary
    news.state = 'staged'
    assert engine._next_task() is news


def test_news_deadline_can_wait_for_idle_inside_its_bounded_publication_window(system):
    engine, _, _, clock, store = system
    engine.enable()
    task = ReplyTask(store.create('news-idle', clock()), clock(), clock(), '', state='generated', operator_plan=True)
    task.send_guard = lambda: True; task.deadline_at = clock() + 7200
    engine.tasks.append(task)
    clock.advance(300)
    assert engine._active(task) is True
    assert engine._deadline(task) == task.created + 7200
