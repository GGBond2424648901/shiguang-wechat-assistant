import json
import httpx
import pytest


@pytest.mark.parametrize('template',['保持口语。','保持口语。{persona}'])
def test_edited_persona_reaches_replies_and_proactive_openings_on_the_next_turn(tmp_path,template):
    from test_reply_delivery import completion
    from xiaomao.companion import Candidate
    from xiaomao.domain import Message
    from xiaomao.persona import save_persona
    folder=tmp_path/'config/presets';folder.mkdir(parents=True)
    (folder/'xiaoling.yml').write_text(f'prompts:\n  - role: system\n    content: "{template}"\n',encoding='utf-8')
    model,payloads=completion(tmp_path,'{"send":true,"text":"行呀。"}')
    opener=Candidate('test','idle',100,220,1,'2026-11-05')
    for persona in ('小玲喜欢拍雨夜的倒影。','小玲喜欢拍晴天的云。'):
        save_persona(tmp_path,persona)
        model.participate([Message('有什么想法？')])
        model.proactive([Message('刚聊过摄影')],opener)
        for request in payloads[-2:]:
            system=request['messages'][0]['content']
            assert system.count(persona)==1 and '保持口语。' in system
    assert all('雨夜的倒影' not in p['messages'][0]['content'] for p in payloads[-2:])


def test_chatluna_yaml_prompts_and_roles_are_used_without_executing_templates(tmp_path):
    from xiaomao.presets import load_preset
    folder=tmp_path/'config/presets';folder.mkdir(parents=True)
    (folder/'xiaoling.yml').write_text('''keywords: [小玲]
prompts:
  - role: system
    content: "你是{name}。{persona}"
  - role: user
    content: "在吗"
  - role: assistant
    content: "耳朵竖起来啦，怎么啦主人？"
format_user_prompt: "{sender}说：{prompt}"
''',encoding='utf-8')
    preset=load_preset(tmp_path)
    assert preset.messages('小玲','温柔活泼')[0]['content']=='你是小玲。温柔活泼'
    assert preset.messages('小玲','温柔活泼')[-1]['role']=='assistant'
    assert preset.user_message('朋友','{__import__(x)}')=='朋友说：{__import__(x)}'


@pytest.mark.parametrize('document',["!!python/object/apply:os.system [echo nope]",'prompts: [{role: tool, content: x}]',"prompts: [{role: system, content: 'sk-"+'a'*32+"'}]"])
def test_preset_rejects_unsafe_or_invalid_documents(tmp_path,document):
    from xiaomao.presets import load_preset
    folder=tmp_path/'config/presets';folder.mkdir(parents=True)
    (folder/'xiaoling.yml').write_text(document,encoding='utf-8')
    with pytest.raises(ValueError):load_preset(tmp_path)


def test_model_history_uses_real_user_assistant_roles_and_targets_latest_input(tmp_path):
    from xiaomao.model import ChatModel
    from xiaomao.config import Settings
    from xiaomao.domain import Message
    folder=tmp_path/'config/presets';folder.mkdir(parents=True)
    (folder/'xiaoling.yml').write_text(json.dumps({'prompts':[
        {'role':'system','content':'{persona}'},
        {'role':'user','content':'周末去拍照吗？'},
        {'role':'assistant','content':'可以去公园试试。'},
    ]},ensure_ascii=False),encoding='utf-8')
    captured=[]
    def endpoint(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'今晚吃番茄炒蛋怎么样，喵？'}}]})
    model=ChatModel(Settings(tmp_path,model_base_url='http://127.0.0.1:11434/v1'),client=httpx.Client(transport=httpx.MockTransport(endpoint)))
    model.reply([Message('你好','朋友'),Message('来啦，怎么啦主人？','','out'),Message('今晚吃什么','朋友')])
    messages=[m for m in captured[0]['messages'] if m['role']!='system']
    assert [m['role'] for m in messages[-3:]]==['user','assistant','user']
    assert messages[-2]['content']=='来啦，怎么啦主人？'
    assert '今晚吃什么' in messages[-1]['content']
    assert len(messages)==3
    assert any(m['role']=='system' and '虚构示例' in m['content'] for m in captured[0]['messages'])


def test_transient_model_errors_are_marked_retryable_without_server_body(tmp_path):
    from xiaomao.model import ChatModel,ModelError
    from xiaomao.config import Settings
    model=ChatModel(Settings(tmp_path,model_base_url='http://127.0.0.1:11434/v1'),client=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(503,text='private echo'))))
    with pytest.raises(ModelError) as caught:model.models()
    assert caught.value.retryable and 'private echo' not in str(caught.value)
