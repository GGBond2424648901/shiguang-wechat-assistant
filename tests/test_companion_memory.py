import json
import pytest
from xiaomao.domain import Message


def test_backup_restore_preserves_target_routine_delivery_ledger(tmp_path):
    from copy import deepcopy
    from xiaomao.memory import ConversationMemory
    from xiaomao.companion_store import CompanionStore
    from xiaomao.companion_backup import export_backup,restore_backup
    source=ConversationMemory(tmp_path/'source','account/a')
    source_notes=CompanionStore(tmp_path/'source','account/a')
    path=tmp_path/'backup.xlc';export_backup(path,'long-password',source,source_notes)
    dest=ConversationMemory(tmp_path/'dest','account/a')
    notes=CompanionStore(tmp_path/'dest','account/a')
    history=[dict(routine_id='morning',day='2026-11-07',status='verified'),
             dict(routine_id='evening',day='2026-11-07',status='unknown')]
    notes.data['planner']['routine_history']=deepcopy(history)
    notes.save()
    restore_backup(path,'long-password',dest,notes)
    assert notes.data['planner']['routine_history']==history
    assert CompanionStore(tmp_path/'dest','account/a').data['planner']['routine_history']==history
    assert not notes.data['preferences']['enabled']


def test_reflection_accepts_only_exact_inbound_sources_and_owns_identity():
    from xiaomao.companion_memory import accept_reflection,source_ref,recall
    a=Message('我喜欢无糖咖啡',speaker_id='p_'+'a'*16,message_id='a',created_at=100)
    b=Message('我下周考数学',speaker_id='p_'+'b'*16,message_id='b',created_at=101)
    out=Message('你可以试试甜咖啡',direction='out',message_id='out')
    result={'facts':[{'source':source_ref('a'),'quote':'喜欢无糖咖啡','kind':'preference','speaker':'forged'},
                     {'source':source_ref('b'),'quote':'下周考物理','kind':'fact'},
                     {'source':source_ref('out'),'quote':'试试甜咖啡','kind':'fact'}],
            'diary':'今天聊到喝咖啡。','sources':[source_ref('a')]}
    facts,diary=accept_reflection(result,[a,b,out],[],1000)
    assert len(facts)==1 and facts[0]['speaker']==a.speaker_id
    assert diary['text']=='今天聊到喝咖啡。'
    assert '喜欢无糖咖啡' in recall(facts,'咖啡怎么喝',a.speaker_id)
    assert not recall(facts,'咖啡怎么喝',b.speaker_id)
    facts2,diary2=accept_reflection({'facts':[],'diary':'没有依据','sources':['wrong']},[a],facts,1001)
    assert len(facts2)==1 and diary2 is None


def test_recall_preserves_negation_and_time_qualifiers_around_excerpt():
    from xiaomao.companion_memory import accept_reflection,source_ref,recall
    m=Message('以前喜欢无糖咖啡，现在我不喜欢无糖咖啡了。',message_id='negated',speaker_id='p_'+'a'*16)
    result={'facts':[{'source':source_ref(m.message_id),'quote':'喜欢无糖咖啡','kind':'preference'}]}
    facts,_=accept_reflection(result,[m],[],100)
    context=recall(facts,'咖啡',m.speaker_id)
    assert '现在我不喜欢无糖咖啡了' in context


def test_password_backup_authenticates_scope_and_preserves_newer_memory(tmp_path):
    from xiaomao.memory import ConversationMemory
    from xiaomao.companion_store import CompanionStore
    from xiaomao.companion_backup import export_backup,preview_backup,restore_backup
    memory=ConversationMemory(tmp_path/'a','account/alice');notes=CompanionStore(tmp_path/'a','account/alice')
    memory.remember(Message('只喜欢咖啡',message_id='one',created_at=100))
    notes.data['preferences']['enabled']=True;notes.save()
    path=tmp_path/'private.xlc'
    export_backup(path,'test-passphrase',memory,notes)
    assert '只喜欢咖啡'.encode() not in path.read_bytes()
    with pytest.raises(ValueError):preview_backup(path,'wrong-password','account/alice')
    with pytest.raises(ValueError):preview_backup(path,'test-passphrase','account/bob')
    preview=preview_backup(path,'test-passphrase','account/alice')
    assert preview['messages']==1 and preview['facts']==0
    dest=ConversationMemory(tmp_path/'b','account/alice');destnotes=CompanionStore(tmp_path/'b','account/alice')
    dest.remember(Message('后来改喝茶了',message_id='two',created_at=200))
    restore_backup(path,'test-passphrase',dest,destnotes)
    assert [m.message_id for m in dest.messages]==['one','two']
    assert not destnotes.data['preferences']['enabled']
    assert not destnotes.data['planner']['unanswered']
    damaged=bytearray(path.read_bytes());damaged[-3]^=1;path.write_bytes(damaged)
    with pytest.raises(ValueError):preview_backup(path,'test-passphrase','account/alice')


def test_daily_snapshot_is_encrypted_bounded_and_contains_no_credentials(tmp_path):
    from xiaomao.memory import ConversationMemory
    from xiaomao.companion_store import CompanionStore
    from xiaomao.companion_backup import daily_snapshot
    m=ConversationMemory(tmp_path,'account/alice');s=CompanionStore(tmp_path,'account/alice')
    m.remember(Message('私密回忆',message_id='a',created_at=100))
    (tmp_path/'credentials.dpapi').write_bytes(b'never-include-this')
    for n in range(10):daily_snapshot(m,s,1800000000+n*86400)
    paths=list((tmp_path/'companion-snapshots'/s.digest).glob('*.dpapi'))
    assert len(paths)==7
    import win32crypt
    plain=win32crypt.CryptUnprotectData(paths[-1].read_bytes(),s.entropy,None,None,1)[1]
    assert b'never-include-this' not in plain
    assert json.loads(plain)['scope']=='account/alice'


def test_restore_cannot_import_before_disabling_proactive(tmp_path,monkeypatch):
    from xiaomao.memory import ConversationMemory
    from xiaomao.companion_store import CompanionStore
    from xiaomao.companion_backup import export_backup,restore_backup
    m=ConversationMemory(tmp_path/'source','account/a');n=CompanionStore(tmp_path/'source','account/a')
    m.remember(Message('导入的消息',message_id='imported',created_at=100))
    path=tmp_path/'backup.xlc';export_backup(path,'long-password',m,n)
    dest=ConversationMemory(tmp_path/'dest','account/a');notes=CompanionStore(tmp_path/'dest','account/a')
    notes.data['preferences']['enabled']=True;notes.save()
    def fail():raise OSError('simulated-disk-error')
    monkeypatch.setattr(notes,'save',fail)
    with pytest.raises(OSError):restore_backup(path,'long-password',dest,notes)
    assert not dest.messages and not dest.path.exists()


def test_late_restore_failure_reports_partial_merge_and_stays_disabled(tmp_path,monkeypatch):
    from xiaomao.memory import ConversationMemory
    from xiaomao.companion_store import CompanionStore
    from xiaomao.companion_backup import export_backup,restore_backup
    m=ConversationMemory(tmp_path/'source','account/a');n=CompanionStore(tmp_path/'source','account/a')
    m.remember(Message('导入的消息',message_id='imported',created_at=100))
    path=tmp_path/'backup.xlc';export_backup(path,'long-password',m,n)
    dest=ConversationMemory(tmp_path/'dest','account/a');notes=CompanionStore(tmp_path/'dest','account/a')
    notes.data['preferences']['enabled']=True;notes.save()
    original=notes.save;calls=[]
    def fail_later():
        calls.append(1)
        if len(calls)==2:raise OSError('simulated-disk-error')
        original()
    monkeypatch.setattr(notes,'save',fail_later)
    with pytest.raises(ValueError,match='部分记忆可能已合并'):restore_backup(path,'long-password',dest,notes)
    assert not CompanionStore(tmp_path/'dest','account/a').data['preferences']['enabled']
    assert ConversationMemory(tmp_path/'dest','account/a').messages[0].message_id=='imported'


def test_model_proactive_can_choose_silence_and_profile_does_not_replace_boundary(tmp_path):
    import httpx
    from xiaomao.model import ChatModel,ModelError
    from xiaomao.config import Settings
    from xiaomao.companion import Candidate
    payloads=[];answers=['{"send":false,"text":""}','{"send":true,"text":"咖啡要凉啦，记得喝呀 (・ω・)"}','{"send":"yes","text":"坏格式"}','接着聊呀']
    def endpoint(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':answers.pop(0)}}]})
    model=ChatModel(Settings(tmp_path,model_base_url='http://127.0.0.1:11434/v1'),httpx.Client(transport=httpx.MockTransport(endpoint)))
    c=Candidate('test','idle',100,220,1,'2026-11-04')
    assert model.proactive([Message('忙一会')],c,'温柔些') is None
    assert '咖啡' in model.proactive([Message('咖啡泡好了')],c,'温柔些')
    with pytest.raises(ModelError):model.proactive([],c,'')
    assert model.reply_profile([Message('你好')],'偶尔颜文字')=='接着聊呀'
    assert any('偶尔颜文字' in m['content'] for m in payloads[-1]['messages'] if m['role']=='system')
    assert '固定运行边界' in payloads[-1]['messages'][0]['content']
    assert all('tools' not in p for p in payloads)


def test_requested_opening_uses_explicit_intent_but_still_respects_model_silence(tmp_path):
    import httpx
    from xiaomao.model import ChatModel
    from xiaomao.config import Settings
    from xiaomao.companion import Candidate
    captured=[]
    def endpoint(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"send":false,"text":""}'}}]})
    model=ChatModel(Settings(tmp_path,model_base_url='http://127.0.0.1:11434/v1'),httpx.Client(transport=httpx.MockTransport(endpoint)))
    assert model.proactive([Message('今天不要再联系我')],Candidate('x','requested',100,220,1,'2026-11-04')) is None
    system='\n'.join(m['content'] for m in captured[0]['messages'] if m['role']=='system')
    assert '没有合适话题就 send=false' not in system
    assert '明确要求停止联系' in system
    assert '普通表情或旧话题结束' in system


def test_proactive_history_is_data_not_assistant_json_examples(tmp_path):
    import httpx
    from xiaomao.model import ChatModel
    from xiaomao.config import Settings
    from xiaomao.companion import Candidate
    captured=[]
    def endpoint(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"send":true,"text":"来聊个小点子呀。"}'}}]})
    model=ChatModel(Settings(tmp_path,model_base_url='http://127.0.0.1:11434/v1'),httpx.Client(transport=httpx.MockTransport(endpoint)))
    context=[Message('之前回答',direction='out',speaker_id='self'),Message('收到',speaker_id='p_'+'a'*16)]
    assert model.proactive(context,Candidate('x','requested',100,220,1,'2026-11-04'))=='来聊个小点子呀。'
    history=[m for m in captured[0]['messages'] if m['role']!='system']
    assert all(m['role']=='user' for m in history)
    assert '助手历史发言' in history[0]['content']
    assert 'self' in history[0]['content'] and 'p_'+'a'*16 in history[1]['content']
