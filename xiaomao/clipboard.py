"""Ephemeral clipboard readback. Never logs, persists, or sends prior clipboard data."""
import ctypes
from ctypes import wintypes as W
from contextlib import contextmanager
import os
import struct
import time


class ClipboardUnavailable(RuntimeError):pass


class ClipboardSnapshotUnavailable(ClipboardUnavailable):
    """Read-only preparation failed; no clipboard write or editor input yet."""

class RichClipboard(ClipboardSnapshotUnavailable):
    """An active clipboard format cannot be losslessly replaced."""


def snapshot_for_paste(clipboard,assert_focus,wait):
    # Screenshot apps may advertise image formats before delayed rendering is
    # ready. Retry only this read-only stage, never Ctrl+V or a partial write.
    for attempt in range(3):
        assert_focus()
        try:return clipboard.snapshot()
        except RichClipboard:raise
        except ClipboardUnavailable:
            if attempt<2:wait(.08)
    raise ClipboardSnapshotUnavailable('剪贴板暂不可读取，尚未输入文字')


def pasted_text(clipboard,text,paste_action,assert_focus,wait,*,snapshot=None):
    """Use a temporary Unicode payload; never retry an uncertain paste.

    Clipboard formats remain in memory only. Restore them only while the
    sequence still belongs to this operation, preserving concurrent user copies.
    The caller must verify the actual editor contents before sending.
    """
    sequence,saved=snapshot if snapshot is not None else snapshot_for_paste(clipboard,assert_focus,wait)
    owned_sequence=None
    try:
        assert_focus()
        owned_sequence=clipboard.replace_text(text,sequence)
        if owned_sequence is None:raise ClipboardUnavailable('剪贴板已变化，未粘贴文字')
        assert_focus()
        if clipboard.current_sequence()!=owned_sequence:
            raise ClipboardUnavailable('剪贴板被其他程序更改，未粘贴其中内容')
        paste_action()
        wait(.15)  # let the target consume Ctrl+V before restoring all formats
        assert_focus()
    finally:
        if owned_sequence is not None:clipboard.restore(saved,owned_sequence)


def copied_text(clipboard,copy_action,assert_focus,wait,expected=None):
    original_sequence,saved=clipboard.snapshot()
    owned_sequence=None
    copy_started=False
    try:
        assert_focus();copy_started=True;copy_action()
        for _ in range(12):
            wait(.05)
            sequence,text,owned=clipboard.read_text()
            if sequence!=original_sequence:
                if not owned:raise ClipboardUnavailable('剪贴板被其他程序更改，未使用其中内容')
                owned_sequence=sequence
                assert_focus()
                if text is not None:return text
            assert_focus()
        raise ClipboardUnavailable('输入框复制暂未完成')
    finally:
        if owned_sequence is None and copy_started and expected is not None:
            try:
                sequence,text,owned=clipboard.read_text()
                if owned and sequence!=original_sequence and text==expected:owned_sequence=sequence
            except Exception:pass
        if owned_sequence is not None:clipboard.restore(saved,owned_sequence)


def file_drop(path):
    """Unicode DROPFILES payload; GIF bytes never touch image clipboard formats."""
    from pathlib import Path
    path=Path(path)
    if not path.is_absolute() or '\0' in str(path):raise ClipboardUnavailable('表情文件路径无效')
    return struct.pack('<IiiII',20,0,0,0,1)+(str(path)+'\0\0').encode('utf-16-le')


def pasted_file(clipboard,path,paste_action,assert_focus,wait):
    sequence,saved=snapshot_for_paste(clipboard,assert_focus,wait)
    owned=None
    try:
        assert_focus();owned=clipboard.restore({15:file_drop(path)},sequence)
        if owned is None:raise ClipboardUnavailable('剪贴板已变化，未粘贴表情')
        assert_focus()
        if clipboard.current_sequence()!=owned:raise ClipboardUnavailable('剪贴板已变化，未粘贴表情')
        paste_action();wait(.35);assert_focus()
    finally:
        if owned is not None:clipboard.restore(saved,owned)


class NativeClipboard:
    def __init__(self,pid):
        if os.name!='nt':raise ClipboardUnavailable('仅支持 Windows')
        self.pid=pid
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.user.GetClipboardData.argtypes=[W.UINT];self.user.GetClipboardData.restype=W.HANDLE
        self.user.SetClipboardData.argtypes=[W.UINT,W.HANDLE];self.user.SetClipboardData.restype=W.HANDLE
        self.user.GetClipboardOwner.restype=W.HWND
        self.user.GetWindowThreadProcessId.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]
        self.kernel.GlobalSize.argtypes=[W.HANDLE];self.kernel.GlobalSize.restype=ctypes.c_size_t
        self.kernel.GlobalLock.argtypes=[W.HANDLE];self.kernel.GlobalLock.restype=ctypes.c_void_p
        self.kernel.GlobalUnlock.argtypes=[W.HANDLE]
        self.kernel.GlobalAlloc.argtypes=[W.UINT,ctypes.c_size_t];self.kernel.GlobalAlloc.restype=W.HANDLE
        self.kernel.GlobalFree.argtypes=[W.HANDLE]

    @contextmanager
    def opened(self,owner=None):
        self.user.OpenClipboard.argtypes=[W.HWND]
        # Clipboard managers / WeChat delayed rendering hold this lock briefly.
        # Retry acquiring the lock only: never replay a write or Ctrl+V.
        for attempt in range(8):
            if self.user.OpenClipboard(owner):break
            if attempt==7:raise ClipboardUnavailable('剪贴板暂被占用')
            time.sleep(.025)
        try:yield
        finally:self.user.CloseClipboard()

    def _bytes(self,fmt):
        handle=self.user.GetClipboardData(fmt)
        if not handle:raise ClipboardUnavailable('剪贴板格式不可读取')
        size=self.kernel.GlobalSize(handle)
        if size>64*1024*1024:raise ClipboardUnavailable('剪贴板过大，保留原内容')
        ptr=self.kernel.GlobalLock(handle)
        if not ptr:raise ClipboardUnavailable('剪贴板格式暂不支持')
        try:return ctypes.string_at(ptr,size)
        finally:self.kernel.GlobalUnlock(handle)

    def _portable_formats(self):
        # Caller holds OpenClipboard. Windows synthesizes bitmap/palette
        # from DIB, but standalone handles and embedded metafiles are opaque.
        formats=[];fmt=0
        while True:
            fmt=self.user.EnumClipboardFormats(fmt)
            if not fmt:break
            formats.append(fmt)
        if any(f in formats for f in (2,9)) and not any(f in formats for f in (8,17)):
            raise RichClipboard('原剪贴板位图无法无损保存')
        if any(f in formats for f in (3,14)):
            raise RichClipboard('保留原剪贴板图元文件')
        return formats

    def check_formats(self):
        with self.opened():self._portable_formats()

    def snapshot(self):
        with self.opened():
            formats=self._portable_formats()
            saved={f:self._bytes(f) for f in formats if f not in (2,9)}
            return self.user.GetClipboardSequenceNumber(),saved

    def read_text(self):
        with self.opened():
            pid=W.DWORD()
            self.user.GetWindowThreadProcessId(self.user.GetClipboardOwner(),ctypes.byref(pid))
            owned=pid.value==self.pid
            text=None
            if owned and self.user.IsClipboardFormatAvailable(13):
                raw=self._bytes(13);text=raw.decode('utf-16-le').split('\0',1)[0]
            return self.user.GetClipboardSequenceNumber(),text,owned

    def current_sequence(self):
        return self.user.GetClipboardSequenceNumber()

    def replace_text(self,text,expected_sequence):
        return self.restore({13:(text+'\0').encode('utf-16-le')},expected_sequence)

    def restore(self,saved,expected_sequence):
        import win32gui
        allocated={}
        owner=win32gui.CreateWindowEx(0,'STATIC','',0,0,0,0,0,0,0,0,None)
        try:
            for fmt,data in saved.items():
                handle=self.kernel.GlobalAlloc(0x42,len(data) or 1)
                if not handle:raise ClipboardUnavailable('无法恢复原剪贴板')
                allocated[fmt]=handle
                pointer=self.kernel.GlobalLock(handle)
                if not pointer:raise ClipboardUnavailable('无法恢复原剪贴板')
                try:ctypes.memmove(pointer,data,len(data))
                finally:self.kernel.GlobalUnlock(handle)
            with self.opened(owner):
                if self.user.GetClipboardSequenceNumber()!=expected_sequence:return
                if not self.user.EmptyClipboard():raise ClipboardUnavailable('无法恢复原剪贴板')
                for fmt,handle in list(allocated.items()):
                    if not self.user.SetClipboardData(fmt,handle):raise ClipboardUnavailable('无法恢复原剪贴板')
                    del allocated[fmt]  # ownership transferred to Windows
            # CloseClipboard can publish synthesized text formats and increment
            # the sequence. Returning the in-lock number mislabels our own write
            # as an external clipboard edit and prevents Ctrl+V entirely.
            return self.user.GetClipboardSequenceNumber()
        finally:
            for handle in allocated.values():self.kernel.GlobalFree(handle)
            win32gui.DestroyWindow(owner)
