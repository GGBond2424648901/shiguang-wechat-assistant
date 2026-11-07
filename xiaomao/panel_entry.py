"""One GUI per profile, reopened by the resident-owned notification icon."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time


def _identity(root):
    return hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()


class PanelLease:
    def __init__(self,root):
        import win32api,win32event,winerror
        self.name='Local\\Shiguang.Panel.'+_identity(root)
        self.handle=win32event.CreateMutex(None,True,self.name)
        self.owner=win32api.GetLastError()!=winerror.ERROR_ALREADY_EXISTS
        self.event=None
        self.exit_event=self.exit_accepted=self.exit_cancelled=None

    def open_signal(self):
        import win32event
        if not self.owner:raise ValueError('面板已由另一个进程打开')
        if self.event is None:self.event=win32event.CreateEvent(None,False,False,self.name+'.Show')
        for field,suffix in (('exit_event','.Exit'),('exit_accepted','.ExitAccepted'),('exit_cancelled','.ExitCancelled')):
            if getattr(self,field) is None:
                setattr(self,field,win32event.CreateEvent(None,False,False,self.name+suffix))

    def consume_signal(self):
        import win32event
        return bool(self.event and win32event.WaitForSingleObject(self.event,0)==win32event.WAIT_OBJECT_0)

    def consume_exit_signal(self):
        import win32event
        return bool(self.exit_event and win32event.WaitForSingleObject(self.exit_event,0)==win32event.WAIT_OBJECT_0)

    def acknowledge_exit(self,accepted):
        import win32event
        win32event.SetEvent(self.exit_accepted if accepted else self.exit_cancelled)

    def close(self):
        import win32api,win32event
        for field in ('event','exit_event','exit_accepted','exit_cancelled'):
            handle=getattr(self,field)
            if handle is not None:win32api.CloseHandle(handle);setattr(self,field,None)
        if self.handle is not None:
            if self.owner:win32event.ReleaseMutex(self.handle)
            win32api.CloseHandle(self.handle);self.handle=None


def signal_panel(root):
    """Signal Tk's own thread; never manipulate Tk from a tray callback."""
    import win32api,win32event
    try:handle=win32event.OpenEvent(2,False,'Local\\Shiguang.Panel.'+_identity(root)+'.Show')
    except win32api.error:return False
    try:win32event.SetEvent(handle);return True
    finally:win32api.CloseHandle(handle)


def _root_exists(root):
    try:return bool(root.winfo_exists())
    except Exception:return False  # Tk may already have destroyed its interpreter.


def watch_panel_signal(root,lease,on_exit=None,on_show=None):
    if not _root_exists(root):return
    if on_exit is not None and lease.consume_exit_signal():
        root.deiconify();root.lift();root.focus_force()
        try:accepted=bool(on_exit())
        except Exception:
            lease.acknowledge_exit(False)
            if _root_exists(root):root.after(200,lambda:watch_panel_signal(root,lease,on_exit,on_show))
            raise
        lease.acknowledge_exit(accepted)
    elif lease.consume_signal():
        root.deiconify();root.lift();root.focus_force()
        # A second launcher invocation must also recover a missing resident.
        # Merely polling this event must never restart a deliberately stopped one.
        if on_show is not None:on_show()
    if _root_exists(root):root.after(200,lambda:watch_panel_signal(root,lease,on_exit,on_show))


def _panel_present(name):
    import win32api,win32event
    try:handle=win32event.OpenMutex(0x00100000,False,name)
    except win32api.error as error:
        if error.winerror==2:return False
        raise ValueError('无法核对助手面板，退出未执行，请从面板重试') from None
    win32api.CloseHandle(handle)
    return True


def _exit_channel(name):
    import win32api,win32event
    handles=[]
    try:
        for suffix,access in (('.Exit',2),('.ExitAccepted',0x00100002),('.ExitCancelled',0x00100002)):
            handles.append(win32event.OpenEvent(access,False,name+suffix))
        return handles
    except win32api.error as error:
        for handle in handles:win32api.CloseHandle(handle)
        if error.winerror!=2:
            raise ValueError('无法联系助手面板，退出未执行，请从面板重试') from None
        return None


def request_stop_and_exit(root,resident):
    """Let an existing GUI own its dirty guard and shutdown; no Tk calls here.

    This runs on a tray action thread. Once delivered, a request may wait while
    the user reviews unsaved edits. Cancellation is acknowledged separately so
    a later ordinary panel close cannot accidentally stop the resident.
    """
    import win32api,win32event
    name='Local\\Shiguang.Panel.'+_identity(root)
    deadline=time.monotonic()+2
    while True:
        handles=_exit_channel(name)
        if handles:break
        if not _panel_present(name):resident.stop();return
        if time.monotonic()>=deadline:
            raise ValueError('助手面板仍在启动，退出请求尚未送达，请稍后从托盘重试')
        time.sleep(.05)
    try:
        for handle in handles[1:]:win32event.ResetEvent(handle)
        win32event.SetEvent(handles[0])
        while True:
            result=win32event.WaitForMultipleObjects(handles[1:],False,100)
            if result in (win32event.WAIT_OBJECT_0,win32event.WAIT_OBJECT_0+1):return
            if not _panel_present(name):resident.stop();return
    finally:
        for handle in handles:win32api.CloseHandle(handle)


def show_or_launch_panel(root):
    root=Path(root).resolve()
    if signal_panel(root):return
    executable=Path(sys.executable)
    if not getattr(sys,'frozen',False) and executable.with_name('pythonw.exe').exists():
        executable=executable.with_name('pythonw.exe')
    arguments=[str(executable)]
    if not getattr(sys,'frozen',False):arguments+=['-m','xiaomao']
    arguments+=['--root',str(root),'gui']
    cwd=executable.parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent.parent
    from .launch import in_job,_launch_wmi
    if in_job():_launch_wmi(arguments,cwd);return
    flags=(subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP|0x01000000) if os.name=='nt' else 0
    try:
        subprocess.Popen(arguments,cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,close_fds=True,creationflags=flags)
    except PermissionError:
        if os.name!='nt':raise
        _launch_wmi(arguments,cwd)


def resident_tray(root,resident,*,tray_factory=None,opener=show_or_launch_panel):
    from .tray import TrayIcon
    def status():
        data=resident.status()
        mode={'auto':'自动回复中','paused':'已暂停','observe':'只观察'}.get(data['mode'],'运行中')
        return f"{mode} · {len(data.get('group_names',()))} 个会话 · 待处理 {data.get('pending_count',0)} 条"
    def failed(error):resident.tray_error=str(error)
    tray=(tray_factory or TrayIcon)(on_open=lambda:opener(root),on_pause=resident.pause,
        on_exit=lambda:request_stop_and_exit(root,resident),status_text=status,on_error=failed)
    return tray.start()
