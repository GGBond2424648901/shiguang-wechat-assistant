import hashlib
import json
import os

import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def codec():
    """An authenticated bytes codec stands in for user-scoped Windows DPAPI."""
    cipher = Fernet(Fernet.generate_key())
    return cipher.encrypt, cipher.decrypt


def store_at(runtime, codec):
    from xiaomao.account_profiles import AccountProfiles
    return AccountProfiles(runtime, protect=codec[0], unprotect=codec[1])


def profile_path(runtime, account):
    return runtime / "account-profiles" / (hashlib.sha256(account.encode("utf-8")).hexdigest() + ".dpapi")


def write_envelope(runtime, account, envelope, codec):
    path = profile_path(runtime, account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(codec[0](json.dumps(envelope, ensure_ascii=False).encode("utf-8")))
    return path


def test_two_accounts_with_same_view_name_keep_independent_routes(tmp_path, codec):
    store = store_at(tmp_path, codec)
    first = {"self_name": "同名昵称", "directory": {"group": "蓝色群"}, "calibration": [10, 20]}
    second = {"self_name": "同名昵称", "directory": {"group": "绿色群"}, "calibration": [80, 90]}
    store.save("wxid_first", first)
    store.save("wxid_second", second)

    reopened = store_at(tmp_path, codec)
    assert reopened.load("wxid_first") == first
    assert reopened.load("wxid_second") == second
    assert {path.name for path in (tmp_path / "account-profiles").iterdir()} == {
        hashlib.sha256(b"wxid_first").hexdigest() + ".dpapi",
        hashlib.sha256(b"wxid_second").hexdigest() + ".dpapi",
    }


def test_saved_and_loaded_values_do_not_alias_callers(tmp_path, codec):
    store = store_at(tmp_path, codec)
    values = {"directory": {"groups": ["原群"]}}
    store.save("wxid_one", values)
    values["directory"]["groups"].append("未保存")
    loaded = store.load("wxid_one")
    assert loaded == {"directory": {"groups": ["原群"]}}
    loaded["directory"]["groups"].append("读取后修改")
    assert store.load("wxid_one") == {"directory": {"groups": ["原群"]}}


def test_ciphertext_hides_identity_and_route_values(tmp_path, codec):
    store_at(tmp_path, codec).save("wxid_示例米饭同学", {"directory": "私人专属群聊", "scale": 1.5})
    data = profile_path(tmp_path, "wxid_示例米饭同学").read_bytes()
    assert b"wxid_" not in data
    assert "示例米饭同学".encode() not in data
    assert "私人专属群聊".encode() not in data
    assert b'"directory"' not in data


def test_missing_profile_returns_none_without_creating_directories(tmp_path, codec):
    assert store_at(tmp_path, codec).load("wxid_missing") is None
    assert not (tmp_path / "account-profiles").exists()


@pytest.mark.parametrize("account", ["", " ", None, 7, "a" * 257, "a/b", "a\\b", "a\x00b", "a\nb", "a\x7fb", "a\x85b"])
def test_invalid_account_is_rejected_for_save_and_load(tmp_path, codec, account):
    store = store_at(tmp_path, codec)
    with pytest.raises(ValueError, match="账户|account"):
        store.save(account, {})
    with pytest.raises(ValueError, match="账户|account"):
        store.load(account)
    assert not (tmp_path / "account-profiles").exists()


@pytest.mark.parametrize("envelope", [
    {"version": 1, "account": "wxid_other", "values": {}},
    {"version": 2, "account": "wxid_one", "values": {}},
    {"version": True, "account": "wxid_one", "values": {}},
    {"account": "wxid_one", "values": {}},
    {"version": 1, "account": "wxid_one", "values": []},
    {"version": 1, "account": "wxid_one"},
    [],
])
def test_malformed_or_cross_account_envelope_is_rejected(tmp_path, codec, envelope):
    write_envelope(tmp_path, "wxid_one", envelope, codec)
    with pytest.raises(ValueError, match="配置|profile"):
        store_at(tmp_path, codec).load("wxid_one")


def test_copying_another_accounts_ciphertext_is_rejected(tmp_path, codec):
    store = store_at(tmp_path, codec)
    store.save("wxid_one", {"directory": "第一组"})
    profile_path(tmp_path, "wxid_two").write_bytes(profile_path(tmp_path, "wxid_one").read_bytes())
    with pytest.raises(ValueError, match="配置|profile"):
        store.load("wxid_two")


@pytest.mark.parametrize("damage", ["tampered", "truncated", "empty", "invalid_json", "invalid_utf8"])
def test_corrupt_profile_raises_clear_error(tmp_path, codec, damage):
    store = store_at(tmp_path, codec)
    store.save("wxid_one", {"directory": "私密群"})
    path = profile_path(tmp_path, "wxid_one")
    data = path.read_bytes()
    if damage == "tampered":
        data = data[:20] + bytes([data[20] ^ 1]) + data[21:]
    elif damage == "truncated":
        data = data[:10]
    elif damage == "empty":
        data = b""
    elif damage == "invalid_json":
        data = codec[0](b'{"version":')
    else:
        data = codec[0](b"\xff")
    path.write_bytes(data)
    with pytest.raises(ValueError, match="配置|profile") as caught:
        store.load("wxid_one")
    assert "wxid_one" not in str(caught.value)
    assert "私密群" not in str(caught.value)


@pytest.mark.parametrize("values", [[], "route", None, {"bad": object()}, {"number": float("nan")}])
def test_non_json_dictionary_values_are_rejected(tmp_path, codec, values):
    with pytest.raises(ValueError, match="配置|profile"):
        store_at(tmp_path, codec).save("wxid_one", values)
    assert not profile_path(tmp_path, "wxid_one").exists()


def test_values_larger_than_one_mib_are_rejected_for_save_and_load(tmp_path, codec):
    values = {"directory": "界" * (1024 * 1024 // 3)}
    store = store_at(tmp_path, codec)
    with pytest.raises(ValueError, match="配置|profile"):
        store.save("wxid_one", values)
    write_envelope(tmp_path, "wxid_one", {"version": 1, "account": "wxid_one", "values": values}, codec)
    with pytest.raises(ValueError, match="配置|profile"):
        store.load("wxid_one")


def test_encryption_failure_preserves_existing_profile(tmp_path, codec):
    store = store_at(tmp_path, codec)
    store.save("wxid_one", {"directory": "保留"})
    path = profile_path(tmp_path, "wxid_one")
    previous = path.read_bytes()

    def fail_encryption(data):
        raise RuntimeError("codec unavailable")

    from xiaomao.account_profiles import AccountProfiles
    failing = AccountProfiles(tmp_path, protect=fail_encryption, unprotect=codec[1])
    with pytest.raises((ValueError, RuntimeError)):
        failing.save("wxid_one", {"directory": "替换"})
    assert path.read_bytes() == previous
    assert store.load("wxid_one") == {"directory": "保留"}
    assert list(path.parent.glob("*.tmp")) == []


def test_duplicate_envelope_fields_are_rejected(tmp_path, codec):
    path = profile_path(tmp_path, "wxid_one")
    path.parent.mkdir()
    path.write_bytes(codec[0](b'{"version":2,"version":1,"account":"wxid_one","values":{}}'))
    with pytest.raises(ValueError, match="配置|profile"):
        store_at(tmp_path, codec).load("wxid_one")


def test_values_at_one_mib_boundary_are_accepted(tmp_path, codec):
    # Compact UTF-8 JSON {"x":"..."} contributes eight bytes around the value.
    values = {"x": "a" * (1024 * 1024 - 8)}
    store = store_at(tmp_path, codec)
    store.save("wxid_one", values)
    assert store.load("wxid_one") == values


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows user scoped")
def test_default_dpapi_uses_profile_entropy_and_persists_for_new_store(tmp_path):
    import win32crypt
    from xiaomao.account_profiles import AccountProfiles

    values = {"directory": {"group": "原用户私有配置"}}
    AccountProfiles(tmp_path).save("wxid_示例米饭同学", values)
    ciphertext = profile_path(tmp_path, "wxid_示例米饭同学").read_bytes()
    assert "原用户私有配置".encode() not in ciphertext
    assert "示例米饭同学".encode() not in ciphertext
    _, plain = win32crypt.CryptUnprotectData(ciphertext, b"xiaomao:account-profile:v1", None, None, 1)
    assert json.loads(plain) == {"version": 1, "account": "wxid_示例米饭同学", "values": values}
    assert AccountProfiles(tmp_path).load("wxid_示例米饭同学") == values
