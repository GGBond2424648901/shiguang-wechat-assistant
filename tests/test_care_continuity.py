from dataclasses import replace
import json

import httpx
import pytest

from xiaomao.domain import Message
from xiaomao.persona_modules import PersonaModule, save_modules

A, B = 'p_' + 'a' * 16, 'p_' + 'b' * 16


def care_persona(root):
    return save_modules(root, [
        PersonaModule('v3', 'V3 语言人格', '# 大肥鱼软萌人格\n## 调皮\n开心时可以热闹地接梗'),
        PersonaModule('v14', 'V14 主动关怀',
                      '# 主动关怀\n## 处境关怀与实际帮助\n接住正在发生的困难，给出可用的下一步\n'
                      '## 甜蜜陪伴与主动关心\n用贴心的大姐姐口吻关心，温柔也有自己的主意'),
    ], title='大肥鱼模块化人格', expected_revision=None)


def test_current_need_not_global_role_score_controls_warmth():
    from xiaomao.dafeiyu import role_profile, EmotionalState
    text = role_profile(EmotionalState(joy=90, favor=0, intimacy=0))
    assert '历史背景' in text and '当前处境优先' in text
    assert '不是可爱、温柔或关心的解锁条件' in text


def test_owner_mapping_does_not_downgrade_the_common_persona(tmp_path):
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    model = ChatModel(Settings(tmp_path, group='private', conversation_targets={
        'private': {'kind': 'private', 'id': 'test_contact', 'account': 'test_account'}}))
    assert '保持当前启用人格的表达风格与主动性' in model._owner_tone()
    assert '不能确认主人' in model._owner_tone()


def test_member_evidence_does_not_treat_other_members_reply_as_my_mood():
    from xiaomao.dialogue_guidance import interaction_evidence
    evidence = interaction_evidence([
        Message('甲的庆祝', direction='out', reply_to_speaker=A, delivery_status='verified'),
        Message('乙的安慰', direction='out', reply_to_speaker=B, delivery_status='verified'),
        Message('现在雨变大了', speaker_id=A),
    ])
    assert evidence['recent_sent'] == ['甲的庆祝']


@pytest.mark.parametrize('text', ['你为什么不主动关心？然后给出方案呢', '希望你多照顾一下我的感受'])
def test_care_feedback_is_detected_for_same_member_learning(text):
    from xiaomao.feedback_learning import explicit_feedback
    assert explicit_feedback(text)


def test_care_uses_same_member_evidence_instead_of_keyword_selected_examples(tmp_path):
    from xiaomao.persona_voice import voice_focus
    from xiaomao.dialogue_guidance import interaction_evidence
    care_persona(tmp_path)
    rain = Message('已经在路上了，没有伞', speaker_id=A, message_id='a1')
    for followup in ('现在变大了呢', '你为什么不主动关心？然后给出方案呢', '所以你应该怎么和我说？'):
        history = [rain, Message(followup, speaker_id=A, message_id='a2')]
        assert '完整启用原稿已经加载' in voice_focus(tmp_path, history)
        assert interaction_evidence(history)['recent_member_turns'][0]['text'] == rain.text
    evidence = interaction_evidence([rain, Message('现在变大了呢', speaker_id=B)])
    assert rain.text not in str(evidence)


@pytest.mark.parametrize('fields', [{'speaker_id': ''}, {'speaker_id': A, 'quoted': True},
                                   {'speaker_id': A, 'quality': .2}])
def test_untrusted_context_does_not_supply_a_care_antecedent(fields):
    from xiaomao.dialogue_guidance import interaction_evidence
    evidence = interaction_evidence([Message('在路上没有伞', **fields), Message('现在变大了呢', speaker_id=A)])
    assert '在路上没有伞' not in str(evidence)


def test_care_availability_does_not_depend_on_words_or_force_a_scene(tmp_path):
    from xiaomao.persona_voice import voice_focus
    care_persona(tmp_path)
    cases = ['回家啦，先不聊了', '写一个下雨的故事', '换个话题，你玩过这个游戏吗',
             '脚下这条路已经漫水了', '我终于不用再输验证码啦']
    prompts = [voice_focus(tmp_path, [Message(text, speaker_id=A)]) for text in cases]
    assert len(set(prompts)) == 1
    assert '完整语义、否定、引用' in prompts[0]
    assert '示例不是本会话经历' in prompts[0]


def test_enabled_care_module_controls_full_source_without_code_specific_ids(tmp_path):
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    from xiaomao.persona_voice import voice_focus
    state=care_persona(tmp_path)
    model=ChatModel(Settings(tmp_path))
    care=next(m for m in state.modules if m.id=='v14')
    assert care.text in model._persona_instructions([])
    assert 'V14' not in voice_focus(tmp_path,[])
    save_modules(tmp_path,[replace(m,enabled=False) if m.id=='v14' else m for m in state.modules],
                 title=state.title,expected_revision=state.revision)
    assert care.text not in model._persona_instructions([])


@pytest.mark.parametrize('mode', ['reply', 'participate', 'reply_plan'])
def test_all_reply_paths_keep_full_persona_and_complete_practical_help(tmp_path, mode):
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    state = care_persona(tmp_path)
    calls = []
    def endpoint(req):
        calls.append(json.loads(req.content))
        result = {'text': '哎呀，先找有顶的地方躲躲雨，我们再想怎么回家'}
        if mode != 'reply': result['send'] = True
        if mode == 'reply_plan': result['sticker'] = ''
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(
            result, ensure_ascii=False)
        }, 'finish_reason': 'stop'}]})
    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        model = ChatModel(Settings(tmp_path, model_base_url='http://localhost'), client)
        messages = [Message('下大雨了，没有带伞怎么办', speaker_id=A, message_id='rain')]
        if mode == 'reply_plan': model.reply_plan(messages, choices=[], optional=True)
        else: getattr(model, mode)(messages)
    payload = calls[0]['messages']
    assert len(calls) == 1 and all(m.text in payload[0]['content'] for m in state.modules)
    assert '完整启用原稿已经加载' in payload[-2]['content']
    assert '不在共情和办法之间二选一' in '\n'.join(m['content'] for m in payload)


def test_proactive_generation_uses_same_full_care_module(tmp_path, monkeypatch):
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    from xiaomao.companion import Candidate
    state = care_persona(tmp_path)
    captured = []
    model = ChatModel(Settings(tmp_path))
    def generate(instructions, *args, **kwargs):
        captured.append((instructions, kwargs['stable_prefix']))
        return {'send': True, 'text': '今天终于有空歇歇啦，给你留一会儿不用赶路的时间'}
    monkeypatch.setattr(model, '_companion_json', generate)
    assert model.proactive([], Candidate('one', 'idle', 1, 2, 0, '2026-11-07'))
    assert '本轮人格执行' in captured[0][0]
    assert all(m.text in captured[0][1] for m in state.modules)


@pytest.mark.parametrize('mode', ['plain', 'direct', 'participation', 'media'])
def test_sent_bubbles_form_one_complete_historical_response_with_all_receipts(mode):
    from xiaomao.model import _conversation, _reply_history, _message_ref
    messages = [Message('哎呀，先别淋着走', direction='out', speaker_id='self', message_id='out1',
                        reply_to_id='rain', reply_to_speaker=A, delivery_status='verified'),
                Message('找个能避雨的地方，再看看能不能买把伞', direction='out', speaker_id='self', message_id='out2',
                        reply_to_id='rain', reply_to_speaker=A, delivery_status='verified')]
    turns = _reply_history(_conversation(messages), mode)
    assert len(turns) == 2
    metadata = json.loads(turns[0]['content'].split('\n', 1)[1])
    assert [p['message'] for p in metadata['parts']] == [_message_ref('out1'), _message_ref('out2')]
    assert all(p['delivery_status'] == 'verified' for p in metadata['parts'])
    body = turns[1]['content'] if mode == 'plain' else json.loads(turns[1]['content'])['text']
    assert body == '\n'.join(m.text for m in messages)


@pytest.mark.parametrize('changes', [
    {'reply_to_speaker': B}, {'reply_to_id': 'other'}, {'reply_to_id': ''},
    {'delivery_status': 'unknown'}, {'delivery_status': 'not_sent'}, {'speaker_id': 'unknown'},
])
def test_history_merge_never_combines_other_recipients_or_unconfirmed_parts(changes):
    from xiaomao.model import _conversation, _reply_history
    first = Message('第一泡', direction='out', speaker_id='self', message_id='o1',
                    reply_to_id='rain', reply_to_speaker=A, delivery_status='verified')
    second = replace(first, text='第二泡', message_id='o2', **changes)
    turns = _reply_history(_conversation([first, second]), 'direct')
    assert len([t for t in turns if t['role'] == 'assistant']) == 2


def test_incoming_message_breaks_history_bubble_merge_even_for_same_question():
    from xiaomao.model import _conversation, _reply_history
    first = Message('第一泡', direction='out', speaker_id='self', message_id='o1',
                    reply_to_id='rain', reply_to_speaker=A, delivery_status='verified')
    turns = _reply_history(_conversation([first, Message('有新情况', speaker_id=A),
                                         replace(first, text='补充', message_id='o2')]), 'direct')
    assert len([t for t in turns if t['role'] == 'assistant']) == 2
