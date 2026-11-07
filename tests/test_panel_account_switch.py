from dataclasses import replace
from types import SimpleNamespace
import pytest
from xiaomao.config import Settings
from xiaomao.gui import Panel


class Var:
    def __init__(self,value=''):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value


def panel(tmp_path):
    p=Panel.__new__(Panel)
    p.directory=tmp_path
    p.settings=Settings(tmp_path,calibration_account='old')
    values={'display_name':'self_display_name','at_unique':'self_name_unique_confirmed',
            'auto_on_launch':'auto_on_launch','split_replies':'split_replies','idle_wake':'idle_wake',
            'owner_tone':'owner_confirmed','model_name':'model_name','target_group':'group'}
    for field,cfg in values.items():setattr(p,field,Var(getattr(p.settings,cfg)))
    p.secret=Var();p.notice=Var();p.view=SimpleNamespace(pages={},render=lambda:None)
    return p


def test_clean_panel_follows_backend_account_without_clicking(tmp_path):
    p=panel(tmp_path)
    fresh=replace(p.settings,calibration_account='new',account_display_name='新微信昵称',self_display_name='新微信昵称')
    fresh.save()
    p.sync_account_settings({'account_scope':'new','account_display_name':'新微信昵称'})
    assert p.settings==fresh and p.display_name.get()=='新微信昵称'
    assert not p.at_unique.get()


def test_unsaved_inputs_are_preserved_and_not_applied_to_new_account(tmp_path):
    p=panel(tmp_path)
    p.display_name.set('没保存的旧账号昵称')
    replace(p.settings,calibration_account='new',account_display_name='新微信昵称').save()
    p.sync_account_settings({'account_scope':'new','account_display_name':'新微信昵称'})
    assert p.settings.calibration_account=='old'
    assert p.display_name.get()=='没保存的旧账号昵称'
    assert '未保存' in p.notice.get()


def test_dirty_conversation_selection_is_not_discarded_by_background_switch(tmp_path):
    p=panel(tmp_path)
    p.view.pages={'sessions':SimpleNamespace(dirty=lambda:True)}
    replace(p.settings,calibration_account='new').save()
    p.sync_account_settings({'account_scope':'new'})
    assert p.settings.calibration_account=='old'


def test_stale_settings_save_cannot_change_current_accounts_nickname(tmp_path):
    p=panel(tmp_path)
    fresh=replace(p.settings,calibration_account='new',self_display_name='新微信昵称')
    fresh.save()
    p.secret_state=Var();p.client=SimpleNamespace()
    p.work=lambda action,*args,**kw:action()
    with pytest.raises(ValueError,match='账号已'):
        p.save_settings()
    assert Settings.load(tmp_path).self_display_name=='新微信昵称'


def test_discarding_session_edits_rebases_clean_global_account_fields(tmp_path):
    p=panel(tmp_path)
    p._form_account='old'
    p._form_baseline={'display_name':'','at_unique':False,'auto_on_launch':False,
                     'split_replies':True,'idle_wake':False,'owner_tone':False,'model_name':p.settings.model_name}
    fresh=replace(p.settings,calibration_account='new',account_display_name='新微信昵称',self_display_name='新微信昵称')
    fresh.save()
    p.view.pages={'sessions':SimpleNamespace(dirty=lambda:True)}
    p.sync_account_settings({'account_scope':'new','account_display_name':'新微信昵称'})
    p.settings=Settings.load(tmp_path)  # Existing SessionsPage.discard does this.
    p.view.pages.clear()
    p.sync_account_settings({'account_scope':'new','account_display_name':'新微信昵称'})
    assert p.display_name.get()=='新微信昵称'
    assert p._form_account=='new'


def test_reloading_settings_cannot_make_old_form_authorized_for_new_account(tmp_path):
    p=panel(tmp_path);p._form_account='old'
    fresh=replace(p.settings,calibration_account='new',self_display_name='新微信昵称')
    fresh.save();p.settings=fresh
    p.client=SimpleNamespace();p.work=lambda action,*a,**kw:action()
    with pytest.raises(ValueError,match='账号已'):p.save_settings()


def test_legacy_mixed_accounts_can_open_panel_to_correct_configuration(tmp_path):
    p=panel(tmp_path)
    p.settings=replace(p.settings,conversation_targets={'遗留对象':{'id':'peer','account':'other','kind':'private'}})
    p.remember_account_form()
    p.sync_account_settings({'account_scope':'old'})
    assert '账号' in p.notice.get()
