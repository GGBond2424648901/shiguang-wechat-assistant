import pytest

from xiaomao.domain import Message


def memory(tmp_path, group='群甲', **kwargs):
    from xiaomao.memory import ConversationMemory
    return ConversationMemory(tmp_path, group, **kwargs)


def test_memory_survives_restart_and_preserves_id_direction_kind_and_image(tmp_path):
    instance = memory(tmp_path, clock=lambda: 100)
    text = Message('群里秘密的项目名字', message_id='db:1', created_at=99)
    photo = Message('静态表情', kind='emoji', message_id='db:2', created_at=100)
    object.__setattr__(photo, 'image_data', b'\x89PNG\r\n\x1a\nprivate-image')
    reply = Message('收到了', direction='out', message_id='db:3', created_at=101)
    for item in (text, photo, reply):
        assert instance.remember(item)
    recovered = memory(tmp_path, clock=lambda: 100+30*86400)
    assert recovered.messages == [text, photo, reply]
    assert recovered.messages[1].image_data == photo.image_data
    assert not recovered.remember(text)
    assert memory(tmp_path, group='群乙').messages == []
    disk = b''.join(path.read_bytes() for path in tmp_path.iterdir() if path.is_file())
    assert '群里秘密的项目名字'.encode() not in disk
    assert b'private-image' not in disk
    assert '群甲'.encode() not in disk


def test_memory_compacts_older_history_and_preserves_recent_raw_messages(tmp_path):
    instance = memory(tmp_path)
    originals = [Message('消息'+str(i), message_id='db:'+str(i)) for i in range(97)]
    for item in originals:
        instance.remember(item)
    class Summarizer:
        def compact(self, previous, older):
            assert previous == ''
            assert older == originals[:73]
            return '早先讨论项目月光；发布日期未知。'
    assert instance.compact_if_needed(Summarizer())
    assert instance.messages == originals[73:]
    snapshot = instance.context()
    assert snapshot[0].kind == 'summary'
    assert snapshot[0].direction == 'in'
    assert snapshot[0].text == '早先讨论项目月光；发布日期未知。'
    assert snapshot[1:] == originals[73:]
    assert memory(tmp_path).summary == snapshot[0].text
    assert not memory(tmp_path).remember(originals[0])
    instance.remember(Message('更晚的消息', message_id='db:97'))
    assert snapshot[1:] == originals[73:]


def test_failed_compaction_preserves_raw_history_for_next_retry(tmp_path):
    from xiaomao.model import ModelError
    instance = memory(tmp_path)
    originals = [Message('这是需要保留的上下文。'*70, message_id=str(i)) for i in range(30)]
    for item in originals:
        instance.remember(item)
    class Failing:
        def compact(self, previous, older):
            raise ModelError('网络暂不可用', retryable=True)
    assert not instance.compact_if_needed(Failing())
    assert instance.messages == originals
    assert memory(tmp_path).messages == originals
    class Recovery:
        def compact(self, previous, older):
            return '前六条消息讨论同一事项。'
    assert instance.compact_if_needed(Recovery())
    assert len(memory(tmp_path).messages) == 24


def test_stable_ids_dedupe_text_and_allow_image_upgrade_once(tmp_path):
    instance = memory(tmp_path)
    unresolved = Message('图片', kind='image', message_id='db:42')
    resolved = Message('图片', kind='image', message_id='db:42')
    object.__setattr__(resolved, 'image_data', b'\x89PNG\r\n\x1a\nframe')
    assert instance.remember(unresolved)
    assert instance.remember(resolved)
    assert not instance.remember(resolved)
    assert len(memory(tmp_path).messages) == 1
    assert memory(tmp_path).messages[0].image_data == resolved.image_data


def test_compaction_includes_previous_summary_and_ignores_newer_additions(tmp_path):
    instance = memory(tmp_path)
    originals = [Message('消息'+str(i), message_id=str(i)) for i in range(97)]
    for item in originals:
        instance.remember(item)
    class First:
        def compact(self, previous, older):
            instance.remember(Message('并发到达', message_id='new'))
            return '第一轮摘要'
    assert instance.compact_if_needed(First())
    assert instance.messages == originals[73:] + [Message('并发到达', message_id='new')]
    class Second:
        def compact(self, previous, older):
            assert previous == '第一轮摘要'
            assert older == [originals[73]]
            return '第二轮摘要'
    assert instance.compact_if_needed(Second(), force=True)
    assert memory(tmp_path).summary == '第二轮摘要'


def test_encrypted_file_cannot_be_loaded_for_another_group(tmp_path):
    from xiaomao.memory import MemoryPersistenceError
    first, second = memory(tmp_path), memory(tmp_path, group='群乙')
    first.remember(Message('群甲秘密', message_id='1'))
    second.path.write_bytes(first.path.read_bytes())
    with pytest.raises(MemoryPersistenceError):
        memory(tmp_path, group='群乙')


def test_failed_save_leaves_prior_memory_unchanged(tmp_path, monkeypatch):
    from xiaomao.memory import MemoryPersistenceError
    import os
    instance = memory(tmp_path)
    instance.remember(Message('已保存', message_id='1'))
    def fail_replace(*args):
        raise OSError('server-secret')
    monkeypatch.setattr(os, 'replace', fail_replace)
    with pytest.raises(MemoryPersistenceError) as caught:
        instance.remember(Message('未保存', message_id='2'))
    assert 'server-secret' not in str(caught.value)
    assert [m.text for m in instance.messages] == ['已保存']
    assert [m.text for m in memory(tmp_path).messages] == ['已保存']


def test_memory_strips_nickname_media_xml_and_media_key(tmp_path):
    import json
    import win32crypt
    instance = memory(tmp_path)
    instance.remember(Message('图片', sender='不应作为问题的昵称', kind='image', message_id='1',
                              media_raw='<msg>private-media-token</msg>', media_key='private-key'))
    saved = instance.messages[0]
    assert saved.sender == '' and saved.media_raw == '' and saved.media_key == ''
    _, plain = win32crypt.CryptUnprotectData(instance.path.read_bytes(), instance._entropy, None, None, 1)
    record = json.loads(plain)['messages'][0]
    assert 'sender' not in record and 'media_raw' not in record and 'media_key' not in record
    assert b'private-media-token' not in plain and b'private-key' not in plain


def test_bounded_storage_preserves_prior_file_on_capacity_failure(tmp_path, monkeypatch):
    from xiaomao.memory import MemoryPersistenceError
    instance = memory(tmp_path)
    instance.remember(Message('已存事实', message_id='1'))
    previous = instance.path.read_bytes()
    monkeypatch.setattr(instance, 'MAX_FILE_BYTES', 8192)
    with pytest.raises(MemoryPersistenceError):
        instance.remember(Message('x'*10000, message_id='2'))
    assert instance.path.read_bytes() == previous
    assert [item.text for item in instance.messages] == ['已存事实']


def test_oversized_image_is_rejected_without_dropping_prior_history(tmp_path):
    from xiaomao.memory import MemoryPersistenceError
    instance = memory(tmp_path)
    instance.remember(Message('先前事实', message_id='1'))
    with pytest.raises(MemoryPersistenceError):
        instance.remember(Message('超大图片', kind='image', message_id='2', image_data=b'x'*(4*1024*1024+1)))
    assert [item.text for item in instance.messages] == ['先前事实']


def test_image_volume_triggers_summary_even_when_text_is_short(tmp_path):
    instance = memory(tmp_path)
    png = b'\x89PNG\r\n\x1a\n'+b'x'*(512*1024)
    for i in range(25):
        instance.remember(Message('图', kind='image', message_id=str(i), image_data=png))
    class Summarizer:
        def compact(self, previous, older):
            assert len(older) == 1 and older[0].image_data == png
            return '首张图像的可见内容。'
    assert instance.compact_if_needed(Summarizer())
    assert len(instance.messages) == 24


def test_image_upgrade_during_compaction_retains_material_for_fresh_summary(tmp_path):
    instance = memory(tmp_path)
    instance.remember(Message('图片', kind='image', message_id='image:1'))
    for i in range(96):
        instance.remember(Message('后续消息'+str(i), message_id=str(i)))
    class Updating:
        def compact(self, previous, older):
            assert older[0].image_data == b''
            instance.remember(Message('图片', kind='image', message_id='image:1', image_data=b'\x89PNG\r\n\x1a\nnew-image'))
            return '旧材料没有图片可看。'
    assert not instance.compact_if_needed(Updating())
    assert instance.summary == ''
    assert len(instance.messages) == 97
    assert instance.messages[0].image_data == b'\x89PNG\r\n\x1a\nnew-image'
    assert memory(tmp_path).messages[0].image_data == instance.messages[0].image_data
