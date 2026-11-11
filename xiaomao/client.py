"""GUI 与 MCP 共用的受限本机控制客户端。"""
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import httpx
from .privacy import private_dir


class ControlUnavailable(RuntimeError):
    pass


class ControlConnecting(ControlUnavailable):
    """The current worker is present but its state cannot be confirmed yet."""

    def __init__(self, message="后台仍在运行，正在自动重连"):
        super().__init__(message)


class ControlClient:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._pid=None

    def _endpoint(self):
        try:
            data = json.loads((self.root / "runtime" / "control.json").read_text(encoding="utf-8"))
            if not isinstance(data,dict) or data.get("host") != "127.0.0.1" or not isinstance(data["port"], int) or not 1024 <= data["port"] <= 65535 or not isinstance(data["token"], str) or len(data["token"]) < 32:
                raise ValueError()
            self._pid=data.get('pid') if isinstance(data.get('pid'),int) and data['pid']>0 else None
            return f"http://127.0.0.1:{data['port']}", data["token"]
        except (OSError, ValueError, KeyError, TypeError):
            raise ControlUnavailable("助手尚未运行，或本机控制信息无效") from None

    def request(self, operation: str, payload=None) -> dict:
        if operation not in ("status", "pending", "observe", "enable", "pause", "stop", "reply_latest", "companion_preview", "companion_once","reload_windows","stop_if_idle","queue_text","queue_outbound","companion_configure","enable_selected","pause_selected","configure_reply_rules","configure_reply_defaults","news_command","news_invalidate"):
            raise ControlUnavailable("不支持这个控制操作")
        url, token = self._endpoint()
        try:
            with httpx.Client(timeout=3, follow_redirects=False, trust_env=False) as client:
                headers = {"Authorization": "Bearer " + token}
                response = client.get(url+"/"+operation, headers=headers) if operation in ("status", "pending") else client.post(url+"/"+operation, headers=headers, json=payload or {})
                data = response.json()
        except (httpx.HTTPError, ValueError):
            raise ControlUnavailable("无法连接本机助手") from None
        if response.status_code != 200:
            raise ControlUnavailable(data.get("error", "控制请求失败"))
        return {"running": True, **data}

    def status(self):
        try:
            return self.request("status")
        except ControlUnavailable:
            from .windows import worker_scope
            if worker_scope(self.root) == "current":
                raise ControlConnecting() from None
            raise

    def pause(self): return self.request("pause")
    def observe(self): return self.request("observe")
    def enable(self): return self.request("enable")
    def pending(self): return self.request("pending")
    def stop(self): return self.request("stop")
    def stop_if_idle(self):return self.request('stop_if_idle')
    def reply_latest(self,**target):return self.request('reply_latest',target)
    def companion_preview(self,**target):return self.request('companion_preview',target)
    def companion_once(self,**target):return self.request('companion_once',target)
    def companion_configure(self,group,scope,preferences):
        return self.request('companion_configure',dict(group=group,scope=scope,preferences=preferences))
    def enable_selected(self,group,scope):
        return self.request('enable_selected',dict(group=group,scope=scope))
    def pause_selected(self,group,scope):
        return self.request('pause_selected',dict(group=group,scope=scope))
    def reload_windows(self):return self.request('reload_windows')
    def news_command(self, subscription, action, edition=None):
        return self.request('news_command', dict(subscription=subscription, action=action, edition=edition))
    def news_invalidate(self, account, subscription):
        return self.request('news_invalidate', dict(account=account, subscription=subscription))
    def configure_reply_rules(self,group,scope,preferences,*,expected=None):
        payload=dict(group=group,scope=scope,preferences=preferences)
        if expected is not None:payload['expected']=expected
        return self.request('configure_reply_rules',payload)
    def configure_reply_defaults(self,preferences):
        return self.request('configure_reply_defaults',dict(preferences=preferences))
    def queue_text(self, group, text, request_id):
        return self.request('queue_text',dict(group=group,text=text,request_id=request_id))

    def queue_outbound(self,group,scope,text,sticker_ids,request_id):
        return self.request('queue_outbound',dict(group=group,scope=scope,text=text,
                                               sticker_ids=sticker_ids,request_id=request_id))

    def start(self,initial_mode=None) -> dict:
        if initial_mode not in (None,'auto','observe','paused'):raise ValueError('启动模式无效')
        from .launch import spawn_worker
        from .windows import worker_scope
        conflict = "另一份拾光微信聊天助手后台已在运行。请在原面板的「运行设置」点击「完全退出」，再启动当前助手。"
        deadline = time.monotonic()+15
        started = False
        while time.monotonic() < deadline:
            try:
                return self.status()
            except ControlUnavailable:
                pass
            scope = worker_scope(self.root)
            if scope == "other":
                raise ControlUnavailable(conflict)
            if scope is None and not started:
                directory = private_dir(self.root / "runtime")
                with (directory / "worker.log").open("ab") as output:
                    spawn_worker(self.root, output, initial_mode=initial_mode)
                started = True
                deadline = time.monotonic()+15
            time.sleep(.2)
        scope = worker_scope(self.root)
        if scope == "other":
            raise ControlUnavailable(conflict)
        try:
            with (self.root / "runtime" / "worker.log").open("a", encoding="utf-8") as output:
                timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
                print(f"{timestamp} [control] status deadline, worker scope: {scope or 'none'}", file=output)
        except OSError:
            pass
        if scope == "current":
            raise ControlConnecting()
        if scope == "unidentified":
            raise ControlUnavailable("桌面已有小猫后台，当前数据目录未连接。请在原面板「完全退出」后再试。")
        raise ControlUnavailable("助手启动失败，请查看本机 runtime/worker.log")

    def wait_stopped(self, timeout=45):
        deadline = time.monotonic()+timeout
        path = self.root / "runtime" / "control.json"
        while (path.exists() or self._process_alive()) and time.monotonic() < deadline:
            time.sleep(.1)
        if path.exists() or self._process_alive():
            raise ControlUnavailable("助手仍在等待当前请求结束，请稍后重试")

    def _process_alive(self):
        if not self._pid:return False
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes as W
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];kernel.OpenProcess.restype=W.HANDLE
            kernel.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD];kernel.CloseHandle.argtypes=[W.HANDLE]
            handle=kernel.OpenProcess(0x100000,False,self._pid)
            if not handle:return ctypes.get_last_error() not in (87,1168)
            try:return kernel.WaitForSingleObject(handle,0)!=0
            finally:kernel.CloseHandle(handle)
        try:os.kill(self._pid,0);return True
        except ProcessLookupError:return False
