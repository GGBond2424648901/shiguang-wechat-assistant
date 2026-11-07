import pytest

from test_bubble_delivery import delivery, question, ALICE, BOB
from test_engine import system
from test_stickers import library
from xiaomao.stickers import ReplyPlan, StickerLibrary


def setup_delivery(delivery, mode):
    engine, feed, _, clock, store = delivery
    engine.settings.reply_mode = engine.policy.mode = mode
    library(engine.settings.root)
    engine.stickers = StickerLibrary(engine.settings.root)
    engine.desktop.type_sticker = feed.stage_sticker
    engine.desktop.verify_sticker_input = lambda item: feed.sticker == item
    engine.model.reply_plan = lambda messages, **kw: (
        ReplyPlan(sticker='s_1') if '表情' in messages[-1].text else ReplyPlan(text='先回答你。'))
    return engine, feed, clock, store


@pytest.mark.parametrize('mode',['all','natural','mentions'])
def test_waiting_sticker_preserves_clipboard_without_editor_focus_and_yields_to_text(delivery,mode):
    engine,feed,clock,store=setup_delivery(delivery,mode)
    reason=['rich']
    engine.desktop.sticker_clipboard_status=lambda:reason[0]
    question(engine,'@小玲 发一张鲸鱼娘GIF表情','gif',ALICE)
    engine.observe_once()
    assert len(engine.tasks)==1 and engine.desktop.events==[] and engine.desktop.prepared==0
    assert '剪贴板' in engine.pending()[0]['progress'] and '富文本' in engine.reason
    question(engine,'@小玲 今天怎么样','text',BOB)
    # A fresh probe may still be blocked even when the next attempt is due.
    clock.advance(10)
    engine.observe_once()
    assert feed.sent==['先回答你。'] and engine.tasks[0].messages[0].message_id=='gif'
    reason[0]=''
    clock.advance(8)
    engine.observe_once()
    assert not engine.tasks and feed.sent==['先回答你。','[表情]']
    assert store.counts()=={'verified':2} and engine.desktop.events.count('focus')==2


def test_waiting_sticker_expires_with_reason_without_editor_input(delivery):
    engine,feed,clock,store=setup_delivery(delivery,'all')
    engine.desktop.sticker_clipboard_status=lambda:'rich'
    question(engine,'小玲 发一张鲸鱼娘GIF表情')
    engine.observe_once();clock.advance(engine.settings.expiry_seconds+1);engine.observe_once()
    assert not feed.sent and not engine.tasks and engine.mode=='auto'
    assert store.counts()=={'skipped':1}
    assert engine.last_result['reason']=='sticker_clipboard_rich' and '富文本' in engine.last_result['summary']


@pytest.mark.parametrize('problem,expected',[('rich','rich'),('busy','busy'),('available','')])
def test_sticker_preflight_only_enumerates_formats(monkeypatch,problem,expected):
    from types import SimpleNamespace
    from xiaomao import windows,clipboard
    def check(self):
        if problem=='rich':raise clipboard.RichClipboard('WPS')
        if problem=='busy':raise clipboard.ClipboardUnavailable('busy')
    fake=type('Clipboard',(),{'check_formats':check})
    monkeypatch.setattr(clipboard,'NativeClipboard',lambda pid:fake())
    assert windows.WindowsDesktop(SimpleNamespace(pid=1)).sticker_clipboard_status()==expected


def test_wps_copy_between_preflight_and_paste_is_pending_not_a_repeated_editor_attempt(delivery):
    from xiaomao.windows import StickerClipboardBlocked
    engine,feed,clock,store=setup_delivery(delivery,'natural')
    engine.desktop.sticker_clipboard_status=lambda:''
    def blocked(sticker):
        engine.desktop.sticker_clipboard_status=lambda:'rich'
        raise StickerClipboardBlocked('rich')
    engine.desktop.type_sticker=blocked
    question(engine,'小玲 发一张鲸鱼娘GIF表情')
    engine.observe_once()
    assert engine.desktop.events==['focus'] and not feed.sent
    assert '富文本' in engine.pending()[0]['progress'] and engine.mode=='auto'
    clock.advance(8);engine.observe_once()
    assert engine.desktop.events==['focus'] and not feed.sent


@pytest.mark.parametrize('paste_started',[False,True])
def test_only_a_failure_before_native_gif_paste_can_yield_the_queue(tmp_path,monkeypatch,paste_started):
    from types import SimpleNamespace
    from xiaomao import clipboard,windows
    library(tmp_path)
    lib=StickerLibrary(tmp_path)
    sticker=lib.prepare(lib.items[0])
    desktop=windows.WindowsDesktop(SimpleNamespace(pid=1))
    shortcuts=[]
    desktop._shortcut=lambda *keys:shortcuts.append(keys)
    monkeypatch.setattr(clipboard,'NativeClipboard',lambda pid:object())
    def fail(native,path,paste_action,assert_focus,wait):
        if paste_started:paste_action()
        raise clipboard.RichClipboard('WPS')
    monkeypatch.setattr(clipboard,'pasted_file',fail)
    with pytest.raises(windows.DesktopUnavailable) as error:
        desktop.type_sticker(sticker)
    assert (isinstance(error.value,windows.StickerClipboardBlocked)) is (not paste_started)
    assert shortcuts==([(0x11,0x56)] if paste_started else [])


def test_private_format_data_must_be_readable_before_editor_is_focused_again(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from xiaomao import clipboard,windows
    library(tmp_path)
    lib=StickerLibrary(tmp_path)
    item=lib.prepare(lib.items[0])
    desktop=windows.WindowsDesktop(SimpleNamespace(pid=1))
    desktop.assert_input_focus=lambda:None
    desktop.cancelled=SimpleNamespace(wait=lambda seconds:False)
    readable=[False]
    calls=[]
    class Clip:
        def check_formats(self):calls.append('formats')
        def snapshot(self):
            calls.append('snapshot')
            if not readable[0]:raise clipboard.ClipboardUnavailable('unreadable private handle')
            return 1,{}
    monkeypatch.setattr(clipboard,'NativeClipboard',lambda pid:Clip())
    assert desktop.sticker_clipboard_status()=='' and calls==['formats']
    with pytest.raises(windows.StickerClipboardBlocked):
        desktop.type_sticker(item)
    assert desktop.sticker_clipboard_status()=='busy'
    readable[0]=True
    assert desktop.sticker_clipboard_status()==''
    calls.clear()
    assert desktop.sticker_clipboard_status()=='' and calls==['formats']
