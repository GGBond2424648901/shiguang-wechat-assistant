"""Synthetic QA members must never write mood state into real conversations."""
from dataclasses import replace
import pytest
from scripts.qa_model import isolated_model
from xiaomao.config import Settings


def test_only_qa_root_can_back_a_synthetic_model(tmp_path):
    cfg=Settings(tmp_path)
    model=isolated_model(cfg)
    assert model.settings.root.is_relative_to(cfg.runtime/'qa')
    assert model.settings.memory_scope!=cfg.memory_scope and not model.settings.owner_confirmed
    with pytest.raises(ValueError):isolated_model(cfg,cfg)
    with pytest.raises(ValueError):
        isolated_model(cfg,replace(model.settings,model_base_url='http://localhost'))
    with pytest.raises(ValueError):
        isolated_model(cfg,replace(model.settings,root=cfg.runtime/'qa'/'..'/'..'))


@pytest.mark.parametrize('extra,allowed', [
    ([],True), ([('other','文件传输助手🐟')],False), ([('me','文件传输助手')],False),
])
def test_selfchat_proof_fails_for_visual_twins_including_current_account(extra,allowed):
    import sqlite3
    from scripts.qa_model import verify_filehelper
    from xiaomao.contacts import read_directory
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT,delete_flag INTEGER)')
    conn.executemany("INSERT INTO contact VALUES(?,?,'',0)",[('filehelper','文件传输助手'),*extra])
    rows=read_directory(conn,'me','account')
    if allowed:
        verify_filehelper(rows,'account')
        with pytest.raises(ValueError):verify_filehelper(rows,'different account')
    else:
        with pytest.raises(ValueError):verify_filehelper(rows,'account')
    with pytest.raises(ValueError):verify_filehelper([r for r in rows if r.id!='filehelper'],'account')
