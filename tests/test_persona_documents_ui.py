import tkinter as tk

from test_persona_settings_pages import panel
from xiaomao.persona import load_persona, save_persona


def test_markdown_picker_imports_multiple_docs_without_replacing_main_or_losing_draft(panel, monkeypatch):
    from xiaomao.persona_documents import list_persona_documents
    from xiaomao.persona_ui import PersonaPage
    save_persona(panel.directory, '# 小玲\n【表达风格】\n旧稿保持不动\n')
    first = panel.directory/'第一份.md'; first.write_bytes('# 声线\n所有细节都要保留'.encode())
    second = panel.directory/'第二份.md'; second.write_bytes('# 关系\n不同成员分别记忆'.encode())
    monkeypatch.setattr('xiaomao.persona_ui.filedialog.askopenfilenames', lambda **kwargs:(str(first),str(second)))
    page = PersonaPage(panel.root,panel)
    page.editor.insert('end','未保存的主稿修改')
    page.import_button.invoke()
    first_doc, second_doc = list_persona_documents(panel.directory)
    assert not first_doc.enabled and not second_doc.enabled
    assert second_doc.title == '第二份' and page.document_id == second_doc.id
    assert page.save_button.cget('text') == '保存并启用'
    assert load_persona(panel.directory) == '# 小玲\n【表达风格】\n旧稿保持不动\n'
    page.select_document('')
    page.toggle_preview()
    assert '未保存的主稿修改' in page.editor.get('1.0','end-1c')
    assert len(page.document_picker['values']) == 3


def test_paste_dialog_imports_full_text_under_custom_name_before_activation(panel):
    from xiaomao.persona_documents import list_persona_documents, render_persona_documents
    from xiaomao.persona_ui import PersonaPage
    primary = load_persona(panel.directory)
    page = PersonaPage(panel.root,panel)
    page.paste_button.invoke()
    dialog = page.import_dialog
    dialog.title_var.set('完整规则')
    dialog.category_var.set('记忆与上下文')
    body = '# 多段原文\n'+('每一项都保留\n'*2500)+'最后一项'
    dialog.editor.insert('1.0',body)
    dialog.save_button.invoke()
    document = list_persona_documents(panel.directory)[0]
    assert document.text == body and document.category == 'memory' and document.title == '完整规则'
    assert not document.enabled and render_persona_documents(panel.directory) == ''
    page.select_category('identity')
    page.editor.insert('end','新增细节')
    page.save()
    assert '新增细节' in list_persona_documents(panel.directory)[0].text
    assert '新增细节' in render_persona_documents(panel.directory)
    assert load_persona(panel.directory) == primary
    assert document.source_path.read_bytes() == body.encode()


def test_picker_save_switches_one_supplement_and_shows_persisted_state(panel):
    from xiaomao.persona_documents import import_persona_text, list_persona_documents, render_persona_documents
    from xiaomao.persona_ui import PersonaPage
    first = import_persona_text(panel.directory, '# 重要补充\n珍贵细节', title='第一份')
    second = import_persona_text(panel.directory, '# 新补充\n新的细节', title='第二份', enabled=False)
    page = PersonaPage(panel.root,panel)
    page.select_document(second.id)
    assert '第一份' in page.activation_status.get() and '第二份' not in page.activation_status.get()
    assert page.save_button.cget('text') == '保存并启用' and not page.save_button.instate(('disabled',))
    assert [doc.enabled for doc in list_persona_documents(panel.directory)] == [True, False]
    page.save_button.invoke()
    assert [doc.enabled for doc in list_persona_documents(panel.directory)] == [False, True]
    assert '第二份' in panel.notice.get() and '已启用' in panel.notice.get()
    assert '第二份' in page.activation_status.get() and '第一份' not in page.activation_status.get()
    assert '珍贵细节' not in render_persona_documents(panel.directory)
    assert '新的细节' in render_persona_documents(panel.directory)
    page.destroy()
    reopened = PersonaPage(panel.root,panel)
    assert '第二份' in reopened.activation_status.get()
    assert reopened.document_id == '' and reopened.disable_button.grid_info()
    reopened.editor.insert('end','主稿未保存的修改')
    reopened.disable_button.invoke()
    assert render_persona_documents(panel.directory) == ''
    assert '仅主稿' in reopened.activation_status.get()
    assert '主稿未保存的修改' in reopened.editor.get('1.0','end-1c')
    assert first.source_path.exists() and second.source_path.exists()


def test_rename_updates_picker_and_active_label_without_editing_source(panel, monkeypatch):
    from xiaomao.persona_documents import import_persona_text, get_persona_document
    from xiaomao.persona_ui import PersonaPage
    document = import_persona_text(panel.directory, '# 完整规则\n保留每一句', title='导入时的文件名')
    page = PersonaPage(panel.root,panel)
    page.select_document(document.id)
    monkeypatch.setattr('xiaomao.persona_ui.simpledialog.askstring', lambda *args, **kwargs: '我的补充稿')
    page.rename_button.invoke()
    updated = get_persona_document(panel.directory, document.id)
    assert updated.title == '我的补充稿' and updated.enabled and updated.sha256 == document.sha256
    assert '我的补充稿' in page.document_picker.get() and '我的补充稿' in page.activation_status.get()


def test_hierarchical_markdown_opens_readable_body_and_inherits_parent_category_without_rewriting(panel):
    from xiaomao.persona_documents import import_persona_text, render_persona_documents
    from xiaomao.persona_ui import PersonaPage
    from xiaomao.persona import join_persona
    raw=('# 完整人格\n\n# 第二章：语言与节奏\n\n## 2.1 自然开口\n短句，不用客服腔\n'
         '\n# 第三章：情绪\n\n## 3.1 开心\n有一点小得意\n'
         '\n# 第四章：记忆\n\n## 4.1 小约定\n记住真实发生的事\n')
    document=import_persona_text(panel.directory,raw,title='层级稿',category='style')
    model_source=render_persona_documents(panel.directory)
    page=PersonaPage(panel.root,panel);page.select_document(document.id)
    assert '短句，不用客服腔' in page.editor.get('1.0','end-1c')
    assert next(s for s in page.sections if '3.1 开心' in s.heading).category=='style'
    assert next(s for s in page.sections if '4.1 小约定' in s.heading).category=='memory'
    assert join_persona(page.sections)==raw and page.draft_status.get()=='已保存'
    parent=next(i for i,s in enumerate(page.sections) if '第二章' in s.heading)
    page.select_section(parent)
    assert '短句，不用客服腔' in page.editor.get('1.0','end-1c')
    assert page.editor.cget('state')=='disabled' and '章节预览' in page.heading.get()
    page._stash()
    assert join_persona(page.sections)==raw and page.draft_status.get()=='已保存'
    page.select_category('memory')
    assert '记住真实发生的事' in page.editor.get('1.0','end-1c')
    assert render_persona_documents(panel.directory)==model_source


def test_persona_settings_show_actual_harness_fallback_state(panel):
    from xiaomao.persona_ui import PersonaPage
    page=PersonaPage(panel.root,panel)
    assert '兼容模式' in page.corpus_status.get()
    assert page.compiler_evidence['backend']=='python-fallback'
    assert page.compiler_evidence['fallback_reason']


def test_async_document_save_does_not_overwrite_the_document_selected_afterward(panel):
    from xiaomao.persona_documents import import_persona_text, get_persona_document
    from xiaomao.persona_ui import PersonaPage
    first=import_persona_text(panel.directory,'# 第一份\n旧正文',title='第一份',category='identity')
    second=import_persona_text(panel.directory,'# 第二份\n不应改动',title='第二份',category='identity')
    page=PersonaPage(panel.root,panel);page.select_document(first.id);page.select_category('identity')
    page.editor.insert('end','第一份的新增正文')
    pending=[]
    panel.work=lambda call,done,failed=None,**kwargs:pending.append((call,done))
    page.save()
    page.select_document(second.id)
    call,done=pending[0]
    done(call())
    assert '第一份的新增正文' in get_persona_document(panel.directory,first.id).text
    assert get_persona_document(panel.directory,second.id).text=='# 第二份\n不应改动'
    assert page.source=='# 第二份\n不应改动'


def test_rapid_document_refresh_keeps_latest_compiler_result_with_real_work_queue(panel,monkeypatch):
    import queue
    import threading
    import time
    from xiaomao.gui import Panel
    from xiaomao.model import ChatModel
    from xiaomao.persona_ui import PersonaPage
    entered,release=threading.Event(),threading.Event()
    snapshot=ChatModel.prompt_snapshot
    def slow_snapshot(model):
        if not entered.is_set():
            entered.set()
            assert release.wait(3)
        return snapshot(model)
    monkeypatch.setattr(ChatModel,'prompt_snapshot',slow_snapshot)
    panel.results=queue.Queue();panel.mutation_lock=threading.RLock()
    panel.pending_mutations=0;panel.exiting=False
    panel.show_error=lambda error:panel.notice.set(str(error))
    panel.work=lambda *args,**kwargs:Panel.work(panel,*args,**kwargs)
    page=PersonaPage(panel.root,panel)
    try:
        assert entered.wait(1)
        page._refresh_compiler_state()
    finally:release.set()
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        while not panel.results.empty():
            callback,value=panel.results.get_nowait();callback(value)
        if page.compiler_evidence:break
        time.sleep(.01)
    assert page.compiler_evidence.get('backend')=='python-fallback'
    assert '兼容模式' in page.corpus_status.get()
