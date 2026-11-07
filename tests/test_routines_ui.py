from copy import deepcopy

import pytest

from test_companion_pacing_ui import page
from test_routines import at, morning, preferences


def test_routines_tab_saves_editable_fields_only_for_selected_conversation(page):
    widget, calls = page
    assert 'routines' in widget.tabs
    widget.select('routines')
    widget.scope_choice.set('乙群'); widget._choose_scope()
    editor = widget.routines_editor
    editor.add_preset('morning')
    assert not calls
    editor.time.set('09:15')
    editor.intent.set('早安，记得吃早餐，聊聊今天的安排')
    editor.enabled.set(True)
    editor.prefer_sticker.set(True)
    for index, variable in enumerate(editor.weekdays):
        variable.set(index < 5)
    widget.save_routines()
    group, scope, values = calls[-1]
    assert group == '乙群' and scope == 'a/乙群@chatroom'
    routine = values['routines'][0]
    assert routine['time'] == '09:15' and routine['weekdays'] == [0, 1, 2, 3, 4]
    assert routine['enabled'] and routine['prefer_sticker'] and '早餐' in routine['intent']
    assert widget.panel.settings.group == '甲群'
    assert '已保存' in widget.feedback.get()


def test_presets_remain_disabled_and_never_turn_on_companionship_implicitly(page):
    widget, calls = page
    editor = widget.routines_editor
    for name in ('morning', 'noon', 'afternoon', 'evening'):
        editor.add_preset(name)
        editor.apply_edit()
    widget.save_routines()
    values = calls[-1][2]
    assert not values['enabled']
    assert len(values['routines']) == 4
    assert all(not row['enabled'] for row in values['routines'])
    assert [row['time'] for row in values['routines']] == ['09:00', '12:00', '15:00', '20:00']


def test_cancel_restores_original_fields_and_discards_a_new_draft(page):
    widget, calls = page
    row = morning()
    widget._apply_prefs(preferences(routines=[row]))
    editor = widget.routines_editor
    editor.edit(row['id'])
    editor.time.set('06:00'); editor.intent.set('临时编辑')
    editor.cancel_edit()
    assert editor.time.get() == '09:00' and editor.values() == [row]
    editor.new()
    editor.intent.set('不保存这条')
    editor.cancel_edit()
    widget.save_routines()
    assert calls[-1][2]['routines'] == [row]


def test_unrelated_policy_save_preserves_schedules_and_schedule_save_preserves_policy(page):
    widget, calls = page
    row = morning()
    original = preferences(routines=[row], style='自然温柔', interests='散步')
    widget._apply_prefs(original)
    widget.interests.set('咖啡')
    widget.save()
    assert calls[-1][2]['routines'] == [row]
    editor = widget.routines_editor
    editor.edit(row['id']); editor.time.set('09:30')
    widget.save_routines()
    payload = calls[-1][2]
    assert payload['interests'] == '咖啡' and payload['style'] == '自然温柔'
    assert payload['routines'][0]['time'] == '09:30'


def test_invalid_time_keeps_draft_and_prevents_configuration(page):
    widget, calls = page
    widget.routines_editor.add_preset('morning')
    widget.routines_editor.time.set('9点')
    widget.save_routines()
    assert not calls and 'HH:MM' in widget.feedback.get()
    assert widget.routines_editor.time.get() == '9点'


def test_scope_switch_discards_other_conversations_unsaved_routine_draft(page):
    widget, calls = page
    widget.routines_editor.add_preset('morning')
    widget.routines_editor.enabled.set(True)
    widget.scope_choice.set('乙群'); widget._choose_scope()
    assert widget.routines_editor.values() == []
    widget.save_routines()
    assert calls[-1][0:2] == ('乙群', 'a/乙群@chatroom')
    assert calls[-1][2]['routines'] == []


def test_queued_save_cannot_write_after_scope_switch(page):
    widget, calls = page
    jobs = []
    widget.panel.work = lambda fn, ok, fail, **kw: jobs.append((fn, ok, fail))
    widget.routines_editor.add_preset('morning')
    widget.save_routines()
    old_work, old_done, _ = jobs[0]
    widget.scope_choice.set('乙群'); widget._choose_scope()
    with pytest.raises(ValueError, match='切换'):
        old_work()
    old_done({'groups': []})
    assert not calls and widget.cfg.group == '乙群'
    assert widget.routines_editor.values() == []


def test_schedule_layout_stacks_editor_on_narrow_width_and_shows_receipt_state(page):
    widget, _ = page
    editor = widget.routines_editor
    editor.arrange(1000)
    assert int(editor.right.grid_info()['column']) == 1
    editor.arrange(550)
    assert int(editor.right.grid_info()['row']) == 1
    row = morning()
    editor.set_values([row])
    editor.set_status([dict(id=row['id'], enabled=True, next_due_at=at(9, day=8), status='unknown')],
                      [dict(routine_id=row['id'], day='2026-11-07', due_at=at(9), status='unknown', definition=deepcopy(row))])
    assert '09:00' in editor.next_time.get()
    values = editor.history_view.item(editor.history_view.get_children()[0], 'values')
    assert any('未知' in str(value) for value in values)


def test_editing_a_new_preset_does_not_discard_it(page):
    widget, _ = page
    editor = widget.routines_editor
    editor.add_preset('morning')
    identity = editor.values()[0]['id']
    editor.edit(identity)
    assert len(editor.values()) == 1


def test_dynamic_schedule_rows_bind_wheel_once(page):
    widget, _ = page
    editor = widget.routines_editor
    editor.add_preset('morning')
    editor.apply_edit(); editor.cancel_edit()
    assert editor.rows_frame.bindtags().count(widget.wheel_tag) == 1
