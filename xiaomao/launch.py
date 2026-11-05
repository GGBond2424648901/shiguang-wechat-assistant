"""普通本机守护进程启动；宿主作业不允许分离时由本机WMI创建。"""
import os
from pathlib import Path
import subprocess
import sys
import ctypes


def in_job():
    if os.name!='nt':return False
    from ctypes import wintypes as W
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=W.HANDLE
    kernel.IsProcessInJob.argtypes=[W.HANDLE,W.HANDLE,ctypes.POINTER(W.BOOL)]
    result=W.BOOL()
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(),None,ctypes.byref(result)):
        raise OSError('无法核实本机启动进程状态')
    return bool(result.value)


def _launch_wmi(arguments,root):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    service=startup=process=params=result=None
    try:
        service=win32com.client.Dispatch('WbemScripting.SWbemLocator').ConnectServer('.','root\\cimv2')
        startup=service.Get('Win32_ProcessStartup').SpawnInstance_()
        startup.ShowWindow=0
        startup.CreateFlags=0x01000000|0x08000000  # BREAKAWAY_FROM_JOB | NO_WINDOW
        process=service.Get('Win32_Process')
        params=process.Methods_('Create').InParameters.SpawnInstance_()
        params.CommandLine=subprocess.list2cmdline(arguments)
        params.CurrentDirectory=str(root)
        params.ProcessStartupInformation=startup
        result=process.ExecMethod_('Create',params)
        if result.ReturnValue!=0:raise OSError('本机独立进程启动失败，返回代码'+str(result.ReturnValue))
        return int(result.ProcessId)
    finally:
        result=params=process=startup=service=None
        pythoncom.CoUninitialize()


def spawn_worker(root,output,initial_mode=None):
    executable=Path(sys.executable)
    frozen = getattr(sys, 'frozen', False)
    if not frozen and os.name=='nt' and executable.with_name('pythonw.exe').exists():
        executable=executable.with_name('pythonw.exe')
    arguments=[str(executable)]
    if not frozen:
        arguments.extend(['-m', 'xiaomao'])
    arguments.extend(['--root', str(Path(root).resolve()), 'daemon'])
    if initial_mode is not None:
        if initial_mode not in ('auto','observe','paused'):raise ValueError('启动模式无效')
        arguments.extend(['--initial-mode',initial_mode])
    cwd = executable.parent if frozen else Path(__file__).resolve().parent.parent
    options=dict(cwd=cwd,stdin=subprocess.DEVNULL,stdout=output,stderr=output,close_fds=True)
    if frozen:
        options['env'] = {**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'}
    flags=(subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP|0x01000000) if os.name=='nt' else 0
    if in_job():return _launch_wmi(arguments,cwd)
    try:return subprocess.Popen(arguments,**options,creationflags=flags)
    except PermissionError:
        if os.name!='nt':raise
        return _launch_wmi(arguments,cwd)
