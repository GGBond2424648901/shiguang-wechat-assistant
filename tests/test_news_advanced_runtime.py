from copy import deepcopy
from test_news_runtime import rig, preview


def test_grouped_sources_record_aliases_only_after_native_receipt(rig):
    row = preview(rig)
    article = row['articles'][0]
    alias = 'https://repost.example.org/same-event'
    article['sources'] = [{'url': article['url']}, {'url': alias}]
    rig.store.update_edition('account-a', row['id'], articles=[article], parts=['first'], snapshot={**row['snapshot'], 'groupSameEvent': True})
    rig.runtime.command('sub1', 'push', row['id'])
    rig.runtime.tick()
    assert not rig.store.seen_urls('account-a', 'account-a/friend')
    task = rig.sent[-1][3]
    rig.states[task] = dict(state='verified', attempted=True)
    rig.runtime.tick()
    assert {article['url'], alias} <= rig.store.seen_urls('account-a', 'account-a/friend')


def test_new_url_same_event_skipped_per_target_but_real_update_retained(rig):
    sent = dict(url='https://one.example.org/a', title='Project Phoenix launches offline export',
                summary='Project Phoenix now supports offline export for all users.', publishedAt=rig.clock[0])
    rig.store.seen_events = lambda account, scope: [deepcopy(sent)] if scope == 'account-a/friend' else []
    rig.store.mark_event_seen = lambda *args: None
    row = preview(rig)
    repost = {**row['articles'][0], **sent, 'url': 'https://two.example.org/repost'}
    update = {**row['articles'][1], **sent, 'url': 'https://three.example.org/update',
              'summary': 'Project Phoenix withdrew offline export due to a security flaw.'}
    row['articles'] = [repost, update]
    row['snapshot']['groupSameEvent'] = True
    prepared = rig.runtime._prepare_deliveries('account-a', row, rig.clock[0])
    assert [part['url'] for part in prepared['deliveries'][0]['parts']] == [update['url']]
    other = deepcopy(row)
    other['snapshot']['targets'][0]['scope'] = 'account-a/another-friend'
    prepared = rig.runtime._prepare_deliveries('account-a', other, rig.clock[0])
    assert len(prepared['deliveries'][0]['parts']) == 2


def test_same_url_substantial_update_is_delivered_when_grouping_enabled(rig):
    old = dict(url='https://example.com/news/1', title='Project Phoenix launches offline export',
               summary='Project Phoenix now supports offline export for all users.', publishedAt=rig.clock[0])
    rig.store.seen[('account-a', 'account-a/friend')] = {old['url']}
    rig.store.seen_events = lambda *args: [old]
    row = preview(rig)
    updated = {**row['articles'][0], **old, 'summary': 'Project Phoenix withdrew offline export due to a security flaw.'}
    row['articles'] = [updated]
    row['snapshot']['groupSameEvent'] = True
    prepared = rig.runtime._prepare_deliveries('account-a', row, rig.clock[0])
    assert len(prepared['deliveries'][0]['parts']) == 1


def test_same_event_under_new_url_waits_behind_pending_native_send(rig):
    old = dict(url='https://one.example.org/a', title='Project Phoenix launches offline export',
               summary='Project Phoenix now supports offline export for all users.', publishedAt=rig.clock[0])
    first = preview(rig)
    rig.store.update_edition('account-a', first['id'], articles=[{**first['articles'][0], **old}], parts=['first'],
        snapshot={**first['snapshot'], 'groupSameEvent': True})
    rig.runtime.command('sub1', 'push', first['id']); rig.runtime.tick()
    assert len(rig.sent) == 1
    # Another subscription can collect the same event under a different URL.
    second_config = {**rig.config, 'id': 'sub2'}
    rig.store.subs[('account-a', 'sub2')] = second_config
    second = rig.store.create_edition('account-a', 'sub2', {**second_config, 'groupSameEvent': True}, 'preview', '', rig.clock[0])
    rig.store.update_edition('account-a', second['id'], state='waiting', publishRequested=True, articles=[{**first['articles'][0], **old, 'url': 'https://two.example.org/repost'}], parts=['second'])
    rig.runtime.tick()
    assert len(rig.sent) == 1

