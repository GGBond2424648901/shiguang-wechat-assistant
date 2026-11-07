from dataclasses import replace
import json
import sqlite3


def test_database_keeps_stable_distinct_speakers_without_exposing_names(tmp_path):
    from test_database import database
    from xiaomao.database import DatabaseFeed, MemoryDatabase
    path=tmp_path/'message_0.db'
    database(path,[(1,11,100,1,2,'甲的话'),(2,12,100,2,3,'乙的话'),(3,13,100,3,2,'甲的追问')])
    with sqlite3.connect(path) as c:c.execute('INSERT INTO Name2Id VALUES(?)',('other_friend',))
    source=MemoryDatabase(path)
    def read(room):
        return DatabaseFeed(lambda:[source],room,'wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:101).poll().history
    first=read('group@chatroom');again=read('group@chatroom');elsewhere=read('elsewhere@chatroom')
    assert first[0].speaker_id and first[0].speaker_id==first[2].speaker_id==again[0].speaker_id
    assert first[0].speaker_id!=first[1].speaker_id and first[0].speaker_id!=elsewhere[0].speaker_id
    assert all(not m.sender and 'friend' not in m.speaker_id for m in first)
    source.close()


def test_memory_enriches_existing_receipt_and_retains_association_on_reload(tmp_path):
    from xiaomao.domain import Message
    from xiaomao.memory import ConversationMemory
    memory=ConversationMemory(tmp_path,'群')
    incoming=Message('我在上海',speaker_id='p_'+'a'*16,message_id='in1')
    outgoing=Message('记住啦',direction='out',message_id='out1')
    memory.remember(incoming);memory.remember(outgoing)
    assert memory.remember(replace(outgoing,reply_to_id='in1',reply_to_speaker=incoming.speaker_id))
    memory.remember(outgoing)  # Later baseline must not erase the association.
    loaded=ConversationMemory(tmp_path,'群')
    assert len(loaded.messages)==2
    assert loaded.messages[-1].reply_to_speaker==incoming.speaker_id
    assert loaded.messages[-1].reply_to_id=='in1'


def test_interleaved_model_history_attributes_member_and_answer_but_not_nickname(tmp_path):
    import httpx
    from xiaomao.model import ChatModel
    from xiaomao.domain import Message
    from xiaomao.config import Settings
    payloads=[]
    def endpoint(request):
        payloads.append(json.loads(request.content))
        fields = {'summary':'成员甲在上海，乙在北京。'} if len(payloads)>1 else {'text':'你说的是上海呀 (´▽｀)'}
        content=json.dumps(fields,ensure_ascii=False)
        return httpx.Response(200,json={'choices':[{'message':{'content':content}}]})
    a,b='p_'+'a'*16,'p_'+'b'*16
    turns=[Message('我在上海','不可当正文的昵称',speaker_id=a,message_id='1'),
           Message('我在北京',speaker_id=b,message_id='2'),
           Message('北京今天凉快',direction='out',reply_to_speaker=b,reply_to_id='2'),
           Message('我刚才说哪儿来着？',speaker_id=a,message_id='3')]
    model=ChatModel(Settings(tmp_path,model_base_url='http://127.0.0.1'),client=httpx.Client(transport=httpx.MockTransport(endpoint)))
    model.reply(turns);sent=payloads[-1]['messages']
    latest=json.loads(sent[-1]['content'])
    assert latest['speaker']==a and latest['body']=='我刚才说哪儿来着？'
    answer=next(m for m in sent if '北京今天凉快' in m['content'])
    assert answer['role']=='assistant' and json.loads(answer['content'])=={'text':'北京今天凉快'}
    relation=sent[sent.index(answer)-1]
    assert relation['role']=='user' and '关联信息' in relation['content']
    assert json.loads(relation['content'].split('\n',1)[1])['reply_to_speaker']==b
    assert '不可当正文的昵称' not in json.dumps(sent,ensure_ascii=False)
    assert a in sent[-2]['content'] and '不同成员' in sent[-2]['content']
    model.compact('',turns)
    assert '成员' in payloads[-1]['messages'][0]['content']


def test_legacy_memory_leaves_unknown_speaker_unassigned(tmp_path):
    from xiaomao.memory import ConversationMemory
    message=ConversationMemory._decode_message(dict(text='旧记录',direction='in',kind='text',message_id='old',created_at=1,image_data=''))
    assert message.speaker_id=='' and message.reply_to_id==''


def test_name_to_identity_migration_keeps_summary_and_does_not_overwrite_newer_memory(tmp_path):
    from xiaomao.memory import ConversationMemory
    from xiaomao.domain import Message
    old=ConversationMemory(tmp_path,'群名');old.remember(Message('前文',message_id='old'))
    old._save(old.messages,'旧摘要',old._seen);old.summary='旧摘要'
    new=ConversationMemory(tmp_path,'account/room@chatroom')
    assert new.import_legacy(old)
    new.remember(Message('新记录',message_id='new'))
    assert not new.import_legacy(old)
    loaded=ConversationMemory(tmp_path,'account/room@chatroom')
    assert [m.text for m in loaded.messages]==['前文','新记录'] and loaded.summary=='旧摘要'
