from types import SimpleNamespace
import os
import pytest


@pytest.mark.skipif(os.name!='nt',reason='Windows native handle paths')
def test_unrelated_deleted_file_stat_error_does_not_hide_valid_wechat_db_handle(tmp_path,monkeypatch):
    import psutil
    import psutil._pswindows as backend
    from xiaomao.dbcodec.discovery import active_account_ids
    folder=tmp_path/'account/db_storage/message';folder.mkdir(parents=True)
    db=folder/'message_0.db';db.write_bytes(b'db')
    deleted='C:/$Extend/$Deleted/unreadable-temp-file'
    class Process:
        def open_files(self):raise psutil.AccessDenied(123)
    monkeypatch.setattr(psutil,'Process',lambda pid:Process())
    monkeypatch.setattr(backend.cext,'proc_open_files',lambda pid:[deleted,str(db),str(tmp_path/'config.db')])
    monkeypatch.setattr(backend,'convert_dos_path',lambda path:path)
    from pathlib import Path
    original=Path.is_file
    def checked(path):
        assert '$Deleted' not in str(path)  # don't probe the inaccessible handle
        return original(path)
    monkeypatch.setattr(Path,'is_file',checked)
    assert active_account_ids([{'id':'selected','path':str(tmp_path/'account')}],[{'pid':123}])=={'selected'}


@pytest.mark.skipif(os.name!='nt',reason='Windows native handle paths')
def test_handle_permission_failure_never_guesses_account_from_directory(tmp_path,monkeypatch):
    import psutil
    import psutil._pswindows as backend
    from xiaomao.dbcodec.discovery import active_account_ids
    class Process:
        def open_files(self):raise psutil.AccessDenied(123)
    monkeypatch.setattr(psutil,'Process',lambda pid:Process())
    def denied(pid):raise PermissionError('process denied')
    monkeypatch.setattr(backend.cext,'proc_open_files',denied)
    assert active_account_ids([{'id':'selected','path':str(tmp_path/'account')}],[{'pid':123}])==set()
