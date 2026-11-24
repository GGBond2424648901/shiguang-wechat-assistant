from dataclasses import replace

import pytest

from test_conversation_rules import live
from test_reply_preferences import configured
from xiaomao.client import ControlClient, ControlUnavailable
from xiaomao.config import Settings
from xiaomao.control import ControlServer
from xiaomao.desktop_sessions import DesktopSessions
from xiaomao.reply_preferences import validate_rule_changes
from xiaomao.voice_policy import VoicePreferences, choose_reply_medium


@pytest.mark.parametrize(('message', 'medium'), [
    ('给我发个语音吧', 'voice'),
    ('能用语音回我吗？', 'voice'),
    ('可以给我发一条微信语音吗？', 'voice'),
    ('回复我一条语音吧', 'voice'),
    ('我想听听你的声音', 'voice'),
    ('我想听你说话', 'voice'),
    ('不要用文字，发个语音', 'voice'),
    ('@小玲，用语音说一遍', 'voice'),
    ('@拾光\u2005测试，用语音回复', 'voice'),
    ('那不唱歌，你用语音回复我@拾光\u2005', 'voice'),
    ('@拾光\u2005那唱，来段轻快的', 'sing'),
    ('那唱个轻快点的@拾光\u2005', 'sing'),
    ('那你唱《一首歌》吧', 'sing'),
    ('那唱反调就太烦了', 'text'),
    ('你用语音回复我，不唱歌', 'voice'),
    ('Send me a voice message please', 'voice'),
    ('可以给我唱一首歌吗？', 'sing'),
    ('我想听你唱歌', 'sing'),
    ('唱一小段给我听', 'sing'),
    ('能给我唱生日快乐吗？', 'sing'),
    ('Could you sing me a song?', 'sing'),
    ('用语音给我唱首歌', 'sing'),
    ('别唱歌了，用语音回我', 'voice'),
    ('你们语音设置怎么弄？', 'text'),
    ('发语音的按钮在哪？', 'text'),
    ('朋友刚刚给我发了个语音', 'text'),
    ('刚才他说“给我发个语音”', 'text'),
    ('你会唱歌吗？', 'text'),
    ('早上好', 'text'),
])
def test_explicit_requests_work_with_automatic_frequency_zero(message, medium):
    assert choose_reply_medium(message, VoicePreferences(enabled=True),
                               sample=0.01, sender_ready=True) == medium


@pytest.mark.parametrize('message', [
    '别发语音了，打字回复我吧',
    '可以用语音说吗？还是打字回复吧',
    '用文字告诉我怎么设置',
    '文字就好',
    '不用唱歌也不用发语音',
    'Please reply in text instead of sending a voice message',
])
def test_explicit_text_requests_override_automatic_voice(message):
    assert choose_reply_medium(message, VoicePreferences(enabled=True, frequency=100),
                               sample=0, sender_ready=True) == 'text'


def test_frequency_controls_ordinary_replies_without_enabling_singing():
    preferences = VoicePreferences(enabled=True, frequency=20)
    assert choose_reply_medium('今天真开心', preferences, sample=0.19, sender_ready=True) == 'voice'
    assert choose_reply_medium('今天真开心', preferences, sample=0.20, sender_ready=True) == 'text'


def test_runtime_observes_voice_off_before_resident_reloads_the_session(tmp_path):
    from xiaomao.voice_runtime import VoiceRuntime

    saved = configured(tmp_path).with_conversation_rules('甲群', {'voice_enabled': True})
    saved.save()
    stale = saved.for_group('甲群')
    runtime = VoiceRuntime(tmp_path)
    assert runtime.preferences(stale).enabled
    saved.with_conversation_rules('甲群', {'voice_enabled': False}).save(expected=saved)
    assert stale.voice_preferences('甲群').enabled
    assert not runtime.preferences(stale).enabled


@pytest.mark.parametrize(('enabled', 'sender_ready'), [(False, True), (True, False)])
def test_unavailable_or_disabled_voice_always_uses_text(enabled, sender_ready):
    preferences = VoicePreferences(enabled=enabled, frequency=100)
    assert choose_reply_medium('唱首歌给我听', preferences, sample=0, sender_ready=sender_ready) == 'text'
    assert choose_reply_medium('发条语音给我', preferences, sample=0, sender_ready=sender_ready) == 'text'


def test_voice_preferences_default_off_and_survive_save_by_bound_identity(tmp_path):
    cfg = configured(tmp_path)
    cfg.save()
    original = Settings.load(tmp_path)
    assert not original.voice_preferences('甲群').enabled
    assert original.voice_preferences('甲群').frequency == 0
    assert original.voice_preferences('甲群').role_id is None

    updated = original.with_conversation_rules('甲群', {
        'voice_enabled': True, 'voice_frequency': 10, 'voice_role_id': 'fabric.role-1',
    })
    updated.save(expected=original)
    saved = Settings.load(tmp_path)
    settings = saved.for_group('甲群').voice_preferences('甲群')
    assert (settings.enabled, settings.frequency, settings.role_id) == (True, 10, 'fabric.role-1')
    assert not saved.voice_preferences('乙群').enabled
    saved_inactive = saved.with_conversation_rules('乙群', {'voice_enabled': True})
    assert replace(saved_inactive, monitor_all_groups=False).voice_preferences('乙群').enabled

    switched = replace(saved, conversation_targets={**saved.conversation_targets, '甲群': {
        'account': 'account-b', 'id': 'room-a@chatroom', 'kind': 'group',
    }})
    assert not switched.voice_preferences('甲群').enabled
    assert not saved.with_saved_conversations(('乙群', '朋友')).conversation_rules


@pytest.mark.parametrize('rule', [
    {'voice_enabled': 1}, {'voice_frequency': True}, {'voice_frequency': -1},
    {'voice_frequency': 101}, {'voice_frequency': 0.5}, {'voice_role_id': ''},
    {'voice_role_id': 'invalid\nrole'}, {'voice_role_id': ' r '}, {'voice_role_id': 'x' * 241},
])
def test_invalid_voice_preference_values_are_rejected(rule):
    with pytest.raises(ValueError):
        validate_rule_changes(rule)


def test_voice_fields_are_not_global_preferences():
    with pytest.raises(ValueError):
        validate_rule_changes({'voice_enabled': True}, defaults=True)


def test_desktop_voice_settings_use_live_scoped_reply_rule_updates(live):
    root = live.settings.root
    with ControlServer(root, live):
        client = ControlClient(root)
        assert DesktopSessions(root, client).read()['voiceSendReady'] is False
        role_checks = []

        def voice_ready(role_id=None):
            role_checks.append(role_id)
            return role_id == 'fabric.role-1'

        service = DesktopSessions(root, client, voice_send_ready=voice_ready)
        snapshot = service.read()
        assert snapshot['voiceSendReady'] is False and role_checks == [None]
        a, b, _ = snapshot['items']
        before = {'reply_mode': None, 'sticker_frequency': None, 'voice_enabled': None,
                  'voice_frequency': None, 'voice_role_id': None}
        assert a['voiceEnabled'] is None and a['voiceFrequency'] is None and a['voiceRoleId'] is None

        client.configure_reply_rules('甲群', live.settings.conversation_scope('甲群'), {'reply_mode': 'mentions'})
        result = service.rules(snapshot['revision'], a['id'], None, None, before,
                               voiceEnabled=True, voiceFrequency=5, voiceRoleId='fabric.role-1')
        assert result['items'][0]['replyMode'] == 'mentions'
        assert result['items'][0]['voiceEnabled'] is True
        assert result['items'][0]['voiceFrequency'] == 5
        assert result['items'][0]['voiceRoleId'] == 'fabric.role-1'
        assert result['items'][0]['voiceSendReady'] is False  # This saved contact still has no reviewed window.
        assert result['voiceSendReady'] is False and role_checks == [None, None, 'fabric.role-1']
        assert result['items'][1] == b
        assert live.engines[0].settings.voice_preferences('甲群').role_id == 'fabric.role-1'

        with pytest.raises(ControlUnavailable, match='已在其他窗口更改'):
            service.rules(snapshot['revision'], a['id'], None, None, before,
                          voiceEnabled=True, voiceFrequency=25, voiceRoleId='fabric.role-1')

        cleared = service.rules(result['revision'], a['id'], 'mentions', None,
                                voiceEnabled=False, voiceFrequency=5, voiceRoleId=None)
        assert cleared['items'][0]['voiceEnabled'] is False
        assert cleared['items'][0]['voiceRoleId'] is None
        assert not live.engines[0].settings.voice_preferences('甲群').enabled
