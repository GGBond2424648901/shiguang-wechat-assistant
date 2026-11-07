"""A document import must preserve its source and reach the model without truncation."""
import hashlib
import json

import httpx
import pytest

from xiaomao.config import Settings
from xiaomao.domain import Message
from xiaomao.model import ChatModel, ModelError
from xiaomao.persona import load_persona, save_persona


def test_large_markdown_import_keeps_original_bytes_and_multiple_categories(tmp_path):
    from xiaomao.persona_documents import import_persona_file, list_persona_documents, render_persona_documents
    source = tmp_path/'完整人格.md'
    original = ('\ufeff# 完整人格\r\n'+('保留全部细节和空格  \r\n'*1800)+'## 最后规则\r\n不要遗漏末尾\r\n').encode('utf-8')
    source.write_bytes(original)
    document = import_persona_file(tmp_path, source, category='style')
    second = tmp_path/'记忆规则.markdown'; second.write_bytes('# 记忆\n按来源记忆'.encode())
    import_persona_file(tmp_path, second, category='memory')
    loaded = list_persona_documents(tmp_path)
    assert [item.category for item in loaded] == ['style', 'memory']
    assert document.sha256 == hashlib.sha256(original).hexdigest()
    assert document.source_path.read_bytes() == original
    assert document.text.endswith('## 最后规则\r\n不要遗漏末尾\r\n')
    rendered = render_persona_documents(tmp_path)
    assert document.text not in rendered and '# 记忆\n按来源记忆' in rendered


def test_paste_edit_disable_and_reenable_preserves_every_revision(tmp_path):
    from xiaomao.persona_documents import import_persona_text, list_persona_documents, update_persona_document, render_persona_documents
    first = import_persona_text(tmp_path, '# 补充\n原文不要改', title='自定义规则', category='custom')
    updated = update_persona_document(tmp_path, first.id, text='# 补充\n新版完整正文', expected_sha256=first.sha256)
    assert first.source_path.read_text(encoding='utf-8') == '# 补充\n原文不要改'
    assert updated.source_path.read_text(encoding='utf-8') == '# 补充\n新版完整正文'
    with pytest.raises(ValueError):
        update_persona_document(tmp_path, first.id, text='覆盖新稿', expected_sha256=first.sha256)
    update_persona_document(tmp_path, first.id, enabled=False)
    assert '新版完整正文' not in render_persona_documents(tmp_path)
    assert list_persona_documents(tmp_path)[0].enabled is False
    update_persona_document(tmp_path, first.id, enabled=True, category='relationship')
    assert '新版完整正文' in render_persona_documents(tmp_path)
    assert list_persona_documents(tmp_path)[0].category == 'relationship'


def test_invalid_import_leaves_corpus_unchanged_and_never_archives_secrets(tmp_path):
    from xiaomao.persona_documents import import_persona_text, list_persona_documents
    with pytest.raises(ValueError):
        import_persona_text(tmp_path, '# 误粘贴\nsk-'+'a'*32, title='规则')
    assert list_persona_documents(tmp_path) == ()
    assert not any(b'sk-' in p.read_bytes() for p in (tmp_path/'config').rglob('*') if p.is_file())


def test_corrupt_or_missing_source_is_never_silently_omitted(tmp_path):
    from xiaomao.persona_documents import import_persona_text, render_persona_documents
    document = import_persona_text(tmp_path, '# 规则\n全部都要', title='重要规则')
    document.source_path.write_text('损坏后的正文', encoding='utf-8')
    with pytest.raises(ValueError, match='校验'):
        render_persona_documents(tmp_path)
    document.source_path.unlink()
    with pytest.raises(ValueError, match='原文'):
        render_persona_documents(tmp_path)


def test_primary_persona_accepts_long_prompt_and_archives_prior_source(tmp_path):
    from xiaomao.persona_documents import persona_source_directory
    save_persona(tmp_path, '# 旧规则\r\n不得丢失原稿\r\n')
    long_prompt = '# 新版\n'+('详细性格\n'*6000)
    save_persona(tmp_path, long_prompt)
    assert load_persona(tmp_path) == long_prompt
    originals = [p.read_bytes() for p in persona_source_directory(tmp_path).glob('*') if p.is_file()]
    assert '# 旧规则\r\n不得丢失原稿\r\n'.encode() in originals


def _local_model(tmp_path, endpoint):
    return ChatModel(Settings(tmp_path, model_base_url='http://127.0.0.1:11434/v1'),
                     client=httpx.Client(transport=httpx.MockTransport(endpoint)))


def test_model_switches_the_only_supplement_on_the_next_generation(tmp_path):
    from xiaomao.persona_documents import import_persona_text, update_persona_document
    main = '# 小玲\n独立基础人格的完整文本'
    save_persona(tmp_path, main)
    first = import_persona_text(tmp_path, '# 第一份\n第一份专属措辞', title='第一份')
    second = import_persona_text(tmp_path, '# 第二份\n第二份专属措辞', title='第二份', enabled=False)
    payloads = []
    def endpoint(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={'choices':[{'message':{'content':'接住啦'}}]})
    model = _local_model(tmp_path, endpoint)
    model.reply([Message('第一轮')])
    update_persona_document(tmp_path, second.id, enabled=True)
    model.reply([Message('下一轮')])
    before, after = [item['messages'][0]['content'] for item in payloads]
    assert main in before and main in after and '固定运行边界' in after
    assert '第一份专属措辞' in before and '第二份专属措辞' not in before
    assert '第一份专属措辞' not in after and '第二份专属措辞' in after
    assert load_persona(tmp_path) == main and first.source_path.is_file()


def test_legacy_manifest_with_multiple_enabled_uses_last_enabled_source(tmp_path):
    from xiaomao.persona_documents import import_persona_text, list_persona_documents, render_persona_documents
    first = import_persona_text(tmp_path, '早先的原文', title='较早')
    last = import_persona_text(tmp_path, '后来的原文', title='较新')
    path = tmp_path/'config'/'persona_documents.json'
    data = json.loads(path.read_bytes())
    for row in data['documents']:
        row['enabled'] = True
    legacy = json.dumps(data, ensure_ascii=False).encode()
    path.write_bytes(legacy)
    assert [doc.enabled for doc in list_persona_documents(tmp_path)] == [False, True]
    assert first.text not in render_persona_documents(tmp_path) and last.text in render_persona_documents(tmp_path)
    assert path.read_bytes() == legacy and first.source_path.is_file()


def test_model_prefix_stays_identical_across_dynamic_context_and_loads_every_document(tmp_path):
    from xiaomao.persona_documents import import_persona_text
    save_persona(tmp_path, '# 小玲\n保留鲸鱼娘人格\n【语言习惯（全面拟人层）】\n闲聊不带句号')
    import_persona_text(tmp_path, '# 完整V3\n'+('每段全部保留\n'*2400)+'末尾不可遗漏', title='完整V3', category='style')
    payloads = []
    def endpoint(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={'choices':[{'message':{'content':'接住啦'}}]})
    model = _local_model(tmp_path, endpoint)
    model.reply([Message('嘿嘿，之前说过啦', direction='out'), Message('接着聊')], profile='喜欢月光项目')
    model.reply([Message('行吧，继续', direction='out'), Message('换个说法')], profile='另一位成员偏好')
    first, second = [p['messages'][0] for p in payloads]
    assert first == second
    assert first['role'] == 'system' and '末尾不可遗漏' in first['content']
    assert '保留鲸鱼娘人格' in first['content'] and '固定运行边界' in first['content']
    assert '喜欢月光项目' not in first['content'] and '另一位成员偏好' not in first['content']
    assert any('喜欢月光项目' in str(m['content']) for m in payloads[0]['messages'][1:])


def test_repeated_generations_reuse_corpus_until_a_source_changes(tmp_path, monkeypatch):
    from pathlib import Path
    from xiaomao.persona_documents import import_persona_text, update_persona_document
    document = import_persona_text(tmp_path, '# 规则\n初稿全部保留', title='规则')
    model = _local_model(tmp_path, lambda request: httpx.Response(200, json={'choices':[{'message':{'content':'好的'}}]}))
    model.reply([Message('第一轮')])
    original_read = Path.read_bytes
    reads = []
    def read(path):
        if path == document.source_path: reads.append(path)
        return original_read(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    model.reply([Message('第二轮')]); model.reply([Message('第三轮')])
    assert reads == []
    update_persona_document(tmp_path, document.id, text='# 规则\n新稿全部保留')
    assert '新稿全部保留' in model._persona_instructions([])


def test_provider_usage_reports_real_cache_evidence_and_aggregate_tokens_without_text(tmp_path):
    replies = iter([
        {'prompt_tokens':120, 'completion_tokens':8, 'total_tokens':128, 'prompt_cache_hit_tokens':96, 'prompt_cache_miss_tokens':24},
        {'prompt_tokens':140, 'completion_tokens':9, 'total_tokens':149, 'prompt_cache_hit_tokens':120, 'prompt_cache_miss_tokens':20},
        {'prompt_tokens':100, 'completion_tokens':7, 'total_tokens':107},
    ])
    model = _local_model(tmp_path, lambda request: httpx.Response(200, json={
        'choices':[{'message':{'content':'收到'}}], 'usage':next(replies)}))
    for _ in range(3): model.reply([Message('不得出现在证据中的私密正文')])
    evidence = model.usage_snapshot()
    assert evidence['requests'] == 3 and evidence['prompt_tokens'] == 360
    assert evidence['completion_tokens'] == 24 and evidence['total_tokens'] == 384
    assert evidence['cache_reported_requests'] == 2
    assert evidence['prompt_cache_hit_tokens'] == 216 and evidence['prompt_cache_miss_tokens'] == 44
    assert evidence['cache_hit_ratio'] == pytest.approx(216/260)
    assert evidence['last']['cache_reported'] is False
    assert '私密正文' not in json.dumps(evidence, ensure_ascii=False)


def test_usage_absence_or_invalid_values_cannot_be_claimed_as_cache_hits(tmp_path):
    values = iter([None, {'prompt_tokens':True, 'prompt_cache_hit_tokens':99}, {'prompt_tokens':10,'prompt_cache_hit_tokens':100,'prompt_cache_miss_tokens':0}])
    model = _local_model(tmp_path, lambda request: httpx.Response(200, json={
        'choices':[{'message':{'content':'正常'}}], 'usage':next(values)}))
    for _ in range(3): model.reply([Message('继续')])
    evidence = model.usage_snapshot()
    assert evidence['cache_reported_requests'] == 0
    assert evidence['cache_hit_ratio'] is None


def test_compaction_rejects_summary_beyond_promised_bound_without_cutting_text(tmp_path):
    model = _local_model(tmp_path, lambda request: httpx.Response(200, json={
        'choices':[{'message':{'content':json.dumps({'summary':'完整事实'*700})}}]}))
    with pytest.raises(ModelError):
        model.compact('', [Message('原文仍需保留')])


def test_scheduled_routine_supplies_operator_greeting_intent_after_stable_prefix(tmp_path):
    from xiaomao.companion import Candidate
    payloads = []
    def endpoint(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={'choices':[{'message':{'content':'{"send":true,"text":"早呀，今天慢慢来"}'}}]})
    model = _local_model(tmp_path, endpoint)
    result = model.proactive([], Candidate('routine','routine',100,200,1,'2026-11-07',label='早安与早餐，轻松关心'))
    assert result == '早呀，今天慢慢来'
    sent = payloads[0]['messages']
    assert '早安与早餐' not in sent[0]['content']
    rules = '\n'.join(m['content'] for m in sent[1:] if m['role']=='system')
    assert '明确约定的日常问候' in rules and '正常情况 send=true' in rules
    assert '早安与早餐' in rules and '不重复已确认完成的吃饭' in rules
    assert 'tools' not in payloads[0]
