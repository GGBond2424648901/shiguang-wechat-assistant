from types import SimpleNamespace
import tkinter as tk
from pathlib import Path

from xiaomao.config import Settings
from xiaomao.sticker_ui import StickerPage
from test_stickers import library


def _loaded_page(native_root,tmp_path,count=15):
    library(tmp_path,count);Settings(tmp_path).save();jobs=[]
    panel=SimpleNamespace(directory=tmp_path,work=lambda f,success,error,**kw:jobs.append((f,success,error)))
    page=StickerPage(native_root,panel);page.on_show()
    action,done,_=jobs.pop(0);done(action())
    return page


def test_gallery_wheel_changes_page_only_over_tiles_and_accumulates_small_deltas(native_root,tmp_path):
    page=_loaded_page(native_root,tmp_path)
    try:
        tile=page.gridbox.winfo_children()[0]
        for _ in range(3):page._wheel(SimpleNamespace(widget=tile,delta=-30))
        assert page.page==0
        page._wheel(SimpleNamespace(widget=tile,delta=-30))
        assert page.page==1 and '2/3' in page.counter.cget('text')
        before=page.filter.get()
        page._wheel(SimpleNamespace(widget=page.categories,delta=-120))
        assert page.page==1 and page.filter.get()==before
        page.on_hide()
        page._wheel(SimpleNamespace(widget=page.gridbox,delta=-120))
        assert page.page==1
    finally:page.destroy()


def test_gallery_turn_at_boundary_does_not_destroy_visible_tiles(native_root,tmp_path):
    page=_loaded_page(native_root,tmp_path)
    try:
        tiles=page.gridbox.winfo_children()
        page._turn(-1)
        assert page.page==0 and page.gridbox.winfo_children()==tiles
        page._turn(20)
        assert page.page==2
        tiles=page.gridbox.winfo_children();page._turn(1)
        assert page.page==2 and page.gridbox.winfo_children()==tiles
    finally:page.on_hide();page.destroy()


def test_resize_burst_keeps_tiles_until_settled_and_ignores_threshold_jitter(native_root,tmp_path):
    import time
    page=_loaded_page(native_root,tmp_path)
    def settle():
        end=time.monotonic()+.2
        while time.monotonic()<end:native_root.update();time.sleep(.005)
    try:
        tiles=page.gridbox.winfo_children()
        for width in (950,870,850,900,750):page._resize(SimpleNamespace(width=width))
        assert page.gridbox.winfo_children()==tiles
        settle();assert page.thumb_edge==92
        tiles=page.gridbox.winfo_children()
        for width in (858,862,859,865,861):page._resize(SimpleNamespace(width=width))
        settle()
        assert page.thumb_edge==92 and page.gridbox.winfo_children()==tiles
        page._resize(SimpleNamespace(width=1000));settle()
        assert page.thumb_edge==128 and page.gridbox.winfo_children()!=tiles
    finally:page.on_hide();page.destroy()


def test_real_import_page_preserves_manual_edits_and_confirms_only_selected(native_root,tmp_path):
    items=library(tmp_path,2)
    settings=Settings(tmp_path);settings.save()
    jobs=[]
    panel=SimpleNamespace(directory=tmp_path,work=lambda f,success,error,**kw:jobs.append((f,success,error)))
    page=StickerPage(native_root,panel)
    def finish():
        action,done,error=jobs.pop(0);done(action())
    try:
        page.on_show();finish()
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
        page.on_show();action,done,_=jobs.pop(0);done(action())
        page.add_paths([tmp_path/r['path'] for r in items])
        page.analyze()  # Same button requests stop while one read is running.
        action,done,_=jobs.pop(0);done(action())
        assert not page.busy and not jobs
        assert sum(r.picture is not None for r in page.rows.values())==1
        assert not page.lib.shelf.snapshot()[0]
    finally:page.on_hide();page.destroy()


def test_viewing_library_item_never_adds_an_upload(native_root,tmp_path):
    library(tmp_path,2);Settings(tmp_path).save();jobs=[]
    panel=SimpleNamespace(directory=tmp_path,work=lambda f,success,error,**kw:jobs.append((f,success,error)))
    page=StickerPage(native_root,panel)
    try:
        page.on_show();action,done,_=jobs.pop(0);done(action())
        page.edit_item(page.lib.items[0])
        assert page.tab=='detail'
        assert not page.rows and not page.queue.get_children()
        while jobs:
            action,done,_=jobs.pop(0);done(action())
        assert page.detail_save.cget('text')=='保存修改'
        page.switch('library')
        assert not page.rows and not page.lib.shelf.snapshot()[0]
    finally:page.on_hide();page.destroy()


def test_library_revisit_reuses_tiles_and_detail_save_survives_reload(native_root,tmp_path):
    library(tmp_path,2);Settings(tmp_path).save();jobs=[]
    panel=SimpleNamespace(directory=tmp_path,work=lambda f,success,error,**kw:jobs.append((f,success,error)))
    page=StickerPage(native_root,panel)
    def finish():
        action,done,_=jobs.pop(0);done(action())
    try:
        page.on_show();finish();tiles=page.gridbox.winfo_children()
        page.on_hide();page.on_show()
        assert page.gridbox.winfo_children()==tiles
        first=page.lib.items[0];page.edit_item(first);finish()
        page.detail.fields['meaning'].set('新的聊天用途')
        page.back_to_library();assert page.tab=='detail'  # protect unsaved edits
        page.detail.save();finish();page.back_to_library()
        from xiaomao.stickers import StickerLibrary
        assert next(s for s in StickerLibrary(tmp_path).items if s.sha256==first.sha256).meaning=='新的聊天用途'
        assert page.tab=='library' and not page.rows
    finally:page.on_hide();page.destroy()


def test_detail_out_of_order_inspection_keeps_current_item(native_root,tmp_path):
    library(tmp_path,2);Settings(tmp_path).save();jobs=[]
    panel=SimpleNamespace(directory=tmp_path,work=lambda f,success,error,**kw:jobs.append((f,success,error)))
    page=StickerPage(native_root,panel)
    try:
        page.on_show();action,done,_=jobs.pop(0);done(action())
        first,second=page.lib.items
        page.edit_item(first);old=jobs.pop(0)
        page.edit_item(second);new=jobs.pop(0)
        new[1](new[0]());old[1](old[0]())
        assert page.detail.picture.path==second.path and page.detail.item==second
        page.on_hide();assert page.detail.image is None and page.detail.animation is None
        assert not page.rows
    finally:page.destroy()
