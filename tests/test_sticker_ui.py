from types import SimpleNamespace
import tkinter as tk
from pathlib import Path

from xiaomao.config import Settings
from xiaomao.sticker_ui import StickerPage
from test_stickers import library


def test_real_import_page_preserves_manual_edits_and_confirms_only_selected(native_root,tmp_path):
    items=library(tmp_path,2)
    settings=Settings(tmp_path);settings.save()
    jobs=[]
    panel=SimpleNamespace(directory=tmp_path,work=lambda f,success,error,**kw:jobs.append((f,success,error)))
    page=StickerPage(native_root,panel)
    def finish():
        action,done,error=jobs.pop(0);done(action())
    try:
        page.on_show()
        page.add_paths([tmp_path/r['path'] for r in items])
        native_root.update()
        assert page.busy and not page.lib.shelf.snapshot()[0]
        page.fields['meaning'].set('我手动核对的用途')
        finish();finish()
        assert not page.busy and page.rows[str((tmp_path/items[0]['path']).resolve())].meaning=='我手动核对的用途'
        first=str((tmp_path/items[0]['path']).resolve())
        page.queue.selection_set(first);page.queue.focus(first);page._select_row()
        page.confirm();finish()
        assert len(page.lib.shelf.snapshot()[0])==1
        page.switch('library');page.priorities['菲比'].set('停用');page.save();finish()
        assert not page.lib.collection_enabled('菲比')
        page.on_hide();assert page.animation is None
    finally:page.destroy()


def test_stop_upload_analysis_keeps_results_without_committing(native_root,tmp_path):
    items=library(tmp_path,2);Settings(tmp_path).save();jobs=[]
    panel=SimpleNamespace(directory=tmp_path,work=lambda f,success,error,**kw:jobs.append((f,success,error)))
    page=StickerPage(native_root,panel)
    try:
        page.on_show();page.add_paths([tmp_path/r['path'] for r in items])
        page.analyze()  # Same button requests stop while one read is running.
        action,done,_=jobs.pop(0);done(action())
        assert not page.busy and not jobs
        assert sum(r.picture is not None for r in page.rows.values())==1
        assert not page.lib.shelf.snapshot()[0]
    finally:page.on_hide();page.destroy()
