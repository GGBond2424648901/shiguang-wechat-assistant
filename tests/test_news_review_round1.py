"""Regressions reproduced by the independent news review, round 1."""
from copy import deepcopy

import httpx
import pytest

from test_news_research import NOW, completion, research, result
from test_news_runtime import rig, preview
from xiaomao.news import default_subscription
from xiaomao.news_feeds import parse_feed
from xiaomao.news_quality import group_events, same_event, substantive_update

NUMERIC_CHANGES = [('1.2', '12'), ('-5%', '5%'), ('3-2', '32'),
                   ('2026-11-1', '2026-1-11'), ('<=5', '>=5'), ('5<10', '5>10'), ('5%', '5')]


def event(value, url='https://news.example.org/event', title_value=True):
    return dict(url=url, title='Project Phoenix reports the final result' + (' ' + value if title_value else ''),
                summary='Project Phoenix confirms the final result is ' + value + ' for this release.',
                publishedAt=NOW - 3600, category='technology', topic='', source='news.example.org', sourceType='search')


@pytest.mark.parametrize('old_value,new_value', NUMERIC_CHANGES)
@pytest.mark.parametrize('title_value', [True, False])
def test_numeric_punctuation_and_comparisons_stay_distinct(old_value, new_value, title_value):
    old, new = event(old_value, title_value=title_value), event(new_value, 'https://other.example.org/event', title_value)
    assert not same_event(old, new)
    assert substantive_update(new, old)
    assert len(group_events([old, new])) == 2


@pytest.mark.parametrize('old_value,new_value', NUMERIC_CHANGES)
@pytest.mark.parametrize('same_url', [True, False])
def test_confirmed_numeric_change_survives_target_seen_fence(rig, old_value, new_value, same_url):
    old = event(old_value)
    new = event(new_value, old['url'] if same_url else 'https://other.example.org/update')
    rig.store.seen[('account-a', 'account-a/friend')] = {old['url']}
    rig.store.seen_events = lambda *args: [old]
    row = preview(rig)
    row['snapshot']['groupSameEvent'] = True
    row['articles'] = [{**row['articles'][0], **new}]
    prepared = rig.runtime._prepare_deliveries('account-a', row, rig.clock[0])
    assert [part['url'] for part in prepared['deliveries'][0]['parts']] == [new['url']]


@pytest.mark.parametrize('old_value,new_value', NUMERIC_CHANGES[:3])
@pytest.mark.parametrize('same_url', [True, False])
def test_unknown_receipt_fence_preserves_numeric_event_identity(rig, old_value, new_value, same_url):
    old = event(old_value)
    new = event(new_value, old['url'] if same_url else 'https://other.example.org/update')
    rig.store.uncertain[('account-a', 'account-a/friend')] = {old['url']}
    rig.store.uncertain_events = lambda *args: [old]
    row = preview(rig)
    rig.store.update_edition('account-a', row['id'], state='waiting', publishRequested=True,
        snapshot={**row['snapshot'], 'groupSameEvent': True}, articles=[{**row['articles'][0], **new}], parts=['numeric update'])
    rig.runtime.tick()
    current = rig.store.get_edition('account-a', row['id'])
    if same_url:
        assert not rig.sent, 'ambiguous native receipt must still quarantine the exact URL'
        assert current['deliveries'][0]['parts'][0]['state'] == 'unknown'
    else:
        assert len(rig.sent) == 1, 'a different numeric result at another URL is a distinct event, not a repost'
        assert current['deliveries'][0]['parts'][0]['state'] == 'queued'


def test_combined_search_feed_duplicate_inherits_priority_before_item_limit(tmp_path, monkeypatch):
    ordinary = result(url='https://news.example.org/ordinary', title='Project Aurora publishes its evaluation results')
    preferred = result(url='https://news.example.org/preferred', title='Project Phoenix publishes its offline release')
    def transport(request):
        return httpx.Response(200, json={'results': [ordinary, preferred]} if request.url.path.endswith('/search') else completion('{}'))
    collector, client = research(tmp_path, transport)
    direct = dict(url=preferred['url'], title=preferred['title'], snippet='Project Phoenix released offline export.', sourceType='feed')
    monkeypatch.setattr('xiaomao.news_research.fetch_feed', lambda *args: [direct])
    config = default_subscription()
    config.update(sourceMode='both', itemCount=1, feeds=[dict(id='official', url='https://news.example.org/feed', label='Official', enabled=True,
        priority=10, category='technology', topic='')])
    try:
        rows = collector.collect(config, NOW)['articles']
        assert rows[0]['url'] == preferred['url']
        assert rows[0]['sourcePriority'] == 10
        assert set(rows[0]['collectedVia']) == {'search', 'feed'}
        assert rows[0]['feedId'] == 'official'
    finally: collector.close(); client.close()


def test_feed_only_priority_orders_sources_before_item_limit(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from xiaomao.news_research import NewsResearch
    config = default_subscription()
    config.update(sourceMode='feed', itemCount=1, feeds=[
        dict(id='low', url='https://low.example.org/feed', label='Low', enabled=True, priority=0, category='technology', topic=''),
        dict(id='high', url='https://high.example.org/feed', label='High', enabled=True, priority=10, category='technology', topic='')])
    def fetch(url, *args):
        host = 'high' if 'high.example' in url else 'low'
        return [dict(url=f'https://{host}.example.org/article', title='Project ' + host + ' publishes complete public results',
                     snippet='The team published the verified release details.', sourceType='feed')]
    monkeypatch.setattr('xiaomao.news_research.fetch_feed', fetch)
    collector = NewsResearch(SimpleNamespace(settings=SimpleNamespace(runtime=tmp_path), credentials=SimpleNamespace(get_keys=lambda: [])))
    try:
        row = collector.collect(config, NOW)['articles'][0]
        assert row['url'] == 'https://high.example.org/article' and row['sourcePriority'] == 10
    finally: collector.close()


@pytest.mark.parametrize('channel_base,item_base,link_base,expected', [
    ('https://news.example.org/articles/', '', '', 'https://news.example.org/articles/item'),
    ('../articles/', 'releases/', '', 'https://news.example.org/articles/releases/item'),
    ('../articles/', 'releases/', '../notes/', 'https://news.example.org/articles/notes/item'),
])
def test_rss_xml_base_follows_root_channel_item_link_path(channel_base, item_base, link_base, expected):
    xml = (f'<rss version="2.0" xml:base="https://news.example.org/feeds/"><channel xml:base="{channel_base}">'
           f'<item xml:base="{item_base}"><title>Project release</title><link xml:base="{link_base}">item</link>'
           '<description>Verified release details.</description></item></channel></rss>')
    rows = parse_feed(xml.encode(), 'https://unrelated.example.org/rss')
    assert rows[0]['url'] == expected


@pytest.mark.parametrize('unsafe_base', ['http://127.0.0.1/internal/', 'file:///C:/private/', 'https://user:pass@news.example.org/private/'])
def test_inherited_xml_base_cannot_turn_relative_links_into_unsafe_sources(unsafe_base):
    xml = (f'<rss version="2.0"><channel xml:base="{unsafe_base}"><item><title>Private</title><link>item</link>'
           '<description>Must not be admitted.</description></item></channel></rss>')
    assert parse_feed(xml.encode(), 'https://news.example.org/feed') == []


@pytest.mark.parametrize('old_value,new_value', NUMERIC_CHANGES)
@pytest.mark.parametrize('grouping', [False, True])
def test_collector_keeps_distinct_numeric_results_before_title_dedup(tmp_path, old_value, new_value, grouping):
    rows = [result(url='https://news.example.org/old', title='Project Phoenix reports final result ' + old_value,
                   snippet='The project confirms the result is ' + old_value + ' for this release.'),
            result(url='https://other.example.org/new', title='Project Phoenix reports final result ' + new_value,
                   snippet='The project confirms the result is ' + new_value + ' for this release.')]
    def transport(request):
        return httpx.Response(200, json={'results': rows} if request.url.path.endswith('/search') else completion('{}'))
    collector, client = research(tmp_path, transport)
    config = default_subscription(); config.update(groupSameEvent=grouping, itemCount=2)
    try: assert {row['url'] for row in collector.collect(config, NOW)['articles']} == {row['url'] for row in rows}
    finally: collector.close(); client.close()


@pytest.mark.parametrize('old_value,new_value', NUMERIC_CHANGES[:3])
def test_collector_retains_same_url_numeric_update_after_verified_round(tmp_path, old_value, new_value):
    old = event(old_value)
    new = result(url=old['url'], title=event(new_value)['title'], snippet=event(new_value)['summary'],
                 content='发布时间：2026-11-11 10:00。' + event(new_value)['summary'])
    def transport(request):
        return httpx.Response(200, json={'results': [new]} if request.url.path.endswith('/search') else completion('{}'))
    collector, client = research(tmp_path, transport)
    config = default_subscription(); config.update(groupSameEvent=True, itemCount=1, _seenEventTargets=[[old]])
    try: assert collector.collect(config, NOW, exclude_urls=[old['url']])['articles'][0]['url'] == old['url']
    finally: collector.close(); client.close()


@pytest.mark.parametrize('old_value,new_value', NUMERIC_CHANGES)
def test_headline_only_numeric_change_cannot_hide_behind_similar_title(old_value, new_value):
    old = event(old_value)
    new = {**old, 'title': event(new_value)['title'], 'url': 'https://other.example.org/event'}
    assert not same_event(old, new)
    assert substantive_update(new, old)


def test_combined_duplicate_priority_keeps_category_round_robin(tmp_path, monkeypatch):
    game = result(url='https://news.example.org/game', title='游戏项目公开新的地图和武器设计')
    ordinary = result(url='https://news.example.org/ordinary', title='Project Aurora publishes its evaluation results')
    preferred = result(url='https://news.example.org/preferred', title='Project Phoenix publishes its offline release')
    def transport(request):
        import json
        if request.url.path.endswith('/search'):
            query = json.loads(request.content)['query']
            return httpx.Response(200, json={'results': [game] if '游戏' in query else [ordinary, preferred]})
        return httpx.Response(200, json=completion('{}'))
    collector, client = research(tmp_path, transport)
    monkeypatch.setattr('xiaomao.news_research.fetch_feed', lambda *args: [dict(url=preferred['url'], title=preferred['title'], snippet='Direct release notes.', sourceType='feed')])
    config = default_subscription()
    config.update(categories=['game', 'technology'], sourceMode='both', itemCount=2,
        feeds=[dict(id='preferred', url='https://news.example.org/feed', label='Preferred', enabled=True, priority=10, category='technology', topic='')])
    try:
        rows = collector.collect(config, NOW)['articles']
        assert {row['category'] for row in rows} == {'game', 'technology'}
        assert {row['url'] for row in rows} == {game['url'], preferred['url']}
        assert next(row for row in rows if row['url'] == preferred['url'])['category'] == 'technology'
    finally: collector.close(); client.close()


def test_event_association_retains_priority_of_a_direct_feed_repost():
    original = {**event('1.2'), 'sourcePriority': 0, 'collectedVia': ['search']}
    direct = {**original, 'url': 'https://official.example.org/release', 'sourceType': 'feed', 'sourcePriority': 10,
              'feedId': 'official', 'collectedVia': ['feed']}
    grouped = group_events([original, direct])
    assert len(grouped) == 1 and grouped[0]['sourcePriority'] == 10
    assert set(grouped[0]['collectedVia']) == {'search', 'feed'}
    assert grouped[0]['sources'][1]['sourceType'] == 'feed'


@pytest.mark.parametrize('level', ['rss', 'channel', 'item', 'link'])
def test_private_xml_base_at_any_ancestor_is_rejected(level):
    def attr(tag): return ' xml:base="http://127.0.0.1/private/"' if tag == level else ''
    xml = (f'<rss version="2.0"{attr("rss")}><channel{attr("channel")}><item{attr("item")}><title>Private</title>'
           f'<link{attr("link")}>item</link><description>Private inherited base.</description></item></channel></rss>')
    assert parse_feed(xml.encode(), 'https://news.example.org/feed') == []


def test_atom_inherited_root_entry_link_bases_stay_correct():
    xml = ('<feed xmlns="http://www.w3.org/2005/Atom" xml:base="../root/">'
           '<entry xml:base="entries/"><title>Public entry</title><link xml:base="../articles/" href="item"/>'
           '<summary>Public article body.</summary></entry></feed>')
    assert parse_feed(xml.encode(), 'https://news.example.org/feeds/atom')[0]['url'] == 'https://news.example.org/root/articles/item'
