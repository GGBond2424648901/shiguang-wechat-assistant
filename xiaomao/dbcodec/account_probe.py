"""Bounded read-only login evidence from known message files, never all handles.

RmGetList queries who is using registered files. No shutdown/restart API is used.
All native work stays in a disposable process, outside the observer's GIL.
"""
import ctypes
from ctypes import wintypes as W
import multiprocessing as mp
from pathlib import Path
import re


def file_users(paths):
    class Unique(ctypes.Structure):
        _fields_=[('pid',W.DWORD),('start',W.FILETIME)]
    class Info(ctypes.Structure):
        _fields_=[('process',Unique),('app',W.WCHAR*256),('service',W.WCHAR*64),
                  ('type',ctypes.c_int),('status',W.ULONG),('session',W.DWORD),('restartable',W.BOOL)]
    rm=ctypes.WinDLL('Rstrtmgr')
    rm.RmStartSession.argtypes=[ctypes.POINTER(W.DWORD),W.DWORD,W.LPWSTR]
    rm.RmRegisterResources.argtypes=[W.DWORD,W.UINT,ctypes.POINTER(W.LPCWSTR),W.UINT,
                                    ctypes.POINTER(Unique),W.UINT,ctypes.POINTER(W.LPCWSTR)]
    rm.RmGetList.argtypes=[W.DWORD,ctypes.POINTER(W.UINT),ctypes.POINTER(W.UINT),
                          ctypes.POINTER(Info),ctypes.POINTER(W.DWORD)]
    rm.RmEndSession.argtypes=[W.DWORD]
    for name in ('RmStartSession','RmRegisterResources','RmGetList','RmEndSession'):
        getattr(rm,name).restype=W.DWORD
    handle=W.DWORD();key=ctypes.create_unicode_buffer(33)
    code=rm.RmStartSession(ctypes.byref(handle),0,key)
    if code:raise OSError(code,'Account probe could not start')
    try:
        files=(W.LPCWSTR*len(paths))(*(str(p) for p in paths))
        code=rm.RmRegisterResources(handle,len(paths),files,0,None,0,None)
        if code:raise OSError(code,'Message files could not be queried')
        size=0
        for _ in range(3):
            need=W.UINT();count=W.UINT(size);reason=W.DWORD()
            data=(Info*size)() if size else None
            code=rm.RmGetList(handle,ctypes.byref(need),ctypes.byref(count),data,ctypes.byref(reason))
            if code==0:return {int(p.process.pid) for p in (data or [])[:count.value]}
            if code!=234 or need.value>4096:raise OSError(code,'Account evidence unavailable')
            size=need.value
        raise OSError('Account evidence changed during query')
    finally:rm.RmEndSession(handle)


def _resolve_accounts(accounts,pids,query=file_users):
    active=set()
    for account in accounts:
        folder=Path(account['path'])/'db_storage/message'
        files=sorted(p for p in folder.glob('message_*.db') if re.fullmatch(r'message_\d+\.db',p.name,re.I) and p.is_file())
        if files and set(query(files)) & pids:active.add(account['id'])
    return active


def _probe_worker(accounts,pids,connection):
    try:
        try:result=_resolve_accounts(accounts,pids)
        except Exception:result=set()  # failure is unknown; never use old directory timestamps
        connection.send(result)
    finally:connection.close()


def active_accounts(accounts,processes,timeout=8):
    pids={int(p['pid']) for p in processes if isinstance(p.get('pid'),int) and p['pid']>0}
    if not accounts or not pids:return set()
    allowed={a['id'] for a in accounts}
    context=mp.get_context('spawn');receiver,sender=context.Pipe(duplex=False)
    process=context.Process(target=_probe_worker,args=(accounts,pids,sender),daemon=True)
    try:
        process.start();sender.close()
        if not receiver.poll(timeout):return set()
        result=receiver.recv()
        return result if isinstance(result,set) and result<=allowed else set()
    except (OSError,EOFError):return set()
    finally:
        receiver.close();sender.close()
        if process.pid is not None:
            process.join(.1)
            if process.is_alive():process.terminate();process.join(1)
            process.close()
