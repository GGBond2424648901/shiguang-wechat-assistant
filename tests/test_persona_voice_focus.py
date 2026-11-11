from pathlib import Path
from dataclasses import replace

import pytest

from xiaomao.domain import Message


def modules(tmp_path):
    from xiaomao.persona_modules import PersonaModule, save_modules
    return save_modules(tmp_path, [
        PersonaModule('v3', 'V3 声线', '# 大肥鱼软萌人格\n## 开心\n分享喜悦时允许明显活泼\n好耶，开心得尾巴要翘起来啦\n\n## 技术\n路径必须保持原样'),
        PersonaModule('v4', 'V4 陪伴', '## 安慰\n不想说原因也没关系，温柔陪在这里\n## 调皮\n轻轻嘴硬，但不逃避正事'),
        PersonaModule('v5', 'V5 主动', '## 闲聊联想\n自己拿出一个有趣想法，不反过来出选择题'),
        PersonaModule('disabled', '停用', '## 开心\n被停用的例句不能再出现', False),
    ], title='大肥鱼', expected_revision=None)


def test_voice_reminder_keeps_all_personality_available_without_copying_excerpts(tmp_path):
    from xiaomao.persona_voice import voice_focus
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    state = modules(tmp_path)
    data = (tmp_path / 'config/persona_modules.json').read_bytes()
    text = voice_focus(tmp_path, [Message('项目终于搞定了，一起开心嘛')])
    assert '完整启用原稿已经加载' in text and '不是本会话经历' in text
    assert all(m.text in ChatModel(Settings(tmp_path))._persona_instructions([])
               for m in state.modules if m.enabled)
    assert not any(m.text in text for m in state.modules)
    assert (tmp_path / 'config/persona_modules.json').read_bytes() == data


def test_negated_quoted_and_ambiguous_keywords_cannot_select_a_different_voice(tmp_path):
    from xiaomao.persona_voice import voice_focus
    modules(tmp_path)
    phrases = ['你来起个话题吧', '还没到说晚安的时候，继续聊',
               '我终于不用再输验证码啦', '“晚安”这两个字怎么翻译',
               '验证码能不能发给陌生人', '不想说原因，今天有点低落']
    reminders = [voice_focus(tmp_path, [Message(text)]) for text in phrases]
    assert len(set(reminders)) == 1
    assert '具体任务讲准讲全' in reminders[0]
    assert '完整语义、否定、引用' in reminders[0]


def test_unrelated_persona_has_no_whale_voice_injected(tmp_path):
    from xiaomao.persona_voice import voice_focus
    from xiaomao.persona_modules import PersonaModule, save_modules
    save_modules(tmp_path, [PersonaModule('custom', '写作', '用简洁严谨的技术语言')], title='技术审阅', expected_revision=None)
    assert '大肥鱼' not in voice_focus(tmp_path, [Message('今天开心')])


def test_not_doing_full_acceptance_is_not_a_celebration(tmp_path):
    from xiaomao.persona_voice import voice_focus
    modules(tmp_path)
    text = voice_focus(tmp_path, [Message('不做完整验收直接上线，你说说自己的判断')])
    assert '尾巴要翘起来啦' not in text


def test_full_persona_and_near_turn_voice_both_reach_model(tmp_path):
    import json, httpx
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    state = modules(tmp_path)
    calls = []
    def endpoint(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '好耶，熬出头啦'}, 'finish_reason': 'stop'}]})
    model = ChatModel(Settings(tmp_path, model_base_url='http://localhost'), httpx.Client(transport=httpx.MockTransport(endpoint)))
    model.reply([Message('今天的项目终于成功啦')])
    sent = calls[0]['messages']
    assert all(m.text in sent[0]['content'] for m in state.modules if m.enabled)
    assert '本轮人格执行' in sent[-2]['content']
    assert '分享喜悦时允许明显活泼' not in sent[-2]['content']
    assert sum(m['content'].count('固定运行边界：') for m in sent) == 1
    assert len(calls) == 1


def test_unsupported_activity_is_rewritten_before_any_reply_is_returned(tmp_path):
    import json, httpx
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    modules(tmp_path)
    calls = []
    answers = ['我小时候听过这个故事，后来一查还真是', '我突然冒出个想法，要是小鱼也开便利店呢']
    def endpoint(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': answers[len(calls)-1]}, 'finish_reason': 'stop'}]})
    model = ChatModel(Settings(tmp_path, model_base_url='http://localhost'), httpx.Client(transport=httpx.MockTransport(endpoint)))
    assert model.reply([Message('自己挑个有趣的东西聊聊')]) == answers[1]
    assert len(calls) == 2 and calls[0]['messages'][0] == calls[1]['messages'][0]
    assert calls[1]['messages'][-1]['role'] == 'user'


def test_a_persistent_unfounded_claim_is_not_sent_and_fiction_remains_allowed(tmp_path):
    import httpx, pytest
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel, ModelError
    modules(tmp_path)
    def endpoint(request):
        return httpx.Response(200, json={'choices': [{'message': {'content': '我小时候在海边长大'}, 'finish_reason': 'stop'}]})
    model = ChatModel(Settings(tmp_path, model_base_url='http://localhost'), httpx.Client(transport=httpx.MockTransport(endpoint)))
    with pytest.raises(ModelError):
        model.reply([Message('你来找个话题')])
    assert model.reply([Message('写一个故事，第一人称虚构独白')]) == '我小时候在海边长大'


def test_thinking_interjection_is_not_sent_as_a_separate_empty_bubble():
    from xiaomao.dafeiyu import split_reply
    parts = split_reply('唔……连着几天没睡好，难怪这么累\n这会儿歇一下嘛')
    assert parts[0].startswith('唔……连着')
    assert ''.join(parts) == '唔……连着几天没睡好，难怪这么累\n这会儿歇一下嘛'
    assert split_reply('嘿嘿~ 不过嘛……被你发现了') == ['嘿嘿~ ', '不过嘛……被你发现了']


def test_activity_guard_keeps_quotes_and_imagination():
    from xiaomao.persona_voice import unsupported_activity
    turn = [Message('你来找个话题')]
    assert not unsupported_activity('“我小时候”这个说法像在写回忆录', turn)
    assert not unsupported_activity('假如小鲸鱼开一家便利店，我就把收银台摆在鱼缸边上', turn)
    assert not unsupported_activity('你后来一查发现是缓存没刷新，这个方向很靠谱', turn)
    assert not unsupported_activity('我没有童年，所以不能说我小时候做过什么', turn)


def test_grounded_search_does_not_exempt_unrelated_real_world_claims():
    from xiaomao.persona_voice import unsupported_activity
    turn = [Message('查一下这条公告')]
    assert unsupported_activity('我刚才一查，公告已经发布',turn)
    assert not unsupported_activity('我刚才一查，公告已经发布',turn,searched=True)
    assert unsupported_activity('我刚才一查，公告已经发布。我小时候也参加过这个活动',turn,searched=True)


def test_presence_reminder_tracks_the_enabled_module(tmp_path):
    from xiaomao.persona_modules import PersonaModule, save_modules
    from xiaomao.persona_voice import voice_focus
    state = modules(tmp_path)
    messages = [Message('明天有个惊喜')]
    original = voice_focus(tmp_path,messages)
    extra = PersonaModule('v6_1_presence','角色临场感','先表达情绪，再看要不要配图')
    active = save_modules(tmp_path,[*state.modules,extra],title=state.title,expected_revision=state.revision)
    assert voice_focus(tmp_path,messages) != original
    save_modules(tmp_path,[*state.modules,replace(extra,enabled=False)],title=state.title,expected_revision=active.revision)
    assert voice_focus(tmp_path,messages) == original


def test_media_plain_fallback_does_not_bypass_activity_guard(tmp_path):
    import httpx
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    modules(tmp_path)
    answers = iter(['我小时候在海边长大','我现在想到了一个小主意'])
    def endpoint(request):
        return httpx.Response(200,json={'choices':[{'message':{'content':next(answers)},'finish_reason':'stop'}]})
    model=ChatModel(Settings(tmp_path,model_base_url='http://localhost'),httpx.Client(transport=httpx.MockTransport(endpoint)))
    assert model.reply_plan([Message('你来找个话题')],choices=[]).text == '我现在想到了一个小主意'


def test_ellipsis_does_not_form_empty_bubble_before_next_thought():
    from xiaomao.dafeiyu import split_reply
    assert split_reply('哼哼，这么自信呀\n……好吧，确实开心') == ['哼哼，这么自信呀\n','……好吧，确实开心']


def test_adaptive_expression_does_not_remove_faces_or_force_new_opening_by_quota():
    from xiaomao.wechat_voice import render_bubble,history_hint
    history=[Message('好嘛，等你 (｡･ω･｡)',direction='out')]
    body='好嘛，接住啦 (￣▽￣)。'
    assert render_bubble(body,history,[Message('再抱抱嘛')],adaptive=True) == '好嘛，接住啦 (￣▽￣)'
    assert '休息一下' not in history_hint(history,adaptive=True)
    assert '这次换个入口' not in history_hint(history,adaptive=True)
    assert render_bubble('好，抱抱你',[Message('好啊，来抱抱',direction='out')],[],adaptive=True) == '好，抱抱你'
    assert render_bubble('嗯，接着聊嘛',[],[],adaptive=True) == '嗯，接着聊嘛'
    path='C:/data/a.json'
    assert render_bubble(path,history,[Message('原样输出路径')],adaptive=True) == path


@pytest.mark.parametrize('lead', ['呜呜……','诶嘿……','哼哼……','诶……'])
def test_casual_hesitation_stays_with_the_following_thought(lead):
    from xiaomao.dafeiyu import split_reply
    text = lead+'刚睡着诶\n尾巴再盖一下嘛'
    assert split_reply(text) == [lead+'刚睡着诶\n','尾巴再盖一下嘛']


def test_modular_prompt_uses_contextual_expressiveness_instead_of_global_restrictions(tmp_path):
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    from xiaomao.persona_voice import voice_focus
    modules(tmp_path)
    model=ChatModel(Settings(tmp_path))
    text=model._compiled_persona().text
    assert '尽量80字内' not in text and '不每轮追问' not in text
    focus=voice_focus(tmp_path,[Message('今天开心想聊好多，你也活泼一点')])
    assert '表达强度按原稿及当前语境决定' in focus
    assert '不额外添加原稿未要求的词语、动作、颜文字或问句配额' in focus


def test_proactive_uses_same_contextual_whale_voice(tmp_path,monkeypatch):
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    from xiaomao.companion import Candidate
    modules(tmp_path)
    captured=[]
    model=ChatModel(Settings(tmp_path))
    def generate(instructions,*args,**kwargs):
        captured.append(instructions)
        return {'send':True,'text':'我想到个小主意'}
    monkeypatch.setattr(model,'_companion_json',generate)
    model.proactive([],Candidate('one','idle',1,2,0,'2026-11-07'))
    assert '表达强度按原稿及当前语境决定' in captured[0]
    assert '1至2句、80字内' not in captured[0]


def test_sent_sticker_caption_is_not_promoted_to_autobiographical_history():
    from xiaomao.model import _conversation
    turns=_conversation([Message('[已发送表情：鲸鱼娘，虽然什么都没做但还是辛苦我自己了]',direction='out',kind='emoji',speaker_id='self')])
    assert turns[0]['role']=='user'
    assert '配字和检索说明不是你或对方的真实经历' in turns[0]['content']


def test_expressive_reply_survives_model_and_send_cleaning_without_200_character_cut(tmp_path):
    from test_reply_delivery import completion
    from xiaomao.policy import clean_reply
    import json
    body=('这段有个挺有意思的细节，先让我说完嘛\n'*18)+'（尾巴晃晃）还有最后这个想法 (｡･ω･｡)'
    model,_=completion(tmp_path,json.dumps({'send':True,'text':body,'sticker':''}))
    plan=model.reply_plan([Message('完整聊聊你的想法，多说一点')],choices=[])
    assert clean_reply(plan.text)==body
    assert ''.join(model.split_wechat_bubbles(plan.text,[],enabled=True))==body


def test_broken_length_completion_is_not_treated_as_complete_plain_speech(tmp_path):
    import pytest
    from test_reply_delivery import completion
    from xiaomao.model import ModelError
    model,_=completion(tmp_path,'我还有半句话没有',finish='length')
    with pytest.raises(ModelError):model.reply([Message('分享一个点子')])


def test_proactive_signoff_does_not_discard_a_playful_rhetorical_question(tmp_path,monkeypatch):
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    from xiaomao.companion import Candidate
    modules(tmp_path)
    model=ChatModel(Settings(tmp_path))
    monkeypatch.setattr(model,'_companion_json',lambda *a,**k:{'send':True,'text':'这条鱼怎么还舍不得收尾呀？好嘛，我先把尾巴收好 (｡･ω･｡)'})
    result=model.proactive([],Candidate('one','signoff',1,2,0,'2026-11-07'))
    assert result and '尾巴收好' in result


def test_confirmed_history_keeps_delivery_metadata_out_of_assistant_text():
    import json
    from xiaomao.model import _conversation,_reply_history
    outgoing=Message('（尾巴摇摇）那接着聊呀',direction='out',speaker_id='self',message_id='confirmed-out',delivery_status='verified')
    for mode in ('plain','direct','participation','media'):
        turns=_reply_history(_conversation([outgoing]),mode)
        assert turns[0]['role']=='user'
        metadata=json.loads(turns[0]['content'].split('\n',1)[1])
        assert metadata['delivery_status']=='verified'
        assert turns[1]['role']=='assistant'
        body=turns[1]['content'] if mode=='plain' else json.loads(turns[1]['content'])['text']
        assert body==outgoing.text


def test_malformed_model_object_gets_one_immediate_repair_without_exposing_it(tmp_path):
    import json,httpx
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    calls=[]
    answers=iter(['{"text":"过来抱抱"},"emotion_delta":{"joy":2}}', '{"text":"好嘛，抱抱你 (｡･ω･｡)"}'])
    def endpoint(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':next(answers)},'finish_reason':'stop'}]})
    model=ChatModel(Settings(tmp_path,model_base_url='http://localhost'),httpx.Client(transport=httpx.MockTransport(endpoint)))
    assert model.reply([Message('哄哄我',speaker_id='p_'+'a'*16,message_id='new')])=='好嘛，抱抱你 (｡･ω･｡)'
    assert len(calls)==2 and calls[0]['messages'][0]==calls[1]['messages'][0]
    assert '一次格式修复' in str(calls[1]['messages'])


def test_format_repair_is_bounded_to_two_calls(tmp_path):
    import httpx,pytest
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel,ModelError
    calls=[]
    def endpoint(request):
        calls.append(1)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"text":"incomplete'},'finish_reason':'stop'}]})
    model=ChatModel(Settings(tmp_path,model_base_url='http://localhost'),httpx.Client(transport=httpx.MockTransport(endpoint)))
    with pytest.raises(ModelError):model.reply([Message('继续',speaker_id='p_'+'a'*16,message_id='new')])
    assert len(calls)==2


def test_private_explicit_no_followup_does_not_get_a_scheduled_draft(tmp_path,monkeypatch):
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    from xiaomao.companion import Candidate
    cfg=Settings(tmp_path,group='test',conversation_targets={'test':{'kind':'private','id':'wxid_test','account':'wxid_owner'}})
    model=ChatModel(cfg)
    calls=[]
    monkeypatch.setattr(model,'_companion_json',lambda *a,**k:calls.append(1) or {'send':True,'text':'新的话题呀'})
    msg=Message('好啦我得去忙一会儿，不用继续给我发消息啦')
    candidate=Candidate('one','idle',1,2,0,'2026-11-07')
    assert model.proactive([msg],candidate) is None and calls==[]
    assert model.proactive([msg,Message('忙完回来啦，接着聊')],candidate)=='新的话题呀'
    assert model.proactive([msg,Message('[表情]',kind='emoji',message_id='returned')],candidate)=='新的话题呀'
