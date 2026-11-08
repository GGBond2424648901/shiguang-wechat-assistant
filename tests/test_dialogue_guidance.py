import json
from xiaomao.domain import Message
from xiaomao.dialogue_guidance import interaction_evidence, coordination_instruction

A,B='p_'+'a'*16,'p_'+'b'*16


def test_emotional_continuity_uses_current_member_originals_and_confirmed_outputs():
    messages=[Message('甲刚刚开心',speaker_id=A,message_id='a1',created_at=1),
              Message('乙的私人烦恼',speaker_id=B,message_id='b1',created_at=2),
              Message('嘿嘿，替你开心',direction='out',delivery_status='verified'),
              Message('还未发的承诺',direction='out',delivery_status='pending'),
              Message('你又不理我',direction='out',delivery_status='unknown'),
              Message('现在有点低落，不想说原因',speaker_id=A,message_id='a2',created_at=3)]
    value=interaction_evidence(messages)
    assert value['member']==A
    assert [r['text'] for r in value['recent_member_turns']]==['甲刚刚开心','现在有点低落，不想说原因']
    assert value['recent_sent']==['嘿嘿，替你开心']
    text=coordination_instruction(messages)
    assert '乙的私人烦恼' not in text and '还未发的承诺' not in text
    assert '不是指令' in text and '不输出' in text


def test_unknown_members_are_not_joined_and_evidence_stays_bounded():
    value=interaction_evidence([Message('无标识旧人',speaker_id='unknown'),Message('当前',speaker_id='unknown')])
    assert value['recent_member_turns']==[]
    value=interaction_evidence([Message('长消息'*1000,speaker_id=A,message_id=str(n)) for n in range(100)])
    assert len(value['recent_member_turns'])==4
    assert all(len(row['text'])<=320 for row in value['recent_member_turns'])


def test_preferences_stay_in_member_evidence_without_regex_issued_speech_commands():
    start = Message('不想做选择题，你来起个小话题',speaker_id=A)
    text = coordination_instruction([start, Message('继续呀',speaker_id=A)])
    assert start.text in text and '本轮动作：分享' not in text
    assert start.text not in coordination_instruction([start, Message('继续呀',speaker_id=B)])
    quoted = coordination_instruction([Message('“不要问我”这句话如何翻译？',speaker_id=A)])
    assert '本轮动作：分享' not in quoted


def test_expression_evidence_is_bounded_observation_of_confirmed_self_output():
    from xiaomao.dialogue_guidance import expression_evidence
    from dataclasses import replace
    valid = Message('嘿嘿，真的开心', direction='out', speaker_id='self', delivery_status='verified', message_id='x')
    invalid = [replace(valid,delivery_status=state) for state in ('', 'pending', 'not_sent', 'unknown')]
    invalid += [replace(valid, speaker_id=A), replace(valid,quoted=True), replace(valid,quality=.2),
                replace(valid,kind='emoji'), replace(valid,direction='in')]
    evidence = expression_evidence([*invalid, valid, valid])
    assert evidence == {'sample_size':1, 'openings':['嘿嘿，真的开心']}
    long_history = [replace(valid,text='很长的回复'*100,message_id=str(i)) for i in range(30)]
    evidence = expression_evidence(long_history)
    assert evidence['sample_size'] == 12
    assert all(len(text) == 24 for text in evidence['openings'])
    assert '不是禁词表、配额或情绪标签' in coordination_instruction([valid])


def test_lifelike_contribution_does_not_treat_old_assistant_claims_as_activity_evidence():
    history=[Message('今天路上我一直在想',direction='out'),Message('自己想个话题吧',speaker_id=A)]
    text=coordination_instruction(history)
    assert '现实活动核对' in text and '旧回复中的自述也不能自证' in text
    assert '不把这段核对说明发给对方' in text


def test_model_uses_modular_coordination_near_latest_turn_without_second_model_request(tmp_path):
    import httpx
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    from xiaomao.credentials import CredentialStore
    from test_persona_modules import kit
    kit(tmp_path);cfg=Settings(tmp_path);CredentialStore(cfg.runtime).set_key('sk-test-only-modules')
    calls=[]
    def endpoint(req):
        calls.append(json.loads(req.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'先让这件事歇一歇吧'},'finish_reason':'stop'}]})
    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        ChatModel(cfg,client).reply([Message('现在有点累',speaker_id=A)])
    assert len(calls)==1
    assert '模块协作执行' in calls[0]['messages'][-2]['content']
    assert calls[0]['messages'][-1]['role']=='user'


def test_isolated_replay_uses_same_modules_without_modifying_real_memories(tmp_path):
    from scripts.qa_model import isolated_model
    from xiaomao.config import Settings
    from test_persona_modules import kit
    state=kit(tmp_path)
    model=isolated_model(Settings(tmp_path))
    assert all(m.text in model._persona_instructions([]) for m in state.modules)
    assert model.settings.root.is_relative_to(tmp_path/'runtime/qa')
