from types import SimpleNamespace
import os
import pytest


@pytest.mark.skipif(os.name!='nt',reason='Windows native handle paths')
def test_unrelated_deleted_file_stat_error_does_not_hide_valid_wechat_db_handle(tmp_path,monkeypatch):
    import psutil
    from xiaomao.dbcodec.account_probe import _resolve_accounts
    folder=tmp_path/'account/db_storage/message';folder.mkdir(parents=True)
    db=folder/'message_0.db';db.write_bytes(b'db')
    deleted='C:/$Extend/$Deleted/unreadable-temp-file'
    class Process:
        def open_files(self):raise psutil.AccessDenied(123)
    monkeypatch.setattr(psutil,'Process',lambda pid:Process())
    from pathlib import Path
    original=Path.is_file
    def checked(path):
        assert '$Deleted' not in str(path)  # don't probe the inaccessible handle
        return original(path)
    monkeypatch.setattr(Path,'is_file',checked)
    def query(paths):
        assert paths==[db]
        return {123}
    assert _resolve_accounts([{'id':'selected','path':str(tmp_path/'account')}],{123},query)=={'selected'}


@pytest.mark.skipif(os.name!='nt',reason='Windows native handle paths')
def test_handle_permission_failure_never_guesses_account_from_directory(tmp_path,monkeypatch):
    import psutil
    from xiaomao.dbcodec import account_probe
    received=[]
    def denied(*args):raise PermissionError('process denied')
    monkeypatch.setattr(account_probe,'_resolve_accounts',denied)
    account_probe._probe_worker([{'id':'selected','path':str(tmp_path/'account')}],{123},
                                SimpleNamespace(send=received.append,close=lambda:None))
    assert received==[set()]
