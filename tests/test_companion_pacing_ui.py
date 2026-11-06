from types import SimpleNamespace

import pytest

from xiaomao.companion import CompanionPreferences
from xiaomao.companion_ui import CompanionPage
from xiaomao.config import Settings
from test_companion import at


@pytest.fixture
def page(native_root, tmp_path):
    cfg = Settings(tmp_path, group='甲群', group_names=('甲群','乙群'),
        conversation_targets={name:dict(account='a', id=name+'@chatroom', kind='group')
                              for name in ('甲群','乙群')})
    cfg.save()
    status = {'group':cfg.group, 'group_names':list(cfg.allowed_groups), 'groups':[
        {'name':name, 'mode':'auto', 'pending_count':0,
         'companion':{'reason':'等待时机', 'next_candidate_at':at(10,20), 'next_stage':'空闲话题'}}
        for name in cfg.allowed_groups]}
    calls=[]
    client=SimpleNamespace(companion_configure=lambda *args:calls.append(args) or status)
    panel=SimpleNamespace(settings=cfg,directory=tmp_path,client=client,view=SimpleNamespace(status=status),
        work=lambda fn,ok,*args,**kw:ok(fn()),show_status=lambda result:None)
    widget=CompanionPage(native_root,panel)
    widget.on_show()
    yield widget,calls
    widget.on_hide();widget.destroy()


def test_frequency_fields_roundtrip_and_only_selected_scope_is_saved(page):
    widget,calls=page
    widget.scope_choice.set('乙群');widget._choose_scope()
    widget.enabled.set(True)
    widget.frequency_vars['idle_min_minutes'].set('15')
    widget.frequency_vars['idle_max_minutes'].set('30')
    widget.probability_vars['idle_probability'].set('65')
    widget.probability_vars['followup_probability'].set('42.5')
    widget.limit.set('24');widget.save()
    group,scope,values=calls[0]
    assert group=='乙群' and scope=='a/乙群@chatroom'
    assert values['idle_min_minutes']==15 and values['idle_max_minutes']==30
    assert values['idle_probability']==.65 and values['followup_probability']==.425
    assert values['daily_max']==24 and widget.panel.settings.group=='甲群'
    widget._apply_prefs(CompanionPreferences(**values))
    assert widget.values()==CompanionPreferences(**values)


def test_invalid_frequency_keeps_form_and_prevents_any_configuration_call(page):
    widget,calls=page
    widget.frequency_vars['followup_min_minutes'].set('9')
    widget.frequency_vars['followup_max_minutes'].set('2')
    widget.save()
    assert not calls and '最短不能大于最长' in widget.feedback.get()
    assert widget.frequency_vars['followup_min_minutes'].get()=='9'


def test_status_shows_next_attempt_without_promising_a_send(page):
    widget,_=page
    widget.baseline_state=None
    widget._status_tick()
    assert '下次可尝试 10:20' in widget.live.get()
    assert '可能保持安静' in widget.live.get()
