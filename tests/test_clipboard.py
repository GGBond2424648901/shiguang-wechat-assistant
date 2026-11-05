import pytest


@pytest.mark.parametrize('busy_count',[0,3,99])
def test_native_clipboard_open_retries_contention_without_replaying_operation(monkeypatch,busy_count):
    from types import SimpleNamespace
    from xiaomao import clipboard
    opened=[];closed=[];waits=[];writes=[]
    def acquire(owner):
        opened.append(owner)
        return len(opened)>busy_count
    native=clipboard.NativeClipboard.__new__(clipboard.NativeClipboard)
    native.user=SimpleNamespace(OpenClipboard=acquire,CloseClipboard=lambda:closed.append(1))
    monkeypatch.setattr('time.sleep',waits.append)
    def operation():
        with native.opened():writes.append('once')
    if busy_count==99:
        with pytest.raises(clipboard.ClipboardUnavailable):operation()
        assert not writes and not closed and len(opened)<=10
    else:
        operation()
        assert writes==['once'] and closed==[1] and len(opened)==busy_count+1
    assert sum(waits)<.5


class Clipboard:
    def __init__(self):self.sequence=1;self.data='原来的剪贴板';self.restored=False
    def snapshot(self):return self.sequence,self.data
    def read_text(self):return self.sequence,self.data,True
    def restore(self,data,sequence):
        if self.sequence==sequence:self.data=data;self.restored=True


class PasteClipboard(Clipboard):
    def replace_text(self,text,sequence):
        if self.sequence!=sequence:return None
        self.sequence+=1;self.data=text
        return self.sequence
    def current_sequence(self):return self.sequence


def test_paste_preserves_unicode_and_restores_original_contents():
    from xiaomao.clipboard import pasted_text
    c=PasteClipboard();received=[]
    text='美元计价，20刀。你们试过吗？喵🙂'
    pasted_text(c,text,lambda:received.append(c.data),lambda:None,lambda seconds:None)
    assert received==[text] and c.data=='原来的剪贴板' and c.restored


def test_failed_paste_restores_clipboard_and_is_never_retried():
    from xiaomao.clipboard import pasted_text
    c=PasteClipboard();calls=[]
    def paste():calls.append(1);raise RuntimeError('输入结果不明确')
    with pytest.raises(RuntimeError):pasted_text(c,'正文',paste,lambda:None,lambda seconds:None)
    assert calls==[1] and c.restored


def test_external_clipboard_change_before_paste_is_never_pasted_or_overwritten():
    from xiaomao.clipboard import pasted_text,ClipboardUnavailable
    c=PasteClipboard();calls=[]
    def focus():
        if c.sequence==2:c.sequence=3;c.data='其他程序的新内容'
    with pytest.raises(ClipboardUnavailable):pasted_text(c,'正文',lambda:calls.append(1),focus,lambda seconds:None)
    assert not calls and c.data=='其他程序的新内容' and not c.restored


def test_focus_loss_after_paste_restores_clipboard_without_more_input():
    from xiaomao.clipboard import pasted_text
    c=PasteClipboard();calls=[]
    def focus():
        if calls:raise RuntimeError('用户切换了窗口')
    with pytest.raises(RuntimeError):pasted_text(c,'正文',lambda:calls.append(1),focus,lambda seconds:None)
    assert calls==[1] and c.restored


def test_delayed_image_formats_retry_before_any_clipboard_write_or_paste():
    from xiaomao.clipboard import pasted_text,ClipboardUnavailable
    c=PasteClipboard();original=c.snapshot;attempts=[];pastes=[];waits=[]
    def snapshot():
        attempts.append(1)
        assert not pastes and c.data=='原来的剪贴板'
        if len(attempts)<3:raise ClipboardUnavailable('剪贴板格式不可读取')
        return original()
    c.snapshot=snapshot
    pasted_text(c,'正文🙂',lambda:pastes.append(c.data),lambda:None,waits.append)
    assert len(attempts)==3 and pastes==['正文🙂'] and c.restored


def test_unreadable_clipboard_reports_no_editor_input_after_bounded_retries():
    from xiaomao.clipboard import pasted_text,ClipboardUnavailable,ClipboardSnapshotUnavailable
    c=PasteClipboard();attempts=[];pastes=[]
    def snapshot():attempts.append(1);raise ClipboardUnavailable('busy')
    c.snapshot=snapshot
    with pytest.raises(ClipboardSnapshotUnavailable):
        pasted_text(c,'正文',lambda:pastes.append(1),lambda:None,lambda seconds:None)
    assert len(attempts)==3 and not pastes and c.sequence==1


def test_verified_copy_restores_original_clipboard_without_losing_punctuation():
    from xiaomao.clipboard import copied_text
    c=Clipboard()
    def copy():c.data='喵——对呀🙂';c.sequence+=1
    assert copied_text(c,copy,lambda:None,lambda seconds:None)=='喵——对呀🙂'
    assert c.data=='原来的剪贴板' and c.restored


def test_unrelated_clipboard_change_is_not_used_or_overwritten():
    from xiaomao.clipboard import copied_text,ClipboardUnavailable
    c=Clipboard()
    def copy():c.data='别的程序复制的内容';c.sequence+=1
    c.read_text=lambda:(c.sequence,c.data,False)
    with pytest.raises(ClipboardUnavailable):copied_text(c,copy,lambda:None,lambda seconds:None)
    assert c.data=='别的程序复制的内容' and not c.restored


def test_stale_clipboard_cannot_pass_as_input_readback():
    from xiaomao.clipboard import copied_text,ClipboardUnavailable
    c=Clipboard()
    with pytest.raises(ClipboardUnavailable):copied_text(c,lambda:None,lambda:None,lambda seconds:None)
    assert not c.restored


def test_focus_loss_after_copy_still_restores_owned_clipboard():
    from xiaomao.clipboard import copied_text
    c=Clipboard();checks=[]
    def focus():
        checks.append(1)
        if len(checks)>1:raise RuntimeError('focus changed')
    def copy():c.data='助手正文';c.sequence+=1
    with pytest.raises(RuntimeError):copied_text(c,copy,focus,lambda seconds:None,expected='助手正文')
    assert c.data=='原来的剪贴板' and c.restored


def test_native_read_tracks_sequence_after_delayed_rendering():
    from xiaomao.clipboard import NativeClipboard
    from contextlib import nullcontext
    class Api:
        seq=10
        def GetClipboardOwner(self):return 1
        def GetWindowThreadProcessId(self,owner,pid):pid._obj.value=42
        def IsClipboardFormatAvailable(self,fmt):return True
        def GetClipboardSequenceNumber(self):return self.seq
    c=NativeClipboard.__new__(NativeClipboard);c.pid=42;c.user=Api();c.opened=nullcontext
    def render(fmt):c.user.seq=11;return '正确正文\0'.encode('utf-16-le')
    c._bytes=render
    assert c.read_text()==(11,'正确正文',True)


def test_native_write_returns_sequence_after_close_publishes_formats(monkeypatch):
    import ctypes,win32gui
    from types import SimpleNamespace
    from contextlib import contextmanager
    from xiaomao.clipboard import NativeClipboard
    class Api:
        seq=10
        def GetClipboardSequenceNumber(self):return self.seq
        def EmptyClipboard(self):return True
        def SetClipboardData(self,fmt,handle):return handle
    c=NativeClipboard.__new__(NativeClipboard);c.user=Api()
    buffer=ctypes.create_string_buffer(100)
    c.kernel=SimpleNamespace(GlobalAlloc=lambda *a:42,GlobalLock=lambda handle:ctypes.addressof(buffer),
        GlobalUnlock=lambda handle:None,GlobalFree=lambda handle:None)
    @contextmanager
    def opened(owner):
        yield
        c.user.seq+=1  # Windows may synthesize text formats on CloseClipboard
    c.opened=opened
    monkeypatch.setattr(win32gui,'CreateWindowEx',lambda *a:99)
    monkeypatch.setattr(win32gui,'DestroyWindow',lambda *a:None)
    assert c.replace_text('测试',10)==11
