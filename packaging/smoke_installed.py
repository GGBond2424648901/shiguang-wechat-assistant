"""Verify the installed CLI, OCR and GUI against synthetic data only."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


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

            def do_POST(self):
                if self.headers.get("Authorization") != "Bearer " + token or self.path != "/stop":
                    self.send_error(403)
                    return
                calls.append(self.path)
                (runtime / "control.json").unlink(missing_ok=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"stopped":true}')

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
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline and proc.poll() is None and not calls:
                time.sleep(.2)
            assert calls and proc.poll() is None, f"GUI never connected (exit={proc.poll()})"
            # Use the app's own exit IPC, not WM_CLOSE (which now asks what to do).
            from xiaomao.panel_entry import request_stop_and_exit
            errors=[]
            def request_exit():
                try:
                    request_stop_and_exit(data,SimpleNamespace(stop=lambda:errors.append('GUI exit channel missing')))
                except Exception as error:errors.append(type(error).__name__)
            shutdown=threading.Thread(target=request_exit,daemon=True)
            shutdown.start();shutdown.join(20)
            assert not shutdown.is_alive() and not errors, errors
            if proc.wait(timeout=20) != 0:
                raise RuntimeError("Installed GUI exited with an error")
            assert "/stop" in calls and not (runtime / "control.json").exists()
            print(json.dumps({"doctor":doctor, "synthetic_status_requests":calls.count('/status'),
                              "full_exit_requested":True,"closed":True},ensure_ascii=False))
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
