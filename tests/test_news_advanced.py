from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from xiaomao.news import default_subscription, validate_subscription
from xiaomao.news_research import NewsResearch
from xiaomao.news_store import NewsStore

NOW = datetime(2026, 11, 11, 4, tzinfo=timezone.utc).timestamp()


def test_topic_settings_roundtrip_and_bounds():
    config = default_subscription()
    config.update(categoryTopics={'game': ['三角洲行动']}, topicSettings={'game': {'三角洲行动': {
        'aliases': ['Delta Force'], 'focusTerms': ['更新'], 'exclusions': ['赛事']}},
        'auto': {'摩托车': {'aliases': ['机车'], 'focusTerms': [], 'exclusions': []}}})
    clean = validate_subscription(config)
    assert clean['topicSettings'] == config['topicSettings']
    config['topicSettings']['game']['三角洲行动']['aliases'] = ['x' * 61]
    with pytest.raises(ValueError, match='别名'):
        validate_subscription(config)


def test_long_topic_queries_never_lose_topic_or_category():
    config = default_subscription()
    topic = '公开项目' * 15
    config.update(categories=['game'], categoryTopics={'game': [topic]}, keywords='离线更新' * 45,
        topicSettings={'game': {topic: {'aliases': ['Delta Force'], 'focusTerms': ['新版本'], 'exclusions': []}}})
    queries = NewsResearch._queries(validate_subscription(config), NOW)
    assert any('Delta Force' in query for _, _, query in queries)
    assert all('游戏' in query and topic in query and len(query) <= 120 for _, _, query in queries)
    assert any('新版本' in query for _, _, query in queries)


def test_feed_security_parse_and_bounds():
    from xiaomao.news_feeds import validate_feed_url, parse_feed, MAX_FEED_BYTES
    for url in ['http://127.0.0.1/a', 'http://10.0.0.1/a', 'http://[::1]/a', 'https://user:pass@example.org/rss',
                'http://169.254.169.254/a', 'https://example.org:8443/rss', 'file:///tmp/feed', 'http://2130706433/rss']:
        with pytest.raises(ValueError): validate_feed_url(url)
    for data in [b'<rss><bad>', b'<!DOCTYPE rss [<!ENTITY x "oops">]><rss/>', b'x' * (MAX_FEED_BYTES + 1)]:
        with pytest.raises(ValueError): parse_feed(data, 'https://news.example.org/feed')
    rows = parse_feed(b'<rss version="2.0"><channel><item><title>Project 2 release</title><link>../release</link>'
        b'<description>Offline export added.</description><pubDate>Wed, 11 Nov 2026 02:00:00 GMT</pubDate>'
        b'</item></channel></rss>', 'https://news.example.org/feeds/rss')
    assert rows[0]['url'] == 'https://news.example.org/release'
    assert rows[0]['sourceType'] == 'feed'
    assert '2026-11-11T02:00:00+00:00' in rows[0]['page_excerpt']


def test_atom_relative_link_and_xml_base():
    from xiaomao.news_feeds import parse_feed
    rows = parse_feed(b'<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://news.example.org/articles/">'
        b'<entry><title>Project release</title><link href="item"/><updated>2026-11-11T02:00:00Z</updated>'
        b'<summary>Offline export added.</summary></entry></feed>', 'https://news.example.org/feed')
    assert rows[0]['url'] == 'https://news.example.org/articles/item'
    assert rows[0]['dateKind'] == 'updated'


def test_conservative_event_grouping_preserves_numbers_updates_viewpoints():
    from xiaomao.news_quality import group_events
    def article(url, title='Project Phoenix launches offline export', summary='Project Phoenix now supports offline export for all users.'):
        return dict(url=url, title=title, summary=summary, source='news.example.org', category='technology', topic='', sourceType='search')
    one = article('https://one.example.org/release')
    repost = article('https://two.example.org/repost')
    score = article('https://three.example.org/score', title='Project Phoenix launches offline export 2')
    update = article('https://four.example.org/update', summary='Project Phoenix withdrew offline export due to a security flaw.')
    opinion = article('https://five.example.org/opinion', title='Why Project Phoenix offline export raises privacy concerns')
    grouped = group_events([one, repost, score, update, opinion])
    assert len(grouped) == 4
    assert {s['url'] for s in grouped[0]['sources']} == {one['url'], repost['url']}
    assert grouped[0]['merge']['confidence'] == 'high'


def test_feedback_is_reversible_scoped_and_does_not_modify_sent(tmp_path):
    store = NewsStore(tmp_path)
    try:
        sub = store.save_subscription('account-a', default_subscription())
        other = store.save_subscription('account-a', default_subscription())
        article = dict(url='https://news.example.org/release', title='公开项目发布', source='news.example.org', category='technology', topic='')
        edition = store.create_edition('account-a', sub['id'], sub, 'preview', '', NOW)
        store.update_edition('account-a', edition['id'], articles=[article], parts=['immutable'], state='sent')
        before = deepcopy(store.get_edition('account-a', edition['id']))
        mark = store.save_feedback('account-a', sub['id'], article, 'irrelevant', 'article')
        assert len(store.list_feedback('account-a', sub['id'])) == 1
        assert not store.list_feedback('account-b', sub['id'])
        assert not store.list_feedback('account-a', other['id'])
        assert store.get_edition('account-a', edition['id']) == before
        store.save_feedback('account-a', sub['id'], article, 'like', 'article')
        assert store.list_feedback('account-a', sub['id'])[0]['value'] == 'like'
        store.clear_feedback('account-a', sub['id'], mark['id'])
        assert not store.list_feedback('account-a', sub['id'])
    finally:
        store.close()


def test_topic_conditions_apply_only_to_their_own_topic(tmp_path):
    import httpx
    from test_news_research import research, result, completion
    def transport(request):
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json={'results': [
                result(url='https://news.example.org/delta', title='Delta Force 更新新地图', snippet='Delta Force 更新了新地图。'),
                result(url='https://news.example.org/esports', title='Delta Force 更新赛事', snippet='Delta Force 赛事更新了赛程。'),
                result(url='https://news.example.org/indie', title='独立游戏赛事公开新安排', snippet='独立游戏赛事公开新安排。')]})
        return httpx.Response(200, json=completion('{}'))
    collector, client = research(tmp_path, transport)
    config = default_subscription()
    config.update(categories=['game'], categoryTopics={'game': ['三角洲行动', '独立游戏']}, itemCount=5,
        topicSettings={'game': {'三角洲行动': {'aliases': ['Delta Force'], 'focusTerms': ['更新'], 'exclusions': ['赛事']}}})
    try:
        rows = collector.collect(config, NOW)['articles']
        assert {row['url'] for row in rows} == {'https://news.example.org/delta', 'https://news.example.org/indie'}
        assert next(row for row in rows if 'delta' in row['url'])['topic'] == '三角洲行动'
    finally: collector.close(); client.close()


def test_feed_only_works_without_model_keys_and_feedback_filters(tmp_path, monkeypatch):
    from xiaomao.news_research import NewsResearch
    config = default_subscription()
    config.update(sourceMode='feed', feeds=[dict(id='official', url='https://news.example.org/feed', label='官方源', enabled=True,
        priority=8, category='technology', topic='')])
    def fetch(url, cancelled, runtime):
        return [dict(url='https://news.example.org/release', title='公开项目发布离线导出功能', snippet='公开项目新增离线导出功能。',
                     page_excerpt='发布时间：2026-11-11T02:00:00+00:00。公开项目新增离线导出功能。', sourceType='feed')]
    monkeypatch.setattr('xiaomao.news_research.fetch_feed', fetch)
    model = SimpleNamespace(settings=SimpleNamespace(runtime=tmp_path), credentials=SimpleNamespace(get_keys=lambda: []))
    collector = NewsResearch(model)
    try:
        result = collector.collect(config, NOW)
        assert result['articles'][0]['sourceType'] == 'feed'
        assert result['articles'][0]['feedId'] == 'official'
        assert result['articles'][0]['publishedAt'] == NOW - 7200
        config['_feedback'] = [{'scope': 'article', 'value': 'irrelevant', 'target': 'https://news.example.org/release'}]
        assert not collector.collect(config, NOW)['articles']
    finally: collector.close()


def test_feed_dns_rejects_mixed_public_private_answers(monkeypatch):
    from xiaomao.news_feeds import _public_addresses
    import socket
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(ValueError, match='内部'): _public_addresses('public.example.org', 443)


def test_feed_redirect_to_private_never_connects(monkeypatch):
    import xiaomao.news_feeds as feeds
    connections = []
    class Response:
        status = 302
        def getheader(self, key, default=None): return 'http://127.0.0.1/private' if key == 'Location' else default
    class Connection:
        def __init__(self, *args): connections.append(args)
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(feeds, '_public_addresses', lambda *args: ['93.184.216.34'])
    monkeypatch.setattr(feeds, '_PinnedConnection', Connection)
    with pytest.raises(ValueError): feeds._fetch_bytes('https://public.example.org/feed')
    assert len(connections) == 1


def test_feed_http_content_length_is_bounded(monkeypatch):
    import xiaomao.news_feeds as feeds
    class Response:
        status = 200
        def getheader(self, key, default=None): return str(feeds.MAX_FEED_BYTES + 1) if key == 'Content-Length' else default
        def read(self, n): raise AssertionError('oversized body must not be read')
    class Connection:
        def __init__(self, *args): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(feeds, '_public_addresses', lambda *args: ['93.184.216.34'])
    monkeypatch.setattr(feeds, '_PinnedConnection', Connection)
    with pytest.raises(ValueError, match='大小限制'): feeds._fetch_bytes('https://public.example.org/feed')


def test_source_and_topic_preferences_demote_without_hiding_unrelated_articles():
    from xiaomao.news_quality import apply_feedback
    rows = [dict(url='https://one.example.org/a', source='one.example.org', category='game', topic='三角洲'),
            dict(url='https://two.example.org/b', source='two.example.org', category='game', topic='独立游戏')]
    preferences = [dict(scope='source', target='one.example.org', value='irrelevant')]
    assert [row['url'] for row in apply_feedback(rows, preferences)] == [rows[1]['url'], rows[0]['url']]
    preferences.append(dict(scope='article', target=rows[1]['url'], value='duplicate'))
    assert [row['url'] for row in apply_feedback(rows, preferences)] == [rows[0]['url']]


@pytest.mark.parametrize('encoding', ['utf-16', 'utf-16-le', 'utf-16-be', 'utf-32', 'utf-32-le', 'utf-32-be'])
def test_encoded_xml_dtd_entity_attacks_rejected_before_parse(encoding):
    from xiaomao.news_feeds import parse_feed
    xml = '<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY steal SYSTEM "file:///C:/private.txt">]><rss><channel><title>&steal;</title></channel></rss>'
    with pytest.raises(ValueError, match='实体'): parse_feed(xml.encode(encoding), 'https://news.example.org/feed')


def test_multicast_feed_addresses_rejected():
    from xiaomao.news_feeds import validate_feed_url
    with pytest.raises(ValueError): validate_feed_url('http://224.0.0.1/feed')


def test_feed_connects_pinned_ip_and_checks_tls_for_original_hostname(monkeypatch):
    import xiaomao.news_feeds as feeds
    connections, tls = [], []
    sentinel = SimpleNamespace(close=lambda: None, settimeout=lambda value: None)
    monkeypatch.setattr(feeds.socket, 'create_connection', lambda address, timeout: connections.append((address, timeout)) or sentinel)
    context = SimpleNamespace(wrap_socket=lambda sock, server_hostname: tls.append((sock, server_hostname)) or sentinel)
    monkeypatch.setattr(feeds.ssl, 'create_default_context', lambda: context)
    connection = feeds._PinnedConnection('public.example.org', 443, '93.184.216.34', True, 5)
    connection.connect()
    assert connections == [(('93.184.216.34', 443), 5)]
    assert tls == [(sentinel, 'public.example.org')]
    connection.close()


def test_feed_total_deadline_stops_before_read(monkeypatch):
    import xiaomao.news_feeds as feeds
    times = iter([0, 0, 0, 19])
    monkeypatch.setattr(feeds, 'monotonic', lambda: next(times))
    class Response:
        status = 200
        def getheader(self, key, default=None): return default
        def read(self, n): raise AssertionError('deadline expired; body must not be read')
    class Connection:
        sock = None
        def __init__(self, *args): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(feeds, '_public_addresses', lambda *args: ['93.184.216.34'])
    monkeypatch.setattr(feeds, '_PinnedConnection', Connection)
    with pytest.raises(ValueError, match='超时'): feeds._fetch_bytes('https://public.example.org/feed')


def test_feed_chunked_body_cannot_exceed_limit(monkeypatch):
    import xiaomao.news_feeds as feeds
    monkeypatch.setattr(feeds, 'MAX_FEED_BYTES', 8)
    class Response:
        status = 200
        def getheader(self, key, default=None): return default
        def read(self, n): return b'x' * n
    class Connection:
        sock = None
        def __init__(self, *args): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(feeds, '_public_addresses', lambda *args: ['93.184.216.34'])
    monkeypatch.setattr(feeds, '_PinnedConnection', Connection)
    with pytest.raises(ValueError, match='大小限制'): feeds._fetch_bytes('https://public.example.org/feed')


def test_feed_failure_stays_visible_when_public_search_succeeds(tmp_path, monkeypatch):
    import httpx
    from test_news_research import research, result, completion
    def transport(request):
        return httpx.Response(200, json={'results': [result()]} if request.url.path.endswith('/search') else completion('{}'))
    collector, client = research(tmp_path, transport)
    config = default_subscription()
    config.update(sourceMode='both', feeds=[dict(id='failed-feed', label='失败源', url='https://news.example.org/rss', enabled=True, priority=3, category='', topic='')])
    def fail(*args): raise ValueError('订阅源 XML 格式无效')
    monkeypatch.setattr('xiaomao.news_research.fetch_feed', fail)
    try:
        collected = collector.collect(config, NOW)
        assert collected['articles'] and all(row['sourceType'] == 'search' for row in collected['articles'])
        assert any('公共订阅源失败：失败源' in warning and 'XML' in warning for warning in collected['warnings'])
    finally: collector.close(); client.close()


def test_desktop_feedback_uses_saved_article_authority_and_enforces_scope(tmp_path):
    from dataclasses import replace
    from test_desktop_news import build
    from xiaomao.account_switching import configured_account
    from xiaomao.desktop_service import DesktopService
    cfg, client, service = build(tmp_path)
    sub = service.call('news.save', {'subscription': default_subscription(), 'expected': None})
    other = service.call('news.save', {'subscription': default_subscription(), 'expected': None})
    store = NewsStore(tmp_path / 'runtime')
    article = dict(url='https://news.example.org/item', title='公开项目发布新地图', source='news.example.org', category='game', topic='三角洲')
    edition = store.create_edition(configured_account(cfg), sub['id'], sub, 'preview', '', NOW)
    store.update_edition(configured_account(cfg), edition['id'], articles=[article], parts=['immutable'], state='sent')
    before = deepcopy(store.get_edition(configured_account(cfg), edition['id']))
    try:
        feedback = service.call('news.feedback', dict(subscription=sub['id'], article={**article, 'topic': '伪造主题', 'source': 'evil.example.org'}, value='irrelevant', scope='topic'))
        assert feedback['topic'] == '三角洲' and feedback['source'] == 'news.example.org'
        assert len(service.call('news.feedbackList', {'subscription': sub['id']})['preferences']) == 1
        assert store.get_edition(configured_account(cfg), edition['id']) == before
        with pytest.raises(ValueError, match='不属于'): service.call('news.feedback', dict(subscription=other['id'], article=article, value='like'))
        readonly = DesktopService(tmp_path, client=client, read_only=True)
        for method, params in [('news.feedback', dict(subscription=sub['id'], article=article, value='like')),
                               ('news.clearFeedback', dict(subscription=sub['id'], feedbackId=feedback['id']))]:
            with pytest.raises(ValueError, match='只读'): readonly.call(method, params)
        service.call('news.clearFeedback', dict(subscription=sub['id'], feedbackId=feedback['id']))
        assert not service.call('news.feedbackList', {'subscription': sub['id']})['preferences']
    finally: store.close()


def test_same_title_identical_leading_sentence_does_not_hide_substantial_update(tmp_path):
    import httpx
    from test_news_research import research, result, completion
    rows = [result(url='https://one.example.org/a', title='Project Phoenix introduces offline export',
                   snippet='Offline export is available.', content='发布时间：2026-11-11 10:00。Offline export is available. The release is stable and approved for general use.'),
            result(url='https://two.example.org/b', title='Project Phoenix introduces offline export',
                   snippet='Offline export is available.', content='发布时间：2026-11-11 10:00。Offline export is available. Update: export was withdrawn after a serious security flaw.')]
    def transport(request):
        return httpx.Response(200, json={'results': rows} if request.url.path.endswith('/search') else completion('{}'))
    collector, client = research(tmp_path, transport)
    config = default_subscription(); config['groupSameEvent'] = True
    try: assert len(collector.collect(config, NOW)['articles']) == 2
    finally: collector.close(); client.close()


def test_small_negation_change_in_long_evidence_never_merges():
    from xiaomao.news_quality import group_events
    common = 'Project Phoenix provides offline export with encryption and transparent audited access controls. ' * 8
    one = dict(url='https://one.example.org/a', title='Project Phoenix confirms offline export availability', summary=common + 'Export is available to every user.')
    opposite = {**one, 'url': 'https://two.example.org/b', 'summary': common + 'Export is not available to every user.'}
    assert len(group_events([one, opposite])) == 2


def test_known_events_do_not_starve_new_articles_at_item_limit(tmp_path):
    import httpx
    from test_news_research import research, result, completion
    rows = [result(url='https://one.example.org/old', title='Project Phoenix launches offline export', snippet='Project Phoenix now supports offline export for all users.',
                   content='发布时间：2026-11-11 10:00。Project Phoenix now supports offline export for all users.'),
            result(url='https://two.example.org/new', title='Project Aurora publishes transparent model evaluation', snippet='Project Aurora published detailed model evaluation results.',
                   content='发布时间：2026-11-11 10:00。Project Aurora published detailed model evaluation results.')]
    def transport(request):
        return httpx.Response(200, json={'results': rows} if request.url.path.endswith('/search') else completion('{}'))
    collector, client = research(tmp_path, transport)
    config = default_subscription(); config.update(groupSameEvent=True, itemCount=1)
    config['_seenEventTargets'] = [[dict(url=rows[0]['url'], title=rows[0]['title'], summary=rows[0]['snippet'], publishedAt=NOW-7200)]]
    try: assert collector.collect(config, NOW)['articles'][0]['url'] == rows[1]['url']
    finally: collector.close(); client.close()


def test_feed_watchdog_interrupts_drip_fed_response_headers(monkeypatch):
    import threading
    import xiaomao.news_feeds as feeds
    released = threading.Event()
    original_timer = threading.Timer
    monkeypatch.setattr(feeds.threading, 'Timer', lambda seconds, function, args: original_timer(.03, function, args=args))
    class Socket:
        def shutdown(self, how): released.set()
    class Connection:
        def __init__(self, *args): self.sock = Socket()
        def request(self, *args, **kwargs): pass
        def getresponse(self):
            assert released.wait(1), 'feed watchdog must interrupt incomplete response headers'
            raise OSError('connection interrupted at deadline')
        def close(self): pass
    monkeypatch.setattr(feeds, '_public_addresses', lambda *args: ['93.184.216.34'])
    monkeypatch.setattr(feeds, '_PinnedConnection', Connection)
    with pytest.raises(ValueError, match='超时'): feeds._fetch_bytes('https://public.example.org/feed')
    assert released.is_set()
