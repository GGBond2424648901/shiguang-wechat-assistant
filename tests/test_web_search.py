import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from test_bubble_delivery import delivery
from test_engine import system

from xiaomao.config import Settings
from xiaomao.domain import Message, ReplyTask
from xiaomao.model import ChatModel, ModelError
from xiaomao.web_search import QUERY_GUIDANCE, has_unverified_link, search_public


QUERY = 'Python 3.13.15 发布日期 site:python.org'
URL = 'https://www.python.org/downloads/release/python-31315/'


@pytest.mark.parametrize('source, cited', [
    (URL + '#release-notes', URL),
    (URL + '?spm=search.tracking#1', URL),
    (URL + '?utm_source=search&utm_campaign=release#1', URL),
    (URL + '?version=3.13.15&spm=tracking#1', URL + '?version=3.13.15'),
])
def test_citations_accept_the_same_retrieved_page_without_tracking_or_fragment(source, cited):
    assert not has_unverified_link('官方来源：' + cited, [source])


@pytest.mark.parametrize('cited', [
    URL.replace('python-31315', 'python-31316'),
    URL.replace('www.python.org', 'other.example'),
    URL + '?version=3.13.16',
    URL + '?version=3.13.15&download=unverified',
])
def test_citations_still_reject_a_different_page_or_meaningful_query(cited):
    assert has_unverified_link('官方来源：' + cited, [URL + '?version=3.13.15&spm=tracking#1'])


@pytest.mark.parametrize('suffix', ['?spm_id_from=333.788.recommend_more_video.15#1', '?vd_source=share-token#1'])
@pytest.mark.parametrize('formatting', ['来源：{}', '来源（{}）', '参考【{}】'])
def test_bilibili_citations_tolerate_analytics_and_chinese_punctuation(suffix, formatting):
    video = 'https://www.bilibili.com/video/BV1976MB5EvV/'
    assert not has_unverified_link(formatting.format(video), [video + suffix])
    assert has_unverified_link(formatting.format(video + '?p=2'), [video + suffix])


def test_bilibili_video_identity_ignores_share_metadata_but_preserves_parts_and_other_sites():
    video = 'https://www.bilibili.com/video/BV1976MB5EvV/'
    shared = ('https://www.bilibili.com/tr/video/BV1976MB5EvV/?buvid=public-share&from_spmid=feed'
              '&mid=share-marker&plat_id=116&share_from=ugc&share_medium=android&share_plat=android'
              '&share_session_id=public-session&share_source=QQ&share_tag=s_i&spmid=player'
              '&timestamp=1234567890&unique_k=public-key&up_id=123&is_story_h5=false#1')
    assert not has_unverified_link(video, [shared])
    assert not has_unverified_link(video+'?p=2', [shared.replace('#1','&p=2#1')])
    for other in (video+'?p=2', video.replace('BV1976MB5EvV','BV1Wr5pz9EuT'),
                  video.replace('www.bilibili.com','unverified.example'),
                  video.replace('www.bilibili.com','public-user@www.bilibili.com'),
                  video.replace('www.bilibili.com','www.bilibili.com:8443')):
        assert has_unverified_link(other, [shared])
    assert has_unverified_link('https://example.org/item?mid=2', ['https://example.org/item?mid=1'])


def test_raw_search_urls_preserve_legal_parentheses_and_markdown_citations():
    url = 'https://en.wikipedia.org/wiki/Python_(programming_language)'
    def transport(_):
        return httpx.Response(200,json={'results':[{'url':url,'title':'公开资料'}]})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        rows = search_public(client,'sk-fixture-key-only','Python')
    assert rows[0]['url'] == url
    for text in (url, '来源（'+url+'）', '[来源]('+url+')'):
        assert not has_unverified_link(text,[url])
    assert has_unverified_link(url.replace('Python_', 'Different_'),[url])


def configured(root):
    cfg = Settings(root, group='示例甲', group_names=('示例甲', '示例乙'), conversation_targets={
        '示例甲': {'account': 'demo', 'id': 'first', 'kind': 'private'},
        '示例乙': {'account': 'demo', 'id': 'second', 'kind': 'private'},
    })
    cfg.save()
    return cfg


def completion(text):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]}


def call(arguments):
    return {'choices': [{'finish_reason': 'tool_calls', 'message': {'content': None, 'tool_calls': [{
        'id': 'call_test', 'type': 'function',
        'function': {'name': 'search_web', 'arguments': json.dumps(arguments, ensure_ascii=False)},
    }]}}]}


def planner(payload):
    return payload.get('messages', [{}])[0].get('content') == QUERY_GUIDANCE


def model_for(cfg, client):
    credentials = SimpleNamespace(path=cfg.runtime / 'fixture-key', get_keys=lambda: ['sk-fixture-key-only'])
    return ChatModel(cfg, client=client, credential_store=credentials)


def test_search_switches_default_off_and_stay_scoped_to_verified_chat(tmp_path):
    cfg = configured(tmp_path)
    assert not Settings.load(tmp_path).allows_web_search('示例甲')
    cfg = cfg.with_conversation_rules('示例乙', {'web_search': False})
    cfg = cfg.with_conversation_rules('示例甲', {'web_search': True})
    assert not cfg.allows_web_search('示例甲')
    cfg = replace(cfg, web_search_enabled=True)
    cfg.save()
    saved = Settings.load(tmp_path)
    assert saved.for_group('示例甲').allows_web_search('示例甲')
    assert not saved.for_group('示例乙').allows_web_search('示例乙')
    assert not saved.allows_web_search('同名未绑定')
    assert saved.with_conversation_rules('示例乙', {'web_search': None}).allows_web_search('示例乙')
    with pytest.raises(ValueError):
        saved.with_conversation_rules('示例乙', {'web_search': 1})


def test_web_search_uses_only_the_query_and_returns_a_normal_reply(tmp_path):
    cfg = replace(configured(tmp_path), web_search_enabled=True)
    cfg.save()
    requests = []

    def transport(request):
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        if request.url.path == '/v1/tools/search':
            assert payload == {'query': QUERY, 'count': 4, 'need_content': True}
            assert request.url.host == 'api.deepseek.com'
            return httpx.Response(200, json={'results': [
                {'url': URL, 'title': 'Python 3.13.15', 'summary': '官方发布记录',
                 'date': '2026-08-04T16:00:00+00:00', 'content': '官网正文摘录'},
                {'url': 'http://127.0.0.1/admin', 'title': '内网结果', 'summary': '不应公开'},
                {'url': 'http://0x7f.0.0.1/admin', 'title': '变形内网结果', 'summary': '不应公开'},
            ]})
        if planner(payload):
            assert len(payload['messages']) == 2
            assert payload['messages'][1]['content'] == '帮我查 Python 3.13.15 官方发布日期'
            assert '仅留在对话中的内容' not in json.dumps(payload, ensure_ascii=False)
            return httpx.Response(200, json=completion(json.dumps({'query': QUERY})))
        if len(requests) == 1:
            assert payload['tools'][0]['function']['name'] == 'search_web'
            proposed = call({'query': QUERY})
            calls = proposed['choices'][0]['message']['tool_calls']
            calls.append({**calls[0], 'id': 'other_call', 'function': {
                'name': 'search_web', 'arguments': json.dumps({'query': '不重复查询'})}})
            return httpx.Response(200, json=proposed)
        assert 'tools' not in payload
        evidence = payload['messages'][-3]['content']
        assert 'public_search_result' in evidence and '官网正文摘录' in evidence
        assert 'indexed_local_time' in evidence
        assert URL in evidence and '127.0.0.1' not in evidence and '0x7f.0.0.1' not in evidence
        assert payload['messages'][-1]['role'] == 'user'
        return httpx.Response(200, json=completion(json.dumps(
            {'send': True, 'text': f'官方发布记录在这里\n{URL}', 'sticker': ''})))

    with httpx.Client(transport=httpx.MockTransport(transport), trust_env=False) as client:
        reply = model_for(cfg.for_group('示例甲'), client).reply_plan([
            Message('仅留在对话中的内容', direction='out'),
            Message('帮我查 Python 3.13.15 官方发布日期'),
        ], choices=[])
    assert reply.text == f'官方发布记录在这里\n{URL}'
    assert [path for path, _ in requests] == [
        '/chat/completions', '/chat/completions', '/v1/tools/search', '/chat/completions',
    ]


@pytest.mark.parametrize('disabled', [
    'global', 'conversation', 'mid-turn', 'paused', 'retired-task',
    'unsafe-query', 'private-topic', 'provider-error',
])
def test_disabled_or_failed_search_never_invents_results(tmp_path, disabled):
    cfg = replace(configured(tmp_path), web_search_enabled=disabled != 'global')
    if disabled == 'conversation':
        cfg = cfg.with_conversation_rules('示例甲', {'web_search': False})
    cfg.save()
    paths = []
    active = True

    def transport(request):
        nonlocal active
        payload = json.loads(request.content)
        paths.append(request.url.path)
        if planner(payload):
            query = '示例甲家的地址' if disabled == 'private-topic' else QUERY
            return httpx.Response(200, json=completion(json.dumps({'query': query})))
        if request.url.path == '/v1/tools/search':
            assert disabled == 'provider-error'
            return httpx.Response(429, json={'error': {'message': 'rate limited'}})
        if disabled in ('global', 'conversation'):
            assert 'tools' not in payload
        elif len(paths) == 1:
            if disabled == 'mid-turn':
                replace(cfg, web_search_enabled=False).save(expected=cfg)
            elif disabled == 'paused':
                cfg.with_conversation_pause('示例甲', True).save(expected=cfg)
            elif disabled == 'retired-task':
                active = False
            query = '查 sk-private-credential-123456789' if disabled == 'unsafe-query' else QUERY
            return httpx.Response(200, json=call({'query': query}))
        else:
            assert URL not in json.dumps(payload, ensure_ascii=False)
        return httpx.Response(200, json=completion('这个实时信息暂时核实不了'))

    with httpx.Client(transport=httpx.MockTransport(transport), trust_env=False) as client:
        model = model_for(cfg.for_group('示例甲'), client)
        model.bind_reply_task(ReplyTask('paused-qa', 0, 0, ''), lambda: active)
        reply = model.reply([Message('帮我查这版的发布时间')])
    assert reply == '这个实时信息暂时核实不了'
    assert paths.count('/v1/tools/search') == int(disabled == 'provider-error')


@pytest.mark.parametrize('failed', [False, True])
@pytest.mark.parametrize('stubborn', [False, True])
def test_empty_or_failed_search_rejects_invented_links_before_sending(tmp_path, failed, stubborn):
    cfg = replace(configured(tmp_path), web_search_enabled=True)
    cfg.save()
    paths, final = [], []
    safe = '这次暂时没查到可靠资料，稍后再核对。'

    def transport(request):
        payload = json.loads(request.content)
        paths.append(request.url.path)
        if planner(payload):
            return httpx.Response(200, json=completion(json.dumps({'query': QUERY})))
        if request.url.path == '/v1/tools/search':
            return httpx.Response(429 if failed else 200, json={'results': []})
        if 'tools' in payload:
            return httpx.Response(200, json=call({'query': QUERY}))
        final.append(payload)
        text = '来源 https://fabricated.example/release' if stubborn or len(final) == 1 else safe
        return httpx.Response(200, json=completion(text))

    with httpx.Client(transport=httpx.MockTransport(transport), trust_env=False) as client:
        model = model_for(cfg.for_group('示例甲'), client)
        if stubborn:
            with pytest.raises(ModelError) as error:
                model.reply([Message('帮我查这个版本的发布日期')])
            assert error.value.code == 'invalid_response' and error.value.retryable
        else:
            assert model.reply([Message('帮我查这个版本的发布日期')]) == safe
    assert len(final) == 2
    assert paths.count('/v1/tools/search') == 1


@pytest.mark.parametrize('finish', ['stop', 'length'])
def test_blank_search_answer_retries_without_json_transport_and_keeps_receipt_contract(tmp_path, finish):
    cfg = replace(configured(tmp_path), web_search_enabled=True)
    cfg.save()
    paths, final = [], []
    text = '找到一条相关教程\n' + URL

    def transport(request):
        payload = json.loads(request.content)
        paths.append(request.url.path)
        if planner(payload):
            return httpx.Response(200, json=completion(json.dumps({'query': QUERY})))
        if request.url.path == '/v1/tools/search':
            return httpx.Response(200, json={'results':[{'url':URL,'title':'相关教程'}]})
        if 'tools' in payload:
            return httpx.Response(200, json=call({'query': QUERY}))
        final.append(payload)
        if len(final) == 1:
            data = completion(' \n\t  ')
            data['choices'][0]['finish_reason'] = finish
            return httpx.Response(200, json=data)
        assert 'response_format' not in payload
        assert 'tools' not in payload
        assert payload['messages'] == final[0]['messages']
        return httpx.Response(200, json=completion(json.dumps({'send':True,'text':text,'sticker':''})))

    with httpx.Client(transport=httpx.MockTransport(transport), trust_env=False) as client:
        answer = model_for(cfg.for_group('示例甲'),client).reply_plan([Message('帮我查相关教程')],choices=[])
    assert answer.text == text and not answer.sticker
    assert len(final) == 2
    assert paths.count('/v1/tools/search') == 1


@pytest.mark.parametrize('reference', ['verified', 'other-member', 'unconfirmed', 'not-sent', 'other-scope', 'unknown', 'empty-id'])
def test_followup_query_uses_only_public_sources_from_a_delivered_reply_to_this_member(tmp_path, reference):
    cfg = replace(configured(tmp_path), web_search_enabled=True)
    cfg.save()
    a, b = 'p_'+'a'*16, 'p_'+'b'*16
    if reference in ('unknown', 'empty-id'):
        a = 'unknown' if reference == 'unknown' else ''
    first = '在B站找两条公开的引擎教程'
    followup = '再查第二条教程有没有配套源码仓库'
    video1 = 'https://www.bilibili.com/video/BV1976MB5EvV/'
    video2 = 'https://www.bilibili.com/video/BV1Wr5pz9EuT/'
    repository = 'https://github.com/godotengine/godot-demo-projects'
    first_answer = '第一条：公开教程甲\n'+video1+'\n第二条：公开教程乙\n'+video2
    searches, querying_followup = [], False
    expected_reference = reference == 'verified'

    def transport(request):
        payload = json.loads(request.content)
        if planner(payload):
            content = payload['messages'][-1]['content']
            if not querying_followup:
                return httpx.Response(200, json=completion(json.dumps({'query':'site:bilibili.com Godot 教程'})))
            assert '仅留在私聊中的内容' not in content
            if expected_reference:
                data = json.loads(content)
                assert data['current_question'] == followup
                assert [row['url'] for row in data['previous_public_sources']] == [video1, video2]
                assert data['previous_public_sources'][1]['title'] == '公开教程乙'
            else:
                assert content == followup
            query = '公开教程乙 配套源码仓库' if expected_reference else ''
            return httpx.Response(200, json=completion(json.dumps({'query':query})))
        if request.url.path == '/v1/tools/search':
            searches.append(payload['query'])
            rows = ([{'url':video2,'title':'公开教程乙'}, {'url':video1,'title':'公开教程甲'}]
                    if len(searches)==1 else [{'url':repository,'title':'公开源码仓库'}])
            return httpx.Response(200,json={'results':rows})
        if 'tools' in payload:
            return httpx.Response(200,json=call({'query':QUERY}))
        text = first_answer if not querying_followup else repository if expected_reference else '请补充教程标题'
        return httpx.Response(200,json=completion(text))

    with httpx.Client(transport=httpx.MockTransport(transport),trust_env=False) as client:
        model = model_for(cfg.for_group('示例甲'),client)
        incoming = Message(first,speaker_id=a)
        assert model.reply([incoming]) == first_answer
        querying_followup = True
        if reference == 'other-scope':
            cfg = replace(cfg,conversation_targets={**cfg.conversation_targets,
                          '示例甲':{'account':'demo','id':'changed','kind':'private'}})
            cfg.save()
            model.settings = cfg.for_group('示例甲')
        delivery = 'unconfirmed' if reference=='unconfirmed' else 'not_sent' if reference=='not-sent' else 'verified'
        history = [Message('仅留在私聊中的内容',speaker_id=a),incoming,
                   Message(first_answer,direction='out',reply_to_speaker=a,delivery_status=delivery),
                   Message(followup,speaker_id=b if reference=='other-member' else a)]
        assert model.reply(history) == (repository if expected_reference else '请补充教程标题')
    assert len(searches) == (2 if expected_reference else 1)


def test_settings_and_per_chat_rules_round_trip_from_desktop(tmp_path, monkeypatch):
    from xiaomao import windows
    from xiaomao.client import ControlUnavailable
    from xiaomao.desktop_service import DesktopService
    from xiaomao.desktop_settings import GENERAL

    configured(tmp_path)
    monkeypatch.setattr(windows, 'worker_scope', lambda _: None)

    def disconnected():
        raise ControlUnavailable('test offline')

    service = DesktopService(tmp_path, client=SimpleNamespace(status=disconnected))
    original = service.call('settings.read', {})
    assert original['web_search_enabled'] is False
    fields = {key: original[key] for key in (*GENERAL, 'owner_confirmed')}
    saved = service.call('settings.save', {'expected': original['revision'],
                                          'fields': {**fields, 'web_search_enabled': True}})
    assert saved['web_search_enabled'] is True
    sessions = service.call('sessions.read', {})
    assert sessions['webSearchEnabled'] is True
    row = sessions['items'][1]
    saved = service.call('sessions.rules', {'session': row['id'], 'expected': sessions['revision'],
        'replyMode': None, 'stickerFrequency': None, 'webSearch': False,
        'before': {'reply_mode': None, 'sticker_frequency': None, 'web_search': None}})
    assert saved['items'][0]['webSearch'] is None and saved['items'][1]['webSearch'] is False
    assert Settings.load(tmp_path).allows_web_search('示例甲')
    assert not Settings.load(tmp_path).allows_web_search('示例乙')


def test_live_resident_applies_search_changes_without_a_restart(tmp_path):
    from xiaomao.resident import Resident
    from xiaomao.settings_service import apply_saved_settings
    from xiaomao.store import StateStore

    cfg = configured(tmp_path)
    store = StateStore(cfg.runtime)
    try:
        resident = Resident(cfg, store, desktop_factory=lambda _: object())
        before = {engine.settings.group: id(engine) for engine in resident.engines}
        online = replace(cfg, web_search_enabled=True)
        apply_saved_settings(resident, cfg, online)
        resident.configure_reply_rules('示例乙', 'demo/second', {'web_search': False}, expected={'web_search': None})
        assert {engine.settings.group: id(engine) for engine in resident.engines} == before
        assert [engine.model.settings.allows_web_search(engine.settings.group) for engine in resident.engines] == [True, False]
        assert Settings.load(tmp_path).conversation_rules['demo/second']['web_search'] is False
    finally:
        store.close()


def test_search_reply_is_sent_through_normal_queue_with_receipt(delivery):
    engine, feed, _, clock, store = delivery
    name = engine.settings.group
    cfg = replace(engine.settings, web_search_enabled=True, split_replies=False, stickers_enabled=False,
                  model_base_url='https://api.deepseek.com',
                  conversation_targets={name: {'account': 'demo', 'id': 'group@chatroom', 'kind': 'group'}})
    cfg.save()
    engine.settings = cfg
    sent = f'Python 3.13.15 的官方发布记录\n{URL}'
    paths = []

    def endpoint(request):
        path = request.url.path
        paths.append(path)
        payload = json.loads(request.content)
        if path == '/v1/tools/search':
            return httpx.Response(200, json={'results': [{'url': URL, 'title': 'Python 3.13.15', 'summary': '官方记录'}]})
        if 'tools' in payload:
            return httpx.Response(200, json=call({'query': QUERY}))
        if planner(payload):
            return httpx.Response(200, json=completion(json.dumps({'query': QUERY})))
        return httpx.Response(200, json=completion(json.dumps({'text': sent})))

    with httpx.Client(transport=httpx.MockTransport(endpoint), trust_env=False) as client:
        engine.model = model_for(cfg, client)
        feed.pending.append(Message('查一下 Python 3.13.15 的官方发布记录',
                                    message_id='search-qa', speaker_id='p_' + '1' * 16, created_at=clock()))
        engine.observe_once()
        engine.observe_once()
    assert feed.sent == [sent]
    assert store.counts() == {'verified': 1}
    assert paths == ['/chat/completions', '/chat/completions', '/v1/tools/search', '/chat/completions']


def test_format_repair_and_queue_retry_reuse_evidence_without_repeating_search(tmp_path):
    cfg = replace(configured(tmp_path), web_search_enabled=True)
    cfg.save()
    paths = []
    answers = iter(('错误来源 https://unverified.example/source', URL, URL))

    def transport(request):
        path = request.url.path
        paths.append(path)
        payload = json.loads(request.content)
        if path == '/v1/tools/search':
            return httpx.Response(200, json={'results': [{'url': URL, 'title': 'Python 发布记录'}]})
        if 'tools' in payload:
            return httpx.Response(200, json=call({'query': QUERY}))
        if planner(payload):
            return httpx.Response(200, json=completion(json.dumps({'query': QUERY})))
        content = json.dumps({'send': True, 'text': next(answers), 'sticker': ''})
        return httpx.Response(200, json=completion(content))

    with httpx.Client(transport=httpx.MockTransport(transport), trust_env=False) as client:
        model = model_for(cfg.for_group('示例甲'), client)
        task = ReplyTask('same-turn', 0, 0, '')
        for _ in range(2):
            model.bind_reply_task(task, lambda: True)
            reply = model.reply_plan([Message('查 Python 3.13.15 发布日期')], choices=[])
            assert reply.text == URL
    assert paths.count('/v1/tools/search') == 1
