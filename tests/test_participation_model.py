import json
import httpx
import pytest
from xiaomao.config import Settings
from xiaomao.domain import Message
from xiaomao.model import ChatModel, ModelError


def model_reply(tmp_path, content, *, finish_reason='stop'):
    captured=[]
    def endpoint(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':finish_reason,'message':{'content':content}}]})
    return ChatModel(Settings(tmp_path,model_base_url='http://127.0.0.1'),
        client=httpx.Client(transport=httpx.MockTransport(endpoint))),captured


def test_participation_decides_and_composes_in_one_request_with_latest_member(tmp_path):
    model,requests=model_reply(tmp_path,json.dumps({'send':True,'text':'0会留下，只去掉None。'}))
    a,b='p_'+'a'*16,'p_'+'b'*16
    result=model.participate([Message('我是甲',speaker_id=a),Message('0也保留吗？',speaker_id=b)])
    assert result=='0会留下，只去掉None。'
    assert len(requests)==1 and requests[0]['response_format']=={'type':'json_object'}
    assert json.loads(requests[0]['messages'][-1]['content'])['speaker']==b


def test_participation_can_decline_without_returning_internal_text(tmp_path):
    model,requests=model_reply(tmp_path,json.dumps({'send':False,'text':'不应发出的草稿'}))
    assert model.participate([Message('嗯嗯')]) is None
    assert len(requests)==1


@pytest.mark.parametrize('content',[
    '{"send":"false","text":"错误"}',
    '{"send":true,"text":""}',
    '{"send":true,"text":"正文","tool":"execute"}',
    '普通字符串不应被当作肯定发送',
])
def test_participation_invalid_decision_never_becomes_chat_text(tmp_path,content):
    model,_=model_reply(tmp_path,content)
    with pytest.raises(ModelError):model.participate([Message('你好')])


def test_participation_truncated_json_does_not_send(tmp_path):
    model,_=model_reply(tmp_path,'{"send":true,"text":"不能发送"}',finish_reason='length')
    with pytest.raises(ModelError):model.participate([Message('你好')])


def test_participation_history_uses_decision_format_but_keeps_reply_attribution(tmp_path):
    model,requests=model_reply(tmp_path,'{"send":true,"text":"0会保留。"}')
    speaker='p_'+'a'*16
    model.participate([Message('只排除None',direction='out',speaker_id='self',
        message_id='out1',reply_to_id='in1',reply_to_speaker=speaker),
        Message('那0呢？',speaker_id=speaker,message_id='in2')])
    turns=requests[0]['messages']
    assistant_turns=[json.loads(t['content']) for t in turns if t['role']=='assistant']
    assert all(set(t)=={'send','text'} and type(t['send']) is bool for t in assistant_turns)
    assert assistant_turns[-1]=={'send':True,'text':'只排除None'}
    assert any('reply_to_speaker' in t['content'] and speaker in t['content'] for t in turns if t['role']=='user')


def test_deepseek_whitespace_output_is_retryable_not_a_silence_decision(tmp_path):
    model,_=model_reply(tmp_path,' '*60)
    with pytest.raises(ModelError) as failure:model.participate([Message('需要回应的问题')])
    assert failure.value.retryable


@pytest.mark.parametrize('body',[
    '{"send":true,"text":"正文"}',
    '```json\n{"send":false,"text":""}\n```',
    json.dumps('{"send":true,"text":"正文"}'),
    json.dumps({'speaker':'self','body':'{"send":true,"text":"正文"}'}),
])
def test_nested_participation_envelope_is_not_sent(tmp_path,body):
    model,_=model_reply(tmp_path,json.dumps({'send':True,'text':body}))
    with pytest.raises(ModelError) as failure:model.participate([Message('你好')])
    assert failure.value.retryable


def test_participation_does_not_forbid_legitimate_json_examples(tmp_path):
    body='{"enabled":true,"text":"示例数据"}'
    model,_=model_reply(tmp_path,json.dumps({'send':True,'text':body}))
    assert model.participate([Message('给个JSON示例')])==body


def test_regular_reply_still_returns_plain_text_without_decision_format(tmp_path):
    model,requests=model_reply(tmp_path,'在呢。')
    assert model.reply([Message('@小玲 在吗')])=='在呢。'
    assert 'response_format' not in requests[0]
