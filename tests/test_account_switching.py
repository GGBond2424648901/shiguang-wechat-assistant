from dataclasses import asdict, replace
import threading
import pytest
from xiaomao.config import Settings, TARGET_GROUP
from xiaomao.contacts import Contact
from xiaomao.account_detection import AccountObservation


def configured(tmp_path):
    cfg=Settings(tmp_path,group='群甲',group_names=('群甲','朋友'),
        conversation_targets={'群甲':{'account':'a','id':'shared@chatroom','kind':'group'},
                              '朋友':{'account':'a','id':'friend-a','kind':'private'}},
        model_base_url='http://localhost:11434',auto_on_launch=True,
        self_display_name='旧群昵称',self_name_unique_confirmed=True)
    return cfg.with_conversation_rules('群甲',{'reply_mode':'mentions','sticker_frequency':'expressive'})


def observation(account='b',rows=None,pid=22):
    if rows is None:
        rows=(Contact('shared@chatroom',account,'群乙','group'),
              Contact('friend-b',account,'朋友','private'))
    return AccountObservation(account,'新微信昵称',pid,tuple(rows),())


def test_migrate_only_stable_ids_not_same_names_and_isolate_memory(tmp_path):
    from xiaomao.account_switching import prepare_account_settings
    cfg=configured(tmp_path)
    fresh=prepare_account_settings(cfg,observation())
    assert fresh.saved_groups==('群乙',)
    assert fresh.memory_scope=='b/shared@chatroom' and fresh.memory_scope!=cfg.memory_scope
    assert fresh.for_group('群乙').reply_mode=='mentions'
    assert fresh.for_group('群乙').sticker_frequency=='expressive'
    assert fresh.account_display_name=='新微信昵称' and fresh.account_process_id==22
    assert fresh.self_display_name=='新微信昵称' and not fresh.self_name_unique_confirmed
    assert fresh.binding is None and not fresh.window_bindings
    assert not fresh.owner_private_token
    assert fresh.model_base_url==cfg.model_base_url and fresh.wake_aliases==cfg.wake_aliases


def test_returning_profile_restores_rules_and_room_pause_without_old_handles(tmp_path):
    from xiaomao.account_switching import prepare_account_settings, route_snapshot
    a=configured(tmp_path).with_conversation_pause('朋友',True)
    b=prepare_account_settings(a,observation())
    rows=(Contact('shared@chatroom','a','群甲','group'),Contact('friend-a','a','朋友','private'))
    restored=prepare_account_settings(b,observation('a',rows),route_snapshot(a))
    assert restored.saved_groups==a.saved_groups
    assert restored.is_conversation_paused('朋友') and not restored.is_conversation_paused('群甲')
    assert restored.for_group('群甲').reply_mode=='mentions'
    assert restored.memory_scope==a.memory_scope and restored.binding is None


def test_no_matching_ids_keeps_unbound_placeholder_not_same_named_person(tmp_path):
    from xiaomao.account_switching import prepare_account_settings
    cfg=configured(tmp_path)
    fresh=prepare_account_settings(cfg,observation(rows=[Contact('other','b','朋友','private')]))
    assert fresh.saved_groups==(TARGET_GROUP,) and not fresh.conversation_targets
    assert fresh.calibration_account=='b'


def test_all_manually_paused_rooms_stay_paused_on_new_account(tmp_path):
    from xiaomao.account_switching import prepare_account_settings
    cfg=configured(tmp_path)
    for n in cfg.saved_groups:cfg=cfg.with_conversation_pause(n,True)
    assert prepare_account_settings(cfg,observation()).is_conversation_paused('群乙')


def test_unbound_title_memory_is_isolated_between_accounts(tmp_path):
    from xiaomao.account_switching import prepare_account_settings
    cfg=Settings(tmp_path,calibration_account='a')
    fresh=prepare_account_settings(cfg,observation(rows=[]))
    assert cfg.memory_scope!=fresh.memory_scope
    assert cfg.memory_scope.startswith('a/') and fresh.memory_scope.startswith('b/')


def test_paused_selected_room_does_not_pause_other_saved_room(tmp_path):
    from xiaomao.account_switching import prepare_account_settings
    cfg=replace(configured(tmp_path),monitor_all_groups=False).with_conversation_pause('群甲',True)
    rows=[Contact('shared@chatroom','b','群乙','group'),Contact('friend-a','b','朋友','private')]
    fresh=prepare_account_settings(cfg,observation(rows=rows))
    assert fresh.is_conversation_paused('群乙')
    assert not fresh.is_conversation_paused('朋友')


@pytest.mark.parametrize('rows',[
    [Contact('shared@chatroom','c','群甲','group')],
    [Contact('shared@chatroom','b','群甲','group'),Contact('shared@chatroom','b','群乙','group')],
])
def test_inconsistent_directory_cannot_prepare_routes(tmp_path,rows):
    from xiaomao.account_switching import prepare_account_settings
    with pytest.raises(ValueError):prepare_account_settings(configured(tmp_path),observation(rows=rows))


def test_unselectable_or_ambiguous_names_are_not_automatically_bound(tmp_path):
    from xiaomao.account_switching import prepare_account_settings
    rows=[Contact('shared@chatroom','b','同名群','group',selectable=False,reason='重名')]
    fresh=prepare_account_settings(configured(tmp_path),observation(rows=rows))
    assert fresh.saved_groups==(TARGET_GROUP,)


def test_profiles_cannot_override_model_or_claim_another_account(tmp_path):
    from xiaomao.account_switching import prepare_account_settings,route_snapshot
    cfg=configured(tmp_path)
    profile=route_snapshot(cfg)
    with pytest.raises(ValueError):prepare_account_settings(cfg,observation(),profile)
    profile['model_base_url']='http://localhost:9999'
    with pytest.raises(ValueError):prepare_account_settings(cfg,observation('a',[]),profile)


def test_auto_calibration_skips_hidden_and_minimized_without_capturing(tmp_path,monkeypatch):
    from xiaomao.account_switching import prepare_account_settings, auto_bind_account
    import xiaomao.selection as selection
    def fail(*a,**kw):raise AssertionError('hidden window must not be captured')
    monkeypatch.setattr(selection,'preview_window',fail)
    obs=replace(observation(),windows=({'pid':22,'hwnd':1,'kind':'main','title':'微信','minimized':True},
                                     {'pid':22,'hwnd':2,'kind':'main','title':'微信','minimized':False,'cloaked':2}))
    cfg=prepare_account_settings(configured(tmp_path),obs)
    assert auto_bind_account(cfg,obs,threading.Event())==cfg


@pytest.mark.parametrize('order',[('detached','main'),('main','detached')])
def test_detached_binding_wins_over_shared_main_in_any_z_order(tmp_path,monkeypatch,order):
    from xiaomao.account_switching import prepare_account_settings,auto_bind_account
    from xiaomao.config import Calibration
    import xiaomao.selection as selection
    import xiaomao.windows as windows
    cfg=prepare_account_settings(configured(tmp_path),observation())
    rows={'main':{'pid':22,'hwnd':11,'kind':'main','title':'微信'},
          'detached':{'pid':22,'hwnd':55,'kind':'detached','title':'群乙'}}
    obs=replace(observation(),windows=tuple(rows[k] for k in order))
    regions={'header':[0,0,1,.1],'messages':[0,.1,1,.7],'input':[0,.8,.8,.9],'send':[.8,.9,1,1]}
    def preview(cfg,w,expected):
        return None,Calibration(w['hwnd'],22,800,600,'群乙',regions,True,
            target_group='群乙',window_kind=w['kind'])
    monkeypatch.setattr(selection,'preview_window',preview)
    monkeypatch.setattr(selection,'verify_reviewed_window',lambda cfg,b,c:(b,obs.rows[0]))
    monkeypatch.setattr(windows,'window_cloaked',lambda h:False)
    fresh=auto_bind_account(cfg,obs,threading.Event())
    assert fresh.binding_for('群乙').hwnd==55
    assert fresh.calibration and fresh.calibration['hwnd']==11
