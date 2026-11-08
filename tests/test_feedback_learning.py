import json
import pytest

from xiaomao.domain import Message
from xiaomao.companion_memory import accept_reflection, recall, source_ref

A, B = 'p_'+'a'*16, 'p_'+'b'*16


def test_preferences_follow_member_across_topics_and_keep_corrections():
    old=Message('你可以多用嘿嘿这个口头禅',speaker_id=A,message_id='old',created_at=100)
    new=Message('后来我觉得有点多，你不要老说嘿嘿',speaker_id=A,message_id='new',created_at=200)
    other=Message('我喜欢你多问问题',speaker_id=B,message_id='other',created_at=210)
    facts,_=accept_reflection({'facts':[{'source':source_ref(m.message_id),'quote':m.text,'kind':'preference'}
                                     for m in (old,new,other)]},[old,new,other],[],300)
    found=recall(facts,'今天下雨了',A,now=400)
    assert new.text in found and old.text in found
    assert found.index(old.text)<found.index(new.text)
    assert other.text not in found
    assert not recall(facts,'今天下雨了','unknown',now=400)
    assert new.text not in recall(facts,'今天下雨了',A,now=150)


def test_unprocessed_feedback_survives_restart_without_promoting_a_fact(tmp_path):
    from test_companion_runtime import build
    from xiaomao.companion_store import CompanionStore
    r,store,queue,now=build(tmp_path)
    try:
        m=Message('你不要老说嘿嘿，语气自然一点',speaker_id=A,message_id='feedback',created_at=now[0])
        r.next_maintenance=now[0]+300
        r.observe(m)
        assert r.next_maintenance<=now[0]+5
        assert not r.notes.data['facts']
        r.notes=CompanionStore(tmp_path,'account/group')
        context=r.enrich([Message('晚饭吃什么',speaker_id=A)])
        assert m.text in context[0].text and '待整理' in context[0].text
        assert m.text not in r.enrich([Message('晚饭吃什么',speaker_id=B)])[0].text
        assert m.text not in r.enrich([Message('晚饭吃什么',speaker_id='unknown')])[0].text
        assert m.text.encode() not in r.notes.path.read_bytes()
        # Once it is in the ordinary context it must not be duplicated.
        assert m.text not in r.enrich([m])[0].text
        now[0]+=8*86400
        assert m.text not in r.enrich([Message('晚饭吃什么',speaker_id=A)])[0].text
    finally:r.close();store.close()


@pytest.mark.parametrize('extra',[
    {'direction':'out'}, {'quoted':True}, {'quality':.2}, {'speaker_id':'unknown'}
])
def test_feedback_not_inferred_from_assistant_quotes_or_unknown_people(tmp_path,extra):
    from test_companion_runtime import build
    r,store,queue,now=build(tmp_path)
    try:
        r.observe(Message('你说话温柔点，别总追问',message_id='bad',created_at=now[0],**{'speaker_id':A,**extra}))
        assert '别总追问' not in r.enrich([Message('今天下雨了',speaker_id=A)])[0].text
    finally:r.close();store.close()


def test_v4_proactive_does_not_force_hurt_from_nonresponse(tmp_path,monkeypatch):
    from xiaomao.persona import save_persona
    from xiaomao.model import ChatModel
    from xiaomao.config import Settings
    from xiaomao.companion import Candidate
    save_persona(tmp_path,'# 大肥鱼 · 情绪陪伴与超自然互动增强模块 V4.0\n没人回复不等于受委屈')
    m=ChatModel(Settings(tmp_path));prompts=[]
    def capture(instructions,*args,**kwargs):
        prompts.append(instructions);return {'send':False,'text':''}
    monkeypatch.setattr(m,'_companion_json',capture)
    assert m.proactive([],Candidate('one','signoff',1,100,0,'2026-11-07')) is None
    assert '带点小委屈' not in prompts[0]
    assert '未回复不代表' in prompts[0]


def test_feedback_extraction_keeps_sources_and_asks_for_behavioral_adaptation(tmp_path,monkeypatch):
    from xiaomao.model import ChatModel
    from xiaomao.config import Settings
    captured=[]
    m=ChatModel(Settings(tmp_path))
    def capture(instructions,*args,**kwargs):
        captured.append(instructions);return {'facts':[]}
    monkeypatch.setattr(m,'_companion_json',capture)
    m.reflect([Message('别老追问',message_id='a',speaker_id=A)])
    assert '表达反馈' in captured[0] and '模型参数' in captured[0]


def test_legacy_module_title_keeps_authored_text_without_injecting_fixed_v4_style(tmp_path,monkeypatch):
    from xiaomao.model import ChatModel
    from xiaomao.config import Settings
    from xiaomao.persona import save_persona
    save_persona(tmp_path,'# 大肥鱼 · 情绪陪伴与超自然互动增强模块 V4.0\n完整原稿')
    model=ChatModel(Settings(tmp_path));captured=[]
    def endpoint(method,path,payload):
        captured.append(payload);return {'choices':[{'message':{'content':'终于搞定啦，替你开心'}}]}
    monkeypatch.setattr(model,'_request',endpoint)
    model.reply([Message('项目终于跑通了！')])
    messages=captured[0]['messages']
    assert '完整原稿' in messages[0]['content']
    dynamic = '\n'.join(m['content'] for m in messages[1:] if m['role']=='system')
    assert 'V4.0 当轮表达' not in dynamic
    assert '花费时长' not in dynamic
    assert '不是把每句改成问句' not in dynamic
