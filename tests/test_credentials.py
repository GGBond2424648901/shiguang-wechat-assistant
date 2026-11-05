import os
import pytest


@pytest.mark.skipif(os.name != 'nt', reason='DPAPI is Windows user scoped')
def test_key_is_encrypted_at_rest_and_survives_new_store(tmp_path):
    from xiaomao.credentials import CredentialStore
    key = 'sk-test-only-never-a-real-api-key'
    store = CredentialStore(tmp_path)
    store.set_key(key)
    assert CredentialStore(tmp_path).get_key() == key
    assert store.available
    assert key.encode() not in store.path.read_bytes()
    assert store.path.suffix == '.dpapi'


@pytest.mark.skipif(os.name != 'nt', reason='DPAPI is Windows user scoped')
def test_corrupted_ciphertext_does_not_expose_key_material(tmp_path):
    from xiaomao.credentials import CredentialStore, CredentialError
    store = CredentialStore(tmp_path)
    store.set_key('sk-test-another-fake-key')
    store.path.write_bytes(b'broken ciphertext')
    with pytest.raises(CredentialError) as caught:
        store.get_key()
    assert 'sk-test' not in str(caught.value)


def test_old_ciphertext_can_add_fallbacks_and_update_primary_without_losing_them(tmp_path):
    import win32crypt
    from xiaomao.credentials import CredentialError, CredentialStore
    store = CredentialStore(tmp_path)
    old, backup, replacement = ('sk-test-only-'+s for s in ('legacy','backup','replacement'))
    store.path.write_bytes(win32crypt.CryptProtectData(old.encode(), 'test', store.ENTROPY, None, None, 1))
    assert store.count == 1 and store.get_key() == old
    store.add_keys([backup, backup])
    store.set_key(replacement)
    assert CredentialStore(tmp_path).get_keys() == (replacement, backup)
    ciphertext = store.path.read_bytes()
    assert all(key.encode() not in ciphertext for key in (old,backup,replacement))
    with pytest.raises(CredentialError):
        store.add_keys(['sk-test-only-other','wrong'])
    assert store.path.read_bytes() == ciphertext


def test_two_concurrent_appends_preserve_both_keys(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from xiaomao.credentials import CredentialStore
    store = CredentialStore(tmp_path)
    store.set_key('sk-test-only-primary')
    extras = ['sk-test-only-blue', 'sk-test-only-green']
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda key: CredentialStore(tmp_path).add_keys([key]), extras))
    assert store.count == 3
    assert set(store.get_keys()[1:]) == set(extras)
