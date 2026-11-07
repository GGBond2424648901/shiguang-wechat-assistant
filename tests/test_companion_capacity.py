import json
from xiaomao.companion_memory import accept_reflection, recall, source_ref
from xiaomao.domain import Message

A, B = 'p_' + 'a'*16, 'p_' + 'b'*16


def full_preferences():
    facts = []
    for i in range(200):
        quote = '我原来特别喜欢黑咖啡' if i>=190 else f'我喜欢唱片第{i}面'
        facts.append({'source':source_ref(str(i)), 'speaker':B if i==199 else A, 'quote':quote,
                      'source_text':quote, 'kind':'preference', 'at':100+i})
    return facts


def test_new_correction_survives_full_protected_memory_without_rewriting_other_speaker():
    previous = full_preferences()
    msg = Message('黑咖啡的话我现在不能喝了。',message_id='correction',speaker_id=A,created_at=500)
    result = {'facts':[{'source':source_ref(msg.message_id),'quote':'我现在不能喝了','kind':'fact'}]}
    facts,_ = accept_reflection(result,[msg],previous,500)
    assert len(facts)==200
    assert previous[-1] in facts
    correction = next(f for f in facts if f['source']==source_ref('correction'))
    assert correction['source_text']==msg.text and correction['speaker']==A
    query = '我原来特别喜欢黑咖啡？'
    recalled = [json.loads(line) for line in recall(facts,query,A,now=501).splitlines()]
    assert len(recalled)<=6 and correction in recalled
    assert msg.text not in recall(facts,query,B,now=501)
    assert msg.text not in recall(facts,query,'unknown',now=501)


def test_fresh_resolution_counts_at_resolution_time_in_a_full_memory():
    facts = full_preferences()
    plan = Message('等下次再商定买唱片的时间',message_id='plan',speaker_id=A,created_at=1)
    facts[0] = {'source':source_ref('plan'),'speaker':A,'quote':plan.text,'source_text':plan.text,
                'kind':'thread','state':'open','at':plan.created_at}
    now = 70*86400
    resolved = Message('唱片已到，不必再准备了',message_id='arrived',speaker_id=A,created_at=now)
    other = Message('外面的雨伞已经收了',message_id='umbrella',speaker_id=A,created_at=now)
    items = [{'source':source_ref('arrived'),'quote':resolved.text,'kind':'thread',
              'state':'closed','resolves':source_ref('plan')},
             {'source':source_ref('umbrella'),'quote':other.text,'kind':'fact'}]
    kept,_ = accept_reflection({'facts':items},[resolved,other],facts,now)
    assert len(kept)==200
    assert any(f['source']==source_ref('umbrella') for f in kept)
    closed = next(f for f in kept if f['source']==source_ref('plan'))
    assert closed['state']=='closed' and closed['resolution']['source']==source_ref('arrived')
    assert resolved.text in recall(kept,'准备好了没',A,now=now+1)


def test_import_of_current_evidence_uses_same_capacity_policy_as_reflection(tmp_path):
    from xiaomao.memory import ConversationMemory
    from xiaomao.companion_store import CompanionStore
    from xiaomao.companion_backup import export_backup, restore_backup
    msg = Message('黑咖啡我现在不能喝了',message_id='imported',speaker_id=A,created_at=600)
    picked = {'facts':[{'source':source_ref('imported'),'quote':msg.text,'kind':'fact'}]}
    existing = full_preferences()
    memory = ConversationMemory(tmp_path/'source','scope')
    notes = CompanionStore(tmp_path/'source','scope')
    notes.data['facts'],_ = accept_reflection(picked,[msg],[],600)
    path = tmp_path/'record.xlc'
    export_backup(path,'test-passphrase',memory,notes)
    dest = ConversationMemory(tmp_path/'dest','scope')
    dest_notes = CompanionStore(tmp_path/'dest','scope')
    dest_notes.data['facts'] = existing
    dest_notes.save()
    restore_backup(path,'test-passphrase',dest,dest_notes)
    live,_ = accept_reflection(picked,[msg],existing,600)
    restored = CompanionStore(tmp_path/'dest','scope').data['facts']
    assert len(restored)==200
    assert restored==live and any(f['source']==source_ref('imported') for f in restored)
