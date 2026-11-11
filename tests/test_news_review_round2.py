"""Round 2: terminal prose formatting is not evidence for a resend."""
import httpx
import pytest

from test_news_research import NOW, completion, research, result
from test_news_runtime import rig, preview
from xiaomao.news import default_subscription
from xiaomao.news_quality import same_event, substantive_update

URL = 'https://news.example.org/event'
TITLE = 'Project Phoenix confirms offline release availability'
BODY = 'Project Phoenix confirms offline export is available for all users'


def article(ending='.', title_ending=''):
    return dict(url=URL, title=TITLE + title_ending, summary=BODY + ending,
                source='news.example.org', category='technology', topic='', publishedAt=NOW - 3600)


@pytest.mark.parametrize('ending', ['!', '。', '！', '...', '!!', ''])
def test_terminal_prose_punctuation_is_not_a_substantive_update(ending):
    old, new = article('.'), article(ending)
    assert same_event(old, new)
    assert not substantive_update(new, old)


@pytest.mark.parametrize('title_ending', ['.', '!', '。', '！'])
def test_terminal_title_punctuation_is_not_a_substantive_update(title_ending):
    old, new = article(), article(title_ending=title_ending)
    assert same_event(old, new)
    assert not substantive_update(new, old)


@pytest.mark.parametrize('grouping', [False, True])
@pytest.mark.parametrize('ending', ['!', '。', '！', '...'])
def test_collector_excludes_sent_same_url_with_formatting_only_edit(tmp_path, grouping, ending):
    old = article('.')
    new = result(url=URL, title=TITLE, snippet=BODY + ending,
                 content='发布时间：2026-11-11 10:00。' + BODY + ending)
    def transport(request):
        return httpx.Response(200, json={'results': [new]} if request.url.path.endswith('/search') else completion('{}'))
    collector, client = research(tmp_path, transport)
    config = default_subscription(); config.update(groupSameEvent=grouping, _seenEventTargets=[[old]])
    try:
        collected = collector.collect(config, NOW, exclude_urls=[URL])
        assert not collected['articles'] and not collected['parts']
    finally: collector.close(); client.close()


@pytest.mark.parametrize('grouping', [False, True])
@pytest.mark.parametrize('retained_evidence', [False, True])
def test_native_verified_url_fence_keeps_terminal_edit_out_of_queue(rig, grouping, retained_evidence):
    old, new = article('.'), article('!')
    if retained_evidence:
        old['eventEvidence'], new['eventEvidence'] = old['summary'], new['summary']
    rig.store.seen[('account-a', 'account-a/friend')] = {URL}
    rig.store.seen_events = lambda *args: [old]
    row = preview(rig)
    rig.store.update_edition('account-a', row['id'], state='waiting', publishRequested=True,
        snapshot={**row['snapshot'], 'groupSameEvent': grouping}, articles=[{**row['articles'][0], **new}], parts=['unchanged fact'])
    rig.runtime.tick()
    current = rig.store.get_edition('account-a', row['id'])
    assert not rig.sent
    assert current['deliveries'][0]['state'] == 'verified'
    assert current['deliveries'][0]['parts'] == []


@pytest.mark.parametrize('new_body', [
    'Project Phoenix confirms offline export is not available for all users.',
    'Project Phoenix withdrew offline export after discovering a security vulnerability.',
])
def test_real_factual_body_change_still_qualifies_as_update(new_body):
    old = article('.')
    new = {**old, 'summary': new_body}
    assert not same_event(old, new)
    assert substantive_update(new, old)


def test_word_boundaries_remain_part_of_event_identity():
    old = article('.')
    new = {**old, 'summary': old['summary'].replace('all users', 'allusers')}
    assert not same_event(old, new)


@pytest.mark.parametrize('new_body', [
    'Project Phoenix confirms offline export is not available for all users.',
    'Project Phoenix withdrew offline export after discovering a security vulnerability.',
])
def test_collector_still_allows_factual_body_update_at_verified_url(tmp_path, new_body):
    old = article('.')
    new = result(url=URL, title=TITLE, snippet=new_body, content='发布时间：2026-11-11 10:00。' + new_body)
    def transport(request):
        return httpx.Response(200, json={'results': [new]} if request.url.path.endswith('/search') else completion('{}'))
    collector, client = research(tmp_path, transport)
    config = default_subscription(); config.update(groupSameEvent=True, _seenEventTargets=[[old]])
    try: assert collector.collect(config, NOW, exclude_urls=[URL])['articles'][0]['summary'] == new_body
    finally: collector.close(); client.close()


@pytest.mark.parametrize('new_body', [
    'Project Phoenix confirms offline export is not available for all users.',
    'Project Phoenix withdrew offline export after discovering a security vulnerability.',
])
def test_native_verified_url_fence_still_allows_factual_body_update(rig, new_body):
    old, new = article('.'), {**article('.'), 'summary': new_body, 'eventEvidence': new_body}
    old['eventEvidence'] = old['summary']
    rig.store.seen[('account-a', 'account-a/friend')] = {URL}
    rig.store.seen_events = lambda *args: [old]
    row = preview(rig)
    rig.store.update_edition('account-a', row['id'], state='waiting', publishRequested=True,
        snapshot={**row['snapshot'], 'groupSameEvent': True}, articles=[{**row['articles'][0], **new}], parts=['real update'])
    rig.runtime.tick()
    assert len(rig.sent) == 1
    assert rig.store.get_edition('account-a', row['id'])['deliveries'][0]['parts'][0]['state'] == 'queued'
