"""Verify the installed CLI, OCR and GUI against synthetic data only."""
import argparse
import ctypes
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time


def smoke(app_dir):
    with tempfile.TemporaryDirectory(prefix="xiaomao-release-test-") as temporary:
        data = Path(temporary) / "profile"
        runtime = data / "runtime"
        runtime.mkdir(parents=True)
        token = secrets.token_hex(32)
        calls = []

        class Status(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer " + token or self.path != "/status":
                    self.send_error(403)
                    return
                calls.append(self.path)
                payload = json.dumps({"mode":"observe", "reason":"离线安装检查", "group":"示例会话",
                                      "counts":{}, "pending_count":0}, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        # Force this process tree away from the real installed user profile.
        env = {**os.environ, "LOCALAPPDATA":str(data.parent / "localappdata")}
        cli = [str(app_dir / "XiaomaoCLI.exe"), "--root", str(data)]
        result = subprocess.run([*cli, "doctor", "--ocr"], env=env, capture_output=True,
                                encoding="utf-8", timeout=90, check=True)
        doctor = json.loads(result.stdout.splitlines()[-1])
        assert doctor["ocr_verified"] and not doctor["calibrated"] and not doctor["credential_encrypted"], doctor
        server = ThreadingHTTPServer(("127.0.0.1", 0), Status)
        (runtime / "control.json").write_text(json.dumps({"host":"127.0.0.1", "port":server.server_port,
                                                        "token":token}), "utf-8")
        threading.Thread(target=server.serve_forever,daemon=True).start()
        proc = subprocess.Popen([str(app_dir / "Xiaomao.exe"), "--root", str(data), "gui"], env=env)
        user = ctypes.WinDLL("user32", use_last_error=True)
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.IsWindowVisible.argtypes = [wintypes.HWND]
        user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        found = []
        @callback
        def inspect(hwnd, _):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == proc.pid and user.IsWindowVisible(hwnd):
                title = ctypes.create_unicode_buffer(512)
                user.GetWindowTextW(hwnd, title, len(title))
                if title.value:
                    found.append((hwnd, title.value))
            return True
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline and proc.poll() is None:
                found.clear()
                user.EnumWindows(inspect, 0)
                if found and calls:
                    break
                time.sleep(.2)
            assert found and calls, f"No application window or control request (exit={proc.poll()})"
            user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            if not user.PostMessageW(found[0][0], 0x10, 0, 0):
                raise OSError("Could not close the test GUI")
            if proc.wait(timeout=20) != 0:
                raise RuntimeError("Installed GUI exited with an error")
            print(json.dumps({"doctor":doctor, "window":found[0][1], "synthetic_status_requests":len(calls),
                              "closed":True},ensure_ascii=False))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installation",type=Path)
    smoke(parser.parse_args().installation.resolve())
