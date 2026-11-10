"""The conversation editor stages changes; opening the picker never starts a service."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

tk = pytest.importorskip('tkinter')
from xiaomao.config import Settings
from xiaomao.contacts import Contact
from xiaomao.sessions_ui import DirectoryPage, RATES, SessionsPage

GROUP = Contact('fixture@chatroom', 'a', '摄影交流', 'group', '摄影交流')
PERSON = Contact('fixture-user', 'a', '小夏', 'private', '夏', 'summer')
THIRD = Contact('fixture-new', 'a', '新同事', 'private')
SYSTEM = Contact('filehelper', 'a', '文件传输助手', 'service', selectable=False, reason='系统服务')
TWIN = Contact('fixture-twin', 'a', '视觉同名', 'private', selectable=False, reason='存在同名会话')


@pytest.fixture(scope='module')
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f'Tk desktop unavailable: {error}')
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def panel(tmp_path, tk_root):
    root = tk.Frame(tk_root)
    cfg = Settings(tmp_path, group=GROUP.name, group_names=(GROUP.name, PERSON.name),
                   conversation_targets={c.name: c.binding for c in (GROUP, PERSON)})
    cfg.save()
    p = SimpleNamespace(root=root, settings=cfg, directory=tmp_path, view=SimpleNamespace(pages={}),
                        chosen=[], applied=[], jobs=[], controls=[], selected=[])
    p.view.select = p.selected.append
    p.choose_contact = lambda: p.controls.append('open_directory')
    p.select_contact = p.chosen.append
    p.use_current_group = lambda: p.controls.append('use_current')
    p.work = lambda action, done, failed, **kwargs: p.jobs.append((action, done, failed, kwargs))
    p.apply_group_settings = lambda *args, **kwargs: p.applied.append((args, kwargs))
    yield p
    root.destroy()


def test_editor_rebases_unrelated_edits_and_uses_identity_for_picks(panel):
    page = SessionsPage(panel.root, panel)
    custom = (19, 30, 3, 4)
    panel.settings = replace(panel.settings, min_interval=19, hourly_limit=30, minute_limit=3, pacing_jitter=4)
    page.on_show()
    assert page.rates[page.rate.get()] == custom
    page.mode.set('mentions')
    page.add_contact(THIRD)
    assert not panel.applied and not panel.chosen
    panel.settings = panel.settings.select_conversation(PERSON)
    page.refresh_scope()
    assert page.names == [PERSON.name, GROUP.name, THIRD.name]
    assert page.mode.get() == 'mentions' and not page.monitor_all.get()
    page.promote(THIRD.name)
    page.remove(GROUP.name)
    page.on_show()
    assert page.names == [THIRD.name, PERSON.name]
    panel.switch_busy = True
    page.save()
    assert not page.busy and not panel.applied
    panel.switch_busy = False
    page.save()
    args, kw = panel.applied.pop()
    assert args[:3] == ([THIRD.name, PERSON.name], 'mentions', custom)
    assert kw['contacts'] == [THIRD] and not kw['monitor_all']
    args[4]('测试失败')
    assert not page.busy and page.names == [THIRD.name, PERSON.name]


def test_editor_refuses_system_conflicting_bindings_and_cross_account_staging(panel):
    page = SessionsPage(panel.root, panel)
    original = list(page.names)
    for item in (SYSTEM, TWIN, replace(PERSON, id='wrong'), replace(THIRD, account='other')):
        page.add_contact(item)
    assert page.names == original and not page.picked
    page.remove(PERSON.name)
    page.remove(GROUP.name)
    assert page.names == [GROUP.name]
    page.add_contact(THIRD)
    panel.settings = replace(panel.settings, conversation_targets={
        GROUP.name: {**GROUP.binding, 'account': 'other'}, PERSON.name: {**PERSON.binding, 'account': 'other'}})
    page.refresh_scope()
    assert THIRD.name not in page.names and not page.picked
    assert (5, 60, 6, 7) in RATES.values() and (4, 180, 10, 4) in RATES.values()


def test_page_save_includes_current_conversation_rule_draft(panel):
    page=SessionsPage(panel.root,panel);page.on_show()
    page._pick_row(PERSON.name)
    editor=page.rule_editor
    editor.inherit_mode.set(False);editor.mode.set('mentions')
    editor.inherit_stickers.set(False);editor.frequency.set('仅按需')
    page.save()
    args,kw=panel.applied.pop()
    assert kw['rule_edit']==(PERSON.name,'a/fixture-user',
                           {'reply_mode':'mentions','sticker_frequency':'requested'})
    args[4]('写入失败')
    assert editor.dirty() and editor.mode.get()=='mentions'


@pytest.mark.parametrize('success',[True,False])
def test_general_save_locks_rule_editor_until_completion(panel,success):
    page=SessionsPage(panel.root,panel);page.on_show()
    editor=page.rule_editor
    editor.inherit_mode.set(False);editor.mode.set('mentions');editor._states()
    page.save()
    assert editor.busy and editor.apply.cget('state')=='disabled'
    assert all(str(r.cget('state'))=='disabled' for r in editor.radios)
    editor.save();assert not panel.jobs
    args,kw=panel.applied.pop()
    if success:
        name,_,preferences=kw['rule_edit']
        panel.settings=panel.settings.with_conversation_rules(name,preferences)
        panel.settings.save();args[3]()
        assert not editor.dirty()
    else:
        args[4]('保存失败');assert editor.dirty()
    assert not editor.busy and editor.apply.cget('state')=='normal'


@pytest.mark.parametrize('field',['mode','listeners','order','monitor','pacing','stickers'])
def test_all_session_drafts_block_leaving_and_can_be_discarded(panel,field):
    page=SessionsPage(panel.root,panel);page.on_show()
    if field=='mode':page.mode.set('mentions')
    elif field=='listeners':page.remove(PERSON.name)
    elif field=='order':page.promote(PERSON.name)
    elif field=='monitor':page.monitor_all.set(False)
    elif field=='pacing':page.rate_values[0].set('17')
    else:page.stickers.set(False)
    assert not page.can_leave()
    page.discard()
    assert page.can_leave()
    assert page.names==list(panel.settings.saved_groups)


def test_directory_filters_and_staging_have_no_service_side_effects(panel, monkeypatch):
    rows = [GROUP, PERSON, SYSTEM, TWIN]
    monkeypatch.setattr('xiaomao.sessions_ui.load_directory', lambda directory, cancelled: rows)
    sessions = SessionsPage(panel.root, panel)
    panel.view.pages['sessions'] = sessions
    picker = DirectoryPage(panel.root, panel)
    picker.on_show()
    action, done, _, options = panel.jobs.pop()
    assert not options['mutates']
    done(action())
    assert picker.rows == rows and not panel.chosen and not panel.applied
    picker.kind.set('个人')
    picker.query.set('summer')
    assert picker.visible == [PERSON]
    picker.list.selection_set('0')
    picker.stage()
    assert panel.selected == ['sessions'] and sessions.picked[PERSON.name] == PERSON
    assert not panel.applied and not panel.chosen
    picker.select()
    assert panel.chosen == [PERSON]
    picker.use_current()
    assert panel.controls == ['use_current']
    picker.query.set('')
    picker.kind.set('系统')
    picker.list.selection_set('0')
    picker.stage()
    picker.select()
    assert panel.chosen == [PERSON] and panel.selected == ['sessions']
    picker.kind.set('个人')
    picker.query.set(TWIN.name)
    picker.list.selection_set('0')
    picker.select()
    assert panel.chosen == [PERSON]


def test_directory_rejects_visual_name_collision_even_across_filters(panel, monkeypatch):
    twin = replace(PERSON, id='twin', name='小夏🐟')
    monkeypatch.setattr('xiaomao.sessions_ui.load_directory', lambda directory, cancelled: [PERSON, twin])
    picker = DirectoryPage(panel.root, panel)
    picker.on_show()
    read, done, _, _ = panel.jobs.pop()
    done(read())
    picker.query.set('summer')
    picker.list.selection_set('0')
    picker._selection()
    assert not picker.list.selection()
    picker.select()
    assert not panel.chosen


def test_directory_drops_callbacks_after_hide_refresh_and_scope_change(panel):
    picker = DirectoryPage(panel.root, panel)
    picker.on_show()
    _, old, _, _ = panel.jobs.pop()
    picker.on_hide()
    old([PERSON])
    assert not picker.rows
    picker.on_show()
    _, another, _, _ = panel.jobs.pop()
    picker.refresh()
    _, fresh, _, _ = panel.jobs.pop()
    another([PERSON])
    assert not picker.rows
    panel.settings = panel.settings.select_conversation(PERSON)
    fresh([GROUP])
    assert not picker.rows and len(panel.jobs) == 1


def test_custom_rate_edits_parse_validate_rebase_and_recover_from_presets(panel):
    from xiaomao.sessions_ui import CUSTOM_RATE, short_rate
    page=SessionsPage(panel.root,panel)
    for var,value in zip(page.rate_values,('４．５','1.25','10','500')):
        var.set(value)
    assert page.rate.get()==CUSTOM_RATE and '5.75' in page.rate_note.cget('text')
    page.save()
    args,kw=panel.applied.pop()
    assert args[2]==(4.5,500,10,1.25) and kw['keep_pacing'] is False
    args[4]('测试结束')
    page.rate_values[3].set('')
    panel.settings=panel.settings.select_conversation(PERSON)
    page.refresh_scope()
    assert page.rate_values[3].get()==''
    page.save()
    assert not panel.applied and '每小时上限' in page.note.get()
    page.rate.set(short_rate(next(iter(RATES))))
    assert [v.get() for v in page.rate_values]==['4','4','10','180']
    page.rate.set(CUSTOM_RATE)
    assert page.rate_values[3].get()=='' and page.rate_values[0].get()=='４．５'


def test_unedited_low_frequency_legacy_rate_stays_unchanged_on_unrelated_save(panel):
    panel.settings=replace(panel.settings,min_interval=310.125,pacing_jitter=0,minute_limit=1,
                           hourly_limit=2,natural_pacing=False)
    page=SessionsPage(panel.root,panel)
    assert '固定间隔' in page.rate_note.cget('text')
    page.stickers.set(False)
    page.save()
    args,kw=panel.applied.pop()
    assert args[2]==(310.125,2,1,0) and kw['keep_pacing']


@pytest.mark.parametrize('values',[['3','','10','500'],['NaN','3','10','500'],['3','31','1','2'],
                                     ['3','1','2.5','5'], ['3','1','61','1'], ['3','1','1','3601']])
def test_invalid_custom_rates_never_reach_service(panel,values):
    page=SessionsPage(panel.root,panel)
    for field,text in zip(page.rate_values,values):
        field.set(text)
    page.save()
    assert page.note.get() and not panel.applied and not page.busy


def test_saved_row_keys_stage_primary_without_starting_a_service(panel):
    page=SessionsPage(panel.root,panel)
    assert page.selected_name==GROUP.name
    assert page._move_row(GROUP.name,1)=='break' and page.selected_name==PERSON.name
    page._row_action(PERSON.name,page.promote)
    assert page.names[0]==PERSON.name and page.selected_name==PERSON.name
    page.add_contact(THIRD)
    assert page.selected_name==THIRD.name
    page._row_action(THIRD.name,page.remove)
    assert THIRD.name not in page.names and not panel.applied and not panel.chosen


def test_directory_keyboard_staging_and_filters_keep_exact_selection(monkeypatch,panel):
    rows=[SYSTEM,GROUP,PERSON]
    monkeypatch.setattr('xiaomao.sessions_ui.load_directory',lambda *args,**kwargs:rows)
    sessions=SessionsPage(panel.root,panel)
    panel.view.pages['sessions']=sessions
    picker=DirectoryPage(panel.root,panel)
    picker.on_show()
    action,done,_,_=panel.jobs.pop()
    done(action())
    picker._first_result(None)
    picker._selection()
    assert picker._selected()==GROUP
    picker.query.set('摄影')
    assert picker._selected()==GROUP
    picker._stage_key(None)
    assert sessions.selected_name==GROUP.name and not panel.chosen


def test_batch_checks_survive_filtering_and_apply_atomically_to_draft(monkeypatch,panel):
    rows=[GROUP,PERSON,THIRD,SYSTEM]
    monkeypatch.setattr('xiaomao.sessions_ui.load_directory',lambda *args,**kwargs:rows)
    sessions=SessionsPage(panel.root,panel)
    sessions.monitor_all.set(False)
    panel.view.pages['sessions']=sessions
    picker=DirectoryPage(panel.root,panel);picker.on_show()
    read,done,_,_=panel.jobs.pop();done(read())
    picker.query.set(GROUP.name);picker.toggle_checked(picker.visible[0])
    picker.kind.set('个人');picker.query.set(THIRD.name);picker.toggle_checked(picker.visible[0])
    picker.query.set('无匹配');assert not picker.visible and len(picker.checked)==2
    picker.stage()
    assert sessions.names==[GROUP.name,PERSON.name,THIRD.name] and sessions.monitor_all.get()
    assert list(sessions.picked)==[GROUP.name,THIRD.name] and not panel.applied and not panel.chosen


def test_invalid_batch_cannot_partially_stage_or_enable_listening(panel):
    sessions=SessionsPage(panel.root,panel);before=list(sessions.names)
    sessions.monitor_all.set(False)
    assert not sessions.add_contacts([THIRD,replace(PERSON,id='changed')],together=True)
    assert sessions.names==before and sessions.picked=={} and not sessions.monitor_all.get()


def test_pending_large_directory_search_cannot_select_a_stale_row(monkeypatch,panel):
    rows=[replace(PERSON,id=f'id-{i}',name=f'临时联系人{i}') for i in range(520)]
    monkeypatch.setattr('xiaomao.sessions_ui.load_directory',lambda *args,**kwargs:rows)
    picker=DirectoryPage(panel.root,panel)
    picker.on_show()
    action,done,_,_=panel.jobs.pop()
    done(action())
    picker.list.selection_set('0')
    picker._selection()
    picker.query.set('无法匹配')
    assert picker.search_job
    picker.select()
    assert not panel.chosen and not picker.visible and picker.search_job is None


def test_custom_entries_reach_the_real_panel_apply_and_reload_without_resuming(panel,monkeypatch):
    from dataclasses import asdict
    from types import MethodType
    from xiaomao.gui import Panel
    from test_vision import binding
    panel.settings=replace(panel.settings,calibration=asdict(binding()))
    panel.settings.save()
    original=panel.settings
    started=[]
    panel.client=SimpleNamespace(status=lambda:{'mode':'paused'},start=lambda **kw:started.append(kw) or {})
    panel.apply_group_settings=MethodType(Panel.apply_group_settings,panel)
    panel.work=lambda action,done,failed,**kw:done(action())
    panel.view.render=lambda:None
    panel.show_status=lambda state:None
    panel.target_group=tk.StringVar(panel.root)
    panel.at_unique=tk.BooleanVar(panel.root)
    panel.owner_tone=tk.BooleanVar(panel.root)
    monkeypatch.setattr('xiaomao.selection.load_directory',lambda *args:[GROUP,PERSON])
    monkeypatch.setattr('xiaomao.selection.refresh_binding',lambda cfg:cfg.binding)
    monkeypatch.setattr('xiaomao.selection.stop_confirmed',lambda *args:None)
    page=SessionsPage(panel.root,panel)
    for field,value in zip(page.rate_values,('4.5','1.25','10','500')):
        field.set(value)
    assert Settings.load(panel.directory).hourly_limit==original.hourly_limit
    page.save()
    saved=Settings.load(panel.directory)
    assert (saved.min_interval,saved.hourly_limit,saved.minute_limit,saved.pacing_jitter)==(4.5,500,10,1.25)
    assert not page.busy and not page.rate_edited and started==[{'initial_mode':'paused'}]
