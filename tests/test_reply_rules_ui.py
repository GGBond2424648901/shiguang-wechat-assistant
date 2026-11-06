from types import SimpleNamespace
from xiaomao.config import Settings
from test_reply_preferences import configured


def panel_for(root,tmp_path):
    cfg=configured(tmp_path);cfg.save();jobs=[]
    p=SimpleNamespace(directory=tmp_path,settings=cfg,
        work=lambda action,done,failed,**kw:jobs.append((action,done,failed)))
    return p,jobs


def test_selecting_another_conversation_preserves_unsaved_rules(native_root,tmp_path):
    from xiaomao.reply_rules_ui import ReplyRulesEditor
    p,jobs=panel_for(native_root,tmp_path)
    editor=ReplyRulesEditor(native_root,p)
    try:
        assert editor.select('甲群')
        editor.inherit_mode.set(False);editor.mode.set('mentions')
        assert not editor.select('乙群')
        assert editor.group=='甲群' and '未保存' in editor.note.get()
        editor.discard()
        assert editor.select('乙群') and not jobs
        assert p.settings.group=='甲群'
    finally:editor.destroy()


def test_rule_save_captures_target_identity_and_reports_application(native_root,tmp_path,monkeypatch):
    from xiaomao.reply_rules_ui import ReplyRulesEditor
    p,jobs=panel_for(native_root,tmp_path);record=[]
    def persist(root,preferences,group=None,scope=None):
        record.append((group,scope,preferences))
        Settings.load(root).with_conversation_rules(group,preferences).save()
        return {'groups':[{'name':group,'rules_pending':True}]}
    monkeypatch.setattr('xiaomao.reply_rules_ui.persist_preferences',persist)
    editor=ReplyRulesEditor(native_root,p)
    try:
        editor.select('乙群');editor.inherit_mode.set(False);editor.mode.set('mentions')
        editor.inherit_stickers.set(False);editor.frequency.set('爱用表情');editor.save()
        assert editor.busy and not editor.select('甲群')
        action,done,_=jobs.pop();done(action())
        assert record==[('乙群','account-a/room-b@chatroom',{'reply_mode':'mentions','sticker_frequency':'expressive'})]
        assert '待' in editor.note.get() and not editor.dirty() and not editor.busy
    finally:editor.destroy()


def test_failed_rule_save_keeps_user_edit_and_shows_failure(native_root,tmp_path):
    from xiaomao.reply_rules_ui import ReplyRulesEditor
    p,jobs=panel_for(native_root,tmp_path);editor=ReplyRulesEditor(native_root,p)
    try:
        editor.select('朋友');editor.inherit_stickers.set(False);editor.frequency.set('仅按需');editor.save()
        _,_,fail=jobs.pop();fail('保存失败，请重试')
        assert editor.dirty() and not editor.busy and '失败' in editor.note.get()
        assert not Settings.load(tmp_path).conversation_rules
    finally:editor.destroy()
