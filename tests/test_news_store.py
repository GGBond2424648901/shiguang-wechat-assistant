from concurrent.futures import ThreadPoolExecutor
import os

import pytest

from xiaomao.news import default_subscription
from xiaomao.news_store import NewsStore


def test_pending_audits_every_native_intent_even_behind_many_ready_previews(tmp_path):
    store = NewsStore(tmp_path)
    config = store.save_subscription('account', default_subscription())
    first = store.create_edition('account', config['id'], config, 'preview', '', 1)
    store.update_edition('account', first['id'], state='waiting', deliveries=[{
        'scope':'scope','parts':[{'requestId':'news-intent','taskId':'','state':'dispatching'}]}])
    for index in range(65):
        row = store.create_edition('account', config['id'], config, 'preview', '', index+2)
        store.update_edition('account', row['id'], state='ready')
    assert first['id'] in {row['id'] for row in store.pending('account')}
    store.delete_subscription('account', config['id'], config['revision'])
    assert store.get_edition('account',first['id'])['receiptPending'] is True
    assert first['id'] in {row['id'] for row in store.pending('account')}
    store.close()


def test_subscriptions_persist_and_require_revision_under_account_isolation(tmp_path):
    store = NewsStore(tmp_path)
    config = default_subscription()
    config.update(recipients=['chat-1'], targets=[{'session': 'chat-1', 'name': '测试', 'scope': 'scope-1'}])
    saved = store.save_subscription('account-a', config)
    assert saved['revision'] == 1 and saved['targets'][0]['scope'] == 'scope-1'
    assert store.list_subscriptions('account-b') == []
    assert store.get_subscription('account-b', saved['id']) is None
    with pytest.raises(ValueError, match='版本|冲突|刷新'):
        store.save_subscription('account-a', {**saved, 'name': '旧版本'})
    saved = store.save_subscription('account-a', {**saved, 'name': '新名称'}, expected_revision=1)
    assert saved['revision'] == 2
    store.close()
    again = NewsStore(tmp_path)
    assert again.get_subscription('account-a', saved['id'])['name'] == '新名称'
    again.close()


def test_concurrent_saves_cannot_both_overwrite_the_same_revision(tmp_path):
    stores = [NewsStore(tmp_path), NewsStore(tmp_path)]
    saved = stores[0].save_subscription('account', default_subscription())

    def update(index):
        try:
            return stores[index].save_subscription('account', {**saved, 'name': str(index)}, 1)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, [0, 1]))
    assert sum(row is not None for row in results) == 1
    assert stores[0].get_subscription('account', saved['id'])['revision'] == 2
    for store in stores:
        store.close()


def test_daily_slots_are_unique_but_preview_and_accounts_remain_independent(tmp_path):
    store = NewsStore(tmp_path)
    saved = store.save_subscription('account-a', default_subscription())
    store.save_subscription('account-b', saved)
    first = store.create_edition('account-a', saved['id'], saved, 'scheduled', '2026-11-11', 100)
    assert first['state'] == 'collecting' and first['articles'] == []
    assert first['warnings'] == first['deliveries'] == first['parts'] == []
    assert store.create_edition('account-a', saved['id'], saved, 'scheduled', '2026-11-11', 101) is None
    assert store.create_edition('account-b', saved['id'], saved, 'scheduled', '2026-11-11', 101)
    assert store.create_edition('account-a', saved['id'], saved, 'preview', '', 102)
    assert store.create_edition('account-a', saved['id'], saved, 'preview', '', 103)
    assert len(store.history('account-a', saved['id'])) == 3
    assert store.get_edition('account-b', first['id']) is None
    store.close()


def test_delete_cancels_unfinished_editions_and_late_workers_cannot_revive_them(tmp_path):
    store = NewsStore(tmp_path)
    saved = store.save_subscription('account', default_subscription())
    edition = store.create_edition('account', saved['id'], saved, 'preview', '', 100)
    with pytest.raises(ValueError):
        store.delete_subscription('account', saved['id'], 99)
    store.delete_subscription('account', saved['id'], 1)
    assert store.list_subscriptions('account') == []
    late = store.update_edition('account', edition['id'], state='ready', articles=[{'title': 'late'}])
    assert late['state'] == 'cancelled' and late['articles'] == []
    assert store.history('account', saved['id'])[0]['id'] == edition['id']
    assert store.create_edition('account', saved['id'], saved, 'preview', '', 102) is None
    store.close()


def test_merge_updates_are_durable_and_preview_does_not_mark_sources_seen(tmp_path):
    store = NewsStore(tmp_path)
    saved = store.save_subscription('account', default_subscription())
    edition = store.create_edition('account', saved['id'], saved, 'preview', '', 100)
    store.update_edition('account', edition['id'], state='ready', articles=[{'url': 'https://example.org/a'}],
                         publishRequested=True, recipientProgress={'scope': 1})
    updated = store.update_edition('account', edition['id'], warnings=['有来源时间未知'])
    assert updated['recipientProgress'] == {'scope': 1} and updated['publishRequested']
    assert len(store.pending('account')) == 1
    assert store.seen_urls('account', 'scope') == set()
    store.mark_seen('account', 'scope', ['https://example.org/a?utm_source=share#1'], 101)
    assert store.seen_urls('account', 'scope') == {'https://example.org/a'}
    assert store.seen_urls('other', 'scope') == store.seen_urls('account', 'other') == set()
    with pytest.raises(ValueError):
        store.mark_seen('account', 'scope', ['http://127.0.0.1/private'], 102)
    store.update_edition('account', edition['id'], state='sent')
    assert store.pending('account') == []
    assert len(store.history('account', None, limit=1)) == 1
    store.close()


@pytest.mark.parametrize('account', ['', None, 'x' * 201])
def test_implicit_or_unbounded_accounts_are_rejected(tmp_path, account):
    store = NewsStore(tmp_path)
    with pytest.raises(ValueError):
        store.list_subscriptions(account)
    store.close()


@pytest.mark.parametrize('state', ['verified', 'unknown', 'partial'])
def test_completed_delivery_editions_do_not_reenter_pending_after_restart(tmp_path, state):
    store = NewsStore(tmp_path)
    saved = store.save_subscription('account', default_subscription())
    edition = store.create_edition('account', saved['id'], saved, 'preview', '', 100)
    store.update_edition('account', edition['id'], state=state)
    store.close()
    again = NewsStore(tmp_path)
    assert again.pending('account') == []
    again.delete_subscription('account', saved['id'], 1)
    assert again.get_edition('account', edition['id'])['state'] == state
    again.close()


def test_database_and_journal_are_private_to_the_current_os_user(tmp_path):
    folder = tmp_path / 'private-news'
    store = NewsStore(folder)
    store.save_subscription('account', default_subscription())
    paths = [folder, store.path, *folder.glob('news.sqlite3-*')]
    if os.name == 'nt':
        import win32api
        import win32con
        import win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            for path in paths:
                descriptor = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
                acl = descriptor.GetSecurityDescriptorDacl()
                assert acl.GetAceCount() == 1
                assert acl.GetAce(0)[2] == sid
        finally:
            token.Close()
    else:
        assert all(path.stat().st_mode & 0o077 == 0 for path in paths)
    store.close()


def test_cancelled_inflight_receipts_remain_auditable_without_reviving_edition(tmp_path):
    store = NewsStore(tmp_path)
    saved = store.save_subscription('account', default_subscription())
    edition = store.create_edition('account', saved['id'], saved, 'preview', '', 100)
    store.update_edition('account', edition['id'], state='cancelled', receiptPending=True,
                         deliveries=[{'scope': 's', 'parts': [{'taskId': 'task', 'state': 'queued'}]}])
    store.delete_subscription('account', saved['id'], 1)
    assert [row['id'] for row in store.pending('account')] == [edition['id']]
    audited = store.update_edition('account', edition['id'], state='ready', articles=[{'title': 'late'}],
                                   receiptPending=False,
                                   deliveries=[{'scope': 's', 'parts': [{'taskId': 'task', 'state': 'verified'}]}])
    assert audited['state'] == 'cancelled' and audited['articles'] == []
    assert audited['deliveries'][0]['parts'][0]['state'] == 'verified'
    assert not audited['receiptPending'] and store.pending('account') == []
    store.close()


def test_deleting_subscription_retains_queued_receipt_audit(tmp_path):
    store = NewsStore(tmp_path)
    saved = store.save_subscription('account', default_subscription())
    edition = store.create_edition('account', saved['id'], saved, 'preview', '', 100)
    store.update_edition('account', edition['id'], state='publishing',
                         deliveries=[{'scope': 's', 'parts': [{'taskId': 'task', 'state': 'queued'}]}])
    store.delete_subscription('account', saved['id'], 1)
    pending = store.pending('account')
    assert len(pending) == 1 and pending[0]['state'] == 'cancelled'
    assert pending[0]['receiptPending']
    store.close()


def test_uncertain_sources_survive_history_pagination_and_restart(tmp_path):
    store = NewsStore(tmp_path)
    saved = store.save_subscription('account', default_subscription())
    old = store.create_edition('account', saved['id'], saved, 'preview', '', 1)
    store.update_edition('account', old['id'], state='unknown')
    store.mark_uncertain('account', 'scope', ['https://example.org/old?utm_source=search#top'], 2)
    for index in range(129):
        row = store.create_edition('account', saved['id'], saved, 'preview', '', index + 3)
        store.update_edition('account', row['id'], state='verified')
    assert old['id'] not in {row['id'] for row in store.history('account', limit=128)}
    store.close()
    again = NewsStore(tmp_path)
    assert again.uncertain_urls('account', 'scope') == {'https://example.org/old'}
    assert again.seen_urls('account', 'scope') == set()
    assert again.uncertain_urls('other', 'scope') == again.uncertain_urls('account', 'other') == set()
    again.close()


def test_late_verified_receipt_clears_only_its_account_scope_quarantine(tmp_path):
    store = NewsStore(tmp_path)
    source = 'https://example.org/item'
    store.mark_uncertain('account', 'scope', [source], 1)
    store.mark_uncertain('account', 'other', [source], 1)
    store.mark_uncertain('other', 'scope', [source], 1)
    store.mark_seen('account', 'scope', [source + '?utm_source=receipt#top'], 2)
    assert store.uncertain_urls('account', 'scope') == set()
    assert store.seen_urls('account', 'scope') == {source}
    assert store.uncertain_urls('account', 'other') == store.uncertain_urls('other', 'scope') == {source}
    # A stale pending callback cannot quarantine a source after verified won.
    store.mark_uncertain('account', 'scope', [source], 3)
    assert store.uncertain_urls('account', 'scope') == set()
    store.close()


@pytest.mark.parametrize('urls', [
    ['http://127.0.0.1/private'], ['https://example.org/a', 'file:///private'],
    ['https://example.org/a'] * 97, 'https://example.org/a',
])
def test_uncertain_sources_reject_unsafe_or_unbounded_batches_atomically(tmp_path, urls):
    store = NewsStore(tmp_path)
    with pytest.raises(ValueError):
        store.mark_uncertain('account', 'scope', urls, 1)
    assert store.uncertain_urls('account', 'scope') == set()
    store.close()


@pytest.mark.parametrize('state', ['failed', 'partial'])
def test_terminal_editions_with_inflight_receipts_remain_pending_until_audited(tmp_path, state):
    store = NewsStore(tmp_path)
    saved = store.save_subscription('account', default_subscription())
    edition = store.create_edition('account', saved['id'], saved, 'preview', '', 100)
    store.update_edition('account', edition['id'], state=state, receiptPending=True,
                         deliveries=[{'scope': 's', 'parts': [{'taskId': 'task', 'state': 'queued'}]}])
    assert [row['id'] for row in store.pending('account')] == [edition['id']]
    store.update_edition('account', edition['id'], receiptPending=False,
                         deliveries=[{'scope': 's', 'parts': [{'taskId': 'task', 'state': 'verified'}]}])
    assert store.pending('account') == []
    assert store.get_edition('account', edition['id'])['state'] == state
    store.close()
