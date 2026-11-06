from dataclasses import replace
from xiaomao.domain import Message
from xiaomao.memory import ConversationMemory
from test_engine import system


def test_engine_preserves_only_trusted_member_labels_in_encrypted_memory(system):
    engine,_,_,clock,_=system
    member='p_'+'a'*16
    incoming=Message('今天去拍照',sender='阿星',message_id='incoming',speaker_id=member,
                     created_at=clock(),sender_source='wechat_nickname',sender_observed_at=clock())
    engine._remember(incoming)
    assert engine.memory.member_names()=={member:'阿星'}
    assert engine.memory.messages[-1].sender=='阿星'
    engine._remember(replace(incoming,message_id='fake',sender='伪造昵称',sender_source=''))
    assert engine.memory.member_names()=={member:'阿星'}
    # The trusted roster may label this stable member, never the forged label.
    assert engine.memory.messages[-1].sender=='阿星'
    reopened=ConversationMemory(engine.settings.runtime,engine.settings.memory_scope)
    assert reopened.member_names()=={member:'阿星'}


def test_quiet_rename_refreshes_known_member_outside_recent_message_window(tmp_path,monkeypatch):
    import sqlite3
    from test_member_names import _hub
    hub,path=_hub(tmp_path,monkeypatch)
    try:
        feed=hub.feed('社团')
        message_path=path.parent.parent/'message/message_0.db'
        with sqlite3.connect(message_path) as conn:
            conn.executemany(f'INSERT INTO [{feed.table}] VALUES(?,?,1,100,?,?,?)',
                [(n,n,n,3,'其他成员的聊天') for n in range(5,40)])
        initial=feed.poll();alpha=initial.history[0].speaker_id
        memory=ConversationMemory(hub.settings.runtime,hub.settings.memory_scope)
        for message in initial.history:memory.remember(message)
        assert memory.member_names()[alpha]=='南风'
        with sqlite3.connect(path) as conn:
            conn.execute('UPDATE contact SET remark=? WHERE username=?',('改名阿南','wxid_test_alpha'))
        batch=feed.poll()
        assert not batch.new and len(batch.history)==24
        assert all(m.speaker_id!=alpha for m in batch.history)
        memory.refresh_members(batch.history)
        assert memory.member_names()[alpha]=='南风'
        profiles=feed.member_profiles(memory.known_member_ids())
        assert all(k.startswith('p_') for k in profiles)
        assert memory.refresh_member_profiles(profiles)
        assert memory.member_names()[alpha]=='改名阿南'
        encrypted=memory.path.read_bytes()
        assert not memory.refresh_member_profiles(feed.member_profiles(memory.known_member_ids()))
        assert memory.path.read_bytes()==encrypted
        assert not memory.refresh_member_profiles({'p_'+'f'*16:{'name':'其他会话的人','source':'wechat_nickname','observed_at':1}})
        assert '其他会话的人' not in memory.member_names().values()
    finally:hub.close()
