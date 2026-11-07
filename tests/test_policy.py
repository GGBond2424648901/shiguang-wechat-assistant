import pytest


@pytest.mark.parametrize("text,expected", [
    ("小猫，下午好", True), ("小猫帮我看看", True), (" @小猫 你好", True),
    ("我家的小猫很可爱", False), ("@张三\u2005有空吗", True),
    ("@张三丰 你好", False), ("@所有人 你好", False), ("你好", False),
])
def test_only_addressed_text_triggers(text, expected):
    from xiaomao.domain import Message
    from xiaomao.policy import TriggerPolicy
    assert TriggerPolicy("小猫", "张三").matches(Message(text, "朋友")) is expected


@pytest.mark.parametrize("fields", [
    {"direction": "out"}, {"kind": "image"}, {"quoted": True},
    {"quality": 0.6}, {"direction": "unknown"}, {"kind": "system"},
])
def test_non_replyable_messages_never_trigger(fields):
    from xiaomao.domain import Message
    from xiaomao.policy import TriggerPolicy
    assert not TriggerPolicy("小猫", "张三").matches(Message("小猫你好", "朋友", **fields))


def test_reply_controls_cannot_turn_into_send_keys():
    from xiaomao.policy import clean_reply
    assert clean_reply("  好的\r\n没问题\x00\t🙂  ") == "  好的\n没问题\t🙂  "
    assert clean_reply("喵" * 230) == "喵" * 230
    with pytest.raises(ValueError):clean_reply("喵" * 4001)
    with pytest.raises(ValueError):
        clean_reply("\x00 \n")


def test_internal_reply_envelope_is_decoded_before_text_limit():
    import json
    from xiaomao.policy import clean_reply
    body='（耳朵动了动）这是正常中文呀，颜文字也留着 (・ω・)'
    value=json.dumps({'speaker':'self','message':'m_'+'1'*16,'reply_to':'m_'+'2'*16,
                      'reply_to_speaker':'p_'+'3'*16,'body':body},ensure_ascii=True)
    assert clean_reply(value)==body
    assert clean_reply('```json\n'+value+'\n```')==body
    assert clean_reply(json.dumps(value))==body
    with pytest.raises(ValueError):clean_reply(value,limit=8)


@pytest.mark.parametrize('value',[
    '{"speaker":"self","message":"m_1111111111111111","body":"没写完',
    '{"speaker":"self","body":null}',
    '{"speaker":"self","body":{"text":"不是字符串"}}',
    '回复如下：{"speaker":"self","body":"你好"}',
    '{"speaker":"self","body":"你好","tool_calls":[]}',
])
def test_broken_internal_envelope_is_never_sent(value):
    from xiaomao.policy import clean_reply
    with pytest.raises(ValueError):clean_reply(value)


def test_regular_json_examples_and_plain_unicode_are_preserved():
    from xiaomao.policy import clean_reply
    for value in ('{"name":"小玲","value":1}','```json {"body":"请求正文"} ```',
                  '{"speaker":"narrator","body":"self is a Python parameter"}','中文、日本語、🙂 (≧▽≦)'):
        assert clean_reply(value)==value


def test_config_rejects_other_targets_and_unencrypted_remote_urls(tmp_path):
    from xiaomao.config import Settings
    with pytest.raises(ValueError):
        Settings(root=tmp_path, group="")
    with pytest.raises(ValueError):
        Settings(root=tmp_path, model_base_url="http://example.com/v1")
    settings = Settings(root=tmp_path, model_base_url="http://127.0.0.1:11434/v1", model_name="")
    settings.save()
    assert Settings.load(tmp_path).assistant_name == "小猫"
    assert not Settings.load(tmp_path).model_ready


def test_empty_display_name_disables_at_trigger():
    from xiaomao.domain import Message
    from xiaomao.policy import TriggerPolicy
    assert not TriggerPolicy("小猫", "").matches(Message("@我 你好", "朋友"))


def test_at_trigger_requires_confirmed_unique_group_nickname(tmp_path):
    from xiaomao.config import Settings
    from xiaomao.domain import Message
    from xiaomao.policy import TriggerPolicy
    settings=Settings(tmp_path,self_display_name="张三")
    assert not TriggerPolicy("小猫",settings.at_display_name).matches(Message("@张三 你好","成员"))
    settings.self_name_unique_confirmed=True
    assert TriggerPolicy("小猫",settings.at_display_name).matches(Message("@张三 你好","成员"))


def test_xiaoling_persona_name_is_a_wake_alias():
    from xiaomao.domain import Message
    from xiaomao.policy import TriggerPolicy
    assert TriggerPolicy('小猫','',aliases=('小玲',)).matches(Message('小玲，下午好','朋友'))


@pytest.mark.parametrize('text,expected', [
    ('大家好，@小玲\u2005你怎么看？', True), ('说到这里@小玲，帮我看看', True),
    ('@小玲', True), ('@小玲🙂', True), ('小玲你好', True),
    ('@小玲猫 你好', False), ('大家 @小玲2 看看', False), ('我家的小猫很可爱', False),
])
def test_addressed_recognizes_midline_alias_and_exact_at_name(text, expected):
    from xiaomao.domain import Message
    from xiaomao.policy import TriggerPolicy
    policy = TriggerPolicy('小猫', '', ('小玲',), mode='natural')
    assert policy.addressed(Message(text)) is expected


@pytest.mark.parametrize('mode', ['natural', 'all'])
def test_conversational_modes_admit_text_and_identified_media_only(mode):
    from xiaomao.domain import Message
    from xiaomao.policy import TriggerPolicy
    policy = TriggerPolicy('小猫', '', ('小玲',), mode=mode)
    assert policy.matches(Message('普通话题'))
    assert policy.matches(Message('[图片]', kind='image', message_id='image'))
    assert not policy.matches(Message('[图片]', kind='image'))
    assert not policy.matches(Message('小玲你好', direction='out'))
    assert not policy.matches(Message('小玲你好', quoted=True))


def test_confirmed_name_xiaoling_and_old_default_config_use_ling_jade(tmp_path):
    from xiaomao.config import Settings
    from xiaomao.domain import Message
    from xiaomao.policy import TriggerPolicy
    settings=Settings(tmp_path)
    policy=TriggerPolicy(settings.assistant_name,'',settings.wake_aliases)
    assert policy.matches(Message('小玲你好','群友'))
    assert not policy.matches(Message('小铃你好','群友'))
    settings.wake_aliases=('小猫','小铃')
    settings.save()
    loaded=Settings.load(tmp_path)
    assert tuple(loaded.wake_aliases)==('小猫','小玲')
