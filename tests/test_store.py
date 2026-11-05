def test_conversation_is_encrypted_isolated_and_reopened_without_nicknames(tmp_path):
    from xiaomao.store import StateStore
    from xiaomao.domain import Message
    store=StateStore(tmp_path)
    store.save_context('甲群',[Message('这是自己开发的小工具','昵称甲'),Message('很可爱呀','','out')],100)
    assert store.load_context('乙群',101)==[]
    store.close();store=StateStore(tmp_path)
    messages=store.load_context('甲群',101)
    assert [(m.text,m.direction,m.sender) for m in messages]==[('这是自己开发的小工具','in',''),('很可爱呀','out','')]
    assert all('这是自己开发的小工具'.encode() not in p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
    assert store.load_context('甲群',100+86401)==[] and store.load_context('甲群',99)==[]
    next(tmp_path.glob('conversation-*.dpapi')).write_bytes(b'corrupt')
    assert store.load_context('甲群',101)==[]
    store.close()


def test_restart_never_requeues_unfinished_or_unknown_sends(tmp_path):
    from xiaomao.store import StateStore
    store = StateStore(tmp_path)
    staged = store.create("salted-digest-a", 100)
    sending = store.create("salted-digest-b", 100)
    store.update(staged, "staged")
    store.mark_sending(sending, 100)
    store.close()
    reopened = StateStore(tmp_path)
    assert reopened.recover() == 1
    assert reopened.get(staged)["state"] == "skipped"
    assert reopened.get(sending)["state"] == "unknown"
    assert reopened.allow_send(110, 30, 6)[0] is False
    reopened.close()


def test_rate_limit_survives_restart_and_counts_unknown_attempts(tmp_path):
    from xiaomao.store import StateStore
    store = StateStore(tmp_path)
    for now in [0, 40, 80, 120, 160, 200]:
        task = store.create(f"digest-{now}", now)
        store.mark_sending(task, now)
    assert store.allow_send(231, 30, 6)[0] is False
    assert store.allow_send(3601, 30, 6)[0] is True
    assert store.allow_send(100, 30, 6)[0] is False
    store.close()


def test_database_never_contains_message_or_reply_body(tmp_path):
    from xiaomao.store import StateStore
    store = StateStore(tmp_path)
    digest = store.fingerprint("小猫，私密正文")
    task = store.create(digest, 100)
    store.update(task, "skipped", "expired")
    store.close()
    contents = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
    assert "私密正文".encode() not in contents
    assert "小猫".encode() not in contents


def test_receipt_is_persistent_group_bound_and_rejects_old_matching_message(tmp_path):
    from xiaomao.store import StateStore
    from xiaomao.domain import Message
    store=StateStore(tmp_path);task=store.create('incoming',100)
    store.mark_sending(task,100,group='甲群',reply='唯一的回复',baseline_ids=['old'])
    store.close();store=StateStore(tmp_path)
    assert not store.confirm_receipt(task,'乙群',Message('唯一的回复',direction='out',message_id='new',created_at=101))
    assert not store.confirm_receipt(task,'甲群',Message('唯一的回复',direction='out',message_id='old',created_at=101))
    assert not store.confirm_receipt(task,'甲群',Message('不一样',direction='out',message_id='new',created_at=101))
    assert store.confirm_receipt(task,'甲群',Message('唯一的回复',direction='out',message_id='new',created_at=101))
    assert store.get(task)['state']=='verified'
    store.close()
    assert all('唯一的回复'.encode() not in p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
