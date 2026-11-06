"""Credential edits use identity and never reveal stored secret material to UI."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from xiaomao.credentials import CredentialError, CredentialStore


PRIMARY = 'sk-test-only-primary-AAAA'
BACKUP = 'sk-test-only-backup-BBBB'
EXTRA = 'sk-test-only-extra-CCCC'


def summaries(store):
    assert callable(getattr(store, 'list_keys', None)), 'Saved keys need safe stable summaries'
    return store.list_keys()


def test_saved_summaries_survive_reload_without_exposing_keys(tmp_path):
    store = CredentialStore(tmp_path)
    store.set_keys([PRIMARY, BACKUP])
    rows = summaries(store)
    assert [row.suffix for row in rows] == ['AAAA', 'BBBB']
    assert len({row.identifier for row in rows}) == 2
    assert rows == summaries(CredentialStore(tmp_path))
    assert all(key not in repr(rows) for key in (PRIMARY, BACKUP))
    assert all('sk-' not in row.identifier for row in rows)


def test_stale_row_deletes_its_key_after_another_row_was_removed(tmp_path):
    store = CredentialStore(tmp_path)
    store.set_keys([PRIMARY, BACKUP, EXTRA])
    primary, backup, _ = summaries(store)
    CredentialStore(tmp_path).remove_key(primary.identifier)
    store.remove_key(backup.identifier)
    assert CredentialStore(tmp_path).get_keys() == (EXTRA,)
    assert all(key.encode() not in store.path.read_bytes() for key in (PRIMARY, BACKUP, EXTRA))


def test_stale_removed_row_cannot_delete_a_new_key(tmp_path):
    store = CredentialStore(tmp_path)
    store.set_keys([PRIMARY, BACKUP])
    stale = summaries(store)[1]
    store.set_keys([PRIMARY, EXTRA])
    with pytest.raises(CredentialError, match='列表已变化'):
        store.remove_key(stale.identifier)
    assert store.get_keys() == (PRIMARY, EXTRA)


def test_concurrent_append_and_remove_preserve_the_addition(tmp_path):
    store = CredentialStore(tmp_path)
    store.set_keys([PRIMARY, BACKUP])
    identifier = summaries(store)[0].identifier
    with ThreadPoolExecutor(2) as pool:
        deleted = pool.submit(CredentialStore(tmp_path).remove_key, identifier)
        added = pool.submit(CredentialStore(tmp_path).add_keys, [EXTRA])
        deleted.result(); added.result()
    assert store.get_keys() == (BACKUP, EXTRA)


def test_concurrent_deletions_keep_one_key(tmp_path):
    store = CredentialStore(tmp_path)
    store.set_keys([PRIMARY, BACKUP])
    rows = summaries(store)
    def remove(row):
        try:
            CredentialStore(tmp_path).remove_key(row.identifier)
        except CredentialError as error:
            return str(error)
        return 'removed'
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(remove, rows))
    assert outcomes.count('removed') == 1
    assert any('至少保留' in value for value in outcomes)
    assert store.count == 1


def test_last_key_cannot_be_removed_and_ciphertext_is_unchanged(tmp_path):
    store = CredentialStore(tmp_path)
    store.set_key(PRIMARY)
    row = summaries(store)[0]
    before = store.path.read_bytes()
    with pytest.raises(CredentialError, match='至少保留'):
        store.remove_key(row.identifier)
    assert store.path.read_bytes() == before


def test_unconfigured_store_has_no_safe_rows(tmp_path):
    assert summaries(CredentialStore(tmp_path)) == ()
