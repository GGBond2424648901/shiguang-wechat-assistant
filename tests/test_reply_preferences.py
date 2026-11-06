from dataclasses import replace
import pytest
from xiaomao.config import Settings


def configured(root):
    return Settings(root,group='甲群',group_names=('甲群','乙群','朋友'),
        conversation_targets={
            '甲群':{'account':'account-a','id':'room-a@chatroom','kind':'group'},
            '乙群':{'account':'account-a','id':'room-b@chatroom','kind':'group'},
            '朋友':{'account':'account-a','id':'person-c','kind':'private'}})


def test_legacy_settings_inherit_the_existing_defaults(tmp_path):
    cfg=configured(tmp_path)
    assert cfg.sticker_frequency=='natural'
    assert cfg.conversation_rules=={}
    assert cfg.for_group('乙群').reply_mode=='natural'


def test_three_conversations_have_independent_modes_and_sticker_preferences(tmp_path):
    cfg=configured(tmp_path)
    cfg=cfg.with_conversation_rules('乙群',{'reply_mode':'mentions','sticker_frequency':'requested'})
    cfg=cfg.with_conversation_rules('朋友',{'reply_mode':'all','sticker_frequency':'expressive'})
    assert [cfg.for_group(n).reply_mode for n in cfg.allowed_groups]==['natural','mentions','all']
    assert [cfg.for_group(n).sticker_frequency for n in cfg.allowed_groups]==['natural','requested','expressive']
    assert cfg.reply_mode=='natural' and cfg.sticker_frequency=='natural'


def test_overrides_survive_reload_and_inherit_changed_defaults(tmp_path):
    cfg=configured(tmp_path).with_conversation_rules('甲群',{'reply_mode':'mentions'})
    cfg=replace(cfg,sticker_frequency='expressive');cfg.save()
    loaded=Settings.load(tmp_path)
    assert loaded.for_group('甲群').reply_mode=='mentions'
    assert loaded.for_group('甲群').sticker_frequency=='expressive'
    cleared=loaded.with_conversation_rules('甲群',{'reply_mode':None})
    assert cleared.for_group('甲群').reply_mode=='natural' and not cleared.conversation_rules


def test_same_display_name_on_another_account_cannot_receive_old_rules(tmp_path):
    cfg=configured(tmp_path).with_conversation_rules('甲群',{'reply_mode':'mentions'})
    targets={**cfg.conversation_targets,'甲群':{'account':'account-b','id':'room-a@chatroom','kind':'group'}}
    other=replace(cfg,conversation_targets=targets)
    assert other.for_group('甲群').reply_mode=='natural'
    assert cfg.for_group('甲群').reply_mode=='mentions'


def test_removed_conversation_loses_its_rule_without_mutating_others(tmp_path):
    cfg=configured(tmp_path).with_conversation_rules('甲群',{'reply_mode':'mentions'})
    cfg=cfg.with_conversation_rules('乙群',{'reply_mode':'all'})
    kept=cfg.with_saved_conversations(('乙群','朋友'))
    assert kept.conversation_rules=={'account-a/room-b@chatroom':{'reply_mode':'all'}}
    assert len(cfg.conversation_rules)==2


@pytest.mark.parametrize('changes',[{'reply_mode':'anything'}, {'sticker_frequency':'always'},
    {'group':'乙群'}, {'reply_mode':True}, {'sticker_frequency':4}])
def test_invalid_rule_changes_are_rejected(tmp_path,changes):
    with pytest.raises(ValueError):configured(tmp_path).with_conversation_rules('甲群',changes)


def test_unbound_conversation_cannot_get_an_identity_based_override(tmp_path):
    with pytest.raises(ValueError,match='身份'):
        Settings(tmp_path).with_conversation_rules('示例会话',{'reply_mode':'all'})
