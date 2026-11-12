import json
import threading
import time
from dataclasses import replace
from unittest.mock import patch

import pytest

from xiaomao.config import Settings
from xiaomao.desktop_persona import DesktopPersona
from xiaomao.domain import Message


def studio_class():
    from xiaomao.desktop_studio import DesktopStudio
    return DesktopStudio


class Client:
    def __init__(self):
        self.calls = []
        self.mode = 'auto'

    def status(self):
        return {'account_scope': 'account', 'group_names': ['群'],
                'groups': [{'name': '群', 'mode': self.mode}]}

    def pending(self):
        return {'tasks': []}

    def queue_outbound(self, *args):
        self.calls.append(args)
        return {'queued': True, 'id': 'task1'}


@pytest.fixture
def env(tmp_path):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/persona.md').write_text('原始人格完整原文', encoding='utf-8')
    cfg = Settings(tmp_path, group='群', conversation_targets={'群': {'account': 'account', 'id': 'peer@chatroom', 'kind': 'group'}})
    with patch('xiaomao.config.Settings.load', return_value=cfg):
        yield tmp_path, cfg, Client()


def finish(studio, job):
    for _ in range(200):
        state = studio.state(job['job'])
        if state['status'] not in ('queued', 'running'):
            return state
        time.sleep(.01)
    pytest.fail('job did not finish')


def test_draft_compiles_complete_and_multiturn_isolated(env):
    root, cfg, client = env
    persona = DesktopPersona(root).read()
    rows = [{**persona['modules'][0], 'text': '试聊草稿完整内容'}]
    seen = []

    def reply(model, messages):
        seen.append((model._compiled_persona().text, list(messages), model.settings.root))
        return '试聊回答'

    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with patch('xiaomao.model.ChatModel.reply', reply):
        studio = studio_class()(root, client)
        first = finish(studio, studio.chat(persona['revision'], rows, text='第一问'))
        second = finish(studio, studio.chat(persona['revision'], rows, text='第二问', conversation=first['conversation']))
    assert second['status'] == 'completed'
    assert '试聊草稿完整内容' in seen[0][0] and '原始人格完整原文' not in seen[0][0]
    assert [m.text for m in seen[1][1]] == ['第一问', '试聊回答', '第二问']
    assert all(not path.exists() for _, _, path in seen)
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before
    assert not client.calls


@pytest.mark.parametrize('reverse', [False, True])
def test_whole_persona_uses_every_enabled_module_without_order_priority(env, reverse):
    from xiaomao.model import ChatModel
    from xiaomao.persona_modules import PersonaModule, save_modules
    root, cfg, client = env
    sources = [PersonaModule('identity', '身份', '身份原文：一只喜欢蓝色相机的小鱼。'),
               PersonaModule('voice', '语气', '语气原文：开心时会俏皮地接话。'),
               PersonaModule('companion', '陪伴', '陪伴原文：难过时先听对方说完。')]
    if reverse:
        sources.reverse()
    sources.append(PersonaModule('disabled', '停用设定', '停用原文：不应进入本轮人格。', enabled=False))
    save_modules(root, sources, title='完整人格', expected_revision=None)
    persona = DesktopPersona(root).read()
    formal = ChatModel(cfg)._compiled_persona().text
    seen = []
    before = (root / 'config/persona_modules.json').read_bytes()

    def reply(model, messages):
        seen.append(model._compiled_persona().text)
        return '整套人格试聊回答'

    with patch('xiaomao.model.ChatModel.reply', reply):
        studio = studio_class()(root, client)
        state = finish(studio, studio.chat(persona['revision'], persona['modules'], text='来聊聊今天。'))
    assert state['status'] == 'completed'
    assert state['result']['enabledModules'] == [row.title for row in sources if row.enabled]
    assert formal == seen[0]
    for prompt in (formal, seen[0]):
        assert '排列仅用于整理，不表示权重或优先级' in prompt
        assert all(row.text in prompt for row in sources if row.enabled)
        assert sources[-1].text not in prompt
        assert [prompt.index(row.text) for row in sources if row.enabled] == sorted(prompt.index(row.text) for row in sources if row.enabled)
    assert (root / 'config/persona_modules.json').read_bytes() == before
    assert not client.calls


def test_cancel_and_new_draft_fence_late_reply(env):
    root, cfg, client = env
    p = DesktopPersona(root).read()
    entered, release = threading.Event(), threading.Event()

    def reply(model, messages):
        entered.set(); release.wait(2)
        return '不得显示的旧回复'

    with patch('xiaomao.model.ChatModel.reply', reply):
        studio = studio_class()(root, client)
        job = studio.chat(p['revision'], p['modules'], text='问题')
        assert entered.wait(1)
        assert studio.cancel(job['job'])['status'] == 'canceled'
        release.set()
        time.sleep(.05)
        assert studio.state(job['job'])['status'] == 'canceled'
        assert 'result' not in studio.state(job['job'])


def test_account_switch_invalidates_model_completion(env):
    root, cfg, client = env
    p = DesktopPersona(root).read()
    entered, release = threading.Event(), threading.Event()
    def reply(model, messages):
        entered.set(); release.wait(2); return '旧账号回答'
    with patch('xiaomao.model.ChatModel.reply', reply):
        studio = studio_class()(root, client)
        job = studio.chat(p['revision'], p['modules'], text='问题')
        assert entered.wait(1)
        with patch('xiaomao.config.Settings.load', return_value=replace(cfg, calibration_account='', conversation_targets={})):
            release.set()
            assert finish(studio, job)['status'] == 'invalidated'


def test_summary_range_malformed_and_idempotent_guarded_send(env):
    root, cfg, client = env
    selected = cfg.conversation_pause_key('群')
    messages = [Message('范围外', created_at=1), Message('周五开会', created_at=1735804800), Message('好的', created_at=1735804900)]
    class Memory:
        def __init__(self, runtime, scope, **kwargs):
            assert kwargs['read_only'] is True and scope == cfg.memory_scope
            self.messages = messages
    cfg.runtime.mkdir()
    (cfg.runtime / 'fingerprint.key').write_bytes(b'0' * 32)
    captured = []
    def summary(model, instructions, sources, **kwargs):
        captured.extend(sources)
        return {'highlights': ['讨论周五会议'], 'tasks': [], 'dates': ['周五（原文，具体日期未明确）']}
    with patch('xiaomao.desktop_studio.ConversationMemory', Memory), patch('xiaomao.model.ChatModel._companion_json', summary):
        studio = studio_class()(root, client)
        result = finish(studio, studio.summary(selected, '2025-01-02T16:00', '2025-01-02T17:00'))
        assert result['result']['messageCount'] == 2
        assert len(captured) == 2 and all(m.text != '范围外' for m in captured)
        client.mode = 'paused'
        with pytest.raises(ValueError): studio.send_summary(result['job'], '预览总结')
        client.mode = 'auto'
        studio.send_summary(result['job'], '编辑后的准确总结')
        studio.send_summary(result['job'], '编辑后的准确总结')
        assert len(client.calls) == 1 and client.calls[0][2] == '编辑后的准确总结'
        studio.cancel(result['job'])
        with pytest.raises(ValueError): studio.send_summary(result['job'], '预览总结')


def test_readonly_rejects_jobs_and_summary_bad_output(env):
    root, cfg, client = env
    studio = studio_class()(root, client, read_only=True)
    with pytest.raises(ValueError): studio.chat('', [], text='hello')
    with pytest.raises(ValueError): studio.summary('x', '2025-01-01', '2025-01-02')


def test_summary_rejects_unverified_target_and_malformed_model_data(env):
    root, cfg, client = env
    studio = studio_class()(root, client)
    with pytest.raises(ValueError): studio.summary('unverified', '2025-01-01', '2025-01-02')
    cfg.runtime.mkdir()
    (cfg.runtime / 'fingerprint.key').write_bytes(b'0' * 32)
    class Memory:
        def __init__(self, *args, **kwargs): self.messages = [Message('hello', created_at=1735804800)]
    with patch('xiaomao.desktop_studio.ConversationMemory', Memory), patch('xiaomao.model.ChatModel._companion_json', return_value={'highlights': 'bad', 'tasks': [], 'dates': []}):
        job = finish(studio, studio.summary(cfg.conversation_pause_key('群'), '2025-01-02', '2025-01-03'))
        assert job['status'] == 'failed' and 'result' not in job
        assert 'bad' not in job['error']
        with pytest.raises(ValueError): studio.send_summary(job['job'], 'unsafe')


def test_reset_new_draft_and_new_request_cannot_reuse_late_output(env):
    root, cfg, client = env
    p = DesktopPersona(root).read()
    entered, release = threading.Event(), threading.Event()
    def reply(model, messages):
        if messages[-1].text == 'slow': entered.set(); release.wait(2)
        return 'answer'
    with patch('xiaomao.model.ChatModel.reply', reply):
        studio = studio_class()(root, client)
        first = finish(studio, studio.chat(p['revision'], p['modules'], text='first', request_id='first'))
        old = studio.chat(p['revision'], p['modules'], text='slow', conversation=first['conversation'], request_id='old')
        assert entered.wait(1)
        rows = [{**p['modules'][0], 'text': 'new complete draft'}]
        new = finish(studio, studio.chat(p['revision'], rows, text='new', conversation=first['conversation'], request_id='new'))
        release.set()
        assert new['conversation'] != first['conversation']
        assert new['result']['turns'] == 1
        assert studio.state(old['job'])['status'] == 'canceled'
        duplicate = studio.chat(p['revision'], rows, text='new', conversation=new['conversation'], request_id='new')
        assert duplicate['job'] == new['job']
        reset = finish(studio, studio.chat(p['revision'], rows, text='reset', conversation=new['conversation'], reset=True))
        assert reset['result']['turns'] == 1


def test_timeout_worker_cap_and_protocol_rejects_oversize_output(env):
    root, cfg, client = env
    p = DesktopPersona(root).read()
    entered, release = threading.Event(), threading.Event()
    def reply(model, messages): entered.set(); release.wait(2); return 'late'
    with patch('xiaomao.model.ChatModel.reply', reply):
        studio = studio_class()(root, client)
        studio.TIMEOUT = .02
        first = studio.chat(p['revision'], p['modules'], text='one')
        assert entered.wait(1)
        second = studio.chat(p['revision'], p['modules'], text='two')
        with pytest.raises(ValueError): studio.chat(p['revision'], p['modules'], text='three')
        time.sleep(.04)
        assert studio.state(first['job'])['status'] == 'timed_out'
        release.set()
        assert finish(studio, second)['status'] == 'timed_out'
    with patch('xiaomao.model.ChatModel.reply', return_value='x' * 12001):
        studio = studio_class()(root, client)
        assert finish(studio, studio.chat(p['revision'], p['modules'], text='one'))['status'] == 'failed'


def test_memory_limit_and_empty_range_are_honest_and_no_formal_write(env):
    root, cfg, client = env
    cfg.runtime.mkdir()
    (cfg.runtime / 'fingerprint.key').write_bytes(b'0' * 32)
    class Memory:
        def __init__(self, *args, **kwargs):
            self.messages = [Message(str(index), created_at=1735804800 + index) for index in range(150)]
    with patch('xiaomao.desktop_studio.ConversationMemory', Memory), patch('xiaomao.model.ChatModel._companion_json', return_value={'highlights': [], 'tasks': [], 'dates': []}) as model:
        studio = studio_class()(root, client)
        full = finish(studio, studio.summary(cfg.conversation_pause_key('群'), '2025-01-02', '2025-01-03', request_id='full'))
        assert full['result']['messageCount'] == 120 and full['result']['availableCount'] == 150
        assert full['result']['truncated'] is True
        empty = finish(studio, studio.summary(cfg.conversation_pause_key('群'), '2024-01-01', '2024-01-02', request_id='empty'))
        assert empty['result']['messageCount'] == 0 and empty['result']['insufficient'] is True
        assert model.call_count == 1
        assert studio.state(full['job'])['status'] == 'canceled'


def test_edited_send_size_scope_switch_and_receipt_evidence(env):
    root, cfg, client = env
    studio = studio_class()(root, client)
    result = finish(studio, studio.summary(cfg.conversation_pause_key('群'), '2024-01-01', '2024-01-02'))
    with pytest.raises(ValueError): studio.send_summary(result['job'], 'x' * 401)
    with pytest.raises(ValueError): studio.send_summary(result['job'], 'x\x00')
    receipt = studio.send_summary(result['job'], '要点\n行动\t日期')
    assert receipt['state'] == 'queued' and client.calls[0][2] == '要点\n行动\t日期'
    cfg.runtime.mkdir(exist_ok=True)
    import sqlite3
    with sqlite3.connect(cfg.runtime / 'state.sqlite3') as db:
        db.executescript("CREATE TABLE tasks(id TEXT,state TEXT); CREATE TABLE send_receipts(task_id TEXT); INSERT INTO tasks VALUES ('task1','verified');")
    assert studio.state(result['job'])['delivery']['state'] == 'unconfirmed'
    with sqlite3.connect(cfg.runtime / 'state.sqlite3') as db: db.execute("INSERT INTO send_receipts VALUES ('task1')")
    assert studio.state(result['job'])['delivery']['state'] == 'verified'
    with patch('xiaomao.config.Settings.load', return_value=replace(cfg, conversation_targets={'群': {'account': 'changed', 'id': 'peer@chatroom', 'kind': 'group'}})):
        assert studio.state(result['job'])['status'] == 'invalidated'
        with pytest.raises(ValueError): studio.send_summary(result['job'], '要点')


def test_modular_trial_preserves_complete_enabled_corpus_and_original_preset(env):
    root, cfg, client = env
    from xiaomao.persona_modules import PersonaModule, save_modules
    save_modules(root, (PersonaModule('first', '第一模块', 'a' * 12000 + '完整尾部'),
                        PersonaModule('second', '停用模块', '不应进入模型的停用规则', False)),
                 title='完整模块人格', expected_revision=None)
    presets = root / 'config/presets'
    presets.mkdir()
    (presets / 'xiaoling.yml').write_text('prompts:\n- role: system\n  content: "原有预设约定\\n{persona}"\n- role: user\n  content: "虚构用户示例"\n- role: assistant\n  content: "虚构助手示例"\n', encoding='utf-8')
    persona = DesktopPersona(root).read()
    captured = []
    def reply(model, messages):
        captured.append(model._compiled_persona().text)
        return '正文'
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with patch('xiaomao.model.ChatModel.reply', reply):
        studio = studio_class()(root, client)
        job = finish(studio, studio.chat(persona['revision'], persona['modules'], title=persona['title'], text='试聊'))
    assert job['status'] == 'completed'
    assert 'a' * 12000 + '完整尾部' in captured[0]
    assert '原有预设约定' in captured[0] and '虚构助手示例' in captured[0]
    assert '不应进入模型的停用规则' not in captured[0]
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_legacy_trial_keeps_enabled_supplement_category_and_draft_revision(env):
    root, cfg, client = env
    from xiaomao.persona_documents import import_persona_text
    import_persona_text(root, '正式补充原文', title='相处规则', category='relationship', enabled=True)
    persona = DesktopPersona(root).read()
    rows = [{**row, 'text': '补充草稿完整尾部' if row['id'] != 'main' else row['text']} for row in persona['modules']]
    captured = []
    def reply(model, messages): captured.append(model._compiled_persona().text); return '正文'
    with patch('xiaomao.model.ChatModel.reply', reply):
        studio = studio_class()(root, client)
        assert finish(studio, studio.chat(persona['revision'], rows, text='试聊'))['status'] == 'completed'
    assert '补充草稿完整尾部' in captured[0] and '相处规则' in captured[0]
    assert '正式补充原文' not in captured[0]
    assert DesktopPersona(root).read() == persona
    with pytest.raises(ValueError): studio.chat('stale-revision', rows, text='试聊')


def test_cancel_later_turn_keeps_committed_history_and_old_cancel_cannot_wipe_new_job(env):
    root, cfg, client = env
    p = DesktopPersona(root).read()
    entered, release = threading.Event(), threading.Event()
    captured = []
    def reply(model, messages):
        captured.append([m.text for m in messages])
        if messages[-1].text == 'cancel me': entered.set(); release.wait(2)
        return 'answer'
    with patch('xiaomao.model.ChatModel.reply', reply):
        studio = studio_class()(root, client)
        first = finish(studio, studio.chat(p['revision'], p['modules'], text='remember first'))
        later = studio.chat(p['revision'], p['modules'], conversation=first['conversation'], text='cancel me')
        assert entered.wait(1)
        studio.cancel(later['job'])
        third = finish(studio, studio.chat(p['revision'], p['modules'], conversation=first['conversation'], text='what did I say?'))
        assert captured[-1] == ['remember first', 'answer', 'what did I say?']
        studio.cancel(later['job'])
        fourth = finish(studio, studio.chat(p['revision'], p['modules'], conversation=third['conversation'], text='continue'))
        assert captured[-1] == ['remember first', 'answer', 'what did I say?', 'answer', 'continue']
        release.set()


def test_ambiguous_queue_failure_freezes_exact_text_and_reconciles_original_receipt(env):
    root, cfg, client = env
    from xiaomao.store import StateStore
    store = StateStore(cfg.runtime)
    actual = []
    def accept_then_timeout(group, scope, text, stickers, request_id):
        actual.append((group, scope, text, request_id))
        task_id = store.create_unique(store.fingerprint(f'local-plan:{scope}:{request_id}'), time.time())
        if task_id:
            store.update(task_id, 'generated')
            raise TimeoutError('accepted response lost')
        return {'queued': False, 'duplicate': True}
    client.queue_outbound = accept_then_timeout
    studio = studio_class()(root, client)
    result = finish(studio, studio.summary(cfg.conversation_pause_key('群'), '2024-01-01', '2024-01-02'))
    delivery = studio.send_summary(result['job'], 'original exact text')
    assert delivery['state'] in ('unresolved', 'generated')
    assert delivery['submittedText'] == 'original exact text'
    with pytest.raises(ValueError): studio.send_summary(result['job'], 'different edited retry')
    same = studio.send_summary(result['job'], 'original exact text')
    assert same['id'] and same['state'] == 'generated'
    assert len(actual) == 1
    store.update(same['id'], 'verified')
    with store.db:
        store.db.execute('INSERT INTO send_receipts(task_id,group_digest,reply_digest,baseline,at) VALUES(?,?,?,?,?)',
                         (same['id'], store.fingerprint(cfg.memory_scope), 'reply', '[]', time.time()))
    assert studio.state(result['job'])['delivery']['state'] == 'verified'
    store.db.close()


def test_identical_retry_after_unresolved_submission_uses_original_id_and_handles_duplicate_without_id(env):
    root, cfg, client = env
    from xiaomao.store import StateStore
    store = StateStore(cfg.runtime)
    submitted = []
    def queue(group, scope, text, stickers, request_id):
        submitted.append((text, request_id))
        if len(submitted) == 1: raise TimeoutError('outcome unknown')
        identity = store.create_unique(store.fingerprint(f'local-plan:{scope}:{request_id}'), time.time())
        store.update(identity, 'generated')
        return {'queued': False, 'duplicate': True}
    client.queue_outbound = queue
    studio = studio_class()(root, client)
    result = finish(studio, studio.summary(cfg.conversation_pause_key('群'), '2024-01-01', '2024-01-02'))
    first = studio.send_summary(result['job'], 'frozen original')
    assert first['state'] == 'unresolved'
    with pytest.raises(ValueError): studio.send_summary(result['job'], 'edited')
    recovered = studio.send_summary(result['job'], 'frozen original')
    assert recovered['id'] and recovered['state'] == 'generated'
    assert submitted[0] == submitted[1]
    assert recovered['submittedText'] == 'frozen original'
    store.db.close()

