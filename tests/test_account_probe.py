from types import SimpleNamespace
import pytest


def test_only_exact_message_files_and_requested_wechat_process_prove_login(tmp_path):
    from xiaomao.dbcodec.account_probe import _resolve_accounts
    accounts=[]
    for name in ('active','signed_out','other_process'):
        root=tmp_path/name;folder=root/'db_storage/message';folder.mkdir(parents=True)
        (folder/'message_0.db').write_bytes(b'db')
        (folder/'config.db').write_bytes(b'not login evidence')
        (folder/'message_0.db-wal').write_bytes(b'wal')
        accounts.append({'id':name,'path':str(root)})
    def query(paths):
        assert all(p.name=='message_0.db' for p in paths)
        name=paths[0].parents[2].name
        return {'active':{12},'signed_out':set(),'other_process':{99}}[name]
    assert _resolve_accounts(accounts,{12},query)=={'active'}


@pytest.mark.parametrize('result,expected',[(None,set()),({'active'},{'active'}),({'foreign'},set()),('malformed',set())])
def test_native_query_timeout_crash_or_untrusted_result_never_guesses_account(tmp_path,monkeypatch,result,expected):
    from xiaomao.dbcodec import account_probe
    events=[]
    receiver=SimpleNamespace(poll=lambda timeout:result is not None,recv=lambda:result,
                             close=lambda:events.append('receiver_closed'))
    sender=SimpleNamespace(close=lambda:events.append('sender_closed'))
    process=SimpleNamespace(pid=1,start=lambda:events.append('started'),join=lambda timeout:None,
                            is_alive=lambda:True,terminate=lambda:events.append('terminated'),
                            close=lambda:events.append('process_closed'))
    context=SimpleNamespace(Pipe=lambda duplex:(receiver,sender),Process=lambda **kwargs:process)
    monkeypatch.setattr(account_probe.mp,'get_context',lambda _:context)
    assert account_probe.active_accounts([{'id':'active','path':str(tmp_path)}],[{'pid':12}],timeout=.01)==expected
    assert 'terminated' in events and 'process_closed' in events and 'receiver_closed' in events


def test_account_discovery_does_not_enumerate_unrelated_process_handles(tmp_path,monkeypatch):
    import psutil
    from xiaomao.dbcodec import discovery,account_probe
    monkeypatch.setattr(psutil,'Process',lambda *args:pytest.fail('native open_files can hang or crash the worker'))
    monkeypatch.setattr(account_probe,'active_accounts',lambda accounts,processes:{'active'})
    assert discovery.active_account_ids([{'id':'active','path':str(tmp_path)}],[{'pid':12}])=={'active'}
